import os
import csv
import json
import uuid
import string
import secrets
import logging
import threading
import time
import zipfile
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify, session, send_file, redirect, url_for
import io

from ai_marking import mark_script, get_available_providers, PROVIDERS
from pdf_generator import generate_report_pdf, generate_overview_pdf
from db import db, init_db, Assignment, Student, Submission, Teacher, Class, TeacherClass, DepartmentConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
_flask_secret = os.getenv('FLASK_SECRET_KEY', '')
if not _flask_secret:
    _flask_secret = os.urandom(32).hex()
    logger.warning('FLASK_SECRET_KEY not set — using random key (sessions will not survive restarts)')
app.secret_key = _flask_secret

app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV') != 'development'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB

_ENV_ACCESS_CODE = os.getenv('ACCESS_CODE', '').strip()  # keep for legacy
_ENV_TEACHER_CODE = os.getenv('TEACHER_CODE', '').strip() or _ENV_ACCESS_CODE
_ENV_DEMO_MODE = os.getenv('DEMO_MODE', 'FALSE').upper() == 'TRUE'
_ENV_DEPT_MODE = os.getenv('DEPT_MODE', 'FALSE').upper() == 'TRUE'
_ENV_APP_TITLE = os.getenv('APP_TITLE', 'AI Feedback Systems')

# Demo mode: restricted to 3 budget models only
DEMO_MODELS = {
    'anthropic': {
        'label': 'Anthropic',
        'models': {'claude-haiku-4-5-20251001': 'Claude Haiku 4.5'},
        'default': 'claude-haiku-4-5-20251001',
    },
    'openai': {
        'label': 'OpenAI',
        'models': {'gpt-5.4-mini': 'GPT-5.4 Mini'},
        'default': 'gpt-5.4-mini',
    },
    'qwen': {
        'label': 'Qwen',
        'models': {'qwen3.5-plus-2026-02-15': 'Qwen 3.5 Plus'},
        'default': 'qwen3.5-plus-2026-02-15',
    },
}

# Initialize database
init_db(app)


# ---------------------------------------------------------------------------
# Config helpers: DB-backed configuration with env var fallback
# ---------------------------------------------------------------------------

def _get_config(key, default=''):
    """Get config from DB (DepartmentConfig), falling back to env var."""
    try:
        cfg = DepartmentConfig.query.filter_by(key=key).first()
        if cfg and cfg.value:
            return cfg.value
    except Exception:
        pass
    return default


def _set_config(key, value):
    """Set a config value in DepartmentConfig."""
    cfg = DepartmentConfig.query.filter_by(key=key).first()
    if cfg:
        cfg.value = value
    else:
        cfg = DepartmentConfig(key=key, value=value)
        db.session.add(cfg)
    db.session.commit()


def _is_setup_complete():
    """Check if the setup wizard has been completed."""
    try:
        cfg = DepartmentConfig.query.filter_by(key='setup_complete').first()
        if cfg and cfg.value == 'true':
            return True
    except Exception:
        pass
    # Also consider setup complete if env vars are configured
    if os.getenv('DEPT_MODE') or os.getenv('DEMO_MODE') or os.getenv('TEACHER_CODE'):
        return True
    return False


def get_app_mode():
    """Get app mode from DB, falling back to env vars."""
    cfg = DepartmentConfig.query.filter_by(key='app_mode').first()
    if cfg and cfg.value:
        return cfg.value
    # Fall back to env vars
    if _ENV_DEMO_MODE and _ENV_DEPT_MODE:
        return 'demo_department'
    if _ENV_DEMO_MODE:
        return 'demo'
    if _ENV_DEPT_MODE:
        return 'department'
    return 'normal'


def get_app_title():
    """Get app title from DB, falling back to env var."""
    cfg = DepartmentConfig.query.filter_by(key='app_title').first()
    if cfg and cfg.value:
        return cfg.value
    return _ENV_APP_TITLE


def get_teacher_code():
    """Get teacher code from DB, falling back to env var."""
    cfg = DepartmentConfig.query.filter_by(key='teacher_code').first()
    if cfg and cfg.value:
        return cfg.value
    return _ENV_TEACHER_CODE


def is_demo_mode():
    """Check if app is in demo mode."""
    mode = get_app_mode()
    return mode in ('demo', 'demo_department')


def is_dept_mode():
    """Check if app is in department mode."""
    mode = get_app_mode()
    return mode in ('department', 'demo_department')


# Seed fake data when both DEMO_MODE and DEPT_MODE are enabled via env vars
if _ENV_DEMO_MODE and _ENV_DEPT_MODE:
    with app.app_context():
        from seed_data import seed_demo_department
        seed_demo_department(db, Teacher, Class, TeacherClass, Assignment, Student, Submission)


# ---------------------------------------------------------------------------
# Security: headers, rate limiting, error handlers
# ---------------------------------------------------------------------------

@app.context_processor
def inject_dept_context():
    """Make dept_mode, demo_mode, app_title and current teacher available in all templates."""
    teacher = _current_teacher()  # works for both modes now
    return {
        'dept_mode': is_dept_mode(),
        'demo_mode': is_demo_mode(),
        'app_title': get_app_title(),
        'current_teacher': teacher,
    }


@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


@app.errorhandler(413)
def too_large(e):
    return jsonify({'success': False, 'error': 'Upload too large. Maximum 100MB total.'}), 413


_rate_limits = {}
_rate_lock = threading.Lock()


def _check_rate_limit(key):
    """Simple in-memory rate limiter. Returns False if limit exceeded."""
    with _rate_lock:
        now = time.time()
        _rate_limits.setdefault(key, [])
        _rate_limits[key] = [t for t in _rate_limits[key] if now - t < 60]
        if len(_rate_limits[key]) >= 10:
            return False
        _rate_limits[key].append(now)
        return True

# In-memory job store (thread-safe via GIL for dict ops)
jobs = {}
JOB_TTL_SECONDS = 3600  # 1 hour


def cleanup_old_jobs():
    """Remove jobs older than TTL."""
    now = time.time()
    expired = [jid for jid, j in list(jobs.items()) if now - j['created_at'] > JOB_TTL_SECONDS]
    for jid in expired:
        jobs.pop(jid, None)


def _get_session_keys():
    """Get session-stored API keys (used when DEMO_MODE is FALSE or for bulk)."""
    return session.get('api_keys') or {}




def _is_authenticated():
    """Check if user is authenticated."""
    if is_dept_mode():
        return session.get('teacher_id') is not None
    if get_teacher_code():
        return session.get('teacher_id') is not None
    if not _ENV_ACCESS_CODE:
        return True
    return session.get('authenticated', False)


def _current_teacher():
    """Get the currently logged-in teacher. Returns None if not logged in."""
    teacher_id = session.get('teacher_id')
    if not teacher_id:
        return None
    return Teacher.query.get(teacher_id)


def _is_hod():
    """Check if current user is HOD."""
    teacher = _current_teacher()
    return teacher and teacher.role == 'hod'


def _check_assignment_ownership(asn):
    """Return error response if current user doesn't own this assignment, or None if OK."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    teacher = _current_teacher()
    if not teacher:
        return None  # Non-dept mode, auth already checked
    if teacher.role == 'hod':
        return None  # HOD can access all
    if asn.teacher_id != teacher.id:
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    return None


def _require_hod():
    """Return error response if not HOD, or None if OK."""
    if not is_dept_mode() or not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    if not _is_hod():
        return jsonify({'success': False, 'error': 'HOD access required'}), 403
    return None


def _get_dept_keys():
    """Get department-level API keys from DepartmentConfig."""
    if not is_dept_mode():
        return {}
    from db import _get_fernet
    keys = {}
    for prov in ('anthropic', 'openai', 'qwen'):
        cfg = DepartmentConfig.query.filter_by(key=f'api_key_{prov}').first()
        if cfg and cfg.value:
            f = _get_fernet()
            if f:
                try:
                    keys[prov] = f.decrypt(cfg.value.encode()).decode()
                    continue
                except Exception:
                    pass
            keys[prov] = cfg.value
    return keys


def _resolve_api_keys(assignment):
    """Resolve API keys: assignment-stored → department config → env vars (None)."""
    keys = assignment.get_api_keys()
    if keys:
        return keys
    if is_dept_mode():
        dept_keys = _get_dept_keys()
        if dept_keys:
            return dept_keys
    # Also check wizard-stored keys
    from db import _get_fernet
    wizard_keys = {}
    for prov in ('anthropic', 'openai', 'qwen'):
        cfg = DepartmentConfig.query.filter_by(key=f'api_key_{prov}').first()
        if cfg and cfg.value:
            f = _get_fernet()
            if f:
                try:
                    wizard_keys[prov] = f.decrypt(cfg.value.encode()).decode()
                    continue
                except Exception:
                    pass
            wizard_keys[prov] = cfg.value
    if wizard_keys:
        return wizard_keys
    return None


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
    if not _is_setup_complete():
        return redirect(url_for('setup_wizard'))

    _demo = is_demo_mode()
    _dept = is_dept_mode()

    if _demo and _dept:
        # Auto-login as demo HOD if not already logged in
        if not session.get('teacher_id'):
            hod = Teacher.query.filter_by(role='hod').first()
            if hod:
                session['teacher_id'] = hod.id
                session['teacher_role'] = hod.role
                session['teacher_name'] = hod.name
        teacher = _current_teacher()
        return render_template('hub.html',
                               authenticated=True,
                               dept_mode=True,
                               demo_mode=True,
                               teacher=teacher)
    if _demo and not _dept:
        return render_template('hub.html',
                               authenticated=True,
                               dept_mode=False,
                               demo_mode=True,
                               teacher=None)
    if _dept:
        if not Teacher.query.filter_by(role='hod').first():
            return redirect(url_for('department_setup'))
    authenticated = _is_authenticated()
    teacher = _current_teacher()  # works for both dept and normal mode now
    return render_template('hub.html',
                           authenticated=authenticated,
                           dept_mode=_dept,
                           demo_mode=_demo,
                           teacher=teacher)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('hub'))


@app.route('/mark')
def single_mark_page():
    _demo = is_demo_mode()
    _dept = is_dept_mode()
    if _demo and not _dept:
        # Demo mode: standalone marking with restricted models
        from ai_marking import PROVIDER_KEY_MAP
        providers = {}
        for prov, config in DEMO_MODELS.items():
            env_key = PROVIDER_KEY_MAP.get(prov, '')
            if os.getenv(env_key, ''):
                providers[prov] = config
        # Also check wizard-stored keys
        from db import _get_fernet
        f = _get_fernet()
        for prov, config in DEMO_MODELS.items():
            if prov not in providers:
                cfg = DepartmentConfig.query.filter_by(key=f'api_key_{prov}').first()
                if cfg and cfg.value:
                    providers[prov] = config
        return render_template('index.html',
                               authenticated=True,
                               demo_mode=True,
                               dept_mode=False,
                               providers=providers,
                               all_providers=DEMO_MODELS)
    authenticated = _is_authenticated()
    return render_template('index.html',
                           authenticated=authenticated,
                           demo_mode=_demo,
                           dept_mode=_dept)


@app.route('/class')
def class_page():
    _demo = is_demo_mode()
    _dept = is_dept_mode()
    if _demo and not _dept:
        # Demo mode: explore features, no real DB writes
        return render_template('class.html',
                               authenticated=True,
                               providers={},
                               demo_mode=True,
                               dept_mode=False,
                               teacher=None,
                               all_providers=DEMO_MODELS,
                               assignments=[])
    authenticated = _is_authenticated()
    sk = _get_session_keys()
    providers = get_available_providers(session_keys=sk)
    assignments = []
    teacher = None
    if authenticated:
        if _dept:
            teacher = _current_teacher()
            if teacher and teacher.role == 'hod':
                assignments = Assignment.query.order_by(Assignment.created_at.desc()).all()
            elif teacher:
                assignments = Assignment.query.filter_by(teacher_id=teacher.id)\
                    .order_by(Assignment.created_at.desc()).all()
        else:
            assignments = Assignment.query.order_by(Assignment.created_at.desc()).all()
    return render_template('class.html',
                           authenticated=authenticated,
                           providers=providers,
                           demo_mode=_demo,
                           dept_mode=_dept,
                           teacher=teacher,
                           all_providers=PROVIDERS,
                           assignments=assignments)


@app.route('/verify-code', methods=['POST'])
def verify_code():
    if not _check_rate_limit(f'verify:{request.remote_addr}'):
        return jsonify({'success': False, 'error': 'Too many attempts. Please wait.'}), 429
    data = request.get_json()
    code = (data.get('code') or '').strip()

    if is_dept_mode():
        teacher = Teacher.query.filter_by(code=code).first()
        if not teacher:
            return jsonify({'success': False, 'error': 'Invalid code'}), 401
        if hasattr(teacher, 'is_active') and not teacher.is_active:
            return jsonify({'success': False, 'error': 'Account has been deactivated. Contact your HOD.'}), 403
        session['teacher_id'] = teacher.id
        session['teacher_role'] = teacher.role
        session['teacher_name'] = teacher.name
        redirect_url = '/department' if teacher.role == 'hod' else '/dashboard'
        return jsonify({'success': True, 'redirect': redirect_url})

    # Normal mode with teacher code
    _tc = get_teacher_code()
    if _tc:
        if code == _tc:
            # Master key — find the owner teacher
            teacher = Teacher.query.filter_by(role='owner').first()
            if not teacher:
                session['pending_setup'] = True
                return jsonify({'success': True, 'redirect': '/setup'})
            session['teacher_id'] = teacher.id
            session['teacher_name'] = teacher.name
            return jsonify({'success': True, 'redirect': '/'})
        # Also check if they have a custom code
        teacher = Teacher.query.filter_by(code=code, role='owner').first()
        if teacher:
            session['teacher_id'] = teacher.id
            session['teacher_name'] = teacher.name
            return jsonify({'success': True, 'redirect': '/'})
        return jsonify({'success': False, 'error': 'Invalid code'}), 401

    # Legacy ACCESS_CODE fallback
    if _ENV_ACCESS_CODE and code == _ENV_ACCESS_CODE:
        session['authenticated'] = True
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Invalid access code'}), 401


@app.route('/save-keys', methods=['POST'])
def save_keys():
    """Save user-provided API keys to session."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    data = request.get_json()
    keys = {}
    for prov in ('anthropic', 'openai', 'qwen'):
        val = (data.get(prov) or '').strip()
        if val:
            keys[prov] = val
    session['api_keys'] = keys
    sk = keys if (not is_demo_mode()) else None
    providers = get_available_providers(session_keys=sk)
    return jsonify({'success': True, 'providers': {k: v for k, v in providers.items()}})


@app.route('/clear-keys', methods=['POST'])
def clear_keys():
    """Clear session API keys."""
    session.pop('api_keys', None)
    return jsonify({'success': True})


def _demo_mark():
    """Handle demo mode marking — standalone, in-memory, no DB."""
    assign_type = request.form.get('assign_type', 'short_answer')

    required_fields = ['question_paper', 'script']
    if assign_type != 'rubrics':
        required_fields.append('answer_key')
    for field in required_fields:
        files = request.files.getlist(field)
        if not files or not files[0].filename:
            return jsonify({'success': False, 'error': f'Missing required file: {field}'}), 400
        if len(files) > 10:
            return jsonify({'success': False, 'error': f'Maximum 10 files per upload ({field})'}), 400

    provider = request.form.get('provider', 'anthropic')
    model = request.form.get('model', '')

    # Validate model is in demo allowed list
    if provider not in DEMO_MODELS:
        return jsonify({'success': False, 'error': 'Invalid provider for demo mode'}), 400
    if model not in DEMO_MODELS[provider]['models']:
        return jsonify({'success': False, 'error': 'Invalid model for demo mode'}), 400

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

    # Resolve API keys: check wizard-stored keys for demo mode
    demo_session_keys = None
    from db import _get_fernet
    f = _get_fernet()
    cfg = DepartmentConfig.query.filter_by(key=f'api_key_{provider}').first()
    if cfg and cfg.value:
        if f:
            try:
                demo_session_keys = {provider: f.decrypt(cfg.value.encode()).decode()}
            except Exception:
                demo_session_keys = {provider: cfg.value}
        else:
            demo_session_keys = {provider: cfg.value}

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
              assign_type, scoring_mode, total_marks, demo_session_keys),
        daemon=True
    )
    thread.start()

    return jsonify({'success': True, 'job_id': job_id})


@app.route('/mark', methods=['POST'])
def mark():
    if is_demo_mode() and not is_dept_mode():
        return _demo_mark()

    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    assignment_id = request.form.get('assignment_id')
    student_id = request.form.get('student_id')

    if not assignment_id or not student_id:
        return jsonify({'success': False, 'error': 'Please select a class, assignment, and student'}), 400

    asn = Assignment.query.get(assignment_id)
    if not asn:
        return jsonify({'success': False, 'error': 'Assignment not found'}), 404

    # Ownership check
    err = _check_assignment_ownership(asn)
    if err:
        return err

    student = Student.query.get(int(student_id))
    if not student:
        return jsonify({'success': False, 'error': 'Student not found'}), 404

    # Validate script upload
    script_files = request.files.getlist('script')
    if not script_files or not script_files[0].filename:
        return jsonify({'success': False, 'error': 'Please upload the student script'}), 400
    if len(script_files) > 10:
        return jsonify({'success': False, 'error': 'Maximum 10 files'}), 400

    script_pages = [f.read() for f in script_files if f.filename]

    # Delete existing submission if any
    existing = Submission.query.filter_by(student_id=student.id, assignment_id=assignment_id).first()
    if existing:
        db.session.delete(existing)
        db.session.flush()

    # Create new submission
    sub = Submission(
        student_id=student.id,
        assignment_id=assignment_id,
        status='pending',
    )
    sub.set_script_pages(script_pages)
    db.session.add(sub)
    db.session.commit()

    # Start marking in background using the assignment's stored files/settings
    thread = threading.Thread(
        target=_run_submission_marking,
        args=(app, sub.id, assignment_id),
        daemon=True,
    )
    thread.start()

    return jsonify({
        'success': True,
        'submission_id': sub.id,
        'assignment_id': assignment_id,
    })


@app.route('/status/<job_id>')
def job_status(job_id):
    if not is_demo_mode() and not _is_authenticated():
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
    if not is_demo_mode() and not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    job = jobs.get(job_id)
    if not job or job['status'] != 'done':
        return jsonify({'success': False, 'error': 'No results available'}), 404

    pdf_bytes = generate_report_pdf(job['result'], subject=job.get('subject', ''), app_title=get_app_title())

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name='AI_Marking_Report.pdf'
    )


# ---------------------------------------------------------------------------
# HOD Department Dashboard
# ---------------------------------------------------------------------------

@app.route('/department')
def department_page():
    err = _require_hod()
    if err:
        return redirect(url_for('hub'))

    teacher = _current_teacher()
    classes = Class.query.order_by(Class.name).all()
    teachers = Teacher.query.filter_by(role='teacher').order_by(Teacher.name).all()

    # Bulk load all assignments for these classes
    class_ids = [c.id for c in classes]
    all_assignments = Assignment.query.filter(Assignment.class_id.in_(class_ids)).all() if class_ids else []
    assignments_by_class = {}
    for a in all_assignments:
        assignments_by_class.setdefault(a.class_id, []).append(a)

    # Bulk load student counts by class
    student_counts_by_class = {}
    for cls in classes:
        student_counts_by_class[cls.id] = Student.query.filter_by(class_id=cls.id).count()

    # Bulk load all submissions for these assignments
    asn_ids = [a.id for a in all_assignments]
    all_subs = Submission.query.filter(Submission.assignment_id.in_(asn_ids)).all() if asn_ids else []
    subs_by_assignment = {}
    for s in all_subs:
        subs_by_assignment.setdefault(s.assignment_id, []).append(s)

    class_data = []
    for cls in classes:
        assignments = assignments_by_class.get(cls.id, [])
        students_in_class = student_counts_by_class.get(cls.id, 0)
        total_students = students_in_class * len(assignments)
        total_submissions = 0
        done_submissions = 0
        for asn in assignments:
            subs = subs_by_assignment.get(asn.id, [])
            total_submissions += len(subs)
            done_submissions += sum(1 for s in subs if s.status == 'done')

        class_data.append({
            'id': cls.id,
            'name': cls.name,
            'level': cls.level,
            'teachers': [t.name for t in cls.teachers],
            'assignment_count': len(assignments),
            'total_students': total_students,
            'total_submissions': total_submissions,
            'done_submissions': done_submissions,
            'completion_pct': round(done_submissions / total_students * 100) if total_students > 0 else 0,
        })

    total_assignments = Assignment.query.filter(Assignment.class_id.isnot(None)).count()
    total_subs = Submission.query.count()

    return render_template('department.html',
                           teacher=teacher,
                           classes=class_data,
                           total_teachers=len(teachers),
                           total_classes=len(classes),
                           total_assignments=total_assignments,
                           total_submissions=total_subs,
                           dept_mode=is_dept_mode(),
                           demo_mode=is_demo_mode())


# ---------------------------------------------------------------------------
# Class & Teacher Management
# ---------------------------------------------------------------------------

@app.route('/department/classes')
def department_manage():
    err = _require_hod()
    if err:
        return redirect(url_for('hub'))

    teacher = _current_teacher()
    classes = Class.query.order_by(Class.name).all()
    teachers = Teacher.query.order_by(Teacher.role.desc(), Teacher.name).all()

    return render_template('department_manage.html',
                           teacher=teacher,
                           classes=classes,
                           teachers=teachers,
                           dept_mode=is_dept_mode(),
                           demo_mode=is_demo_mode())


def _generate_teacher_code():
    """Generate a unique 8-char teacher code."""
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(secrets.choice(chars) for _ in range(8))
        if not Teacher.query.filter_by(code=code).first():
            return code


@app.route('/department/teacher/create', methods=['POST'])
def dept_create_teacher():
    err = _require_hod()
    if err:
        return err

    data = request.get_json()
    name = (data.get('name') or '').strip()
    role = data.get('role', 'teacher')
    if not name:
        return jsonify({'success': False, 'error': 'Name is required'}), 400
    if role not in ('teacher', 'hod'):
        return jsonify({'success': False, 'error': 'Invalid role'}), 400

    custom_code = (data.get('code') or '').strip()
    if custom_code:
        if len(custom_code) < 4:
            return jsonify({'success': False, 'error': 'Code must be at least 4 characters'}), 400
        if Teacher.query.filter_by(code=custom_code).first():
            return jsonify({'success': False, 'error': 'Code already in use'}), 400
        code = custom_code
    else:
        code = _generate_teacher_code()

    t = Teacher(
        id=str(uuid.uuid4()),
        name=name,
        code=code,
        role=role,
    )
    db.session.add(t)
    db.session.commit()

    return jsonify({'success': True, 'teacher': {
        'id': t.id, 'name': t.name,
        'code': t.code, 'role': t.role,
    }})


@app.route('/department/teacher/<teacher_id>/delete', methods=['POST'])
def dept_delete_teacher(teacher_id):
    err = _require_hod()
    if err:
        return err

    t = Teacher.query.get_or_404(teacher_id)
    if t.role == 'hod':
        return jsonify({'success': False, 'error': 'Cannot delete HOD'}), 400
    db.session.delete(t)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/department/teacher/<teacher_id>/reset-code', methods=['POST'])
def dept_reset_code(teacher_id):
    err = _require_hod()
    if err:
        return err

    t = Teacher.query.get_or_404(teacher_id)
    data = request.get_json()
    new_code = (data.get('code') or '').strip()

    if new_code:
        if len(new_code) < 4:
            return jsonify({'success': False, 'error': 'Code must be at least 4 characters'}), 400
        existing = Teacher.query.filter_by(code=new_code).first()
        if existing and existing.id != t.id:
            return jsonify({'success': False, 'error': 'Code already in use'}), 400
        t.code = new_code
    else:
        t.code = _generate_teacher_code()

    db.session.commit()
    return jsonify({'success': True, 'code': t.code})


@app.route('/department/teacher/<teacher_id>/revoke', methods=['POST'])
def dept_revoke_teacher(teacher_id):
    err = _require_hod()
    if err:
        return err
    t = Teacher.query.get_or_404(teacher_id)
    if t.role == 'hod':
        return jsonify({'success': False, 'error': 'Cannot revoke HOD'}), 400
    t.is_active = not t.is_active  # Toggle active status
    db.session.commit()
    return jsonify({'success': True, 'is_active': t.is_active})


@app.route('/department/teacher/<teacher_id>/purge', methods=['POST'])
def dept_purge_teacher(teacher_id):
    err = _require_hod()
    if err:
        return err
    t = Teacher.query.get_or_404(teacher_id)
    if t.role == 'hod':
        return jsonify({'success': False, 'error': 'Cannot purge HOD'}), 400
    data = request.get_json() or {}
    keep_data = data.get('keep_data', False)

    if not keep_data:
        # Delete teacher's assignments and their submissions
        assignments = Assignment.query.filter_by(teacher_id=t.id).all()
        for asn in assignments:
            Submission.query.filter_by(assignment_id=asn.id).delete()
            db.session.delete(asn)

    TeacherClass.query.filter_by(teacher_id=t.id).delete()
    db.session.delete(t)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/department/class/create', methods=['POST'])
def dept_create_class():
    err = _require_hod()
    if err:
        return err

    data = request.get_json()
    name = (data.get('name') or '').strip()
    level = (data.get('level') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Class name is required'}), 400

    cls = Class(id=str(uuid.uuid4()), name=name, level=level)
    db.session.add(cls)
    db.session.commit()

    return jsonify({'success': True, 'cls': {
        'id': cls.id, 'name': cls.name, 'level': cls.level,
    }})


@app.route('/department/class/<class_id>/delete', methods=['POST'])
def dept_delete_class(class_id):
    err = _require_hod()
    if err:
        return err

    cls = Class.query.get_or_404(class_id)
    TeacherClass.query.filter_by(class_id=class_id).delete()
    db.session.delete(cls)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/class/<class_id>/students', methods=['GET', 'POST'])
def manage_class_students(class_id):
    """Upload or view class list for a class."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    cls = Class.query.get_or_404(class_id)

    # Ownership check
    teacher = _current_teacher()
    if teacher and teacher.role not in ('hod', 'owner'):
        tc = TeacherClass.query.filter_by(teacher_id=teacher.id, class_id=class_id).first()
        if not tc:
            return jsonify({'success': False, 'error': 'Not assigned to this class'}), 403

    if request.method == 'GET':
        students = _sort_by_index(Student.query.filter_by(class_id=class_id).all())
        return jsonify({
            'success': True,
            'students': [{'id': s.id, 'index': s.index_number, 'name': s.name} for s in students]
        })

    # POST — upload class list
    cl_file = request.files.get('class_list')
    if not cl_file or not cl_file.filename:
        return jsonify({'success': False, 'error': 'Please upload a class list CSV'}), 400

    file_bytes = cl_file.read()
    if len(file_bytes) > 1024 * 1024:
        return jsonify({'success': False, 'error': 'Class list too large (max 1MB)'}), 400

    students_data = _parse_class_list(file_bytes, cl_file.filename)
    if not students_data:
        return jsonify({'success': False, 'error': 'Could not parse class list'}), 400
    if len(students_data) > 500:
        return jsonify({'success': False, 'error': 'Maximum 500 students per class'}), 400

    # Remove existing students without submissions
    existing = Student.query.filter_by(class_id=class_id).all()
    for s in existing:
        has_sub = Submission.query.filter_by(student_id=s.id).first()
        if not has_sub:
            db.session.delete(s)

    # Add new students (skip if already exists by index)
    for s in students_data:
        existing_student = Student.query.filter_by(class_id=class_id, index_number=s['index']).first()
        if not existing_student:
            db.session.add(Student(class_id=class_id, index_number=s['index'], name=s['name']))

    db.session.commit()

    count = Student.query.filter_by(class_id=class_id).count()
    return jsonify({'success': True, 'count': count})


@app.route('/my/class/create', methods=['POST'])
def create_my_class():
    """Create a class in normal (non-dept) mode."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    if is_dept_mode():
        return jsonify({'success': False, 'error': 'Use department class management instead'}), 400

    data = request.get_json()
    name = (data.get('name') or '').strip()
    level = (data.get('level') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Class name is required'}), 400

    teacher = _current_teacher()
    cls = Class(id=str(uuid.uuid4()), name=name, level=level)
    db.session.add(cls)

    if teacher:
        db.session.add(TeacherClass(teacher_id=teacher.id, class_id=cls.id))

    db.session.commit()
    return jsonify({'success': True, 'class_id': cls.id, 'name': cls.name, 'level': cls.level})


@app.route('/my/class/<class_id>/delete', methods=['POST'])
def delete_my_class(class_id):
    """Delete a class in normal mode."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    cls = Class.query.get_or_404(class_id)
    teacher = _current_teacher()
    if teacher:
        tc = TeacherClass.query.filter_by(teacher_id=teacher.id, class_id=class_id).first()
        if not tc:
            return jsonify({'success': False, 'error': 'Not your class'}), 403

    # Delete associated data
    TeacherClass.query.filter_by(class_id=class_id).delete()
    # Students cascade-delete via relationship
    db.session.delete(cls)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/classes')
def api_classes():
    """List classes for the current teacher."""
    if not _is_authenticated():
        return jsonify([])
    teacher = _current_teacher()
    if teacher:
        if teacher.role == 'hod':
            classes = Class.query.order_by(Class.name).all()
        else:
            classes = teacher.classes
    else:
        classes = Class.query.all()
    result = []
    for c in classes:
        student_count = Student.query.filter_by(class_id=c.id).count()
        result.append({'id': c.id, 'name': c.name, 'level': c.level, 'student_count': student_count})
    return jsonify(result)


@app.route('/department/class/<class_id>/assign', methods=['POST'])
def dept_assign_teacher(class_id):
    err = _require_hod()
    if err:
        return err

    data = request.get_json()
    teacher_id = data.get('teacher_id')
    if not teacher_id:
        return jsonify({'success': False, 'error': 'Teacher ID required'}), 400

    Class.query.get_or_404(class_id)
    Teacher.query.get_or_404(teacher_id)

    existing = TeacherClass.query.filter_by(teacher_id=teacher_id, class_id=class_id).first()
    if not existing:
        db.session.add(TeacherClass(teacher_id=teacher_id, class_id=class_id))
        db.session.commit()

    return jsonify({'success': True})


@app.route('/department/class/<class_id>/unassign', methods=['POST'])
def dept_unassign_teacher(class_id):
    err = _require_hod()
    if err:
        return err

    data = request.get_json()
    teacher_id = data.get('teacher_id')
    if not teacher_id:
        return jsonify({'success': False, 'error': 'Teacher ID required'}), 400
    Teacher.query.get_or_404(teacher_id)
    TeacherClass.query.filter_by(teacher_id=teacher_id, class_id=class_id).delete()
    db.session.commit()
    return jsonify({'success': True})


@app.route('/department/keys', methods=['POST'])
def dept_save_keys():
    err = _require_hod()
    if err:
        return err

    from db import _get_fernet
    data = request.get_json()
    for prov in ('anthropic', 'openai', 'qwen'):
        val = (data.get(prov) or '').strip()
        cfg = DepartmentConfig.query.filter_by(key=f'api_key_{prov}').first()
        if val:
            if not cfg:
                cfg = DepartmentConfig(key=f'api_key_{prov}')
                db.session.add(cfg)
            f = _get_fernet()
            cfg.value = f.encrypt(val.encode()).decode() if f else val
        elif cfg:
            db.session.delete(cfg)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/department/insights')
def department_insights():
    err = _require_hod()
    if err:
        return redirect(url_for('hub'))

    teacher = _current_teacher()
    classes = Class.query.order_by(Class.name).all()
    assignments = Assignment.query.filter(Assignment.class_id.isnot(None))\
        .order_by(Assignment.created_at.desc()).all()

    return render_template('department_insights.html',
                           teacher=teacher,
                           classes=classes,
                           assignments=assignments,
                           demo_mode=is_demo_mode(),
                           dept_mode=is_dept_mode())


@app.route('/department/insights/data')
def department_insights_data():
    """API endpoint returning analytics data for charts."""
    err = _require_hod()
    if err:
        return err
    if is_demo_mode() and not is_dept_mode():
        return jsonify({'success': False, 'error': 'Not available in demo mode'}), 403

    assignment_id = request.args.get('assignment_id')
    class_id = request.args.get('class_id')

    query = Submission.query.filter_by(status='done')
    if assignment_id:
        query = query.filter_by(assignment_id=assignment_id)

    submissions = query.all()

    # Pre-load assignments and classes to avoid N+1
    asn_ids = list(set(s.assignment_id for s in submissions))
    all_asns = {a.id: a for a in Assignment.query.filter(Assignment.id.in_(asn_ids)).all()} if asn_ids else {}
    cls_ids = list(set(a.class_id for a in all_asns.values() if a.class_id))
    all_classes = {c.id: c for c in Class.query.filter(Class.id.in_(cls_ids)).all()} if cls_ids else {}

    class_scores = {}
    question_stats = {}

    for sub in submissions:
        asn = all_asns.get(sub.assignment_id)
        if not asn or not asn.class_id:
            continue
        if class_id and asn.class_id != class_id:
            continue

        result = sub.get_result()
        questions = result.get('questions', [])
        if not questions:
            continue

        cls = all_classes.get(asn.class_id)
        cls_name = cls.name if cls else 'Unknown'
        has_marks = any(q.get('marks_awarded') is not None for q in questions)

        if has_marks:
            total_a = sum(q.get('marks_awarded', 0) for q in questions)
            total_p = sum(q.get('marks_total', 0) for q in questions)
            pct = (total_a / total_p * 100) if total_p > 0 else 0
        else:
            correct = sum(1 for q in questions if q.get('status') == 'correct')
            pct = (correct / len(questions) * 100) if questions else 0

        class_scores.setdefault(cls_name, []).append(pct)

        for i, q in enumerate(questions):
            qnum = str(q.get('question_number', i + 1))
            question_stats.setdefault(qnum, {'correct': 0, 'total': 0})
            question_stats[qnum]['total'] += 1
            if q.get('status') == 'correct' or (has_marks and q.get('marks_awarded', 0) == q.get('marks_total', 1)):
                question_stats[qnum]['correct'] += 1

    comparison = {name: round(sum(scores) / len(scores), 1)
                  for name, scores in class_scores.items()}

    all_scores = [s for scores in class_scores.values() for s in scores]
    distribution = {'0-20': 0, '21-40': 0, '41-60': 0, '61-80': 0, '81-100': 0}
    for s in all_scores:
        if s <= 20: distribution['0-20'] += 1
        elif s <= 40: distribution['21-40'] += 1
        elif s <= 60: distribution['41-60'] += 1
        elif s <= 80: distribution['61-80'] += 1
        else: distribution['81-100'] += 1

    q_difficulty = {qnum: round(stats['correct'] / stats['total'] * 100, 1) if stats['total'] else 0
                    for qnum, stats in sorted(question_stats.items(), key=lambda x: x[0])}

    pass_count = sum(1 for s in all_scores if s >= 50)

    return jsonify({
        'success': True,
        'class_comparison': comparison,
        'score_distribution': distribution,
        'question_difficulty': q_difficulty,
        'total_students': len(all_scores),
        'overall_avg': round(sum(all_scores) / len(all_scores), 1) if all_scores else 0,
        'pass_rate': round(pass_count / len(all_scores) * 100, 1) if all_scores else 0,
    })


@app.route('/department/export/csv')
def department_export_csv():
    """Export results as CSV."""
    err = _require_hod()
    if err:
        return err
    if is_demo_mode() and not is_dept_mode():
        return jsonify({'success': False, 'error': 'Not available in demo mode'}), 403

    assignment_id = request.args.get('assignment_id')
    class_id = request.args.get('class_id')

    query = Submission.query.filter_by(status='done')
    if assignment_id:
        query = query.filter_by(assignment_id=assignment_id)

    submissions = query.all()

    # Pre-load all needed data to avoid N+1
    asn_ids = list(set(s.assignment_id for s in submissions))
    all_asns = {a.id: a for a in Assignment.query.filter(Assignment.id.in_(asn_ids)).all()} if asn_ids else {}
    student_ids = list(set(s.student_id for s in submissions))
    all_students = {s.id: s for s in Student.query.filter(Student.id.in_(student_ids)).all()} if student_ids else {}
    cls_ids = list(set(a.class_id for a in all_asns.values() if a.class_id))
    all_classes = {c.id: c for c in Class.query.filter(Class.id.in_(cls_ids)).all()} if cls_ids else {}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Class', 'Student Index', 'Student Name', 'Assignment', 'Score', 'Percentage'])

    for sub in submissions:
        asn = all_asns.get(sub.assignment_id)
        if not asn:
            continue
        if class_id and asn.class_id != class_id:
            continue

        student = all_students.get(sub.student_id)
        if not student:
            continue

        cls = all_classes.get(asn.class_id) if asn.class_id else None
        result = sub.get_result()
        questions = result.get('questions', [])
        has_marks = any(q.get('marks_awarded') is not None for q in questions)

        if has_marks:
            ta = sum(q.get('marks_awarded', 0) for q in questions)
            tp = sum(q.get('marks_total', 0) for q in questions)
            score = f"{ta}/{tp}"
            pct = round(ta / tp * 100, 1) if tp else 0
        else:
            correct = sum(1 for q in questions if q.get('status') == 'correct')
            score = f"{correct}/{len(questions)}"
            pct = round(correct / len(questions) * 100, 1) if questions else 0

        writer.writerow([
            cls.name if cls else '',
            student.index_number,
            student.name,
            asn.title or asn.subject,
            score,
            f"{pct}%",
        ])

    buf = io.BytesIO(output.getvalue().encode('utf-8-sig'))
    return send_file(buf, mimetype='text/csv', as_attachment=True,
                     download_name='department_results.csv')


@app.route('/department/setup', methods=['GET', 'POST'])
def department_setup():
    if not is_dept_mode():
        return redirect(url_for('hub'))

    # If HOD already exists, redirect
    existing_hod = Teacher.query.filter_by(role='hod').first()
    if existing_hod:
        return redirect(url_for('hub'))

    if request.method == 'POST':
        data = request.get_json()
        name = (data.get('name') or '').strip()
        code = (data.get('code') or '').strip()
        if not name or not code:
            return jsonify({'success': False, 'error': 'Name and code are required'}), 400
        if len(code) < 4:
            return jsonify({'success': False, 'error': 'Code must be at least 4 characters'}), 400

        hod = Teacher(
            id=str(uuid.uuid4()),
            name=name,
            code=code,
            role='hod',
        )
        db.session.add(hod)
        db.session.commit()

        session['teacher_id'] = hod.id
        session['teacher_role'] = hod.role
        session['teacher_name'] = hod.name

        return jsonify({'success': True, 'redirect': '/department/classes'})

    return render_template('department_setup.html')


@app.route('/setup', methods=['GET', 'POST'])
def teacher_setup_page():
    """First-time teacher setup for normal (non-dept) mode."""
    if is_dept_mode():
        return redirect(url_for('hub'))
    if not session.get('pending_setup') and not session.get('teacher_id'):
        return redirect(url_for('hub'))

    # If owner already exists, redirect
    if Teacher.query.filter_by(role='owner').first():
        session.pop('pending_setup', None)
        return redirect(url_for('hub'))

    if request.method == 'POST':
        data = request.get_json()
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'success': False, 'error': 'Name is required'}), 400

        teacher = Teacher(
            id=str(uuid.uuid4()),
            name=name,
            code=get_teacher_code(),
            role='owner',
        )
        db.session.add(teacher)
        db.session.commit()

        session.pop('pending_setup', None)
        session['teacher_id'] = teacher.id
        session['teacher_name'] = teacher.name
        return jsonify({'success': True, 'redirect': '/'})

    return render_template('teacher_setup.html')


# ---------------------------------------------------------------------------
# Teacher Dashboard
# ---------------------------------------------------------------------------

@app.route('/dashboard')
def teacher_dashboard():
    if not is_dept_mode() or not _is_authenticated():
        return redirect(url_for('hub'))

    teacher = _current_teacher()
    if not teacher:
        return redirect(url_for('hub'))

    # Bulk load all assignments for teacher's classes
    teacher_class_ids = [cls.id for cls in teacher.classes]
    all_assignments = Assignment.query.filter(
        Assignment.class_id.in_(teacher_class_ids),
        Assignment.teacher_id == teacher.id
    ).order_by(Assignment.created_at.desc()).all() if teacher_class_ids else []
    assignments_by_class = {}
    for a in all_assignments:
        assignments_by_class.setdefault(a.class_id, []).append(a)

    # Bulk load student counts by class
    student_counts_by_class = {}
    for cls in teacher.classes:
        student_counts_by_class[cls.id] = Student.query.filter_by(class_id=cls.id).count()

    # Bulk load all submissions for these assignments
    all_asn_ids = [a.id for a in all_assignments]
    all_subs = Submission.query.filter(Submission.assignment_id.in_(all_asn_ids)).all() if all_asn_ids else []
    subs_by_assignment = {}
    for s in all_subs:
        subs_by_assignment.setdefault(s.assignment_id, []).append(s)

    class_data = []
    for cls in teacher.classes:
        assignments = assignments_by_class.get(cls.id, [])
        asn_data = []
        for asn in assignments:
            students_count = student_counts_by_class.get(cls.id, 0)
            subs = subs_by_assignment.get(asn.id, [])
            done = [s for s in subs if s.status == 'done']

            avg_score = None
            if done:
                scores = []
                for s in done:
                    result = s.get_result()
                    qs = result.get('questions', [])
                    if qs:
                        has_marks = any(q.get('marks_awarded') is not None for q in qs)
                        if has_marks:
                            total_a = sum(q.get('marks_awarded', 0) for q in qs)
                            total_p = sum(q.get('marks_total', 0) for q in qs)
                            scores.append(total_a / total_p * 100 if total_p else 0)
                        else:
                            correct = sum(1 for q in qs if q.get('status') == 'correct')
                            scores.append(correct / len(qs) * 100 if qs else 0)
                if scores:
                    avg_score = round(sum(scores) / len(scores), 1)

            asn_data.append({
                'id': asn.id,
                'title': asn.title or asn.subject or 'Untitled',
                'subject': asn.subject,
                'classroom_code': asn.classroom_code,
                'total_students': students_count,
                'submitted': len(subs),
                'done': len(done),
                'avg_score': avg_score,
            })

        class_data.append({
            'id': cls.id,
            'name': cls.name,
            'level': cls.level,
            'assignments': asn_data,
        })

    return render_template('dashboard.html',
                           teacher=teacher,
                           classes=class_data,
                           dept_mode=is_dept_mode(),
                           demo_mode=is_demo_mode())


# ---------------------------------------------------------------------------
# Bulk marking
# ---------------------------------------------------------------------------


def _split_pdf_variable(pdf_bytes, page_counts):
    """Split a PDF using variable page counts per student. Returns list of PDF bytes."""
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(io.BytesIO(pdf_bytes))
    total = len(reader.pages)
    chunks = []
    offset = 0
    for count in page_counts:
        writer = PdfWriter()
        for p in range(offset, min(offset + count, total)):
            writer.add_page(reader.pages[p])
        buf = io.BytesIO()
        writer.write(buf)
        chunks.append(buf.getvalue())
        offset += count
    return chunks, total


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
                         assign_type, scoring_mode, total_marks, session_keys,
                         assignment_id=None, student_id_map=None):
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

        # Save to DB if in dept mode
        if assignment_id and student_id_map:
            try:
                with app.app_context():
                    student_db_id = student_id_map.get(student['index'])
                    if student_db_id:
                        sub = Submission(
                            student_id=student_db_id,
                            assignment_id=assignment_id,
                            script_bytes=script_bytes,
                            status='error' if result.get('error') else 'done',
                        )
                        sub.set_result(result)
                        sub.marked_at = datetime.now(timezone.utc)
                        db.session.add(sub)
                        db.session.commit()
            except Exception as e:
                db.session.rollback()
                logger.error(f"Failed to save submission for {student['name']}: {e}")

    jobs[job_id]['results'] = results
    jobs[job_id]['status'] = 'done'
    jobs[job_id]['progress'] = {'current': total, 'total': total, 'current_name': 'Complete'}


@app.route('/bulk')
def bulk_page():
    return redirect(url_for('class_page'))


@app.route('/bulk/mark', methods=['POST'])
def bulk_mark():
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    assignment_id = request.form.get('assignment_id')
    if not assignment_id:
        return jsonify({'success': False, 'error': 'Assignment is required'}), 400

    asn = Assignment.query.get(assignment_id)
    if not asn:
        return jsonify({'success': False, 'error': 'Assignment not found'}), 404

    # Ownership check
    err = _check_assignment_ownership(asn)
    if err:
        return err

    if not asn.class_id:
        return jsonify({'success': False, 'error': 'Assignment has no class linked'}), 400

    # Get students from class (sorted)
    all_students = _sort_by_index(Student.query.filter_by(class_id=asn.class_id).all())
    if not all_students:
        return jsonify({'success': False, 'error': 'No students in this class'}), 400

    # Parse page counts (one per student, 0 = skip)
    page_counts_json = request.form.get('page_counts', '')
    if not page_counts_json:
        return jsonify({'success': False, 'error': 'Page counts are required'}), 400

    try:
        page_counts = json.loads(page_counts_json)
    except json.JSONDecodeError:
        return jsonify({'success': False, 'error': 'Invalid page counts data'}), 400

    if len(page_counts) != len(all_students):
        return jsonify({'success': False,
            'error': f'Page counts ({len(page_counts)}) does not match students ({len(all_students)})'}), 400

    # Validate bulk scripts file
    bulk_file = request.files.get('bulk_scripts')
    if not bulk_file or not bulk_file.filename:
        return jsonify({'success': False, 'error': 'Please upload the bulk scripts PDF'}), 400
    bulk_pdf = bulk_file.read()

    # Build list of students to mark (skip those with page_count=0)
    students_to_mark = []
    page_counts_to_split = []
    for student, pc in zip(all_students, page_counts):
        pc = int(pc)
        if pc > 0:
            students_to_mark.append({
                'index': student.index_number,
                'name': student.name,
                'db_id': student.id,
            })
            page_counts_to_split.append(pc)

    if not students_to_mark:
        return jsonify({'success': False, 'error': 'All students are set to skip (0 pages)'}), 400

    # Split PDF using only non-zero page counts
    try:
        student_scripts, pdf_total = _split_pdf_variable(bulk_pdf, page_counts_to_split)
        allocated = sum(page_counts_to_split)
        if allocated != pdf_total:
            return jsonify({'success': False,
                'error': f'Allocated pages ({allocated}) does not match PDF pages ({pdf_total}). Please adjust.'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error splitting PDF: {e}'}), 400

    # Delete existing submissions for students being re-marked
    for s in students_to_mark:
        existing = Submission.query.filter_by(student_id=s['db_id'], assignment_id=assignment_id).first()
        if existing:
            db.session.delete(existing)
    db.session.commit()

    # Use assignment's stored settings
    session_keys = _resolve_api_keys(asn)

    # Build student_id_map for the background thread
    student_id_map = {s['index']: s['db_id'] for s in students_to_mark}

    # Get files from assignment record
    question_paper_pages = [asn.question_paper] if asn.question_paper else []
    answer_key_pages = [asn.answer_key] if asn.answer_key else []
    rubrics_pages = [asn.rubrics] if asn.rubrics else []
    reference_pages = [asn.reference] if asn.reference else []

    cleanup_old_jobs()

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        'status': 'processing',
        'result': None,
        'results': [],
        'subject': asn.subject,
        'created_at': time.time(),
        'progress': {'current': 0, 'total': len(students_to_mark), 'current_name': 'Starting...'},
        'bulk': True,
        'assignment_id': assignment_id,
    }

    thread = threading.Thread(
        target=run_bulk_marking_job,
        args=(job_id, asn.provider, asn.model, question_paper_pages, answer_key_pages,
              rubrics_pages, reference_pages, student_scripts, students_to_mark,
              asn.subject, asn.review_instructions, asn.marking_instructions,
              asn.assign_type, asn.scoring_mode, asn.total_marks, session_keys,
              assignment_id, student_id_map),
        daemon=True
    )
    thread.start()

    return jsonify({'success': True, 'job_id': job_id})


@app.route('/bulk/download/<job_id>')
def bulk_download(job_id):
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    job = jobs.get(job_id)
    if not job or job['status'] != 'done' or not job.get('results'):
        return jsonify({'success': False, 'error': 'No results available'}), 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in job['results']:
            if item['result'].get('error'):
                continue
            pdf_bytes = generate_report_pdf(item['result'], subject=job.get('subject', ''), app_title=get_app_title())
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
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    job = jobs.get(job_id)
    if not job or job['status'] != 'done' or not job.get('results'):
        return jsonify({'success': False, 'error': 'No results available'}), 404

    student_results = [
        {'name': item['name'], 'index': item['index'], 'result': item['result']}
        for item in job['results']
    ]
    pdf_bytes = generate_overview_pdf(student_results, subject=job.get('subject', ''), app_title=get_app_title())

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name='Class_Overview_Report.pdf'
    )


# ---------------------------------------------------------------------------
# Teacher dashboard & student submission portal
# ---------------------------------------------------------------------------

def _sort_by_index(items, key='index_number'):
    """Sort items by index numerically (1, 2, ... 10), then non-numeric alphabetically."""
    def sort_key(item):
        val = getattr(item, key) if hasattr(item, key) else item.get(key, '')
        try:
            return (0, int(val), '')
        except (ValueError, TypeError):
            return (1, 0, str(val))
    return sorted(items, key=sort_key)


def _generate_classroom_code():
    """Generate a short unique classroom code like ENG3E."""
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(secrets.choice(chars) for _ in range(6))
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
            script = sub.get_script_pages()

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
                session_keys=_resolve_api_keys(asn),
            )

            sub.set_result(result)
            sub.status = 'error' if result.get('error') else 'done'
            sub.marked_at = datetime.now(timezone.utc)
        except Exception as e:
            db.session.rollback()
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
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    teacher_obj = _current_teacher()

    # Class is always required now
    class_id = request.form.get('class_id')
    if not class_id:
        return jsonify({'success': False, 'error': 'Please select a class'}), 400

    cls = Class.query.get(class_id)
    if not cls:
        return jsonify({'success': False, 'error': 'Class not found'}), 404

    # Check class has students
    student_count = Student.query.filter_by(class_id=class_id).count()
    if student_count == 0:
        return jsonify({'success': False, 'error': 'This class has no students. Upload a class list first.'}), 400

    # Ownership check
    if teacher_obj:
        if teacher_obj.role not in ('hod', 'owner'):
            tc = TeacherClass.query.filter_by(teacher_id=teacher_obj.id, class_id=class_id).first()
            if not tc:
                return jsonify({'success': False, 'error': 'Not assigned to this class'}), 403

    # API keys
    api_keys = {}
    from ai_marking import PROVIDER_KEY_MAP
    for prov, env_name in PROVIDER_KEY_MAP.items():
        val = os.getenv(env_name, '') or request.form.get(f'api_key_{prov}', '').strip()
        if val:
            api_keys[prov] = val

    if not api_keys:
        # In dept mode, try department keys
        if is_dept_mode():
            dept_keys = _get_dept_keys()
            if dept_keys:
                api_keys = dept_keys
        if not api_keys:
            return jsonify({'success': False, 'error': 'No API keys available. Configure server keys or enter your own.'}), 400

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
        title=request.form.get('title', ''),
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
        class_id=class_id,
        teacher_id=teacher_obj.id if teacher_obj else None,
    )
    # Only store user-provided keys
    user_keys = {}
    for prov in ('anthropic', 'openai', 'qwen'):
        val = request.form.get(f'api_key_{prov}', '').strip()
        if val:
            user_keys[prov] = val
    asn.set_api_keys(user_keys)
    db.session.add(asn)
    db.session.commit()

    return jsonify({
        'success': True,
        'assignment_id': asn.id,
        'classroom_code': asn.classroom_code,
    })


@app.route('/teacher/assignment/<assignment_id>')
def teacher_assignment_detail(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    students = _sort_by_index(Student.query.filter_by(class_id=asn.class_id).all()) if asn.class_id else _sort_by_index(Student.query.filter_by(assignment_id=assignment_id).all())

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
            'student_id': s.id,
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
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
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
            pdf_bytes = generate_report_pdf(result, subject=asn.subject, app_title=get_app_title())
            safe_name = student.name.replace('/', '_').replace('\\', '_')
            zf.writestr(f"{student.index_number}_{safe_name}_report.pdf", pdf_bytes)
    buf.seek(0)

    return send_file(buf, mimetype='application/zip', as_attachment=True,
                     download_name=f'{asn.classroom_code}_reports.zip')


@app.route('/teacher/assignment/<assignment_id>/overview')
def teacher_overview(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
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

    pdf_bytes = generate_overview_pdf(student_results, subject=asn.subject, app_title=get_app_title())

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'{asn.classroom_code}_overview.pdf'
    )


@app.route('/teacher/assignment/<assignment_id>/submit/<int:student_id>', methods=['POST'])
def teacher_submit_for_student(assignment_id, student_id):
    """Teacher uploads a script on behalf of a student."""
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    student = Student.query.get(student_id)
    if not student:
        return jsonify({'success': False, 'error': 'Invalid student'}), 400
    # Validate student belongs to this assignment's class or assignment
    if asn.class_id:
        if not student.class_id or student.class_id != asn.class_id:
            return jsonify({'success': False, 'error': 'Invalid student'}), 400
    elif student.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Invalid student'}), 400

    script_files = request.files.getlist('script')
    if not script_files or not script_files[0].filename:
        return jsonify({'success': False, 'error': 'Please upload a script'}), 400
    if len(script_files) > 10:
        return jsonify({'success': False, 'error': 'Maximum 10 files'}), 400

    script_pages = [f.read() for f in script_files if f.filename]

    existing = Submission.query.filter_by(student_id=student.id, assignment_id=assignment_id).first()
    if existing:
        db.session.delete(existing)
        db.session.flush()

    sub = Submission(
        student_id=student.id,
        assignment_id=assignment_id,
        script_bytes=script_pages[0] if script_pages else None,
        status='pending',
    )
    sub.set_script_pages(script_pages)
    db.session.add(sub)
    db.session.commit()

    thread = threading.Thread(
        target=_run_submission_marking,
        args=(app, sub.id, assignment_id),
        daemon=True,
    )
    thread.start()

    return jsonify({'success': True})


@app.route('/teacher/assignment/<assignment_id>/delete', methods=['POST'])
def teacher_delete_assignment(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    db.session.delete(asn)
    db.session.commit()
    return jsonify({'success': True})


# --- Student submission ---

@app.route('/submit/<assignment_id>')
def student_page(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    return render_template('submit.html', assignment_id=assignment_id, subject=asn.subject, demo_mode=is_demo_mode())


@app.route('/submit/<assignment_id>/verify', methods=['POST'])
def student_verify(assignment_id):
    if is_demo_mode():
        return jsonify({'success': False, 'error': 'Submissions are disabled in demo mode'}), 403
    if not _check_rate_limit(f'student_verify:{request.remote_addr}'):
        return jsonify({'success': False, 'error': 'Too many attempts. Please wait.'}), 429
    asn = Assignment.query.get_or_404(assignment_id)
    data = request.get_json()
    code = (data.get('code') or '').strip().upper()
    if code != asn.classroom_code:
        return jsonify({'success': False, 'error': 'Invalid classroom code'}), 401

    students = _sort_by_index(Student.query.filter_by(class_id=asn.class_id).all()) if asn.class_id else _sort_by_index(Student.query.filter_by(assignment_id=assignment_id).all())
    student_list = [{'id': s.id, 'index': s.index_number, 'name': s.name} for s in students]

    session[f'student_auth_{assignment_id}'] = True
    return jsonify({'success': True, 'students': student_list})


@app.route('/submit/<assignment_id>/upload', methods=['POST'])
def student_upload(assignment_id):
    if is_demo_mode():
        return jsonify({'success': False, 'error': 'Submissions are disabled in demo mode'}), 403
    if not session.get(f'student_auth_{assignment_id}'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    asn = Assignment.query.get_or_404(assignment_id)

    student_id = request.form.get('student_id')
    if not student_id:
        return jsonify({'success': False, 'error': 'Please select your name'}), 400

    student = Student.query.get(student_id)
    if not student:
        return jsonify({'success': False, 'error': 'Invalid student'}), 400
    # Validate student belongs to this assignment's class or assignment
    if asn.class_id:
        if not student.class_id or student.class_id != asn.class_id:
            return jsonify({'success': False, 'error': 'Invalid student'}), 400
    elif student.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Invalid student'}), 400

    script_files = request.files.getlist('script')
    if not script_files or not script_files[0].filename:
        return jsonify({'success': False, 'error': 'Please upload your script'}), 400
    if len(script_files) > 10:
        return jsonify({'success': False, 'error': 'Maximum 10 files per submission'}), 400

    script_pages = [f.read() for f in script_files if f.filename]

    # Delete existing submission if re-submitting
    existing = Submission.query.filter_by(student_id=student.id, assignment_id=assignment_id).first()
    if existing:
        db.session.delete(existing)
        db.session.flush()

    sub = Submission(
        student_id=student.id,
        assignment_id=assignment_id,
        script_bytes=script_pages[0] if script_pages else None,
        status='pending',
    )
    sub.set_script_pages(script_pages)
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
    is_teacher = _is_authenticated()
    is_student = session.get(f'student_auth_{assignment_id}')
    if not is_student and not is_teacher:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Not found'}), 404

    asn = Assignment.query.get(assignment_id)
    response = {'success': True, 'status': sub.status}

    if sub.status in ('done', 'error'):
        result = sub.get_result()
        # Teachers always see results; students only if show_results is on
        if is_teacher or (asn and asn.show_results):
            response['result'] = result
        elif result.get('error'):
            response['result'] = {'error': result['error']}

    return jsonify(response)


@app.route('/submit/<assignment_id>/download/<int:submission_id>')
def download_submission_pdf(assignment_id, submission_id):
    """Download a PDF report for a specific submission."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    if sub.status != 'done':
        return jsonify({'success': False, 'error': 'No results available'}), 404

    asn = Assignment.query.get(assignment_id)
    result = sub.get_result()
    subject = asn.subject if asn else ''
    pdf_bytes = generate_report_pdf(result, subject=subject, app_title=get_app_title())

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name='AI_Marking_Report.pdf'
    )


@app.route('/api/class/<class_id>/assignments')
def api_class_assignments(class_id):
    if not _is_authenticated():
        return jsonify([])
    assignments = Assignment.query.filter_by(class_id=class_id).order_by(Assignment.created_at.desc()).all()
    return jsonify([{
        'id': a.id,
        'title': a.title or a.subject or 'Untitled',
        'subject': a.subject,
        'classroom_code': a.classroom_code,
        'created_at': a.created_at.isoformat() if a.created_at else None,
    } for a in assignments])


@app.route('/api/assignment/<assignment_id>/students')
def api_assignment_students(assignment_id):
    if not _is_authenticated():
        return jsonify([])
    asn = Assignment.query.get_or_404(assignment_id)

    # Get students from class level
    if asn.class_id:
        students = _sort_by_index(Student.query.filter_by(class_id=asn.class_id).all())
    else:
        students = _sort_by_index(Student.query.filter_by(assignment_id=assignment_id).all())

    result = []
    for s in students:
        sub = Submission.query.filter_by(student_id=s.id, assignment_id=assignment_id).first()
        result.append({
            'id': s.id,
            'index': s.index_number,
            'name': s.name,
            'has_submission': sub is not None,
            'status': sub.status if sub else None,
            'submitted_at': sub.submitted_at.isoformat() if sub and sub.submitted_at else None,
            'marked_at': sub.marked_at.isoformat() if sub and sub.marked_at else None,
        })
    return jsonify(result)


# ---------------------------------------------------------------------------
# Setup Wizard & Settings
# ---------------------------------------------------------------------------

@app.route('/setup/wizard', methods=['GET', 'POST'])
def setup_wizard():
    if _is_setup_complete():
        return redirect(url_for('hub'))

    if request.method == 'POST':
        data = request.get_json()
        step = data.get('step')

        if step == 'mode':
            mode = data.get('mode')
            if mode not in ('normal', 'department', 'demo', 'demo_department'):
                return jsonify({'success': False, 'error': 'Invalid mode'}), 400
            _set_config('app_mode', mode)
            return jsonify({'success': True})

        elif step == 'config':
            title = (data.get('title') or '').strip()
            if title:
                _set_config('app_title', title)

            name = (data.get('name') or '').strip()
            code = (data.get('code') or '').strip()

            mode = get_app_mode()

            # Save API keys (encrypted)
            from db import _get_fernet
            f = _get_fernet()
            for prov in ('anthropic', 'openai', 'qwen'):
                val = (data.get(f'api_key_{prov}') or '').strip()
                if val:
                    encrypted = f.encrypt(val.encode()).decode() if f else val
                    _set_config(f'api_key_{prov}', encrypted)

            # Check at least one API key (from wizard or env)
            has_key = False
            for prov in ('anthropic', 'openai', 'qwen'):
                cfg = DepartmentConfig.query.filter_by(key=f'api_key_{prov}').first()
                if cfg and cfg.value:
                    has_key = True
                    break
            if not has_key:
                from ai_marking import PROVIDER_KEY_MAP
                for prov, env_name in PROVIDER_KEY_MAP.items():
                    if os.getenv(env_name, ''):
                        has_key = True
                        break

            if not has_key:
                return jsonify({'success': False, 'error': 'At least one API key is required'}), 400

            if mode == 'normal':
                if not name or not code:
                    return jsonify({'success': False, 'error': 'Name and access code are required'}), 400
                _set_config('teacher_code', code)
                teacher = Teacher(
                    id=str(uuid.uuid4()),
                    name=name,
                    code=code,
                    role='owner',
                )
                db.session.add(teacher)
                db.session.commit()

            elif mode == 'department':
                if not name or not code:
                    return jsonify({'success': False, 'error': 'Name and access code are required'}), 400
                _set_config('teacher_code', code)
                hod = Teacher(
                    id=str(uuid.uuid4()),
                    name=name,
                    code=code,
                    role='hod',
                )
                db.session.add(hod)
                db.session.commit()

            # For demo modes, no teacher/code needed

            # Seed data for demo_department
            if mode == 'demo_department':
                from seed_data import seed_demo_department
                seed_demo_department(db, Teacher, Class, TeacherClass, Assignment, Student, Submission)

            _set_config('setup_complete', 'true')
            return jsonify({'success': True, 'redirect': '/'})

        return jsonify({'success': False, 'error': 'Invalid step'}), 400

    return render_template('setup_wizard.html')


@app.route('/settings')
def settings_page():
    if not _is_authenticated():
        return redirect(url_for('hub'))
    teacher = _current_teacher()
    if teacher and teacher.role not in ('owner', 'hod'):
        return redirect(url_for('hub'))

    # For demo mode without teacher, allow access
    if not teacher and not is_demo_mode():
        return redirect(url_for('hub'))

    # Get current config
    mode = get_app_mode()
    title = get_app_title()
    code = get_teacher_code()

    # Check which API keys are set (don't expose values)
    api_keys_set = {}
    for prov in ('anthropic', 'openai', 'qwen'):
        cfg = DepartmentConfig.query.filter_by(key=f'api_key_{prov}').first()
        has_db = bool(cfg and cfg.value)
        from ai_marking import PROVIDER_KEY_MAP
        has_env = bool(os.getenv(PROVIDER_KEY_MAP.get(prov, ''), ''))
        api_keys_set[prov] = has_db or has_env

    return render_template('settings.html',
                           mode=mode,
                           title=title,
                           code=code,
                           api_keys_set=api_keys_set,
                           teacher=teacher)


@app.route('/settings/update', methods=['POST'])
def settings_update():
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    teacher = _current_teacher()
    if teacher and teacher.role not in ('owner', 'hod'):
        return jsonify({'success': False, 'error': 'Access denied'}), 403

    data = request.get_json()

    if 'title' in data:
        _set_config('app_title', data['title'].strip())

    if 'mode' in data:
        new_mode = data['mode']
        if new_mode in ('normal', 'department', 'demo', 'demo_department'):
            _set_config('app_mode', new_mode)

    if 'code' in data:
        new_code = data['code'].strip()
        if new_code:
            _set_config('teacher_code', new_code)
            # Also update the teacher's code in DB if exists
            if teacher:
                teacher.code = new_code
                db.session.commit()

    # Update API keys
    from db import _get_fernet
    f = _get_fernet()
    for prov in ('anthropic', 'openai', 'qwen'):
        key_field = f'api_key_{prov}'
        if key_field in data:
            val = (data[key_field] or '').strip()
            if val:
                encrypted = f.encrypt(val.encode()).decode() if f else val
                _set_config(f'api_key_{prov}', encrypted)

    return jsonify({'success': True})


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true')
