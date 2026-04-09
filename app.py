import os
import csv
import uuid
import logging
import threading
import time
import zipfile
from flask import Flask, render_template, request, jsonify, session, send_file
import io

from ai_marking import mark_script, get_available_providers, PROVIDERS
from pdf_generator import generate_report_pdf

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

ACCESS_CODE = os.getenv('ACCESS_CODE', 'DEMO2026')
PROVIDE_KEYS = os.getenv('PROVIDE_KEYS', 'TRUE').upper() == 'TRUE'

# In-memory job store (thread-safe via GIL for dict ops)
jobs = {}
JOB_TTL_SECONDS = 3600  # 1 hour


def cleanup_old_jobs():
    """Remove jobs older than TTL."""
    now = time.time()
    expired = [jid for jid, j in jobs.items() if now - j['created_at'] > JOB_TTL_SECONDS]
    for jid in expired:
        del jobs[jid]


def _get_session_keys():
    """Get session-stored API keys (used when PROVIDE_KEYS is FALSE or for bulk)."""
    return session.get('api_keys') or {}


def _effective_keys(force_session=False):
    """Return session keys dict if server keys are disabled or force_session is True."""
    if force_session or not PROVIDE_KEYS:
        return _get_session_keys()
    return None  # Use server env keys


# ---------------------------------------------------------------------------
# Single marking
# ---------------------------------------------------------------------------

def run_marking_job(job_id, provider, model, question_paper_pages, answer_key_pages,
                    script_pages, subject, rubrics_pages, reference_pages,
                    review_instructions, marking_instructions,
                    assign_type, scoring_mode, total_marks, session_keys):
    """Background thread for AI marking."""
    try:
        result = mark_script(
            provider=provider,
            question_paper_pages=question_paper_pages,
            answer_key_pages=answer_key_pages,
            script_pages=script_pages,
            subject=subject,
            rubrics_pages=rubrics_pages,
            reference_pages=reference_pages,
            review_instructions=review_instructions,
            marking_instructions=marking_instructions,
            model=model,
            assign_type=assign_type,
            scoring_mode=scoring_mode,
            total_marks=total_marks,
            session_keys=session_keys,
        )
        jobs[job_id]['result'] = result
        jobs[job_id]['status'] = 'error' if result.get('error') else 'done'
    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        jobs[job_id]['result'] = {'error': str(e)}
        jobs[job_id]['status'] = 'error'


@app.route('/')
def index():
    authenticated = session.get('authenticated', False)
    sk = _effective_keys()
    providers = get_available_providers(session_keys=sk)
    return render_template('index.html',
                           authenticated=authenticated,
                           providers=providers,
                           provide_keys=PROVIDE_KEYS,
                           all_providers=PROVIDERS)


@app.route('/verify-code', methods=['POST'])
def verify_code():
    data = request.get_json()
    code = (data.get('code') or '').strip()
    if code == ACCESS_CODE:
        session['authenticated'] = True
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Invalid access code'}), 401


@app.route('/save-keys', methods=['POST'])
def save_keys():
    """Save user-provided API keys to session."""
    if not session.get('authenticated'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    data = request.get_json()
    keys = {}
    for prov in ('anthropic', 'openai', 'qwen'):
        val = (data.get(prov) or '').strip()
        if val:
            keys[prov] = val
    session['api_keys'] = keys
    sk = keys if (not PROVIDE_KEYS) else None
    providers = get_available_providers(session_keys=sk)
    return jsonify({'success': True, 'providers': {k: v for k, v in providers.items()}})


@app.route('/clear-keys', methods=['POST'])
def clear_keys():
    """Clear session API keys."""
    session.pop('api_keys', None)
    return jsonify({'success': True})


@app.route('/mark', methods=['POST'])
def mark():
    if not session.get('authenticated'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    assign_type = request.form.get('assign_type', 'short_answer')

    # Validate required files (answer_key not required for rubrics mode)
    required_fields = ['question_paper', 'script']
    if assign_type != 'rubrics':
        required_fields.append('answer_key')
    for field in required_fields:
        files = request.files.getlist(field)
        if not files or not files[0].filename:
            return jsonify({'success': False, 'error': f'Missing required file: {field}'}), 400
        if len(files) > 5:
            return jsonify({'success': False, 'error': f'Maximum 5 files per upload ({field})'}), 400

    provider = request.form.get('provider', 'anthropic')
    model = request.form.get('model', '')
    subject = request.form.get('subject', '')
    scoring_mode = request.form.get('scoring_mode', 'status')
    total_marks = request.form.get('total_marks', '')
    review_instructions = request.form.get('review_instructions', '')
    marking_instructions = request.form.get('marking_instructions', '')

    question_paper_pages = [f.read() for f in request.files.getlist('question_paper') if f.filename]
    answer_key_pages = [f.read() for f in request.files.getlist('answer_key') if f.filename]
    script_pages = [f.read() for f in request.files.getlist('script') if f.filename]
    rubrics_pages = [f.read() for f in request.files.getlist('rubrics') if f.filename]
    reference_pages = [f.read() for f in request.files.getlist('reference') if f.filename]

    session_keys = _effective_keys()

    cleanup_old_jobs()

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        'status': 'processing',
        'result': None,
        'subject': subject,
        'created_at': time.time(),
    }

    thread = threading.Thread(
        target=run_marking_job,
        args=(job_id, provider, model, question_paper_pages, answer_key_pages,
              script_pages, subject, rubrics_pages, reference_pages,
              review_instructions, marking_instructions,
              assign_type, scoring_mode, total_marks, session_keys),
        daemon=True
    )
    thread.start()

    return jsonify({'success': True, 'job_id': job_id})


@app.route('/status/<job_id>')
def job_status(job_id):
    if not session.get('authenticated'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    job = jobs.get(job_id)
    if not job:
        return jsonify({'success': False, 'error': 'Job not found'}), 404

    response = {'success': True, 'status': job['status']}
    if job['status'] in ('done', 'error'):
        # Bulk jobs store results in 'results' (list), single in 'result' (dict)
        if job.get('bulk'):
            response['result'] = job.get('results', [])
        else:
            response['result'] = job['result']
    if 'progress' in job:
        response['progress'] = job['progress']
    return jsonify(response)


@app.route('/download/<job_id>')
def download_pdf(job_id):
    if not session.get('authenticated'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    job = jobs.get(job_id)
    if not job or job['status'] != 'done':
        return jsonify({'success': False, 'error': 'No results available'}), 404

    pdf_bytes = generate_report_pdf(job['result'], subject=job.get('subject', ''))

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name='AI_Marking_Report.pdf'
    )


# ---------------------------------------------------------------------------
# Bulk marking
# ---------------------------------------------------------------------------

def _split_pdf(pdf_bytes, pages_per_student):
    """Split a PDF into chunks of N pages. Returns list of PDF bytes."""
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(io.BytesIO(pdf_bytes))
    total = len(reader.pages)
    chunks = []
    for start in range(0, total, pages_per_student):
        writer = PdfWriter()
        for p in range(start, min(start + pages_per_student, total)):
            writer.add_page(reader.pages[p])
        buf = io.BytesIO()
        writer.write(buf)
        chunks.append(buf.getvalue())
    return chunks


def _parse_class_list(file_bytes, filename):
    """Parse class list CSV. Returns list of {index, name} dicts."""
    text = file_bytes.decode('utf-8-sig')
    reader = csv.reader(io.StringIO(text))
    students = []
    for row in reader:
        if not row or not any(cell.strip() for cell in row):
            continue
        # Skip header row
        if row[0].strip().lower() in ('index', 'no', 'no.', 's/n', 'sn', '#', 'number'):
            continue
        if len(row) >= 2:
            students.append({'index': row[0].strip(), 'name': row[1].strip()})
        else:
            students.append({'index': str(len(students) + 1), 'name': row[0].strip()})
    return students


def run_bulk_marking_job(job_id, provider, model, question_paper_pages, answer_key_pages,
                         rubrics_pages, reference_pages, student_scripts, students,
                         subject, review_instructions, marking_instructions,
                         assign_type, scoring_mode, total_marks, session_keys):
    """Background thread for bulk marking — marks each student sequentially."""
    results = []
    total = len(students)

    for i, (student, script_bytes) in enumerate(zip(students, student_scripts)):
        jobs[job_id]['progress'] = {
            'current': i + 1,
            'total': total,
            'current_name': student['name'],
        }

        try:
            result = mark_script(
                provider=provider,
                question_paper_pages=question_paper_pages,
                answer_key_pages=answer_key_pages,
                script_pages=[script_bytes],
                subject=subject,
                rubrics_pages=rubrics_pages,
                reference_pages=reference_pages,
                review_instructions=review_instructions,
                marking_instructions=marking_instructions,
                model=model,
                assign_type=assign_type,
                scoring_mode=scoring_mode,
                total_marks=total_marks,
                session_keys=session_keys,
            )
        except Exception as e:
            logger.error(f"Bulk job {job_id}, student {student['name']} failed: {e}")
            result = {'error': str(e)}

        results.append({
            'index': student['index'],
            'name': student['name'],
            'result': result,
        })

    jobs[job_id]['results'] = results
    jobs[job_id]['status'] = 'done'
    jobs[job_id]['progress'] = {'current': total, 'total': total, 'current_name': 'Complete'}


@app.route('/bulk')
def bulk_page():
    authenticated = session.get('authenticated', False)
    sk = _get_session_keys()
    providers = get_available_providers(session_keys=sk)
    return render_template('bulk.html',
                           authenticated=authenticated,
                           providers=providers,
                           all_providers=PROVIDERS)


@app.route('/bulk/mark', methods=['POST'])
def bulk_mark():
    if not session.get('authenticated'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    session_keys = _get_session_keys()
    if not session_keys:
        return jsonify({'success': False, 'error': 'Please enter your API key first'}), 400

    # Validate files
    for field in ('class_list', 'question_paper', 'bulk_scripts'):
        files = request.files.getlist(field)
        if not files or not files[0].filename:
            return jsonify({'success': False, 'error': f'Missing required file: {field}'}), 400

    assign_type = request.form.get('assign_type', 'short_answer')
    if assign_type != 'rubrics':
        ak_files = request.files.getlist('answer_key')
        if not ak_files or not ak_files[0].filename:
            return jsonify({'success': False, 'error': 'Missing required file: answer_key'}), 400

    pages_per_student = request.form.get('pages_per_student', '')
    try:
        pages_per_student = int(pages_per_student)
        if pages_per_student < 1:
            raise ValueError
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Please enter a valid pages per student number'}), 400

    # Parse class list
    cl_file = request.files.get('class_list')
    students = _parse_class_list(cl_file.read(), cl_file.filename)
    if not students:
        return jsonify({'success': False, 'error': 'Could not parse class list. Use CSV with columns: Index, Name'}), 400

    # Split bulk PDF
    bulk_pdf = request.files.get('bulk_scripts').read()
    try:
        student_scripts = _split_pdf(bulk_pdf, pages_per_student)
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error splitting PDF: {e}'}), 400

    if len(student_scripts) != len(students):
        return jsonify({
            'success': False,
            'error': f'Mismatch: class list has {len(students)} students but PDF splits into '
                     f'{len(student_scripts)} scripts ({len(student_scripts) * pages_per_student} pages ÷ '
                     f'{pages_per_student} pages/student). Check your pages per student setting.'
        }), 400

    provider = request.form.get('provider', 'anthropic')
    model = request.form.get('model', '')
    subject = request.form.get('subject', '')
    scoring_mode = request.form.get('scoring_mode', 'status')
    total_marks = request.form.get('total_marks', '')
    review_instructions = request.form.get('review_instructions', '')
    marking_instructions = request.form.get('marking_instructions', '')

    question_paper_pages = [f.read() for f in request.files.getlist('question_paper') if f.filename]
    answer_key_pages = [f.read() for f in request.files.getlist('answer_key') if f.filename]
    rubrics_pages = [f.read() for f in request.files.getlist('rubrics') if f.filename]
    reference_pages = [f.read() for f in request.files.getlist('reference') if f.filename]

    cleanup_old_jobs()

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        'status': 'processing',
        'result': None,
        'results': [],
        'subject': subject,
        'created_at': time.time(),
        'progress': {'current': 0, 'total': len(students), 'current_name': 'Starting...'},
        'bulk': True,
    }

    thread = threading.Thread(
        target=run_bulk_marking_job,
        args=(job_id, provider, model, question_paper_pages, answer_key_pages,
              rubrics_pages, reference_pages, student_scripts, students,
              subject, review_instructions, marking_instructions,
              assign_type, scoring_mode, total_marks, session_keys),
        daemon=True
    )
    thread.start()

    return jsonify({'success': True, 'job_id': job_id})


@app.route('/bulk/download/<job_id>')
def bulk_download(job_id):
    if not session.get('authenticated'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    job = jobs.get(job_id)
    if not job or job['status'] != 'done' or not job.get('results'):
        return jsonify({'success': False, 'error': 'No results available'}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in job['results']:
            if item['result'].get('error'):
                continue
            pdf_bytes = generate_report_pdf(item['result'], subject=job.get('subject', ''))
            safe_name = item['name'].replace('/', '_').replace('\\', '_')
            zf.writestr(f"{item['index']}_{safe_name}_report.pdf", pdf_bytes)
    buf.seek(0)

    return send_file(
        buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name='Bulk_Marking_Reports.zip'
    )


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
