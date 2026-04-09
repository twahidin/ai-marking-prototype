import os
import csv
import uuid
import string
import random
import logging
import threading
import time
import zipfile
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify, session, send_file, redirect, url_for
import io

from ai_marking import mark_script, get_available_providers, PROVIDERS
from pdf_generator import generate_report_pdf, generate_overview_pdf
from db import db, init_db, Assignment, Student, Submission

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

ACCESS_CODE = os.getenv('ACCESS_CODE', 'DEMO2026')
PROVIDE_KEYS = os.getenv('PROVIDE_KEYS', 'TRUE').upper() == 'TRUE'

# Initialize database
init_db(app)

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
def hub():
    authenticated = session.get('authenticated', False)
    return render_template('hub.html', authenticated=authenticated)


@app.route('/mark')
def single_mark_page():
    authenticated = session.get('authenticated', False)
    sk = _effective_keys()
    providers = get_available_providers(session_keys=sk)
    return render_template('index.html',
                           authenticated=authenticated,
                           providers=providers,
                           provide_keys=PROVIDE_KEYS,
                           all_providers=PROVIDERS)


@app.route('/class')
def class_page():
    authenticated = session.get('authenticated', False)
    sk = _get_session_keys()
    providers = get_available_providers(session_keys=sk)
    assignments = []
    if authenticated:
        assignments = Assignment.query.order_by(Assignment.created_at.desc()).all()
    return render_template('class.html',
                           authenticated=authenticated,
                           providers=providers,
                           provide_keys=PROVIDE_KEYS,
                           all_providers=PROVIDERS,
                           assignments=assignments)


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
    return redirect(url_for('class_page'))


@app.route('/bulk/mark', methods=['POST'])
def bulk_mark():
    if not session.get('authenticated'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    # Bulk always requires user's own session keys
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


@app.route('/bulk/overview/<job_id>')
def bulk_overview(job_id):
    if not session.get('authenticated'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    job = jobs.get(job_id)
    if not job or job['status'] != 'done' or not job.get('results'):
        return jsonify({'success': False, 'error': 'No results available'}), 404

    student_results = [
        {'name': item['name'], 'index': item['index'], 'result': item['result']}
        for item in job['results']
    ]
    pdf_bytes = generate_overview_pdf(student_results, subject=job.get('subject', ''))

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name='Class_Overview_Report.pdf'
    )


# ---------------------------------------------------------------------------
# Teacher dashboard & student submission portal
# ---------------------------------------------------------------------------

def _generate_classroom_code():
    """Generate a short unique classroom code like ENG3E."""
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(chars, k=6))
        if not Assignment.query.filter_by(classroom_code=code).first():
            return code


def _run_submission_marking(app_obj, submission_id, assignment_id):
    """Background thread: mark a student submission."""
    with app_obj.app_context():
        sub = Submission.query.get(submission_id)
        asn = Assignment.query.get(assignment_id)
        if not sub or not asn:
            return

        sub.status = 'processing'
        db.session.commit()

        try:
            qp = [asn.question_paper] if asn.question_paper else []
            ak = [asn.answer_key] if asn.answer_key else []
            rub = [asn.rubrics] if asn.rubrics else []
            ref = [asn.reference] if asn.reference else []
            script = [sub.script_bytes] if sub.script_bytes else []

            result = mark_script(
                provider=asn.provider,
                question_paper_pages=qp,
                answer_key_pages=ak,
                script_pages=script,
                subject=asn.subject,
                rubrics_pages=rub,
                reference_pages=ref,
                review_instructions=asn.review_instructions,
                marking_instructions=asn.marking_instructions,
                model=asn.model,
                assign_type=asn.assign_type,
                scoring_mode=asn.scoring_mode,
                total_marks=asn.total_marks,
                session_keys=asn.get_api_keys(),
            )

            sub.set_result(result)
            sub.status = 'error' if result.get('error') else 'done'
            sub.marked_at = datetime.now(timezone.utc)
        except Exception as e:
            logger.error(f"Submission {submission_id} marking failed: {e}")
            sub.set_result({'error': str(e)})
            sub.status = 'error'
            sub.marked_at = datetime.now(timezone.utc)

        db.session.commit()


@app.route('/teacher')
def teacher_page():
    return redirect(url_for('class_page', _anchor='submissions'))


@app.route('/teacher/create', methods=['POST'])
def teacher_create():
    if not session.get('authenticated'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    if PROVIDE_KEYS:
        return jsonify({'success': False, 'error': 'Student submission is not available in demo mode. Deploy your own instance to use this feature.'}), 403

    # API keys from user input (self-hosted mode only)
    api_keys = {}
    for prov in ('anthropic', 'openai', 'qwen'):
        val = request.form.get(f'api_key_{prov}', '').strip()
        if val:
            api_keys[prov] = val

    if not api_keys:
        return jsonify({'success': False, 'error': 'Please enter at least one API key'}), 400

    # Parse class list
    cl_file = request.files.get('class_list')
    if not cl_file or not cl_file.filename:
        return jsonify({'success': False, 'error': 'Please upload a class list CSV'}), 400
    students_data = _parse_class_list(cl_file.read(), cl_file.filename)
    if not students_data:
        return jsonify({'success': False, 'error': 'Could not parse class list'}), 400

    # Question paper
    qp_files = request.files.getlist('question_paper')
    if not qp_files or not qp_files[0].filename:
        return jsonify({'success': False, 'error': 'Please upload a question paper'}), 400

    assign_type = request.form.get('assign_type', 'short_answer')

    # Answer key (not required for rubrics)
    ak_bytes = None
    if assign_type != 'rubrics':
        ak_files = request.files.getlist('answer_key')
        if not ak_files or not ak_files[0].filename:
            return jsonify({'success': False, 'error': 'Please upload an answer key'}), 400
        ak_bytes = ak_files[0].read()

    # Rubrics
    rub_bytes = None
    rub_files = request.files.getlist('rubrics')
    if rub_files and rub_files[0].filename:
        rub_bytes = rub_files[0].read()

    if assign_type == 'rubrics' and not rub_bytes:
        return jsonify({'success': False, 'error': 'Rubrics file required for essay type'}), 400

    # Reference
    ref_bytes = None
    ref_files = request.files.getlist('reference')
    if ref_files and ref_files[0].filename:
        ref_bytes = ref_files[0].read()

    provider = request.form.get('provider', '')
    if provider not in api_keys:
        return jsonify({'success': False, 'error': 'Selected provider has no API key'}), 400

    asn = Assignment(
        id=str(uuid.uuid4()),
        classroom_code=_generate_classroom_code(),
        subject=request.form.get('subject', ''),
        assign_type=assign_type,
        scoring_mode=request.form.get('scoring_mode', 'status'),
        total_marks=request.form.get('total_marks', ''),
        provider=provider,
        model=request.form.get('model', ''),
        show_results=request.form.get('show_results') == 'on',
        review_instructions=request.form.get('review_instructions', ''),
        marking_instructions=request.form.get('marking_instructions', ''),
        question_paper=qp_files[0].read(),
        answer_key=ak_bytes,
        rubrics=rub_bytes,
        reference=ref_bytes,
    )
    asn.set_api_keys(api_keys)
    db.session.add(asn)

    for s in students_data:
        db.session.add(Student(
            assignment_id=asn.id,
            index_number=s['index'],
            name=s['name'],
        ))

    db.session.commit()

    return jsonify({
        'success': True,
        'assignment_id': asn.id,
        'classroom_code': asn.classroom_code,
    })


@app.route('/teacher/assignment/<assignment_id>')
def teacher_assignment_detail(assignment_id):
    if not session.get('authenticated'):
        return redirect(url_for('teacher_page'))

    asn = Assignment.query.get_or_404(assignment_id)
    students = Student.query.filter_by(assignment_id=assignment_id).order_by(Student.index_number).all()

    student_data = []
    for s in students:
        sub = Submission.query.filter_by(student_id=s.id, assignment_id=assignment_id).first()
        result = sub.get_result() if sub else {}
        questions = result.get('questions', [])
        has_marks = any(q.get('marks_awarded') is not None for q in questions)

        score = None
        if sub and sub.status == 'done' and not result.get('error'):
            if has_marks:
                ta = sum(q.get('marks_awarded', 0) for q in questions)
                tp = sum(q.get('marks_total', 0) for q in questions)
                score = f"{ta}/{tp}"
            else:
                correct = sum(1 for q in questions if q.get('status') == 'correct')
                score = f"{correct}/{len(questions)}"

        student_data.append({
            'index': s.index_number,
            'name': s.name,
            'status': sub.status if sub else 'not_submitted',
            'score': score,
            'submitted_at': sub.submitted_at.strftime('%d %b %H:%M') if sub and sub.submitted_at else None,
        })

    return render_template('teacher_detail.html',
                           assignment=asn,
                           students=student_data)


@app.route('/teacher/assignment/<assignment_id>/download')
def teacher_download_all(assignment_id):
    if not session.get('authenticated'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    asn = Assignment.query.get_or_404(assignment_id)
    submissions = Submission.query.filter_by(assignment_id=assignment_id, status='done').all()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for sub in submissions:
            result = sub.get_result()
            if result.get('error'):
                continue
            student = Student.query.get(sub.student_id)
            if not student:
                continue
            pdf_bytes = generate_report_pdf(result, subject=asn.subject)
            safe_name = student.name.replace('/', '_').replace('\\', '_')
            zf.writestr(f"{student.index_number}_{safe_name}_report.pdf", pdf_bytes)
    buf.seek(0)

    return send_file(buf, mimetype='application/zip', as_attachment=True,
                     download_name=f'{asn.classroom_code}_reports.zip')


@app.route('/teacher/assignment/<assignment_id>/overview')
def teacher_overview(assignment_id):
    if not session.get('authenticated'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    asn = Assignment.query.get_or_404(assignment_id)
    submissions = Submission.query.filter_by(assignment_id=assignment_id, status='done').all()

    student_results = []
    for sub in submissions:
        result = sub.get_result()
        if result.get('error'):
            continue
        student = Student.query.get(sub.student_id)
        if not student:
            continue
        student_results.append({
            'name': student.name,
            'index': student.index_number,
            'result': result,
        })

    pdf_bytes = generate_overview_pdf(student_results, subject=asn.subject)

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'{asn.classroom_code}_overview.pdf'
    )


@app.route('/teacher/assignment/<assignment_id>/delete', methods=['POST'])
def teacher_delete_assignment(assignment_id):
    if not session.get('authenticated'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    asn = Assignment.query.get_or_404(assignment_id)
    db.session.delete(asn)
    db.session.commit()
    return jsonify({'success': True})


# --- Student submission ---

@app.route('/submit/<assignment_id>')
def student_page(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    return render_template('submit.html', assignment_id=assignment_id, subject=asn.subject, demo_mode=PROVIDE_KEYS)


@app.route('/submit/<assignment_id>/verify', methods=['POST'])
def student_verify(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    data = request.get_json()
    code = (data.get('code') or '').strip().upper()
    if code != asn.classroom_code:
        return jsonify({'success': False, 'error': 'Invalid classroom code'}), 401

    students = Student.query.filter_by(assignment_id=assignment_id).order_by(Student.index_number).all()
    student_list = [{'id': s.id, 'index': s.index_number, 'name': s.name} for s in students]

    session[f'student_auth_{assignment_id}'] = True
    return jsonify({'success': True, 'students': student_list})


@app.route('/submit/<assignment_id>/upload', methods=['POST'])
def student_upload(assignment_id):
    if not session.get(f'student_auth_{assignment_id}'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    asn = Assignment.query.get_or_404(assignment_id)

    student_id = request.form.get('student_id')
    if not student_id:
        return jsonify({'success': False, 'error': 'Please select your name'}), 400

    student = Student.query.get(student_id)
    if not student or student.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Invalid student'}), 400

    script_files = request.files.getlist('script')
    if not script_files or not script_files[0].filename:
        return jsonify({'success': False, 'error': 'Please upload your script'}), 400

    # Read first file only (single PDF or image)
    script_bytes = script_files[0].read()

    # Delete existing submission if re-submitting
    existing = Submission.query.filter_by(student_id=student.id, assignment_id=assignment_id).first()
    if existing:
        db.session.delete(existing)
        db.session.flush()

    sub = Submission(
        student_id=student.id,
        assignment_id=assignment_id,
        script_bytes=script_bytes,
        status='pending',
    )
    db.session.add(sub)
    db.session.commit()

    # Start marking in background
    thread = threading.Thread(
        target=_run_submission_marking,
        args=(app, sub.id, assignment_id),
        daemon=True,
    )
    thread.start()

    return jsonify({
        'success': True,
        'submission_id': sub.id,
        'show_results': asn.show_results,
    })


@app.route('/submit/<assignment_id>/status/<int:submission_id>')
def student_submission_status(assignment_id, submission_id):
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Not found'}), 404

    asn = Assignment.query.get(assignment_id)
    response = {'success': True, 'status': sub.status}

    if sub.status in ('done', 'error'):
        result = sub.get_result()
        if asn and asn.show_results:
            response['result'] = result
        elif result.get('error'):
            response['result'] = {'error': result['error']}

    return jsonify(response)


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
