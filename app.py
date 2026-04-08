import os
import uuid
import logging
import threading
import time
from flask import Flask, render_template, request, jsonify, session, send_file
import io

from ai_marking import mark_script, get_available_providers
from pdf_generator import generate_report_pdf

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')

ACCESS_CODE = os.getenv('ACCESS_CODE', 'DEMO2026')

# In-memory job store (thread-safe via GIL for dict ops)
# Structure: {job_id: {status, result, subject, created_at}}
jobs = {}
JOB_TTL_SECONDS = 3600  # 1 hour


def cleanup_old_jobs():
    """Remove jobs older than TTL."""
    now = time.time()
    expired = [jid for jid, j in jobs.items() if now - j['created_at'] > JOB_TTL_SECONDS]
    for jid in expired:
        del jobs[jid]


def run_marking_job(job_id, provider, model, question_paper_bytes, answer_key_bytes,
                    script_bytes, subject, rubrics_bytes, review_instructions, marking_instructions):
    """Background thread for AI marking."""
    try:
        result = mark_script(
            provider=provider,
            question_paper_bytes=question_paper_bytes,
            answer_key_bytes=answer_key_bytes,
            script_bytes=script_bytes,
            subject=subject,
            rubrics_bytes=rubrics_bytes,
            review_instructions=review_instructions,
            marking_instructions=marking_instructions,
            model=model,
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
    providers = get_available_providers()
    return render_template('index.html',
                           authenticated=authenticated,
                           providers=providers)


@app.route('/verify-code', methods=['POST'])
def verify_code():
    data = request.get_json()
    code = (data.get('code') or '').strip()
    if code == ACCESS_CODE:
        session['authenticated'] = True
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Invalid access code'}), 401


@app.route('/mark', methods=['POST'])
def mark():
    if not session.get('authenticated'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    # Validate required files
    for field in ('question_paper', 'answer_key', 'script'):
        if field not in request.files or not request.files[field].filename:
            return jsonify({'success': False, 'error': f'Missing required file: {field}'}), 400

    provider = request.form.get('provider', 'anthropic')
    model = request.form.get('model', '')
    subject = request.form.get('subject', '')
    review_instructions = request.form.get('review_instructions', '')
    marking_instructions = request.form.get('marking_instructions', '')

    # Read file bytes
    question_paper_bytes = request.files['question_paper'].read()
    answer_key_bytes = request.files['answer_key'].read()
    script_bytes = request.files['script'].read()

    rubrics_bytes = None
    if 'rubrics' in request.files and request.files['rubrics'].filename:
        rubrics_bytes = request.files['rubrics'].read()

    # Cleanup old jobs periodically
    cleanup_old_jobs()

    # Create job
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        'status': 'processing',
        'result': None,
        'subject': subject,
        'created_at': time.time(),
    }

    # Run in background thread
    thread = threading.Thread(
        target=run_marking_job,
        args=(job_id, provider, model, question_paper_bytes, answer_key_bytes,
              script_bytes, subject, rubrics_bytes, review_instructions, marking_instructions),
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
        response['result'] = job['result']
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


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
