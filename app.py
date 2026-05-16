import os
import re
import csv
import json
import uuid
import string
import secrets
import logging
import threading
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, session, send_file, redirect, url_for, Response, abort, make_response, g
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix
import io

from ai_marking import mark_script, get_available_providers, PROVIDERS, generate_exemplar_analysis, explain_criterion, evaluate_correction, consume_last_usage, MarkingError, AIProviderError, ResponseParseError
from pdf_generator import generate_report_pdf, generate_overview_pdf
from db import db, init_db, Assignment, AssignmentBank, Student, Submission, Teacher, Class, TeacherClass, DepartmentConfig, TeacherDashboardLayout, BulkJob, SubmissionStatus, utc, FeedbackEdit, DepartmentDashboardLayout, DepartmentGoal, Department, TeacherDepartment

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# UP-32: Sentry. Opt-in via SENTRY_DSN env var so local dev / CI doesn't
# ship events; Railway sets SENTRY_DSN once and every deploy reports.
# Initialised BEFORE `Flask(__name__)` so the Flask integration's
# request-context hooks see every request.
_SENTRY_DSN = os.getenv('SENTRY_DSN', '').strip()
if _SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            integrations=[
                FlaskIntegration(),
                # `logger.exception(...)` (UP-27) is now a real Sentry event;
                # plain `logger.info(...)` stays as a breadcrumb only.
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
            traces_sample_rate=float(os.getenv('SENTRY_TRACES_SAMPLE_RATE', '0.1')),
            release=os.getenv('RAILWAY_GIT_COMMIT_SHA') or os.getenv('GIT_SHA') or None,
            environment=os.getenv('SENTRY_ENVIRONMENT') or os.getenv('FLASK_ENV') or 'production',
            send_default_pii=False,
        )
        logger.info('Sentry initialised (DSN configured)')
    except Exception:
        # Never let a misconfigured Sentry block app boot.
        logger.exception('Sentry init failed — continuing without it')

app = Flask(__name__)

# UP-18: surface real client IPs through Railway's load balancer so the
# in-memory rate limiter (request.remote_addr) doesn't collapse every
# visitor into the LB's address. One hop of trust (x_for/x_proto = 1)
# matches Railway's single reverse-proxy layer.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

_flask_secret = os.getenv('FLASK_SECRET_KEY', '')
_secret_from_env = bool(_flask_secret)
if not _flask_secret:
    _flask_secret = os.urandom(32).hex()
app.secret_key = _flask_secret

app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV') != 'development'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB
# UP-28: bound idle session lifetime. Teachers re-auth after 8h of inactivity,
# which closes the "left the laptop unlocked" hole without nagging during a
# normal marking session. Honored only when `session.permanent = True`, which
# every successful login branch sets via `_finalise_login_session()`.
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)

# UP-04: CSRF protection. Default-on for every POST/PUT/DELETE/PATCH;
# student-submission routes are opted out below via @csrf.exempt because
# they're auth-gated by classroom code (no teacher session to bind a
# token to). The token is exposed in `base.html`'s <meta> tag and a small
# fetch() shim injects it as the `X-CSRFToken` header on every AJAX call.
csrf = CSRFProtect(app)


@app.errorhandler(CSRFError)
def _handle_csrf_error(e):
    """Return a JSON 400 (consistent with the rest of the API) instead of
    the default Flask-WTF HTML page so XHR callers can react cleanly.
    Reuses `_wants_json_response()` (defined below) — but that helper
    isn't in scope at decorator-eval time, so we inline its checks here.
    Kept in sync with `_wants_json_response`: any signal that says "this
    is a fetch/XHR call" routes to JSON; everything else gets plain text."""
    is_fetch = (
        request.path.startswith('/api/')
        or request.is_json
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.headers.get('Sec-Fetch-Dest') == 'empty'
        or request.accept_mimetypes.best == 'application/json'
    )
    if is_fetch:
        return jsonify({'success': False, 'error': 'CSRF token missing or invalid'}), 400
    return Response(f'CSRF check failed: {e.description}', status=400, mimetype='text/plain')

_ENV_ACCESS_CODE = os.getenv('ACCESS_CODE', '').strip()  # keep for legacy
_ENV_TEACHER_CODE = os.getenv('TEACHER_CODE', '').strip() or _ENV_ACCESS_CODE
_ENV_DEMO_MODE = os.getenv('DEMO_MODE', 'FALSE').upper() == 'TRUE'
_ENV_DEPT_MODE = os.getenv('DEPT_MODE', 'FALSE').upper() == 'TRUE'
_ENV_APP_TITLE = os.getenv('APP_TITLE', 'AI Feedback Systems')

# Hide the student-facing "By mistake type" toggle and grouped view until
# the categorisation pipeline is judged robust enough. The pipeline still
# runs (mistake_type lands on result_json so calibration Tier 1 + propagation
# continue to work) — only the student UI is suppressed. Set the env var
# to "TRUE" to re-enable the student grouping view.
_ENV_STUDENT_GROUPING_UI_ENABLED = os.getenv('STUDENT_GROUPING_UI_ENABLED', 'FALSE').upper() == 'TRUE'

# Calibration intent design (2026-05-13) §4.9: teacher-facing inline theme/category
# dropdown. The categorisation pipeline (mistake_type on result_json, FeedbackEdit
# inheritance, calibration retrieval) runs regardless — this flag only controls
# the teacher correction UI surface. Set to "TRUE" to re-enable.
_ENV_TEACHER_THEME_UI_ENABLED = os.getenv('TEACHER_THEME_UI_ENABLED', 'FALSE').upper() == 'TRUE'

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

# Warm jieba's dictionary in a background thread so the first Chinese mark
# after a deploy doesn't pay the ~400ms-cold load cost on the request path.
# Soft-fails if pypinyin/jieba aren't installed yet (e.g. layered deploys).
def _warm_pinyin_libs():
    try:
        from pinyin_annotate import annotate
        annotate('你好世界', mode='vocab')
        logger.info('pinyin libs (jieba + pypinyin) warmed at boot')
    except Exception as _e:
        logger.warning(f'pinyin warmup skipped: {_e}')

threading.Thread(target=_warm_pinyin_libs, daemon=True).start()

# ---------------------------------------------------------------------------
# Auto-persist FLASK_SECRET_KEY in DB so Railway deployments need zero env vars
# ---------------------------------------------------------------------------
with app.app_context():
    if not _secret_from_env:
        _stored = DepartmentConfig.query.filter_by(key='flask_secret_key').first()
        if _stored and _stored.value:
            # Reuse the persisted key so sessions & encrypted data survive restarts
            _flask_secret = _stored.value
            app.secret_key = _flask_secret
        else:
            # First boot — persist the generated key
            _cfg = DepartmentConfig(key='flask_secret_key', value=_flask_secret)
            db.session.add(_cfg)
            db.session.commit()
            logger.info('Auto-generated FLASK_SECRET_KEY and stored in database')
    else:
        # Env var is set — ensure DB copy stays in sync for _get_fernet fallback
        _stored = DepartmentConfig.query.filter_by(key='flask_secret_key').first()
        if _stored:
            _stored.value = _flask_secret
        else:
            _cfg = DepartmentConfig(key='flask_secret_key', value=_flask_secret)
            db.session.add(_cfg)
        db.session.commit()


# ---------------------------------------------------------------------------
# Config helpers: DB-backed configuration with env var fallback
# ---------------------------------------------------------------------------

def _get_config(key, default=''):
    """Get config from DB (DepartmentConfig), falling back to env var.

    Per-request memoised via `g._cfg_cache`. Templates + helpers hit the
    same handful of keys (app_mode, app_title, teacher_code,
    setup_complete) up to 6–10 times per render — without this cache,
    each call is a separate DB roundtrip. Cache is cleared on writes via
    `_set_config` so the post-write read inside the same request sees
    fresh data.
    """
    cache = getattr(g, '_cfg_cache', None)
    if cache is None:
        cache = {}
        try:
            g._cfg_cache = cache
        except RuntimeError:
            # Outside a request context (boot-time helpers) — skip caching.
            cache = None
    if cache is not None and key in cache:
        v = cache[key]
        return v if v is not None else default
    try:
        cfg = DepartmentConfig.query.filter_by(key=key).first()
        val = cfg.value if (cfg and cfg.value) else None
    except Exception:
        val = None
    if cache is not None:
        cache[key] = val
    return val if val is not None else default


def _set_config(key, value):
    """Set a config value in DepartmentConfig."""
    cfg = DepartmentConfig.query.filter_by(key=key).first()
    if cfg:
        cfg.value = value
    else:
        cfg = DepartmentConfig(key=key, value=value)
        db.session.add(cfg)
    db.session.commit()
    # Invalidate per-request cache so subsequent reads in the same
    # request see the new value (e.g. setup wizard saves config then
    # redirects to hub which reads it back).
    try:
        cache = getattr(g, '_cfg_cache', None)
        if isinstance(cache, dict):
            cache.pop(key, None)
    except RuntimeError:
        pass


def _is_setup_complete():
    """Check if the setup wizard has been completed."""
    return _get_config('setup_complete') == 'true'


def get_app_mode():
    """Get app mode from DB, falling back to env vars. Per-request cached."""
    val = _get_config('app_mode')
    if val:
        return val
    if _ENV_DEMO_MODE and _ENV_DEPT_MODE:
        return 'demo_department'
    if _ENV_DEMO_MODE:
        return 'demo'
    if _ENV_DEPT_MODE:
        return 'department'
    return 'normal'


def get_app_title():
    """Get app title from DB, falling back to env var. Per-request cached."""
    return _get_config('app_title') or _ENV_APP_TITLE


def normalize_name(name):
    """UP-47: Unicode NFC normalisation + whitespace trim for any
    human-typed or CSV-ingested name. Tamil / Mandarin / Malay surnames
    in NFD form (separate base char + combining marks) and NFC form
    (precomposed) compare unequal in Python dicts / SQL `=`, sort
    inconsistently, and would produce two `Student` rows for the same
    person. Apply at every write boundary; readers can then trust
    `==` and dict-lookup semantics.

    Returns '' for falsy / non-string inputs."""
    if not name:
        return ''
    if not isinstance(name, str):
        name = str(name)
    return unicodedata.normalize('NFC', name).strip()


def get_teacher_code():
    """Get teacher code from DB, falling back to env var. Per-request cached."""
    return _get_config('teacher_code') or _ENV_TEACHER_CODE


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

def _compute_static_version():
    """Cache-buster for static assets. Uses process start time so every
    Railway redeploy gets a fresh value, regardless of how the build tool
    handles file mtimes."""
    import time
    return str(int(time.time()))


_STATIC_VERSION = _compute_static_version()


@app.context_processor
def inject_dept_context():
    """Make dept_mode, demo_mode, app_title and current teacher available in all templates."""
    teacher = _current_teacher()  # works for both modes now
    from subjects import SUBJECT_DISPLAY_NAMES, SUBJECTS
    return {
        'dept_mode': is_dept_mode(),
        'demo_mode': is_demo_mode(),
        'app_title': get_app_title(),
        'current_teacher': teacher,
        'static_version': _STATIC_VERSION,
        'canonical_subjects': SUBJECT_DISPLAY_NAMES,
        'canonical_subject_aliases': {s['display']: s.get('aliases', []) for s in SUBJECTS},
        'TEACHER_THEME_UI_ENABLED': _ENV_TEACHER_THEME_UI_ENABLED,
    }


_CDN_SCRIPT_SRCS = "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com"
_CDN_STYLE_SRCS = "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com"
_CDN_FONT_SRCS = "https://cdn.jsdelivr.net https://cdnjs.cloudflare.com"

# UP-29: Content-Security-Policy. Goal: limit blast radius if a CDN bundle
# is compromised or if AI-rendered HTML smuggles a script. Notes:
#   * `script-src 'unsafe-inline'` stays for now because UP-26 inline-onclick
#     migration is only partial (dashboard.html done; ~165 sites remain). The
#     SRI hashes pinned in base.html / teacher_insights.html / review.html /
#     bank_preview.html / exemplars.html still constrain CDN integrity.
#   * `worker-src 'self' blob:` is for pdfjs-dist, which spawns its worker
#     from a blob URL constructed in-page.
#   * `frame-ancestors 'self'` plays nicely with the print-all endpoint
#     that wraps a merged PDF in a same-origin iframe (replaces the legacy
#     X-Frame-Options: SAMEORIGIN override at print_all_reports).
_CSP_POLICY = (
    "default-src 'self'; "
    f"script-src 'self' 'unsafe-inline' 'unsafe-eval' {_CDN_SCRIPT_SRCS}; "
    f"style-src 'self' 'unsafe-inline' {_CDN_STYLE_SRCS}; "
    "img-src 'self' data: blob:; "
    f"font-src 'self' data: {_CDN_FONT_SRCS}; "
    "worker-src 'self' blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)


@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # setdefault so individual routes can override (e.g. the print-all
    # merged-PDF endpoint sets SAMEORIGIN so the wrapper-page iframe
    # can embed it). DENY remains the default for everything else.
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    # UP-29: defense-in-depth against XSS / CDN supply-chain compromise.
    response.headers.setdefault('Content-Security-Policy', _CSP_POLICY)
    # UP-29: HSTS — browsers ignore this on plain-HTTP responses, so it's
    # safe to set unconditionally. 2-year max-age + includeSubDomains is the
    # current OWASP recommendation. No `preload` until we're sure we want
    # the preload commitment (it's effectively irreversible).
    response.headers.setdefault(
        'Strict-Transport-Security',
        'max-age=63072000; includeSubDomains',
    )
    return response


@app.route('/healthz')
def healthz():
    """UP-34: cheap liveness probe. No DB touch — Railway's container
    healthcheck and gunicorn's worker recycling poll this constantly,
    and we don't want to hammer the DB on every probe. If the Flask
    request-response cycle works, the worker is alive."""
    return ('ok', 200, {'Content-Type': 'text/plain; charset=utf-8'})


def _wants_json_response():
    """True for XHR / API callers — they get JSON; humans get the branded page.

    Detection signals (any one triggers JSON):
      * `/api/` path prefix
      * Content-Type is JSON (POST body)
      * Legacy `X-Requested-With: XMLHttpRequest`
      * `Accept: application/json` outranks text/html
      * `Sec-Fetch-Dest: empty` — modern browsers tag every `fetch()` call
        with this header, so a server-side 5xx from a feature endpoint
        called via fetch returns JSON (not the HTML _error.html page that
        would then crash the caller's `r.json()` parser).
    """
    if request.path.startswith('/api/'):
        return True
    if request.is_json:
        return True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    if request.headers.get('Sec-Fetch-Dest') == 'empty':
        return True
    accept = request.accept_mimetypes
    return accept.best == 'application/json' and accept['text/html'] < accept['application/json']


def _render_error_page(code, heading, message, json_error):
    """UP-44: branded HTML for browsers, JSON for XHR/API. Falls back to a
    plain string if the template render itself raises (defence in depth so
    the error handler can never itself 500)."""
    if _wants_json_response():
        return jsonify({'success': False, 'error': json_error}), code
    try:
        html = render_template('_error.html', code=code, heading=heading, message=message)
        return html, code
    except Exception:
        logger.exception("error template render failed for code=%s", code)
        return (f'{code} {heading}. {message}', code, {'Content-Type': 'text/plain; charset=utf-8'})


@app.errorhandler(404)
def not_found(e):
    return _render_error_page(
        404,
        'Page not found',
        "That link looks broken or the page was moved.",
        'Not found',
    )


@app.errorhandler(413)
def too_large(e):
    return _render_error_page(
        413,
        'Upload too large',
        'Maximum 100 MB total per upload. Try splitting the PDF or reducing image sizes.',
        'Upload too large. Maximum 100MB total.',
    )


@app.errorhandler(429)
def rate_limited(e):
    return _render_error_page(
        429,
        'Too many requests',
        'You are doing that too often. Wait a minute and try again.',
        'Too many requests',
    )


@app.errorhandler(500)
def internal_error(e):
    # Make the full traceback visible in Railway logs so 500s can be diagnosed.
    logger.exception("500 on %s %s", request.method, request.path)
    return _render_error_page(
        500,
        'Something went wrong',
        'The server hit an unexpected error. The team has been notified — try again in a moment.',
        'Internal server error. Check server logs for details.',
    )


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

# In-memory job store for single-marking (demo). UP-15 moved bulk jobs
# to the `bulk_jobs` table so they survive Railway redeploys; this dict
# now holds only short-lived single-marking jobs (`/mark` route) and the
# print-all-reports state.
jobs = {}
# UP-30: protect `jobs` from concurrent read/write between request handlers
# and background worker threads. Mirrors the existing `_PRINT_JOBS_LOCK`
# pattern. We hold this only across the dict op itself — never around an
# AI call or DB commit — so contention stays sub-microsecond.
_jobs_lock = threading.Lock()
JOB_TTL_SECONDS = 3600  # 1 hour


def _jobs_get(job_id):
    """UP-30: lock-guarded read that returns a *shallow copy* of the job dict
    so callers can read fields without holding the lock. Returns None if no
    such job. Use this instead of `jobs.get(job_id)` everywhere."""
    with _jobs_lock:
        job = jobs.get(job_id)
        return dict(job) if job is not None else None


def _jobs_set(job_id, value):
    """UP-30: lock-guarded create/replace of a job entry."""
    with _jobs_lock:
        jobs[job_id] = value


def _jobs_update(job_id, **fields):
    """UP-30: lock-guarded in-place update of selected fields on an existing
    job entry. No-op if the entry was already removed (TTL cleanup race)."""
    with _jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return
        job.update(fields)


def _bulk_job_load(job_id):
    """UP-15: read a BulkJob and shape it like the legacy in-memory dict so
    the rest of the bulk routes can stay untouched. Returns None if no
    such job (so callers can fall back to the in-memory `jobs` dict for
    single-marking job ids)."""
    bj = BulkJob.query.get(job_id)
    if not bj:
        return None
    return {
        'status': bj.status,
        'subject': bj.subject or '',
        'progress': bj.get_progress(),
        'results': bj.get_results(),
        'skipped': bj.get_skipped(),
        'errors': bj.get_errors(),
        'assignment_id': bj.assignment_id,
        'bulk': True,
        'created_at': bj.started_at.timestamp() if bj.started_at else time.time(),
        '_persistent': True,
    }


def _bulk_job_create(job_id, *, assignment_id, subject, total, skipped):
    """UP-15: create a BulkJob row at the start of a bulk-mark request."""
    bj = BulkJob(
        id=job_id,
        kind='bulk_mark',
        status='processing',
        assignment_id=assignment_id,
        subject=subject,
    )
    bj.set_progress({'current': 0, 'total': total, 'current_name': 'Starting...'})
    bj.set_results([])
    bj.set_skipped(skipped or [])
    db.session.add(bj)
    db.session.commit()
    return bj


def _bulk_job_update(job_id, *, progress=None, results=None, status=None, error_message=None, finished=False, errors_append=None):
    """UP-15: in-place update for a BulkJob during the worker loop.

    Best-effort under a fresh app context — callers from worker threads
    must wrap this in `with app.app_context(): ...` themselves (the
    `run_bulk_marking_job` worker already opens its own context).
    """
    bj = BulkJob.query.get(job_id)
    if not bj:
        return None
    if progress is not None:
        bj.set_progress(progress)
    if results is not None:
        bj.set_results(results)
    if status is not None:
        bj.status = status
    if error_message is not None:
        bj.error_message = error_message
    if errors_append:
        bj.append_error(errors_append)
    if finished:
        bj.finished_at = datetime.now(timezone.utc)
    db.session.commit()
    return bj

# UP-16: bound AI-call fan-outs. Re-marking a 40-student class previously
# spawned 40 simultaneous outbound API calls — provider rate limits would
# kick in and the failing attempts would burn billable cost on retry.
# `max_workers=4` is the sweet spot for Anthropic/OpenAI default tiers and
# matches the gunicorn thread budget. Workers persist across requests;
# the daemon=False default is fine because we let them drain on shutdown.
_MARK_EXEC = ThreadPoolExecutor(max_workers=4, thread_name_prefix='mark')


def _submit_marking(fn, *args, **kwargs):
    """Submit a marking/AI worker through the bounded executor.

    Drop-in for `threading.Thread(target=fn, args=(...), daemon=True).start()`
    on AI-call paths. Boot warmups and short one-shots stay as raw threads.
    """
    return _MARK_EXEC.submit(fn, *args, **kwargs)


def cleanup_old_jobs():
    """Remove jobs older than TTL."""
    now = time.time()
    with _jobs_lock:
        expired = [
            jid for jid, j in jobs.items()
            if now - j.get('created_at', now) > JOB_TTL_SECONDS
        ]
        for jid in expired:
            jobs.pop(jid, None)


def _get_session_keys():
    """Get session-stored API keys (used when DEMO_MODE is FALSE or for bulk).

    UP-14: Flask cookies are signed but not encrypted. Anyone reading the
    cookie (browser extension, shared device) would see API keys in
    base64. We wrap the keys dict in Fernet ciphertext on save and unwrap
    here on read. Legacy dict-shaped sessions still work — they're
    transparently re-encrypted on next `save_keys`.
    """
    raw = session.get('api_keys')
    if not raw:
        return {}
    # Legacy: a bare dict was stored before UP-14.
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            from db import _get_fernet
            f = _get_fernet()
            if not f:
                return {}
            plaintext = f.decrypt(raw.encode('utf-8')).decode('utf-8')
            data = json.loads(plaintext)
            return data if isinstance(data, dict) else {}
        except Exception as _decrypt_err:
            # Tampered or stale ciphertext — drop and let user re-enter keys.
            # Log it so operators notice a key rotation (or attack).
            logger.warning('Session api_keys decrypt failed (stale/tampered): %s', _decrypt_err)
            return {}
    return {}


def _set_session_keys(keys):
    """UP-14: encrypt the api-keys dict with Fernet before stuffing it into
    `session['api_keys']`. If Fernet isn't available (FLASK_SECRET_KEY
    missing AND no DB-stored secret), fall back to plaintext so the app
    still works — surface a warning instead of breaking auth."""
    if not keys:
        session.pop('api_keys', None)
        return
    from db import _get_fernet
    f = _get_fernet()
    if not f:
        logger.warning('No Fernet key available — storing session api_keys in plaintext. Set FLASK_SECRET_KEY.')
        session['api_keys'] = keys
        return
    payload = json.dumps(keys, ensure_ascii=False).encode('utf-8')
    session['api_keys'] = f.encrypt(payload).decode('utf-8')


def _finalise_login_session(*, preserve=None):
    """UP-28: rotate the session ID on every successful login.

    Wipes any pre-auth session (e.g. the `pending_setup` flag captured before
    a teacher record existed) so a fixated cookie from the gate page can't
    inherit teacher privileges. Pass `preserve=('key', ...)` to keep specific
    values across the rotation — currently unused but available if a future
    auth path needs it. Always marks the new session permanent so
    `PERMANENT_SESSION_LIFETIME` (8h) actually kicks in; without `permanent`,
    Flask treats the cookie as session-scope and the lifetime is ignored.
    """
    preserve = preserve or ()
    kept = {k: session[k] for k in preserve if k in session}
    session.clear()
    for k, v in kept.items():
        session[k] = v
    session.permanent = True



def _is_authenticated():
    """Check if user is authenticated.

    UP-03: default-deny. Previously, when no ACCESS_CODE env var and no DB
    teacher_code were set, this returned True for every visitor — anyone with
    the Railway URL got owner-level access on first boot. The hub route
    redirects to the setup wizard before reaching auth checks, so first-run
    still works. Demo mode is intentionally open (no auth surface).
    """
    if is_demo_mode():
        return True  # Demo mode is a sandbox; no auth surface
    if is_dept_mode():
        return session.get('teacher_id') is not None
    # Teacher-based auth (TEACHER_CODE set explicitly, not just inherited from ACCESS_CODE)
    tc = get_teacher_code()
    if tc and tc != _ENV_ACCESS_CODE:
        return session.get('teacher_id') is not None
    # Also accept teacher_id session (wizard-created teachers)
    if session.get('teacher_id'):
        return True
    # Legacy ACCESS_CODE path (only honored when explicitly set)
    if _ENV_ACCESS_CODE:
        return session.get('authenticated', False)
    return False


def _current_teacher():
    """Get the currently logged-in teacher. Returns None if not logged in.

    Per-request memoised via `g._current_teacher` — the inject_dept_context
    context processor plus most route handlers each call this 1–3 times
    per render; without memoisation that's the same SQL roundtrip 3–6×.
    """
    teacher_id = session.get('teacher_id')
    if not teacher_id:
        return None
    try:
        cached = getattr(g, '_current_teacher_obj', None)
        cached_id = getattr(g, '_current_teacher_id', None)
        if cached is not None and cached_id == teacher_id:
            return cached
    except RuntimeError:
        cached = None
    obj = Teacher.query.get(teacher_id)
    try:
        g._current_teacher_obj = obj
        g._current_teacher_id = teacher_id
    except RuntimeError:
        pass
    return obj


# ---------------------------------------------------------------------------
# Role hierarchy constants
# ---------------------------------------------------------------------------
ROLE_HIERARCHY = {'hod': 5, 'subject_head': 4, 'lead': 3, 'manager': 2, 'teacher': 1, 'owner': 5}
ROLES_CAN_MANAGE = {'hod', 'subject_head', 'manager'}
ROLES_CAN_VIEW_INSIGHTS = {'hod', 'subject_head', 'lead', 'owner', 'manager'}
# Department insights are HOD-only — subject heads and leads were dropped
# (the cohort-wide picture is the HOD's planning surface, not a pedagogical
# workspace for subject seniors); managers were never in scope (admin role).
# SH/Lead still get My Class insights for their own roster. `owner` is the
# normal-mode single teacher and is kept so the link works in normal mode.
ROLES_CAN_VIEW_DEPT_INSIGHTS = {'hod', 'owner'}
# Senior pedagogical roles that may address ANY class via the My Class
# insights API (the dropdown still only lists their own classes; this is
# the API-level capability for direct calls). Distinct from the dept
# insights gate above so narrowing one doesn't silently narrow the other.
ROLES_CAN_ADDRESS_ANY_CLASS = {'hod', 'subject_head', 'lead', 'owner'}
# Overview lives inside Insights as its own tab; visibility matches the
# existing management gate (HOD / Subject Head / Manager). Lead/Owner still
# see Department + My Class but no Overview tab.
ROLES_CAN_VIEW_OVERVIEW = {'hod', 'subject_head', 'manager'}
ALL_DEPT_ROLES = ['teacher', 'lead', 'manager', 'subject_head', 'hod']


def _is_hod():
    """Check if current user is HOD."""
    teacher = _current_teacher()
    return teacher and teacher.role == 'hod'


def _can_manage_accounts():
    """Check if current user can manage teacher accounts."""
    teacher = _current_teacher()
    return teacher and teacher.role in ROLES_CAN_MANAGE


def _can_view_insights():
    """Check if current user can view department insights."""
    teacher = _current_teacher()
    return teacher and teacher.role in ROLES_CAN_VIEW_INSIGHTS


def _visible_teachers(viewer):
    """Return query of teachers visible to the viewer based on their role."""
    if not viewer:
        return Teacher.query.filter(False)
    if viewer.role == 'hod':
        return Teacher.query  # sees all
    elif viewer.role == 'subject_head':
        return Teacher.query.filter(Teacher.role != 'hod')  # sees all except HOD
    elif viewer.role == 'manager':
        # Managers see everyone so they can help with password resets across
        # the department. Destructive actions on senior staff are still
        # blocked by `_can_edit_target`; only reset-code is allowed, via
        # `_can_reset_code`.
        return Teacher.query
    return Teacher.query.filter(False)  # teachers/leads see nothing


def _can_edit_target(viewer, target):
    """Check if viewer can edit/delete/revoke the target teacher."""
    if not viewer or not target:
        return False
    if viewer.id == target.id:
        return True  # can always edit self
    v_rank = ROLE_HIERARCHY.get(viewer.role, 0)
    t_rank = ROLE_HIERARCHY.get(target.role, 0)
    if viewer.role == 'hod':
        return True
    if viewer.role == 'subject_head':
        return target.role != 'hod'
    if viewer.role == 'manager':
        # Managers can edit names/roles of everyone except HODs. They cannot
        # demote or remove HODs. The HOD-role assignment guard below
        # (`dept_update_teacher`) prevents managers from creating new HODs.
        return target.role != 'hod'
    return False


def _can_reset_code(viewer, target):
    """Check if viewer can reset (rotate) the target teacher's access code.

    Wider than `_can_edit_target`: managers are allowed to reset codes for
    senior staff (HOD, subject_head, lead) as a password-help capability,
    even though they cannot rename, demote, revoke, or delete those accounts.
    """
    if not viewer or not target:
        return False
    if viewer.id == target.id:
        return True
    if viewer.role == 'hod':
        return True
    if viewer.role == 'subject_head':
        return target.role != 'hod'
    if viewer.role == 'manager':
        return True  # managers can help anyone reset their code
    return False



def _check_assignment_ownership(asn):
    """Authorise the current teacher for this assignment.

    Access is granted when the teacher is:
      - a senior role (hod / subject_head / lead),
      - the original creator (asn.teacher_id), OR
      - on the TeacherClass roster for the assignment's class
        (co-teaching: all teachers assigned to a class share full
        view / edit / mark / delete rights over every assignment in
        that class, regardless of who created it).
    """
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    teacher = _current_teacher()
    if not teacher:
        return None  # Non-dept mode, auth already checked
    if teacher.role in ('hod', 'subject_head', 'lead'):
        return None  # Senior roles can access all
    if asn.teacher_id == teacher.id:
        return None  # Original creator
    if asn.class_id:
        tc = TeacherClass.query.filter_by(
            teacher_id=teacher.id, class_id=asn.class_id).first()
        if tc:
            return None  # Co-teacher on the same class roster
    return jsonify({'success': False, 'error': 'Access denied'}), 403


def _parse_max_drafts(raw):
    """Clamp max_drafts input to [2, 10], default 3."""
    try:
        n = int(raw) if raw else 3
    except (TypeError, ValueError):
        n = 3
    return max(2, min(10, n))


def _compress_pdf(data, target_bytes, dpi_options=(150, 100, 72)):
    """Re-render a PDF at lower DPI until it fits under target_bytes.

    Uses pdf2image + Pillow. Returns compressed bytes if a smaller version is
    produced, otherwise the original data. Never raises — on failure, returns
    the original data and the caller can apply its size check normally.
    """
    if len(data) <= target_bytes:
        return data
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        return data
    best = data
    for dpi in dpi_options:
        try:
            images = convert_from_bytes(data, dpi=dpi)
            if not images:
                continue
            images = [im.convert('RGB') for im in images]
            buf = io.BytesIO()
            images[0].save(
                buf,
                format='PDF',
                save_all=True,
                append_images=images[1:],
                resolution=dpi,
            )
            compressed = buf.getvalue()
            if len(compressed) < len(best):
                best = compressed
                logger.info(
                    f'PDF compressed at {dpi}dpi: {len(data)} -> {len(compressed)} bytes'
                )
            if len(best) <= target_bytes:
                return best
        except Exception as e:
            logger.warning(f'PDF compression at {dpi}dpi failed: {e}')
    return best


def _get_final_submission(student_id, assignment_id):
    """Return the final Submission for a (student, assignment) or None."""
    return Submission.query.filter_by(
        student_id=student_id,
        assignment_id=assignment_id,
        is_final=True,
    ).first()


def _count_drafts(student_id, assignment_id):
    """Return total draft count for a (student, assignment)."""
    return Submission.query.filter_by(
        student_id=student_id,
        assignment_id=assignment_id,
    ).count()


def _next_draft_number(student_id, assignment_id):
    """Return 1 + max existing draft_number, or 1 if none exist."""
    from sqlalchemy import func
    max_n = db.session.query(func.max(Submission.draft_number)).filter_by(
        student_id=student_id,
        assignment_id=assignment_id,
    ).scalar()
    return (max_n or 0) + 1


def _prepare_new_submission(student, assignment):
    """Handle the write-path decision for a new submission.

    Returns (new_sub_unsaved, error_message).
    - If assignment.allow_drafts is False: deletes the existing final (legacy behavior),
      returns a fresh Submission with draft_number = next, is_final = True.
    - If assignment.allow_drafts is True: enforces the cap. If at cap, returns (None, msg).
      Otherwise flips all prior drafts to is_final=False and returns a fresh Submission
      with draft_number = next, is_final = True.

    Caller is responsible for db.session.add(new_sub) and db.session.commit().

    UP-48: the count/check/flip/insert sequence is **not** atomic on its own.
    Two simultaneous `/student_upload` calls for the same student previously
    both passed the cap check, both flipped any prior `is_final=True`, and
    both inserted a new `is_final=True` row — producing either two finals
    or `cap + 1` drafts. We serialise per-student by taking a row-level
    `SELECT ... FOR UPDATE` on the Student row: any concurrent caller for
    the same student blocks here until the first one commits, after which
    its count read picks up the new state. On SQLite (dev) `with_for_update`
    silently no-ops but SQLite's whole-DB write lock provides the same
    guarantee. On PostgreSQL (prod) this is a real per-row lock.
    """
    # Lock first — everything below this point runs serially per student.
    db.session.query(Student).filter_by(id=student.id).with_for_update().first()

    if not assignment.allow_drafts:
        existing = _get_final_submission(student.id, assignment.id)
        if existing:
            db.session.delete(existing)
            db.session.flush()
        new_sub = Submission(
            student_id=student.id,
            assignment_id=assignment.id,
            draft_number=_next_draft_number(student.id, assignment.id),
            is_final=True,
        )
        return new_sub, None

    # Drafts-enabled path
    count = _count_drafts(student.id, assignment.id)
    cap = assignment.max_drafts or 3
    if count >= cap:
        return None, f'Draft limit reached ({count}/{cap}). Delete an older draft to free a slot.'

    # Flip all prior drafts (there may be 0) to is_final=False
    Submission.query.filter_by(
        student_id=student.id,
        assignment_id=assignment.id,
        is_final=True,
    ).update({'is_final': False})
    db.session.flush()

    new_sub = Submission(
        student_id=student.id,
        assignment_id=assignment.id,
        draft_number=_next_draft_number(student.id, assignment.id),
        is_final=True,
    )
    return new_sub, None


def _require_hod():
    """UP-01: STRICT gate — only role == 'hod'. Use for actions that have
    org-wide blast radius (API keys, code rotation, data purge). Previously
    this was loose (any management role); a `manager` could overwrite the
    org's Anthropic/OpenAI/Qwen keys → instant billing hijack.
    """
    if not is_dept_mode() or not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    teacher = _current_teacher()
    if not teacher or teacher.role != 'hod':
        return jsonify({'success': False, 'error': 'HOD access required'}), 403
    return None


def _require_management():
    """UP-01: LOOSE gate — any management role (hod, subject_head, manager).
    Use for class/teacher CRUD where any management role legitimately needs
    access. Equivalent to the historical `_require_hod` behaviour.
    """
    if not is_dept_mode() or not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    if not _can_manage_accounts():
        return jsonify({'success': False, 'error': 'Management access required'}), 403
    return None


def _require_insights_access():
    """Return error response if not allowed on Department insights, or None.

    Gated by ROLES_CAN_VIEW_DEPT_INSIGHTS — managers are excluded; this is
    the pedagogical surface, not the admin one.
    """
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    teacher = _current_teacher()
    if not teacher:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    if teacher.role in ROLES_CAN_VIEW_DEPT_INSIGHTS:
        return None
    return jsonify({'success': False, 'error': 'Access denied'}), 403


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


def _build_calibration_block_for(asn, sub=None):
    """Assemble the marking-prompt calibration block from active
    amend_answer_key FeedbackEdits on this assignment. Returns '' if
    there are no amendments. Never raises — exceptions are swallowed
    and a warning logged; marking is never blocked.
    """
    try:
        from subject_standards import build_effective_answer_key
        merged = build_effective_answer_key(asn, '')
        marker = '── Teacher clarifications'
        if marker in merged:
            idx = merged.find(marker)
            amendments_text = merged[idx:].rstrip()
            if amendments_text:
                logger.info(
                    f"Calibration block resolved for asn={getattr(asn,'id',None)}: "
                    f"{len(amendments_text)} chars"
                )
                return amendments_text
        return ''
    except Exception as e:
        logger.warning(f"_build_calibration_block_for failed: {e}")
        return ''


@app.template_filter('safe_json_loads')
def _safe_json_loads(s, default=None):
    """Jinja filter: parse a JSON string safely.
    Returns `default` (or [] if default is None) on any parse error.
    Use as `{{ value | safe_json_loads(default=[]) }}`.
    """
    try:
        import json as _json
        return _json.loads(s) if s else (default if default is not None else [])
    except (TypeError, ValueError):
        return default if default is not None else []


def _resolve_api_keys(assignment) -> dict[str, str] | None:
    """Resolve API keys: assignment-stored → department config → env vars (None).

    UP-36: explicit return type. `None` means "fall back to env vars" — the
    AI client builder reads `os.environ` when no session keys are passed.
    A non-empty dict maps provider key (`'anthropic'`/`'openai'`/`'qwen'`)
    to the API key string.
    """
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
                    assign_type, scoring_mode, total_marks, session_keys,
                    pinyin_mode='off'):
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
            pinyin_mode=pinyin_mode,
        )
        # UP-35: `mark_script` now raises on failure; a returned dict is success.
        _jobs_update(job_id, result=result, status='done')
    except MarkingError as e:
        # UP-35: typed marking failure — already logged in mark_script.
        _jobs_update(job_id, result={'error': str(e)}, status='error')
    except Exception as e:
        logger.exception("Job %s failed", job_id)
        _jobs_update(job_id, result={'error': str(e)}, status='error')


@app.route('/')
def hub():
    if not _is_setup_complete():
        return redirect(url_for('setup_wizard'))

    _demo = is_demo_mode()
    _dept = is_dept_mode()

    if _demo and _dept:
        # After logout, show the gate instead of auto-logging back in
        if request.args.get('logged_out'):
            return render_template('hub.html',
                                   authenticated=False,
                                   dept_mode=True,
                                   demo_mode=True,
                                   teacher=None)
        # Auto-login as demo HOD if not already logged in
        if not session.get('teacher_id'):
            hod = Teacher.query.filter_by(role='hod').first()
            if hod:
                _finalise_login_session()
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
    return redirect(url_for('hub', logged_out=1))


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
                           dept_mode=_dept,
                           providers={},
                           all_providers={})


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
                               assignments=[],
                               submission_counts={},
                               student_counts={},
                               preloaded_classes=[])
    authenticated = _is_authenticated()
    sk = _get_session_keys()
    providers = get_available_providers(session_keys=sk)
    assignments = []
    teacher = None
    # See perf.py + CLAUDE.md ("Page-load performance"). Listing pages
    # must go through light_assignment_query so the multi-MB blob /
    # heavy-text columns stay on the server.
    from perf import (light_assignment_query, submission_counts as _perf_sub_counts,
                      student_counts_for_assignments as _perf_student_counts,
                      student_counts_for_classes as _perf_class_counts)
    if authenticated:
        if _dept:
            teacher = _current_teacher()
            if teacher and teacher.role in ('hod', 'subject_head', 'lead'):
                assignments = light_assignment_query().order_by(Assignment.created_at.desc()).all()
            elif teacher:
                # Co-teaching: a teacher sees every assignment for any class
                # they're on the TeacherClass roster for, not just the ones
                # they created. Falls back to the assignments they personally
                # authored (covers legacy rows with no class_id).
                class_ids = [tc.class_id for tc in TeacherClass.query
                             .filter_by(teacher_id=teacher.id).all()]
                q = light_assignment_query()
                if class_ids:
                    q = q.filter(db.or_(
                        Assignment.class_id.in_(class_ids),
                        Assignment.teacher_id == teacher.id,
                    ))
                else:
                    q = q.filter(Assignment.teacher_id == teacher.id)
                assignments = q.order_by(Assignment.created_at.desc()).all()
        else:
            assignments = light_assignment_query().order_by(Assignment.created_at.desc()).all()
    # Resolve author names for the "Created by …" label so co-teachers can
    # see at a glance which assignments their teammates added.
    teacher_name_by_id = {}
    author_ids = {a.teacher_id for a in assignments if a.teacher_id}
    if author_ids:
        teacher_name_by_id = {t.id: t.name for t in
                              Teacher.query.filter(Teacher.id.in_(author_ids)).all()}

    # Counts come from perf.py — one GROUP BY per axis, no Jinja
    # `| length` lazy-loads.
    submission_counts = _perf_sub_counts([a.id for a in assignments])
    student_counts = _perf_student_counts(assignments)

    # Preload the class dropdown payload so the form is interactive on
    # first paint — saves a /api/classes round-trip after page load.
    preloaded_classes = []
    if authenticated:
        if _dept and teacher and teacher.role not in ('hod', 'subject_head', 'lead'):
            classes_list = list(teacher.classes) if teacher else []
        else:
            classes_list = Class.query.order_by(Class.name).all()
        if classes_list:
            cls_student_counts = _perf_class_counts([c.id for c in classes_list])
            preloaded_classes = [{
                'id': c.id,
                'name': c.name,
                'level': c.level,
                'student_count': cls_student_counts.get(c.id, 0),
            } for c in classes_list]

    return render_template('class.html',
                           authenticated=authenticated,
                           providers=providers,
                           demo_mode=_demo,
                           dept_mode=_dept,
                           teacher=teacher,
                           all_providers=PROVIDERS,
                           assignments=assignments,
                           teacher_name_by_id=teacher_name_by_id,
                           submission_counts=submission_counts,
                           student_counts=student_counts,
                           preloaded_classes=preloaded_classes)


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
        _finalise_login_session()
        session['teacher_id'] = teacher.id
        session['teacher_role'] = teacher.role
        session['teacher_name'] = teacher.name
        redirect_url = '/department' if teacher.role in ROLES_CAN_MANAGE else '/dashboard'
        return jsonify({'success': True, 'redirect': redirect_url})

    # Normal mode with teacher code
    _tc = get_teacher_code()
    if _tc:
        if secrets.compare_digest(str(code), str(_tc)):
            # Master key — find the owner teacher
            teacher = Teacher.query.filter_by(role='owner').first()
            if not teacher:
                _finalise_login_session()
                session['pending_setup'] = True
                return jsonify({'success': True, 'redirect': '/setup'})
            _finalise_login_session()
            session['teacher_id'] = teacher.id
            session['teacher_name'] = teacher.name
            return jsonify({'success': True, 'redirect': '/'})
        # Also check if they have a custom code
        teacher = Teacher.query.filter_by(code=code, role='owner').first()
        if teacher:
            _finalise_login_session()
            session['teacher_id'] = teacher.id
            session['teacher_name'] = teacher.name
            return jsonify({'success': True, 'redirect': '/'})
        return jsonify({'success': False, 'error': 'Invalid code'}), 401

    # Legacy ACCESS_CODE fallback
    if _ENV_ACCESS_CODE and secrets.compare_digest(str(code), str(_ENV_ACCESS_CODE)):
        _finalise_login_session()
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
    _set_session_keys(keys)
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
        if len(files) > 20:
            return jsonify({'success': False, 'error': f'Maximum 20 files per upload ({field})'}), 400

    provider = request.form.get('provider', 'anthropic')
    model = request.form.get('model', '')

    # Validate model is in demo allowed list
    if provider not in DEMO_MODELS:
        return jsonify({'success': False, 'error': 'Invalid provider for demo mode'}), 400
    if model not in DEMO_MODELS[provider]['models']:
        return jsonify({'success': False, 'error': 'Invalid model for demo mode'}), 400

    subject = request.form.get('subject', '')
    scoring_mode = request.form.get('scoring_mode', 'marks')
    total_marks = request.form.get('total_marks', '')
    review_instructions = request.form.get('review_instructions', '')
    marking_instructions = request.form.get('marking_instructions', '')

    question_paper_pages = [f.read() for f in request.files.getlist('question_paper') if f.filename]
    answer_key_pages = [f.read() for f in request.files.getlist('answer_key') if f.filename]
    script_pages = [f.read() for f in request.files.getlist('script') if f.filename]
    rubrics_pages = [f.read() for f in request.files.getlist('rubrics') if f.filename]
    reference_pages = [f.read() for f in request.files.getlist('reference') if f.filename]

    # UP-17: validate every file before passing to the AI provider — demo
    # mode is the only fully-open route, so a malicious renamed-PDF could
    # otherwise crash Pillow during PDF-to-image conversion.
    err = _validate_upload_blobs(
        question_paper_pages + answer_key_pages + script_pages + rubrics_pages + reference_pages,
        label='upload',
    )
    if err:
        return err

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
    _jobs_set(job_id, {
        'status': 'processing',
        'result': None,
        'subject': subject,
        'created_at': time.time(),
    })

    _submit_marking(
        run_marking_job, job_id, provider, model, question_paper_pages, answer_key_pages,
        script_pages, subject, rubrics_pages, reference_pages,
        review_instructions, marking_instructions,
        assign_type, scoring_mode, total_marks, demo_session_keys,
    )

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
    if len(script_files) > 20:
        return jsonify({'success': False, 'error': 'Maximum 20 files'}), 400

    script_pages = [f.read() for f in script_files if f.filename]
    err = _validate_upload_blobs(script_pages, label='script')
    if err:
        return err

    sub, err = _prepare_new_submission(student, asn)
    if err:
        return jsonify({'success': False, 'error': err}), 400
    sub.status = 'pending'
    sub.set_script_pages(script_pages)
    db.session.add(sub)
    db.session.commit()

    # Start marking in background using the assignment's stored files/settings
    _submit_marking(_run_submission_marking, app, sub.id, assignment_id)

    return jsonify({
        'success': True,
        'submission_id': sub.id,
        'assignment_id': assignment_id,
    })


@app.route('/status/<job_id>')
def job_status(job_id):
    if not is_demo_mode() and not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    # UP-15: try the persistent BulkJob table first; fall back to the
    # in-memory `jobs` dict for short-lived single-marking demo jobs.
    # UP-30: `_jobs_get` returns a shallow copy under lock so further reads
    # in this handler can't race with the marking worker writing back.
    job = _bulk_job_load(job_id) or _jobs_get(job_id)
    if not job:
        return jsonify({'success': False, 'error': 'Job not found'}), 404

    # UP-15 + IDOR-fix: a persistent BulkJob is scoped to one assignment, so
    # check the calling teacher actually owns that assignment before
    # spilling progress/results. In-memory single-marking jobs stay
    # auth-gated only (no assignment).
    if job.get('_persistent') and job.get('assignment_id'):
        asn = Assignment.query.get(job['assignment_id'])
        if asn is not None:
            err = _check_assignment_ownership(asn)
            if err:
                return err

    response = {'success': True, 'status': job['status']}
    if job.get('bulk') and 'skipped' in job:
        response['skipped'] = job.get('skipped', [])
    if job['status'] in ('done', 'error'):
        # Bulk jobs store results in 'results' (list), single in 'result' (dict)
        if job.get('bulk'):
            response['result'] = job.get('results', [])
        else:
            response['result'] = job['result']
        # UP-46: surface per-student errors so the class page can show
        # "N failed — retry these" with names + reasons (no need for the
        # teacher to scrape the server log).
        if job.get('errors'):
            response['errors'] = job['errors']
    if 'progress' in job:
        response['progress'] = job['progress']
    return jsonify(response)


@app.route('/download/<job_id>')
def download_pdf(job_id):
    if not is_demo_mode() and not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    job = _jobs_get(job_id)
    if not job or job['status'] != 'done':
        return jsonify({'success': False, 'error': 'No results available'}), 404

    pdf_bytes = generate_report_pdf(
        job['result'],
        subject=job.get('subject', ''),
        app_title=get_app_title(),
        assignment_name=job.get('assignment_name', '') or job.get('title', ''),
    )

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
    err = _require_management()
    if err:
        return redirect(url_for('hub'))

    teacher = _current_teacher()
    classes = Class.query.order_by(Class.name).all()
    teachers = Teacher.query.filter(Teacher.role != 'hod').order_by(Teacher.name).all()

    # Aggregate everything in three GROUP BY queries + one join — no
    # ORM row materialisation for Submissions or Assignments. The old
    # version hydrated every Submission with its blob/JSON columns
    # (result_json, script_bytes, etc., often MBs each) just to count
    # them; that alone could move hundreds of MB across the DB wire.
    from sqlalchemy import func as _func, case as _case

    class_ids = [c.id for c in classes]

    # Assignments: only need (id, class_id). Lightweight.
    asn_rows = (
        db.session.query(Assignment.id, Assignment.class_id)
        .filter(Assignment.class_id.in_(class_ids))
        .all()
    ) if class_ids else []
    asn_count_by_class = {}
    asn_ids = []
    asn_class_map = {}
    for asn_id, cid in asn_rows:
        asn_count_by_class[cid] = asn_count_by_class.get(cid, 0) + 1
        asn_ids.append(asn_id)
        asn_class_map[asn_id] = cid

    # Student counts per class — single GROUP BY (replaces N+1 loop).
    student_counts_by_class = {cid: 0 for cid in class_ids}
    if class_ids:
        for cid, n in (db.session.query(Student.class_id, _func.count(Student.id))
                       .filter(Student.class_id.in_(class_ids))
                       .group_by(Student.class_id).all()):
            student_counts_by_class[cid] = n

    # Submission counts per assignment — single GROUP BY for total + done.
    # Avoids hydrating blob/JSON columns on every row.
    total_by_asn = {}
    done_by_asn = {}
    if asn_ids:
        sub_rows = (
            db.session.query(
                Submission.assignment_id,
                _func.count(Submission.id),
                _func.sum(_case((Submission.status == 'done', 1), else_=0)),
            )
            .filter(Submission.assignment_id.in_(asn_ids))
            .group_by(Submission.assignment_id)
            .all()
        )
        for asn_id, total, done in sub_rows:
            total_by_asn[asn_id] = total or 0
            done_by_asn[asn_id] = int(done or 0)

    # Class -> teacher names via TeacherClass join (replaces N+1 lazy loads).
    teachers_by_class = {cid: [] for cid in class_ids}
    if class_ids:
        for cid, name in (db.session.query(TeacherClass.class_id, Teacher.name)
                          .join(Teacher, TeacherClass.teacher_id == Teacher.id)
                          .filter(TeacherClass.class_id.in_(class_ids))
                          .order_by(Teacher.name).all()):
            teachers_by_class.setdefault(cid, []).append(name)

    # Roll per-assignment counts up to per-class totals in memory.
    per_class_total_sub = {cid: 0 for cid in class_ids}
    per_class_done_sub = {cid: 0 for cid in class_ids}
    for asn_id, cid in asn_class_map.items():
        per_class_total_sub[cid] += total_by_asn.get(asn_id, 0)
        per_class_done_sub[cid] += done_by_asn.get(asn_id, 0)

    class_data = []
    for cls in classes:
        students_in_class = student_counts_by_class.get(cls.id, 0)
        asn_count = asn_count_by_class.get(cls.id, 0)
        total_students = students_in_class * asn_count
        total_submissions = per_class_total_sub.get(cls.id, 0)
        done_submissions = per_class_done_sub.get(cls.id, 0)

        class_data.append({
            'id': cls.id,
            'name': cls.name,
            'level': cls.level,
            'teachers': teachers_by_class.get(cls.id, []),
            'assignment_count': asn_count,
            'total_students': total_students,
            'total_submissions': total_submissions,
            'done_submissions': done_submissions,
            'completion_pct': round(done_submissions / total_students * 100) if total_students > 0 else 0,
        })

    total_assignments = len(asn_ids)
    total_subs = Submission.query.count()

    can_view_dept = teacher.role in ROLES_CAN_VIEW_DEPT_INSIGHTS

    return render_template('department.html',
                           teacher=teacher,
                           classes=class_data,
                           total_teachers=len(teachers),
                           total_classes=len(classes),
                           total_assignments=total_assignments,
                           total_submissions=total_subs,
                           can_view_dept=can_view_dept,
                           dept_mode=is_dept_mode(),
                           demo_mode=is_demo_mode())


# ---------------------------------------------------------------------------
# Class & Teacher Management
# ---------------------------------------------------------------------------

@app.route('/department/classes')
def department_manage():
    err = _require_management()
    if err:
        return redirect(url_for('hub'))

    teacher = _current_teacher()
    classes = Class.query.order_by(Class.name).all()
    # Filter teachers visible to this user based on their role
    teachers = _visible_teachers(teacher).order_by(Teacher.role.desc(), Teacher.name).all()
    # Per-row permission flags so the template can hide buttons a manager
    # is not allowed to use on senior staff. (Backend still enforces.)
    for t in teachers:
        t.can_edit = _can_edit_target(teacher, t)
        t.can_reset = _can_reset_code(teacher, t)
    # All teachers for class assignment dropdown (including HOD)
    assignable_teachers = Teacher.query.order_by(Teacher.name).all()

    # Option-C dept tagging: surface every active department and the
    # per-teacher current membership so the manage page can render
    # badges + a checkbox picker. Dept tags are the only mechanism
    # that puts a teacher into a department; assignment subjects are
    # never used for auto-tagging.
    all_departments = Department.query.filter_by(is_active=True) \
        .order_by(Department.sort_order, Department.name).all()
    td_rows = TeacherDepartment.query.filter(
        TeacherDepartment.teacher_id.in_([t.id for t in teachers])).all() if teachers else []
    dept_ids_by_teacher = {}
    for row in td_rows:
        dept_ids_by_teacher.setdefault(row.teacher_id, set()).add(row.department_id)
    dept_name_by_id = {d.id: (d.short_name or d.name) for d in all_departments}
    for t in teachers:
        ids = sorted(dept_ids_by_teacher.get(t.id, set()))
        t.dept_ids = ids
        t.dept_chips = [{'id': did, 'label': dept_name_by_id.get(did, '?')} for did in ids]

    # Get masked API key status for display
    from db import _get_fernet
    api_keys_masked = {}
    for prov in ('anthropic', 'openai', 'qwen'):
        cfg = DepartmentConfig.query.filter_by(key=f'api_key_{prov}').first()
        if cfg and cfg.value:
            f = _get_fernet()
            try:
                raw = f.decrypt(cfg.value.encode()).decode() if f else cfg.value
                # Mask: show first 6 and last 4 chars
                if len(raw) > 12:
                    api_keys_masked[prov] = raw[:6] + '***' + raw[-4:]
                else:
                    api_keys_masked[prov] = raw[:3] + '***'
            except Exception:
                api_keys_masked[prov] = '***configured***'
        else:
            from ai_marking import PROVIDER_KEY_MAP
            env_val = os.getenv(PROVIDER_KEY_MAP.get(prov, ''), '')
            if env_val:
                api_keys_masked[prov] = env_val[:6] + '***' + env_val[-4:] if len(env_val) > 12 else '***env***'

    return render_template('department_manage.html',
                           teacher=teacher,
                           classes=classes,
                           teachers=teachers,
                           assignable_teachers=assignable_teachers,
                           all_dept_roles=ALL_DEPT_ROLES,
                           all_departments=all_departments,
                           api_keys_masked=api_keys_masked,
                           dept_mode=is_dept_mode(),
                           demo_mode=is_demo_mode())


def _load_term_schedule_for_settings():
    """Return (rows, source) for the Manage Department term-schedule UI.

    rows is always a list of four {num, start, end} dicts with ISO date
    strings, pre-populated from (in priority order): the HOD override,
    MOE's hardcoded schedule for today's year, or empty strings.
    source is one of 'override', 'moe', 'unset' so the template can
    display where the dates came from."""
    from datetime import datetime as _dt, timezone as _tz
    from moe_terms import MOE_TERMS

    today = _dt.now(_tz.utc).date()
    year = today.year

    override_cfg = DepartmentConfig.query.filter_by(key='term_schedule_override').first()
    if override_cfg and override_cfg.value:
        try:
            data = json.loads(override_cfg.value)
            year = int(data.get('year') or year)
            terms = data.get('terms') or []
            rows = []
            for i in range(1, 5):
                match = next((t for t in terms if int(t.get('num', 0)) == i), None)
                rows.append({
                    'num': i,
                    'start': (match or {}).get('start', ''),
                    'end': (match or {}).get('end', ''),
                })
            return rows, 'override'
        except (ValueError, KeyError, TypeError):
            pass  # Fall through to MOE defaults

    moe = MOE_TERMS.get(year) or MOE_TERMS.get(max(MOE_TERMS) if MOE_TERMS else None)
    if moe:
        moe_by_num = {num: (start, end) for num, start, end in moe}
        rows = []
        for i in range(1, 5):
            pair = moe_by_num.get(i)
            rows.append({
                'num': i,
                'start': pair[0].isoformat() if pair else '',
                'end': pair[1].isoformat() if pair else '',
            })
        return rows, 'moe'

    return [{'num': i, 'start': '', 'end': ''} for i in range(1, 5)], 'unset'


def _generate_teacher_code():
    """Generate a unique 8-char teacher code."""
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(secrets.choice(chars) for _ in range(8))
        if not Teacher.query.filter_by(code=code).first():
            return code


@app.route('/department/teacher/create', methods=['POST'])
def dept_create_teacher():
    err = _require_management()
    if err:
        return err

    data = request.get_json()
    name = (data.get('name') or '').strip()
    role = data.get('role', 'teacher')
    if not name:
        return jsonify({'success': False, 'error': 'Name is required'}), 400
    if role not in ALL_DEPT_ROLES:
        return jsonify({'success': False, 'error': 'Invalid role'}), 400
    # Check the creator has permission to create this role
    creator = _current_teacher()
    if not _can_edit_target(creator, type('', (), {'role': role, 'id': None})()):
        return jsonify({'success': False, 'error': 'Cannot create accounts with this role'}), 403

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


@app.route('/department/teacher/<teacher_id>/update', methods=['POST'])
def dept_update_teacher(teacher_id):
    err = _require_management()
    if err:
        return err
    t = Teacher.query.get_or_404(teacher_id)
    viewer = _current_teacher()
    if not _can_edit_target(viewer, t):
        return jsonify({'success': False, 'error': 'Cannot edit this account'}), 403
    data = request.get_json()
    new_name = (data.get('name') or '').strip()
    new_role = (data.get('role') or '').strip()
    if new_name:
        t.name = new_name
    if new_role and new_role in ALL_DEPT_ROLES:
        # Non-HODs may assign any role except HOD itself, but cannot
        # promote themselves above their current rank.
        if viewer.role != 'hod':
            if new_role == 'hod':
                return jsonify({'success': False, 'error': 'Only HOD can assign HOD role'}), 403
            if viewer.id == t.id and ROLE_HIERARCHY.get(new_role, 0) > ROLE_HIERARCHY.get(viewer.role, 0):
                return jsonify({'success': False, 'error': 'Cannot promote yourself'}), 403
        t.role = new_role

    # Option-C: department membership is set explicitly here, only here.
    # `department_ids` is the full intended membership — we diff against
    # the existing rows so we can preserve is_lead flags for memberships
    # the caller is keeping. Missing key means "leave dept membership
    # alone" (older clients / role-only saves).
    if 'department_ids' in data:
        raw = data.get('department_ids') or []
        if not isinstance(raw, list):
            return jsonify({'success': False, 'error': 'department_ids must be a list'}), 400
        try:
            wanted = {int(x) for x in raw}
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'department_ids must be integers'}), 400
        if wanted:
            valid_ids = {d.id for d in Department.query.filter(
                Department.id.in_(wanted), Department.is_active.is_(True)).all()}
            unknown = wanted - valid_ids
            if unknown:
                return jsonify({'success': False,
                                'error': f'Unknown department ids: {sorted(unknown)}'}), 400
        existing_rows = TeacherDepartment.query.filter_by(teacher_id=t.id).all()
        existing_ids = {r.department_id for r in existing_rows}
        to_remove = existing_ids - wanted
        to_add = wanted - existing_ids
        for r in existing_rows:
            if r.department_id in to_remove:
                db.session.delete(r)
        for did in to_add:
            db.session.add(TeacherDepartment(
                teacher_id=t.id, department_id=did, is_lead=False))

    db.session.commit()

    # Re-read membership so the client can render the updated chips
    # without an extra round-trip.
    fresh_ids = sorted({r.department_id for r in
                        TeacherDepartment.query.filter_by(teacher_id=t.id).all()})
    return jsonify({'success': True, 'teacher': {
        'id': t.id, 'name': t.name, 'role': t.role,
        'department_ids': fresh_ids,
    }})


@app.route('/department/teacher/<teacher_id>/reset-code', methods=['POST'])
def dept_reset_code(teacher_id):
    err = _require_management()
    if err:
        return err

    t = Teacher.query.get_or_404(teacher_id)
    viewer = _current_teacher()
    if not _can_reset_code(viewer, t):
        return jsonify({'success': False, 'error': 'Cannot reset code for this account'}), 403
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
    err = _require_management()
    if err:
        return err
    t = Teacher.query.get_or_404(teacher_id)
    viewer = _current_teacher()
    if not _can_edit_target(viewer, t) or t.id == viewer.id:
        return jsonify({'success': False, 'error': 'Cannot revoke this account'}), 400
    t.is_active = not t.is_active  # Toggle active status
    db.session.commit()
    return jsonify({'success': True, 'is_active': t.is_active})


@app.route('/department/teacher/<teacher_id>/purge', methods=['POST'])
def dept_purge_teacher(teacher_id):
    # Purge is the sole removal path now (Delete was retired — its behaviour
    # is equivalent to Purge with keep_data=false on an account with no
    # data). Per-target authority is still gated by `_can_edit_target`, so
    # managers can only purge non-HOD accounts.
    err = _require_management()
    if err:
        return err
    t = Teacher.query.get_or_404(teacher_id)
    viewer = _current_teacher()
    if not _can_edit_target(viewer, t) or t.id == viewer.id:
        return jsonify({'success': False, 'error': 'Cannot purge this account'}), 400
    data = request.get_json() or {}
    keep_data = data.get('keep_data', False)

    if not keep_data:
        # Delete teacher's assignments and their submissions
        assignments = Assignment.query.filter_by(teacher_id=t.id).all()
        for asn in assignments:
            Submission.query.filter_by(assignment_id=asn.id).delete()
            db.session.delete(asn)
    else:
        # Keep data but null out teacher_id to avoid orphaned FK
        Assignment.query.filter_by(teacher_id=t.id).update({'teacher_id': None})

    TeacherClass.query.filter_by(teacher_id=t.id).delete()
    db.session.delete(t)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/department/class/create', methods=['POST'])
def dept_create_class():
    err = _require_management()
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
    err = _require_management()
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

    try:
        students_data = _parse_class_list(file_bytes, cl_file.filename)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception:
        logger.exception('Class list parse error')
        return jsonify({'success': False, 'error': 'Could not parse file. Please upload a CSV or Excel file.'}), 400
    if not students_data:
        return jsonify({'success': False, 'error': 'Could not parse class list. Check the file format.'}), 400
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


def _check_class_access(class_id):
    """Returns an error response if the current teacher can't access this class, else None."""
    teacher = _current_teacher()
    if not teacher:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    if teacher.role in ('hod', 'owner'):
        return None
    tc = TeacherClass.query.filter_by(teacher_id=teacher.id, class_id=class_id).first()
    if not tc:
        return jsonify({'success': False, 'error': 'Not assigned to this class'}), 403
    return None


@app.route('/class/<class_id>/students/<int:student_id>/edit', methods=['POST'])
def edit_class_student(class_id, student_id):
    """Edit a student's index_number and/or name."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    err = _check_class_access(class_id)
    if err:
        return err
    student = Student.query.get_or_404(student_id)
    if student.class_id != class_id:
        return jsonify({'success': False, 'error': 'Student not in this class'}), 400

    data = request.get_json() or {}
    new_index = (data.get('index') or '').strip()
    # UP-47: NFC + strip in one helper so an inline-edited name matches
    # the CSV-ingested form for the same person.
    new_name = normalize_name(data.get('name'))
    if not new_index or not new_name:
        return jsonify({'success': False, 'error': 'Index and name are required'}), 400
    if len(new_name) > 200 or len(new_index) > 50:
        return jsonify({'success': False, 'error': 'Name or index too long'}), 400

    # Prevent duplicate index within the same class (excluding self)
    dup = Student.query.filter_by(class_id=class_id, index_number=new_index).first()
    if dup and dup.id != student.id:
        return jsonify({'success': False, 'error': f'Another student already has index {new_index}'}), 400

    student.index_number = new_index
    student.name = new_name
    db.session.commit()
    return jsonify({'success': True, 'student': {'id': student.id, 'index': student.index_number, 'name': student.name}})


@app.route('/class/<class_id>/students/<int:student_id>/delete', methods=['POST'])
def delete_class_student(class_id, student_id):
    """Delete a student, blocked if they have any submissions."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    err = _check_class_access(class_id)
    if err:
        return err
    student = Student.query.get_or_404(student_id)
    if student.class_id != class_id:
        return jsonify({'success': False, 'error': 'Student not in this class'}), 400

    sub_count = Submission.query.filter_by(student_id=student.id).count()
    if sub_count > 0:
        return jsonify({
            'success': False,
            'error': f'Cannot delete: {student.name} has {sub_count} submission(s). Delete their submissions first.'
        }), 400

    db.session.delete(student)
    db.session.commit()
    return jsonify({'success': True})


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
        if teacher.role in ('hod', 'subject_head', 'lead'):
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
    err = _require_management()
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
    err = _require_management()
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


@app.route('/department/term-schedule', methods=['GET'])
def dept_get_term_schedule():
    """Return the current term schedule (override > MOE > unset) for
    the cog-menu modal."""
    err = _require_hod()
    if err:
        return err
    rows, source = _load_term_schedule_for_settings()
    return jsonify({'rows': rows, 'source': source})


@app.route('/department/term-schedule', methods=['POST'])
def dept_save_term_schedule():
    """Save the HOD's term schedule override. Body shape:
        {"year": 2026, "terms": [
            {"num": 1, "start": "2026-01-02", "end": "2026-03-13"}, ...
        ]}
    """
    err = _require_hod()
    if err:
        return err

    from datetime import date as _date

    data = request.get_json(silent=True) or {}
    try:
        year = int(data.get('year'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Invalid year'}), 400

    raw_terms = data.get('terms') or []
    cleaned = []
    for t in raw_terms:
        try:
            num = int(t.get('num'))
            start = _date.fromisoformat(t.get('start'))
            end = _date.fromisoformat(t.get('end'))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': f'Invalid dates for term {t.get("num")}'}), 400
        if start > end:
            return jsonify({'success': False, 'error': f'Term {num}: start must be on or before end'}), 400
        cleaned.append({'num': num, 'start': start.isoformat(), 'end': end.isoformat()})

    cleaned.sort(key=lambda x: x['start'])
    for i in range(len(cleaned) - 1):
        if cleaned[i]['end'] >= cleaned[i + 1]['start']:
            return jsonify({
                'success': False,
                'error': f'Term {cleaned[i]["num"]} ends on/after Term {cleaned[i + 1]["num"]} starts',
            }), 400

    cfg = DepartmentConfig.query.filter_by(key='term_schedule_override').first()
    payload = json.dumps({'year': year, 'terms': cleaned}, ensure_ascii=False)
    if cfg:
        cfg.value = payload
    else:
        db.session.add(DepartmentConfig(key='term_schedule_override', value=payload))
    db.session.commit()
    return jsonify({'success': True})


@app.route('/department/term-schedule/reset', methods=['POST'])
def dept_reset_term_schedule():
    """Delete the HOD override so MOE defaults apply again."""
    err = _require_hod()
    if err:
        return err
    cfg = DepartmentConfig.query.filter_by(key='term_schedule_override').first()
    if cfg:
        db.session.delete(cfg)
        db.session.commit()
    return jsonify({'success': True})


@app.route('/insights')
def insights_entrypoint():
    """Every authenticated user lands on My Class first. They can toggle to
    Department / Overview from the tab bar if their role grants access."""
    if not _is_authenticated():
        return redirect(url_for('hub'))
    teacher = _current_teacher()
    if not teacher:
        return redirect(url_for('hub'))
    return redirect(url_for('teacher_insights'))


@app.route('/teacher/insights')
def teacher_insights():
    """My Class insights: per-class, customisable widget grid."""
    if not _is_authenticated():
        return redirect(url_for('hub'))
    teacher = _current_teacher()
    if not teacher:
        return redirect(url_for('hub'))

    # "My Class" means classes the account actually teaches — every role,
    # including HOD/SH/Lead, only sees classes they're on the TeacherClass
    # roster for. Senior roles get the cohort-wide view from
    # /department/insights instead.
    classes = sorted(teacher.classes, key=lambda c: c.name or '')

    # Class selection: ?class_id= takes priority; otherwise pick the first
    # class the user has access to. None is a legit state (no classes yet).
    selected_class_id = request.args.get('class_id', '').strip() or None
    selected_class = None
    if selected_class_id:
        selected_class = next((c for c in classes if c.id == selected_class_id), None)
    if not selected_class and classes:
        selected_class = classes[0]

    can_view_dept = teacher.role in ROLES_CAN_VIEW_DEPT_INSIGHTS
    can_view_overview = teacher.role in ROLES_CAN_VIEW_OVERVIEW

    return render_template(
        'teacher_insights.html',
        teacher=teacher,
        classes=classes,
        selected_class=selected_class,
        can_view_dept=can_view_dept,
        can_view_overview=can_view_overview,
        demo_mode=is_demo_mode(),
        dept_mode=is_dept_mode(),
    )


def _check_class_access_for_teacher(class_id):
    """Authorise the current teacher to read/write dashboards for class_id.

    Pedagogical senior roles (HOD/SH/Lead/Owner) can address any class;
    managers and teachers must be on the class's TeacherClass roster.
    Returns (teacher, error)."""
    if not _is_authenticated():
        return None, (jsonify({'success': False, 'error': 'Not authenticated'}), 401)
    teacher = _current_teacher()
    if not teacher:
        return None, (jsonify({'success': False, 'error': 'Not authenticated'}), 401)
    if teacher.role in ROLES_CAN_ADDRESS_ANY_CLASS:
        return teacher, None
    tc = TeacherClass.query.filter_by(teacher_id=teacher.id, class_id=class_id).first()
    if not tc:
        return None, (jsonify({'success': False, 'error': 'Class not in your roster'}), 403)
    return teacher, None


# UP-40: `teacher_insights_layout_get` / `_put` moved to
# `routes/insights.py` (registered via `app.register_blueprint(insights_bp)`).


# ---------------------------------------------------------------------------
# My Class insights — per-widget data endpoints
# ---------------------------------------------------------------------------

# An assignment must have been live for at least this long before it counts
# toward the "missed submissions" tally. Otherwise a brand-new assignment
# would make every student look behind on day one.
MISSED_GRACE_DAYS = 7


def _missed_submissions_payload(class_id, selected_ids=None):
    """Compute the missed-submissions widget data for a class.

    Always surfaces up to 5 candidate assignments (newest aged past the
    grace window) so the widget can offer a 1-of-5 / 2-of-5 / 3-of-5
    picker. `selected_ids`, when provided, narrows the displayed subset
    to those candidate IDs (preserving newest-first order). Selection
    is clamped to 1-3; bad/unknown IDs are ignored. When `selected_ids`
    is None or empty after validation, the default is the newest 3.

    Returns a dict with: candidates (always, up to 5, for the picker),
    assignments (displayed subset), groups, all_caught_up,
    default_selection (IDs that make up the default view).
    """
    cls = Class.query.get(class_id)
    if not cls:
        return {'candidates': [], 'assignments': [], 'groups': [],
                'all_caught_up': True, 'default_selection': []}

    cutoff = datetime.now(timezone.utc) - timedelta(days=MISSED_GRACE_DAYS)
    # Pull up to 5 candidates (newest first) so the picker has something
    # to offer beyond the default 3.
    candidate_asns = (
        Assignment.query
        .filter(Assignment.class_id == class_id)
        .filter(Assignment.created_at <= cutoff)
        .order_by(Assignment.created_at.desc())
        .limit(5)
        .all()
    )
    if not candidate_asns:
        return {'candidates': [], 'assignments': [], 'groups': [],
                'all_caught_up': True, 'default_selection': []}

    candidate_ids = [a.id for a in candidate_asns]
    default_ids = candidate_ids[:3]

    if selected_ids:
        # Validate against the candidate set, preserve newest-first order,
        # cap at 3.
        valid = {sid for sid in selected_ids if sid in set(candidate_ids)}
        picked = [a for a in candidate_asns if a.id in valid][:3]
        asns = picked if picked else candidate_asns[:3]
    else:
        asns = candidate_asns[:3]

    # Iterate in display order (newest first). Each student gets a parallel
    # `dots` list of bools where True = missed that assignment.
    students = Student.query.filter_by(class_id=class_id).order_by(Student.name).all()
    asn_ids = [a.id for a in asns]

    # Pull every relevant submission in one query, indexed by (student_id, asn_id).
    # UP-10: skip the four large blob/JSON columns — this widget only needs
    # status + scores, not the underlying PDFs or extracted text.
    sub_rows = (
        Submission.query_no_blobs()
        .filter(Submission.assignment_id.in_(asn_ids))
        .filter(Submission.is_final.is_(True))
        .all()
    )
    sub_by_pair = {}
    for s in sub_rows:
        sub_by_pair[(s.student_id, s.assignment_id)] = s

    rows = []
    for st in students:
        dots = []
        for a in asns:
            # Late joiner — wasn't on the roster when the assignment was
            # issued. Don't count it against them.
            joined = st.created_at
            if joined is not None and joined.tzinfo is None:
                joined = joined.replace(tzinfo=timezone.utc)
            asn_when = a.created_at
            if asn_when is not None and asn_when.tzinfo is None:
                asn_when = asn_when.replace(tzinfo=timezone.utc)
            if joined is not None and asn_when is not None and joined > asn_when:
                dots.append(False)
                continue
            sub = sub_by_pair.get((st.id, a.id))
            # Missed = no submission, or any non-`done` status (errored
            # submissions are missed because the student needs to resubmit).
            missed = (sub is None) or (sub.status != 'done')
            dots.append(missed)
        missed_count = sum(1 for d in dots if d)
        if missed_count > 0:
            rows.append({'name': st.name, 'dots': dots, 'missed_count': missed_count})

    # Group descending by missed_count for the "Missed all 3 / 2 of 3 / 1 of 3" headers.
    rows.sort(key=lambda r: (-r['missed_count'], r['name']))
    groups = {}
    for r in rows:
        groups.setdefault(r['missed_count'], []).append({'name': r['name'], 'dots': r['dots']})

    n = len(asns)
    group_list = []
    for missed_count in sorted(groups.keys(), reverse=True):
        if missed_count == n:
            label = 'Missed all ' + str(n)
        else:
            label = 'Missed ' + str(missed_count) + ' of ' + str(n)
        group_list.append({
            'label': label,
            'missed_count': missed_count,
            'students': groups[missed_count],
        })

    return {
        'candidates': [
            {'id': a.id, 'title': a.title or a.subject or 'Untitled', 'classroom_code': a.classroom_code}
            for a in candidate_asns
        ],
        'assignments': [
            {'id': a.id, 'title': a.title or a.subject or 'Untitled', 'classroom_code': a.classroom_code}
            for a in asns
        ],
        'default_selection': default_ids,
        'groups': group_list,
        'all_caught_up': len(rows) == 0,
    }


@app.route('/teacher/insights/widget/missed-submissions')
def teacher_widget_missed_submissions():
    class_id = (request.args.get('class_id') or '').strip()
    if not class_id:
        return jsonify({'success': False, 'error': 'class_id required'}), 400
    teacher, err = _check_class_access_for_teacher(class_id)
    if err:
        return err
    raw = (request.args.get('selected_ids') or '').strip()
    selected_ids = [s for s in (raw.split(',') if raw else []) if s]
    payload = _missed_submissions_payload(class_id, selected_ids=selected_ids or None)
    payload['success'] = True
    return jsonify(payload)


def _submission_percent(sub):
    """Convert a `done` submission's result into a 0-100 percent.

    Returns None when the submission isn't markable (no questions, all
    marks_total zero, or it errored). Used by both the performance trend
    and other widgets that aggregate scores."""
    if not sub or sub.status != 'done':
        return None
    result = sub.get_result()
    if result.get('error'):
        return None
    questions = result.get('questions', [])
    if not questions:
        return None
    has_marks = any(q.get('marks_awarded') is not None for q in questions)
    if has_marks:
        total_a = sum((q.get('marks_awarded') or 0) for q in questions)
        total_p = sum((q.get('marks_total') or 0) for q in questions)
        if total_p <= 0:
            return None
        return round(total_a / total_p * 100, 1)
    # status-mode fallback: not used by performance widget (which excludes
    # status-mode assignments) but kept for callers that don't filter.
    correct = sum(1 for q in questions if q.get('status') == 'correct')
    return round(correct / len(questions) * 100, 1) if questions else None


def _percentile(sorted_values, q):
    """Linear-interpolation percentile (q in [0,100]) on a presorted list."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (q / 100.0) * (len(sorted_values) - 1)
    low_idx = int(rank)
    frac = rank - low_idx
    if low_idx + 1 >= len(sorted_values):
        return sorted_values[-1]
    return sorted_values[low_idx] + frac * (sorted_values[low_idx + 1] - sorted_values[low_idx])


@app.route('/teacher/insights/widget/performance-trend')
def teacher_widget_performance_trend():
    """Per-assignment class average + 25-75 percentile band, oldest-first.

    Status-mode assignments are excluded — their correct/partial/incorrect
    scores aren't comparable to numeric marks on a single y-axis."""
    class_id = (request.args.get('class_id') or '').strip()
    if not class_id:
        return jsonify({'success': False, 'error': 'class_id required'}), 400
    teacher, err = _check_class_access_for_teacher(class_id)
    if err:
        return err

    asns = (
        Assignment.query
        .filter(Assignment.class_id == class_id)
        .filter(Assignment.scoring_mode == 'marks')
        .order_by(Assignment.created_at.asc())
        .all()
    )
    if not asns:
        return jsonify({'success': True, 'points': []})

    asn_ids = [a.id for a in asns]
    # UP-10: defer blob columns — only stats are needed here.
    sub_rows = (
        Submission.query_no_blobs()
        .filter(Submission.assignment_id.in_(asn_ids))
        .filter(Submission.is_final.is_(True))
        .filter(Submission.status == 'done')
        .all()
    )
    subs_by_asn = {}
    for s in sub_rows:
        subs_by_asn.setdefault(s.assignment_id, []).append(s)

    points = []
    for a in asns:
        scores = []
        for s in subs_by_asn.get(a.id, []):
            pct = _submission_percent(s)
            if pct is not None:
                scores.append(pct)
        if not scores:
            # Skip assignments with no markable submissions — they'd only
            # confuse the chart with a hole.
            continue
        scores.sort()
        avg = round(sum(scores) / len(scores), 1)
        points.append({
            'asn_id': a.id,
            'title': a.title or a.subject or 'Untitled',
            'avg': avg,
            'p25': round(_percentile(scores, 25), 1),
            'p75': round(_percentile(scores, 75), 1),
            'n': len(scores),
        })

    return jsonify({'success': True, 'points': points})


# Stoplists for the consultation widget's bigram concept-stuck detection.
# These keep generic AI-feedback chatter ("show working", "be specific") out
# of the bigram count so what surfaces is actual subject-matter language.
_CONSULT_GENERIC_BIGRAMS = {
    'show working', 'be specific', 'include units', 'explain reasoning',
    'more detail', 'your answer', 'the question', 'make sure', 'you need',
    'good attempt', 'well done', 'next time', 'try to', 'remember to',
    'you should', 'see notes', 'review concept', 'go through', 'should be',
    'is not', 'does not', 'this is', 'this question', 'that you', 'in order',
    'at least', 'as well', 'be sure', 'in the', 'is the', 'of the',
    'for the', 'to the', 'on the', 'with the', 'and the',
}
_CONSULT_STOPWORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'if', 'in', 'on', 'at', 'to', 'of',
    'for', 'with', 'you', 'your', 'this', 'that', 'is', 'are', 'was', 'were',
    'be', 'been', 'being', 'has', 'have', 'had', 'do', 'does', 'did', 'will',
    'would', 'can', 'could', 'should', 'shall', 'may', 'might', 'must',
    'i', 'we', 'they', 'he', 'she', 'it', 'as', 'so', 'than', 'then',
    'when', 'where', 'how', 'why', 'what', 'which', 'who', 'whom', 'whose',
    'these', 'those', 'there', 'here', 'all', 'any', 'some', 'no', 'not',
    'too', 'very', 'just', 'about', 'over', 'under', 'into', 'through',
    'from', 'by', 'up', 'down', 'out', 'off', 'one', 'two', 'three',
    'also', 'because', 'before', 'after', 'between', 'during', 'while',
    'such', 'each', 'every', 'other', 'another', 'same',
}
_CONSULT_NOISE_TOKENS = {
    'student', 'students', 'answer', 'answers', 'work', 'working', 'attempt',
    'good', 'partial', 'incorrect', 'correct', 'point', 'points', 'mark',
    'marks', 'question', 'questions', 'response', 'next', 'time', 'review',
    'note', 'notes', 'detail', 'details', 'specific', 'general', 'show',
    'sure', 'try', 'remember', 'consider', 'need',
}


def _consult_bigrams(text):
    """Tokenise feedback text and emit subject-matter bigrams.

    Bigrams are after stopword removal, so "balance equations carefully"
    (input "make sure you balance equations carefully") yields "balance
    equations" — the actual concept handle. Generic-feedback phrases and
    bigrams whose either word is a noise token are filtered out."""
    if not text:
        return []
    text = text.lower()
    text = re.sub(r"[^a-z\s'-]", ' ', text)
    tokens = [t.strip("'-") for t in text.split() if t]
    tokens = [t for t in tokens if t and t not in _CONSULT_STOPWORDS]
    out = []
    for i in range(len(tokens) - 1):
        bg = tokens[i] + ' ' + tokens[i + 1]
        if bg in _CONSULT_GENERIC_BIGRAMS:
            continue
        if tokens[i] in _CONSULT_NOISE_TOKENS or tokens[i + 1] in _CONSULT_NOISE_TOKENS:
            continue
        out.append(bg)
    return out


def _consult_wrong_text(sub):
    """Concatenate improvement (or feedback) text from every wrong question
    on a single submission. Empty string when nothing wrong or no submission."""
    if not sub or sub.status != 'done':
        return ''
    pieces = []
    result = sub.get_result() or {}
    for q in result.get('questions') or []:
        if not _question_wrong(q):
            continue
        text = q.get('improvement') or q.get('feedback') or ''
        if text:
            pieces.append(text)
    return '\n'.join(pieces)


@app.route('/teacher/insights/widget/consultation')
def teacher_widget_consultation():
    """Top 5 students worth checking in with, ranked by severity.

    Triggers (any one fires):
      • avg < 50% across last 3 assignments
      • bottom 15% of class by avg
      • same bigram appears in 2+ of last 3 wrong-feedback texts
        (concept-stuck signal — surfaces a "stuck on X" hint)

    Severity ordering: lower avg = more severe; concept-stuck adds a small
    boost so two students at the same score show the diagnostically richer
    one first."""
    class_id = (request.args.get('class_id') or '').strip()
    if not class_id:
        return jsonify({'success': False, 'error': 'class_id required'}), 400
    teacher, err = _check_class_access_for_teacher(class_id)
    if err:
        return err

    asns = (
        Assignment.query
        .filter(Assignment.class_id == class_id)
        .order_by(Assignment.created_at.desc())
        .limit(3)
        .all()
    )
    if not asns:
        return jsonify({'success': True, 'students': []})

    students = Student.query.filter_by(class_id=class_id).all()
    asn_ids = [a.id for a in asns]
    # UP-10: defer blob columns — only stats are needed here.
    sub_rows = (
        Submission.query_no_blobs()
        .filter(Submission.assignment_id.in_(asn_ids))
        .filter(Submission.is_final.is_(True))
        .filter(Submission.status == 'done')
        .all()
    )
    sub_by_pair = {(s.student_id, s.assignment_id): s for s in sub_rows}

    # Per-student avg over whatever scored submissions exist in the window.
    student_avgs = {}
    for st in students:
        scores = []
        for a in asns:
            sub = sub_by_pair.get((st.id, a.id))
            pct = _submission_percent(sub) if sub else None
            if pct is not None:
                scores.append(pct)
        if scores:
            student_avgs[st.id] = sum(scores) / len(scores)

    if not student_avgs:
        return jsonify({'success': True, 'students': []})

    # Bottom 15% by avg — relative trigger.
    sorted_avgs = sorted(student_avgs.values())
    # cutoff_idx is the score AT which "bottom 15%" tops out
    cutoff_idx = max(0, int(len(sorted_avgs) * 0.15) - 1)
    bottom_15_threshold = sorted_avgs[cutoff_idx]

    candidates = []
    for st in students:
        avg = student_avgs.get(st.id)
        if avg is None:
            continue
        absolute_low = avg < 50
        relative_low = avg <= bottom_15_threshold

        per_asn_bigram_sets = []
        for a in asns:
            sub = sub_by_pair.get((st.id, a.id))
            if sub:
                per_asn_bigram_sets.append(set(_consult_bigrams(_consult_wrong_text(sub))))
        bigram_counts = {}
        for bgset in per_asn_bigram_sets:
            for bg in bgset:
                bigram_counts[bg] = bigram_counts.get(bg, 0) + 1
        repeated = [bg for bg, c in bigram_counts.items() if c >= 2]
        primary_bigram = max(repeated, key=len) if repeated else None
        concept_stuck = bool(primary_bigram)

        if not (absolute_low or relative_low or concept_stuck):
            continue

        if primary_bigram:
            line = ('Avg ' + str(int(round(avg))) + '% — repeatedly stuck on "'
                    + primary_bigram + '" (in 2+ of last 3 assignments). Worth a quick review.')
        else:
            line = ('Avg ' + str(int(round(avg))) + '% across last 3 — consistently low. Worth a check-in.')

        # Severity score: lower avg = more severe (so 100-avg). Concept-stuck
        # gets a small additive boost so it acts as a tiebreaker without
        # outweighing a genuinely lower score.
        severity = (100 - avg) + (5 if concept_stuck else 0)
        candidates.append({
            'name': st.name,
            'avg': round(avg, 1),
            'one_liner': line,
            '_severity': severity,
        })

    candidates.sort(key=lambda c: (-c['_severity'], c['name']))
    top5 = candidates[:5]
    for c in top5:
        c.pop('_severity', None)
    return jsonify({'success': True, 'students': top5})


@app.route('/teacher/insights/widget/encourage')
def teacher_widget_encourage():
    """Top 5 students to encourage, ranked by badge count + signal strength.

    Three badges, all heuristic, no AI:
      📈 Improving — last - first >= +10pp across last 4 assignments
                     (requires 4 scored submissions in that window)
      🏆 Consistent — avg >= 80% across last 3 AND no score < 70%
      ⚡ Quick      — submitted within 24h of release on 2 of last 3

    A student with multiple badges ranks above one with a single badge;
    within the same badge count we sort by signal strength (climb size,
    then average, then speed)."""
    class_id = (request.args.get('class_id') or '').strip()
    if not class_id:
        return jsonify({'success': False, 'error': 'class_id required'}), 400
    teacher, err = _check_class_access_for_teacher(class_id)
    if err:
        return err

    asns_desc = (
        Assignment.query
        .filter(Assignment.class_id == class_id)
        .order_by(Assignment.created_at.desc())
        .limit(4)
        .all()
    )
    if not asns_desc:
        return jsonify({'success': True, 'students': []})
    asns_4 = list(reversed(asns_desc))           # oldest first within last 4
    asns_3 = asns_4[-3:] if len(asns_4) >= 3 else asns_4

    students = Student.query.filter_by(class_id=class_id).all()
    asn_ids = [a.id for a in asns_4]
    # UP-10: defer blob columns — only stats are needed here.
    sub_rows = (
        Submission.query_no_blobs()
        .filter(Submission.assignment_id.in_(asn_ids))
        .filter(Submission.is_final.is_(True))
        .filter(Submission.status == 'done')
        .all()
    )
    sub_by_pair = {(s.student_id, s.assignment_id): s for s in sub_rows}

    def _hours_to_submit(sub, asn):
        if not sub or not sub.submitted_at or not asn.created_at:
            return None
        # UP-37: `utc()` is the centralised "ensure tz-aware" helper. The
        # `if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)`
        # pattern lived in ~9 places across this file — migrate the rest
        # over time.
        sa = utc(sub.submitted_at)
        aw = utc(asn.created_at)
        return max(0.0, (sa - aw).total_seconds() / 3600.0)

    evaluations = []
    asns_3_set = {a.id for a in asns_3}

    for st in students:
        scores_4, scores_3, hours_3 = [], [], []
        for a in asns_4:
            sub = sub_by_pair.get((st.id, a.id))
            pct = _submission_percent(sub) if sub else None
            scores_4.append(pct)
            if a.id in asns_3_set:
                scores_3.append(pct)
                hours_3.append(_hours_to_submit(sub, a))

        badges = []
        # Improving — needs all 4 scores present and a +10pp gain.
        if len(asns_4) >= 4 and all(s is not None for s in scores_4):
            climb = scores_4[-1] - scores_4[0]
            if climb >= 10:
                badges.append({'kind': 'improving', 'first': scores_4[0],
                               'last': scores_4[-1], 'climb': climb})

        # Consistent — needs all 3 scores present in last 3.
        if len(scores_3) >= 3 and all(s is not None for s in scores_3):
            avg3 = sum(scores_3) / len(scores_3)
            if avg3 >= 80 and all(s >= 70 for s in scores_3):
                badges.append({'kind': 'consistent', 'avg': avg3})

        # Quick — 24h on 2-of-3 in last 3.
        if len(hours_3) >= 3:
            quicks = [h for h in hours_3 if h is not None and h <= 24]
            if len(quicks) >= 2:
                badges.append({'kind': 'quick', 'count': len(quicks),
                               'avg_hrs': sum(quicks) / len(quicks)})

        if not badges:
            continue

        # One-line praise, prioritised improving > consistent > quick.
        kinds = {b['kind']: b for b in badges}
        if 'improving' in kinds:
            b = kinds['improving']
            line = ('Up from ' + str(int(round(b['first']))) + '% to '
                    + str(int(round(b['last']))) + '% over last 4 — recognise the climb.')
        elif 'consistent' in kinds:
            b = kinds['consistent']
            line = ('Avg ' + str(round(b['avg'], 1))
                    + '% across last 3 with low variance — solid work, worth a public shout-out.')
        else:
            b = kinds['quick']
            line = ('Submitted within ' + str(int(round(b['avg_hrs'])))
                    + ' hrs of release on recent assignments — appreciate the diligence.')

        # Sort key: more badges first; tiebreaker = primary badge's signal.
        if 'improving' in kinds:
            tie = -kinds['improving']['climb']
        elif 'consistent' in kinds:
            tie = -kinds['consistent']['avg']
        else:
            tie = kinds['quick']['avg_hrs']
        evaluations.append({
            'name': st.name,
            'badges': [b['kind'] for b in badges],
            'one_liner': line,
            '_sort': (-len(badges), tie, st.name),
        })

    evaluations.sort(key=lambda e: e['_sort'])
    top5 = evaluations[:5]
    for e in top5:
        e.pop('_sort', None)
    return jsonify({'success': True, 'students': top5})


def _question_wrong(q):
    """Per-question wrongness used by the weak-questions widget.

    For numeric marks: under half-marks counts as wrong. For status mode:
    anything other than 'correct' (so partial + incorrect) counts as wrong.
    Mixed-mode aggregation works because we ask the same question per
    submission, not across them."""
    awarded = q.get('marks_awarded')
    total = q.get('marks_total')
    if awarded is not None and total:
        try:
            return (float(awarded) / float(total)) < 0.5
        except (TypeError, ValueError, ZeroDivisionError):
            return q.get('status') != 'correct'
    return q.get('status') != 'correct'


def _norm_qpart(s):
    """Normalise a question_part / question_num to a comparable key.
    'Q6c(iii)' -> '6ciii', '6c iii' -> '6ciii', '  6 ' -> '6'."""
    if s is None:
        return ''
    t = re.sub(r'[^a-z0-9]', '', str(s).lower())
    if t.startswith('q'):
        t = t[1:]
    return t


def _first_qpart(s):
    """When the AI says a question_part spans multiple questions
    ('6c / 6d', '6c and 6d'), keep the first recognisable part for matching."""
    if s is None:
        return ''
    parts = re.split(r'[\/,]| and |\s+&\s+', str(s), flags=re.IGNORECASE)
    for p in parts:
        n = _norm_qpart(p)
        if n:
            return n
    return ''


def _compute_area_wrong_rates(assignment_id, areas):
    """For each area, compute the wrong-rate across this assignment's done
    submissions by matching the area's `question_part` against each submission's
    per-question `question_num` (same wrong-definition as the weak-questions
    widget). Returns a list aligned to `areas` of {'pct': int|None}.
    pct is None when no matching question was found in any submission."""
    norms = [_first_qpart((a or {}).get('question_part') or '') for a in (areas or [])]
    if not any(norms):
        return [{'pct': None} for _ in (areas or [])]
    # UP-10: only result_json is read in the loop below.
    subs = (
        Submission.query_no_blobs()
        .filter_by(assignment_id=assignment_id, status='done', is_final=True)
        .all()
    )
    counts = {n: {'total': 0, 'wrong': 0} for n in norms if n}
    for sub in subs:
        result = sub.get_result() or {}
        for i, q in enumerate(result.get('questions') or []):
            key = _norm_qpart(q.get('question_number', q.get('question_num', i + 1)))
            rec = counts.get(key)
            if rec is None:
                continue
            rec['total'] += 1
            if _question_wrong(q):
                rec['wrong'] += 1
    out = []
    for n in norms:
        rec = counts.get(n) if n else None
        if not rec or rec['total'] <= 0:
            out.append({'pct': None})
        else:
            out.append({'pct': int(round(rec['wrong'] * 100 / rec['total']))})
    return out


def _area_display_order(area_rates):
    """Sort indices: areas with a pct first (descending), then None at the end.
    Stable on original index."""
    indexed = list(enumerate(area_rates or []))
    indexed.sort(key=lambda t: (
        0 if t[1].get('pct') is not None else 1,
        -(t[1].get('pct') or 0),
        t[0],
    ))
    return [i for i, _ in indexed]


@app.route('/teacher/insights/widget/weak-questions')
def teacher_widget_weak_questions():
    """Worst questions across the latest 3 assignments, grouped by assignment.

    Each assignment is gated on >30% of the class roster having submitted —
    smaller samples are too noisy to act on. Within an assignment, the top
    3 questions by wrong-rate are returned, but only if their wrong-rate is
    >= 50%. An assignment with no qualifying questions is dropped."""
    class_id = (request.args.get('class_id') or '').strip()
    if not class_id:
        return jsonify({'success': False, 'error': 'class_id required'}), 400
    teacher, err = _check_class_access_for_teacher(class_id)
    if err:
        return err

    asns = (
        Assignment.query
        .filter(Assignment.class_id == class_id)
        .order_by(Assignment.created_at.desc())
        .limit(3)
        .all()
    )
    if not asns:
        return jsonify({'success': True, 'groups': []})

    roster_size = Student.query.filter_by(class_id=class_id).count()
    if roster_size == 0:
        return jsonify({'success': True, 'groups': []})

    asn_ids = [a.id for a in asns]
    # UP-10: defer blob columns — only stats are needed here.
    sub_rows = (
        Submission.query_no_blobs()
        .filter(Submission.assignment_id.in_(asn_ids))
        .filter(Submission.is_final.is_(True))
        .filter(Submission.status == 'done')
        .all()
    )
    subs_by_asn = {}
    for s in sub_rows:
        subs_by_asn.setdefault(s.assignment_id, []).append(s)

    groups = []
    for a in asns:
        subs = subs_by_asn.get(a.id, [])
        # >30% submission gate keeps low-sample assignments off the chart.
        if (len(subs) / roster_size) <= 0.30:
            continue
        q_stats = {}
        for sub in subs:
            result = sub.get_result() or {}
            for i, q in enumerate(result.get('questions') or []):
                qnum = str(q.get('question_number', q.get('question_num', i + 1)))
                rec = q_stats.setdefault(qnum, {'total': 0, 'wrong': 0})
                rec['total'] += 1
                if _question_wrong(q):
                    rec['wrong'] += 1
        ranked = []
        for qnum, rec in q_stats.items():
            if rec['total'] <= 0:
                continue
            rate = rec['wrong'] / rec['total']
            if rate >= 0.50:
                ranked.append({
                    'qnum': qnum,
                    'wrong_pct': int(round(rate * 100)),
                    'wrong_n': rec['wrong'],
                    'total_n': rec['total'],
                })
        ranked.sort(key=lambda r: (-r['wrong_pct'], r['qnum']))
        ranked = ranked[:3]
        if ranked:
            groups.append({
                'asn_id': a.id,
                'title': a.title or a.subject or 'Untitled',
                'questions': ranked,
            })

    return jsonify({'success': True, 'groups': groups})


@app.route('/teacher/insights/widget/submission-rate-trend')
def teacher_widget_submission_rate_trend():
    """On-time submission rate per assignment, oldest-first.

    Definition of submitted matches the missed-submissions widget: only
    submissions in `done` status count (errored / pending submissions
    don't, since the student needs to resubmit). Denominator is the
    class roster size at the time the assignment was created — late
    joiners aren't held against earlier assignments. The latest
    assignment is flagged when it's still inside the 7-day grace window
    so the chart can render it as in-progress."""
    class_id = (request.args.get('class_id') or '').strip()
    if not class_id:
        return jsonify({'success': False, 'error': 'class_id required'}), 400
    teacher, err = _check_class_access_for_teacher(class_id)
    if err:
        return err

    asns = (
        Assignment.query
        .filter(Assignment.class_id == class_id)
        .order_by(Assignment.created_at.asc())
        .all()
    )
    if not asns:
        return jsonify({'success': True, 'points': []})

    students = Student.query.filter_by(class_id=class_id).all()
    asn_ids = [a.id for a in asns]
    # UP-10: defer blob columns — only stats are needed here.
    sub_rows = (
        Submission.query_no_blobs()
        .filter(Submission.assignment_id.in_(asn_ids))
        .filter(Submission.is_final.is_(True))
        .filter(Submission.status == 'done')
        .all()
    )
    done_pairs = {(s.student_id, s.assignment_id) for s in sub_rows}

    now = datetime.now(timezone.utc)
    grace_cutoff = now - timedelta(days=MISSED_GRACE_DAYS)
    points = []
    for a in asns:
        asn_when = a.created_at
        if asn_when is not None and asn_when.tzinfo is None:
            asn_when = asn_when.replace(tzinfo=timezone.utc)
        # Roster at issue-time: students whose joined-date <= asn.created_at.
        eligible = []
        for st in students:
            joined = st.created_at
            if joined is not None and joined.tzinfo is None:
                joined = joined.replace(tzinfo=timezone.utc)
            if joined is None or asn_when is None or joined <= asn_when:
                eligible.append(st)
        if not eligible:
            continue
        done = sum(1 for st in eligible if (st.id, a.id) in done_pairs)
        rate = round(done / len(eligible) * 100, 1)
        in_progress = (asn_when is not None and asn_when > grace_cutoff)
        points.append({
            'asn_id': a.id,
            'title': a.title or a.subject or 'Untitled',
            'rate': rate,
            'done': done,
            'eligible': len(eligible),
            'in_progress': in_progress,
        })

    return jsonify({'success': True, 'points': points})


@app.route('/department/insights')
def department_insights():
    """Department insights — (dept × level)-tabbed widget dashboard.

    Layout JSON lives in DepartmentDashboardLayout (one row per viewer);
    the same layout renders across every (dept × level) tab — only the
    data swaps when the tab changes. See
    docs/superpowers/specs/2026-05-14-department-insights-widgets-design.md.
    """
    err = _require_insights_access()
    if err:
        return redirect(url_for('hub'))

    teacher = _current_teacher()
    classes = Class.query.order_by(Class.name).all()

    # AI provider list (for the AI Analysis widget's provider dropdown).
    from ai_marking import get_available_providers, PROVIDERS
    dept_keys = _get_dept_keys()
    ai_providers = get_available_providers(dept_keys) if dept_keys else get_available_providers()
    if not ai_providers:
        ai_providers = PROVIDERS

    # Level tab list — show all four standard tabs even if empty, plus
    # 'unlevelled' if any class falls outside the taxonomy, plus 'all'.
    from levels import levels_present, LEVEL_LABELS, ORDERED_LEVEL_KEYS
    present = levels_present(classes)
    visible_levels = [
        {'key': k, 'label': LEVEL_LABELS[k], 'has_data': k in present}
        for k in ORDERED_LEVEL_KEYS
    ]
    if 'unlevelled' in present:
        visible_levels.append({'key': 'unlevelled', 'label': 'Unlevelled',
                               'has_data': True})
    visible_levels.append({'key': 'all', 'label': 'All', 'has_data': True})

    # Dept tab list. An HOD only oversees the depts on their
    # TeacherDepartment roster, so the tab strip is scoped to those:
    #   * 1 dept  → no tab UI at all (page locks to that dept).
    #   * 2+ depts → tabs limited to their depts, plus "All" (aggregate
    #                across the depts they oversee).
    # Other senior roles (subject_head / lead / owner) keep the full
    # cross-dept view: every active dept plus "All". HOD with 0 dept tags
    # is treated like a global HOD and gets the full view too.
    all_departments = Department.query.filter_by(is_active=True) \
        .order_by(Department.sort_order, Department.name).all()
    hod_dept_ids = []
    if teacher.role == 'hod':
        hod_dept_ids = [td.department_id for td in
                        TeacherDepartment.query.filter_by(teacher_id=teacher.id).all()]

    forced_dept_id = None  # None = no forcing (JS keeps saved/All default).
    if teacher.role == 'hod' and len(hod_dept_ids) == 1:
        only = next((d for d in all_departments if d.id == hod_dept_ids[0]), None)
        if only is not None:
            visible_depts = [{'key': str(only.id),
                              'label': only.short_name or only.name,
                              'dept_id': only.id}]
            forced_dept_id = only.id
        else:
            # Tagged dept is inactive/missing — fall through to global view.
            visible_depts = [{'key': 'all', 'label': 'All', 'dept_id': None}] + [
                {'key': str(d.id), 'label': d.short_name or d.name, 'dept_id': d.id}
                for d in all_departments]
    elif teacher.role == 'hod' and len(hod_dept_ids) >= 2:
        owned = [d for d in all_departments if d.id in set(hod_dept_ids)]
        visible_depts = [{'key': 'all', 'label': 'All', 'dept_id': None}] + [
            {'key': str(d.id), 'label': d.short_name or d.name, 'dept_id': d.id}
            for d in owned]
    else:
        visible_depts = [{'key': 'all', 'label': 'All', 'dept_id': None}] + [
            {'key': str(d.id), 'label': d.short_name or d.name, 'dept_id': d.id}
            for d in all_departments]

    # Viewer's teaching subjects — feeds the Subject Head's goal modal.
    viewer_subjects = []
    if teacher and teacher.role in ('subject_head', 'hod', 'lead'):
        teaching_class_ids = {c.id for c in (teacher.classes or [])}
        if teaching_class_ids:
            asns = Assignment.query.filter(
                Assignment.class_id.in_(teaching_class_ids)
            ).all()
            seen = set()
            for a in asns:
                s = (a.subject or '').strip()
                if s and s.lower() not in seen:
                    seen.add(s.lower())
                    viewer_subjects.append(s)
            viewer_subjects.sort()

    # Canonical subjects for the goal-modal subject dropdown.
    try:
        from subjects import SUBJECTS
        canonical_subjects = [s['display'] for s in SUBJECTS]
    except Exception:
        canonical_subjects = []

    can_view_overview = teacher.role in ROLES_CAN_VIEW_OVERVIEW

    return render_template('department_insights.html',
                           teacher=teacher,
                           classes=classes,
                           visible_levels=visible_levels,
                           visible_depts=visible_depts,
                           forced_dept_id=forced_dept_id,
                           viewer_subjects=viewer_subjects,
                           canonical_subjects=canonical_subjects,
                           ai_providers=ai_providers,
                           can_view_overview=can_view_overview,
                           demo_mode=is_demo_mode(),
                           dept_mode=is_dept_mode())


@app.route('/department/insights/data')
def department_insights_data():
    """API endpoint returning analytics data for charts."""
    err = _require_insights_access()
    if err:
        return err
    if is_demo_mode() and not is_dept_mode():
        return jsonify({'success': False, 'error': 'Not available in demo mode'}), 403

    assignment_id = request.args.get('assignment_id')
    class_id = request.args.get('class_id')

    # UP-10: defer blob columns — analytics path only reads result_json.
    query = Submission.query_no_blobs().filter_by(status='done', is_final=True)
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
            total_a = sum((q.get('marks_awarded') or 0) for q in questions)
            total_p = sum((q.get('marks_total') or 0) for q in questions)
            pct = (total_a / total_p * 100) if total_p > 0 else 0
        else:
            correct = sum(1 for q in questions if q.get('status') == 'correct')
            pct = (correct / len(questions) * 100) if questions else 0

        class_scores.setdefault(cls_name, []).append(pct)

        for i, q in enumerate(questions):
            qnum = str(q.get('question_number', i + 1))
            question_stats.setdefault(qnum, {'correct': 0, 'total': 0})
            question_stats[qnum]['total'] += 1
            if q.get('status') == 'correct' or (has_marks and (q.get('marks_awarded') or 0) == (q.get('marks_total') or 1)):
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


@app.route('/department/insights/item-analysis')
def department_item_analysis():
    """Compare per-question performance across multiple assignments."""
    err = _require_insights_access()
    if err:
        return err

    ids = request.args.get('assignment_ids', '')
    assignment_ids = [x.strip() for x in ids.split(',') if x.strip()]
    if len(assignment_ids) < 2:
        return jsonify({'success': False, 'error': 'Select at least 2 assignments'}), 400

    assignments = Assignment.query.filter(Assignment.id.in_(assignment_ids)).all()
    if len(assignments) < 2:
        return jsonify({'success': False, 'error': 'Assignments not found'}), 404

    cls_ids = list(set(a.class_id for a in assignments if a.class_id))
    classes = {c.id: c for c in Class.query.filter(Class.id.in_(cls_ids)).all()} if cls_ids else {}

    result = []
    all_qnums = set()

    for asn in assignments:
        subs = Submission.query.filter_by(assignment_id=asn.id, status='done', is_final=True).all()
        q_stats = {}
        for sub in subs:
            questions = sub.get_result().get('questions', [])
            for i, q in enumerate(questions):
                qnum = str(q.get('question_number', i + 1))
                q_stats.setdefault(qnum, {'correct': 0, 'total': 0})
                q_stats[qnum]['total'] += 1
                has_marks = q.get('marks_awarded') is not None
                if has_marks:
                    if (q.get('marks_awarded') or 0) == (q.get('marks_total') or 1):
                        q_stats[qnum]['correct'] += 1
                elif q.get('status') == 'correct':
                    q_stats[qnum]['correct'] += 1
                all_qnums.add(qnum)

        cls = classes.get(asn.class_id)
        questions_pct = {}
        for qnum, stats in q_stats.items():
            questions_pct[qnum] = round(stats['correct'] / stats['total'] * 100, 1) if stats['total'] else 0

        result.append({
            'id': asn.id,
            'title': asn.title or asn.subject or 'Untitled',
            'class_name': cls.name if cls else 'Unknown',
            'questions': questions_pct,
        })

    def sort_key(q):
        try:
            return (0, int(q))
        except ValueError:
            return (1, q)
    sorted_qnums = sorted(all_qnums, key=sort_key)

    return jsonify({
        'success': True,
        'assignments': result,
        'question_numbers': sorted_qnums,
    })


@app.route('/department/insights/analysis')
def department_get_analysis():
    """Retrieve saved AI analysis for given filters."""
    err = _require_insights_access()
    if err:
        return err

    asn_id = request.args.get('assignment_id', 'all')
    cls_id = request.args.get('class_id', 'all')
    key = f'insight_analysis:{asn_id}:{cls_id}'

    cfg = DepartmentConfig.query.filter_by(key=key).first()
    if cfg and cfg.value:
        try:
            data = json.loads(cfg.value)
            return jsonify({'success': True, 'exists': True, **data})
        except Exception:
            pass
    return jsonify({'success': True, 'exists': False})


@app.route('/department/insights/analyze', methods=['POST'])
def department_analyze():
    """Generate AI analysis of insights data.

    LEGACY ROUTE — invoked by the deprecated class_insights.html deep-link.
    The current dashboard calls `/department/insights/analyze-level` below
    (hardened prompt, regex sweep, (dept × level)-keyed cache).
    """
    err = _require_insights_access()
    if err:
        return err

    data = request.get_json()
    provider = data.get('provider')
    model = data.get('model')
    asn_filter = data.get('assignment_id', '')
    cls_filter = data.get('class_id', '')
    item_analysis_data = data.get('item_analysis')

    if not provider:
        return jsonify({'success': False, 'error': 'No provider selected'}), 400

    # Resolve API keys: dept keys → wizard keys → env vars
    dept_keys = _get_dept_keys()
    if not dept_keys:
        # Check wizard-stored keys (normal mode)
        from db import _get_fernet
        for prov in ('anthropic', 'openai', 'qwen'):
            cfg = DepartmentConfig.query.filter_by(key=f'api_key_{prov}').first()
            if cfg and cfg.value:
                f = _get_fernet()
                if f:
                    try:
                        dept_keys[prov] = f.decrypt(cfg.value.encode()).decode()
                        continue
                    except Exception:
                        pass
                dept_keys[prov] = cfg.value
    from ai_marking import get_ai_client
    session_keys = dept_keys if dept_keys else None
    client, model_name, prov_type = get_ai_client(provider, model, session_keys)
    if not client:
        return jsonify({'success': False, 'error': f'No API key for {provider}'}), 400

    # Gather insights data. UP-10: defer blob columns.
    query = Submission.query_no_blobs().filter_by(status='done', is_final=True)
    if asn_filter:
        query = query.filter_by(assignment_id=asn_filter)

    submissions = query.all()
    asn_ids = list(set(s.assignment_id for s in submissions))
    all_asns = {a.id: a for a in Assignment.query.filter(Assignment.id.in_(asn_ids)).all()} if asn_ids else {}
    cls_ids_set = list(set(a.class_id for a in all_asns.values() if a.class_id))
    all_classes = {c.id: c for c in Class.query.filter(Class.id.in_(cls_ids_set)).all()} if cls_ids_set else {}

    class_scores = {}
    question_stats = {}
    student_scores = []

    for sub in submissions:
        asn = all_asns.get(sub.assignment_id)
        if not asn or not asn.class_id:
            continue
        if cls_filter and asn.class_id != cls_filter:
            continue

        result = sub.get_result()
        questions = result.get('questions', [])
        if not questions:
            continue

        cls = all_classes.get(asn.class_id)
        cls_name = cls.name if cls else 'Unknown'
        has_marks = any(q.get('marks_awarded') is not None for q in questions)

        if has_marks:
            total_a = sum((q.get('marks_awarded') or 0) for q in questions)
            total_p = sum((q.get('marks_total') or 0) for q in questions)
            pct = (total_a / total_p * 100) if total_p > 0 else 0
        else:
            correct = sum(1 for q in questions if q.get('status') == 'correct')
            pct = (correct / len(questions) * 100) if questions else 0

        class_scores.setdefault(cls_name, []).append(pct)
        student_scores.append({'class': cls_name, 'score': round(pct, 1)})

        for i, q in enumerate(questions):
            qnum = str(q.get('question_number', i + 1))
            question_stats.setdefault(qnum, {'correct': 0, 'total': 0})
            question_stats[qnum]['total'] += 1
            if q.get('status') == 'correct' or (has_marks and (q.get('marks_awarded') or 0) == (q.get('marks_total') or 1)):
                question_stats[qnum]['correct'] += 1

    if not student_scores:
        return jsonify({'success': False, 'error': 'No data to analyze'}), 400

    # Build prompt data
    class_avgs = {name: round(sum(scores) / len(scores), 1) for name, scores in class_scores.items()}
    q_difficulty = {qnum: round(stats['correct'] / stats['total'] * 100, 1) if stats['total'] else 0
                    for qnum, stats in sorted(question_stats.items(), key=lambda x: x[0])}

    all_scores_flat = [s['score'] for s in student_scores]
    overall_avg = round(sum(all_scores_flat) / len(all_scores_flat), 1)
    pass_rate = round(sum(1 for s in all_scores_flat if s >= 50) / len(all_scores_flat) * 100, 1)

    sorted_students = sorted(student_scores, key=lambda x: x['score'])
    bottom_5 = sorted_students[:5]
    hardest = sorted(q_difficulty.items(), key=lambda x: x[1])[:5]

    prompt_data = f"""Department Performance Data:
- Total students marked: {len(all_scores_flat)}
- Overall average: {overall_avg}%
- Pass rate (>=50%): {pass_rate}%

Class averages:
{chr(10).join(f'  - {name}: {avg}%' for name, avg in class_avgs.items())}

Question difficulty (% fully correct):
{chr(10).join(f'  - Q{qnum}: {pct}%' for qnum, pct in q_difficulty.items())}

Hardest questions:
{chr(10).join(f'  - Q{qnum}: {pct}% correct' for qnum, pct in hardest)}

Lowest-scoring students:
{chr(10).join(f'  - {s["class"]}: {s["score"]}%' for s in bottom_5)}"""

    if item_analysis_data:
        prompt_data += f"\n\nCross-assignment comparison (same questions, different classes):\n{item_analysis_data}"

    system_prompt = """You are an education analytics assistant. Analyze the department performance data and provide:

1. **Summary**: A 2-3 sentence overview of overall performance, highlighting key patterns and notable differences between classes.

2. **Action Items**: 3-5 specific, actionable recommendations. Each should identify WHO needs attention (which class, which students), WHAT the issue is (which topics/questions), and HOW to address it.

Respond in JSON format:
{
  "summary": "...",
  "action_items": ["...", "...", "..."]
}"""

    try:
        if prov_type == 'anthropic':
            response = client.messages.create(
                model=model_name,
                max_tokens=1024,
                system=system_prompt,
                messages=[{'role': 'user', 'content': prompt_data}],
            )
            text = response.content[0].text
        else:
            response = client.chat.completions.create(
                model=model_name,
                max_tokens=1024,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': prompt_data},
                ],
            )
            text = response.choices[0].message.content

        # Parse JSON from response
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = {'summary': text, 'action_items': []}

        summary = parsed.get('summary', '')
        action_items = parsed.get('action_items', [])

    except Exception as e:
        logger.exception('AI analysis failed')
        return jsonify({'success': False, 'error': str(e)}), 500

    # Save to DepartmentConfig
    asn_key = asn_filter or 'all'
    cls_key = cls_filter or 'all'
    config_key = f'insight_analysis:{asn_key}:{cls_key}'
    saved = {
        'summary': summary,
        'action_items': action_items,
        'provider': provider,
        'model': model_name,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }

    cfg = DepartmentConfig.query.filter_by(key=config_key).first()
    if cfg:
        cfg.value = json.dumps(saved, ensure_ascii=False)
    else:
        cfg = DepartmentConfig(key=config_key, value=json.dumps(saved, ensure_ascii=False))
        db.session.add(cfg)
    db.session.commit()

    return jsonify({'success': True, **saved})


# ---------------------------------------------------------------------------
# Department insights — (dept × level)-tabbed dashboard endpoints (v2)
# ---------------------------------------------------------------------------

_VALID_LEVELS = ('sec1', 'sec2', 'sec3', 'sec45', 'unlevelled', 'all')


def _viewer_role_and_subjects():
    """Return (role, teaching_subjects_set) for the current teacher.

    Used by the goal-modal authz and AI prompt assembly. teaching_subjects
    is empty for HOD / Lead / Manager / Owner — they aren't subject-scoped."""
    teacher = _current_teacher()
    if not teacher:
        return None, set()
    if teacher.role == 'subject_head':
        subjects = set()
        for c in (teacher.classes or []):
            for a in Assignment.query.filter_by(class_id=c.id).all():
                if a.subject:
                    subjects.add(a.subject.strip().lower())
        return teacher.role, subjects
    return teacher.role, set()


def _parse_dept_id_arg(raw):
    """Parse the `dept_id` query / form param.

    Returns the integer dept id, or None for "All depts". Treats missing,
    blank, 'all', and unparseable values as None — the dept filter just
    falls through to the school-wide view rather than 400-ing."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s or s == 'all':
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


@app.route('/department/insights/widgets')
def department_insights_widgets():
    """Return all widget payloads for one (dept × level) tab."""
    err = _require_insights_access()
    if err:
        return err
    level = (request.args.get('level') or 'sec1').strip().lower()
    if level not in _VALID_LEVELS:
        level = 'sec1'
    dept_id = _parse_dept_id_arg(request.args.get('dept_id'))
    role, subjects = _viewer_role_and_subjects()
    from dept_insights_widgets import compute_widgets
    payload = compute_widgets(
        level, dept_id=dept_id, viewer_role=role, viewer_subjects=subjects,
    )
    return jsonify({'success': True, **payload})


@app.route('/department/insights/layout', methods=['GET'])
def department_insights_layout_get():
    err = _require_insights_access()
    if err:
        return err
    teacher = _current_teacher()
    row = DepartmentDashboardLayout.query.filter_by(teacher_id=teacher.id).first()
    layout = []
    last_level = 'sec1'
    last_dept_id = None
    if row:
        if row.layout_json:
            try:
                parsed = json.loads(row.layout_json)
                if isinstance(parsed, list):
                    layout = parsed
            except (json.JSONDecodeError, TypeError):
                layout = []
        last_level = row.last_level or 'sec1'
        last_dept_id = row.last_dept_id
    return jsonify({'success': True, 'layout': layout,
                    'last_level': last_level, 'last_dept_id': last_dept_id})


@app.route('/department/insights/layout', methods=['PUT'])
def department_insights_layout_put():
    err = _require_insights_access()
    if err:
        return err
    teacher = _current_teacher()
    data = request.get_json(silent=True) or {}
    layout = data.get('layout')
    last_level = (data.get('last_level') or 'sec1').strip().lower()
    if last_level not in _VALID_LEVELS:
        last_level = 'sec1'
    last_dept_id = _parse_dept_id_arg(data.get('last_dept_id'))
    if not isinstance(layout, list):
        return jsonify({'success': False, 'error': 'layout must be a list'}), 400
    row = DepartmentDashboardLayout.query.filter_by(teacher_id=teacher.id).first()
    payload = json.dumps(layout)
    if row:
        row.layout_json = payload
        row.last_level = last_level
        row.last_dept_id = last_dept_id
    else:
        row = DepartmentDashboardLayout(
            teacher_id=teacher.id, layout_json=payload,
            last_level=last_level, last_dept_id=last_dept_id,
        )
        db.session.add(row)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/department/insights/layout/reset', methods=['POST'])
def department_insights_layout_reset():
    """Wipe the saved layout so the page falls back to its starter template."""
    err = _require_insights_access()
    if err:
        return err
    teacher = _current_teacher()
    row = DepartmentDashboardLayout.query.filter_by(teacher_id=teacher.id).first()
    if row:
        row.layout_json = '[]'
        db.session.commit()
    return jsonify({'success': True})


def _can_edit_goal(goal, role, subjects):
    if role in ('hod', 'lead'):
        return True
    if role == 'subject_head':
        return bool(goal.target_subject) and (goal.target_subject.strip().lower() in subjects)
    return False


def _validate_goal_payload(data, role, subjects, is_create=True):
    """Return (cleaned_dict, error_response_tuple_or_None)."""
    title = (data.get('title') or '').strip()
    metric_type = (data.get('metric_type') or '').strip()
    try:
        target_value = float(data.get('target_value'))
    except (TypeError, ValueError):
        return None, (jsonify({'success': False, 'error': 'target_value must be a number'}), 400)
    target_level = (data.get('target_level') or '').strip().lower() or None
    target_subject = (data.get('target_subject') or '').strip() or None
    department_id = _parse_dept_id_arg(data.get('department_id'))

    if is_create and not title:
        return None, (jsonify({'success': False, 'error': 'title is required'}), 400)
    if metric_type and metric_type not in ('pass_rate', 'avg_score', 'submission_rate'):
        return None, (jsonify({'success': False, 'error': 'unknown metric_type'}), 400)
    if target_level and target_level not in _VALID_LEVELS:
        return None, (jsonify({'success': False, 'error': 'unknown target_level'}), 400)
    if department_id is not None:
        dept = Department.query.filter_by(id=department_id, is_active=True).first()
        if not dept:
            return None, (jsonify({'success': False, 'error': 'unknown department_id'}), 400)

    # Subject Heads must pin a subject they teach.
    if role == 'subject_head':
        if not target_subject:
            return None, (jsonify({'success': False, 'error': 'Subject Heads must pick a target subject'}), 403)
        if target_subject.strip().lower() not in subjects:
            return None, (jsonify({'success': False, 'error': 'You can only set goals for subjects you teach'}), 403)

    return {
        'title': title, 'metric_type': metric_type, 'target_value': target_value,
        'target_level': target_level, 'target_subject': target_subject,
        'department_id': department_id,
    }, None


@app.route('/department/insights/goals', methods=['GET'])
def department_insights_goals_list():
    err = _require_insights_access()
    if err:
        return err
    goals = (
        DepartmentGoal.query
        .filter(DepartmentGoal.deleted_at.is_(None))
        .order_by(DepartmentGoal.created_at.desc())
        .all()
    )
    role, subjects = _viewer_role_and_subjects()
    out = []
    for g in goals:
        out.append({
            'id': g.id,
            'title': g.title,
            'metric_type': g.metric_type,
            'target_value': g.target_value,
            'target_level': g.target_level,
            'target_subject': g.target_subject,
            'department_id': g.department_id,
            'created_by_id': g.created_by_id,
            'can_edit': _can_edit_goal(g, role, subjects),
        })
    return jsonify({'success': True, 'goals': out, 'viewer_role': role,
                    'viewer_subjects': sorted(subjects)})


@app.route('/department/insights/goals', methods=['POST'])
def department_insights_goals_create():
    err = _require_insights_access()
    if err:
        return err
    teacher = _current_teacher()
    role, subjects = _viewer_role_and_subjects()
    if role not in ('hod', 'subject_head', 'lead'):
        return jsonify({'success': False, 'error': 'Not permitted'}), 403
    data = request.get_json(silent=True) or {}
    cleaned, error = _validate_goal_payload(data, role, subjects, is_create=True)
    if error:
        return error
    if not cleaned['metric_type']:
        return jsonify({'success': False, 'error': 'metric_type is required'}), 400
    goal = DepartmentGoal(
        title=cleaned['title'],
        metric_type=cleaned['metric_type'],
        target_value=cleaned['target_value'],
        target_level=cleaned['target_level'],
        target_subject=cleaned['target_subject'],
        department_id=cleaned['department_id'],
        created_by_id=teacher.id,
    )
    db.session.add(goal)
    db.session.commit()
    return jsonify({'success': True, 'id': goal.id})


@app.route('/department/insights/goals/<int:goal_id>', methods=['PATCH'])
def department_insights_goals_update(goal_id):
    err = _require_insights_access()
    if err:
        return err
    goal = DepartmentGoal.query.filter_by(id=goal_id, deleted_at=None).first()
    if not goal:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    role, subjects = _viewer_role_and_subjects()
    if not _can_edit_goal(goal, role, subjects):
        return jsonify({'success': False, 'error': 'Not permitted'}), 403
    data = request.get_json(silent=True) or {}
    cleaned, error = _validate_goal_payload(data, role, subjects, is_create=False)
    if error:
        return error
    if 'title' in data and cleaned['title']:
        goal.title = cleaned['title']
    if cleaned['metric_type']:
        goal.metric_type = cleaned['metric_type']
    if 'target_value' in data:
        goal.target_value = cleaned['target_value']
    if 'target_level' in data:
        goal.target_level = cleaned['target_level']
    if 'target_subject' in data:
        goal.target_subject = cleaned['target_subject']
    if 'department_id' in data:
        goal.department_id = cleaned['department_id']
    db.session.commit()
    return jsonify({'success': True})


@app.route('/department/insights/goals/<int:goal_id>', methods=['DELETE'])
def department_insights_goals_delete(goal_id):
    err = _require_insights_access()
    if err:
        return err
    goal = DepartmentGoal.query.filter_by(id=goal_id, deleted_at=None).first()
    if not goal:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    role, subjects = _viewer_role_and_subjects()
    if not _can_edit_goal(goal, role, subjects):
        return jsonify({'success': False, 'error': 'Not permitted'}), 403
    goal.deleted_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({'success': True})


def _ai_analysis_cache_key(level, dept_id):
    """Cache key for the AI analysis cache, scoped per (dept, level).

    `dept_id=None` is the "All depts" tab; spelled as 'all' in the key
    so DepartmentConfig.key remains a flat string (no NULL handling)."""
    dept_part = 'all' if dept_id is None else str(int(dept_id))
    return f'dept_ai_analysis:dept={dept_part}:level={level}'


@app.route('/department/insights/saved-analysis')
def department_insights_saved_analysis():
    """Return the last cached AI analysis for one (dept × level) tab."""
    err = _require_insights_access()
    if err:
        return err
    level = (request.args.get('level') or 'sec1').strip().lower()
    if level not in _VALID_LEVELS:
        level = 'sec1'
    dept_id = _parse_dept_id_arg(request.args.get('dept_id'))
    cfg = DepartmentConfig.query.filter_by(
        key=_ai_analysis_cache_key(level, dept_id)).first()
    if cfg and cfg.value:
        try:
            return jsonify({'success': True, 'exists': True, **json.loads(cfg.value)})
        except Exception:
            pass
    return jsonify({'success': True, 'exists': False})


@app.route('/department/insights/analyze-level', methods=['POST'])
def department_analyze_level():
    """Hardened AI analysis for the (dept × level)-tabbed dashboard.

    Differs from the legacy `/department/insights/analyze`:
      - Reads the consolidated widgets payload, not a one-shot SQL pull.
      - System prompt forbids naming teachers / classes / students.
      - Output forced into tagged buckets ([Curriculum] [Resource] [PD]
        [Scheduling]).
      - Post-response regex sweep catches any teacher / class names that
        slip through and re-prompts once.
      - Cache key includes dept_id so each (dept × level) keeps its own
        analysis — switching tabs never shows another tab's text.
    """
    err = _require_insights_access()
    if err:
        return err

    data = request.get_json() or {}
    provider = data.get('provider')
    model = data.get('model')
    level = (data.get('level') or 'sec1').strip().lower()
    if level not in _VALID_LEVELS:
        level = 'sec1'
    dept_id = _parse_dept_id_arg(data.get('dept_id'))
    if not provider:
        return jsonify({'success': False, 'error': 'No provider selected'}), 400

    dept_keys = _get_dept_keys()
    if not dept_keys:
        from db import _get_fernet
        for prov in ('anthropic', 'openai', 'qwen'):
            cfg_row = DepartmentConfig.query.filter_by(key=f'api_key_{prov}').first()
            if cfg_row and cfg_row.value:
                f = _get_fernet()
                if f:
                    try:
                        dept_keys[prov] = f.decrypt(cfg_row.value.encode()).decode()
                        continue
                    except Exception:
                        pass
                dept_keys[prov] = cfg_row.value
    from ai_marking import get_ai_client
    client, model_name, prov_type = get_ai_client(provider, model, dept_keys if dept_keys else None)
    if not client:
        return jsonify({'success': False, 'error': f'No API key for {provider}'}), 400

    role, subjects = _viewer_role_and_subjects()
    from dept_insights_widgets import compute_widgets
    payload = compute_widgets(
        level, dept_id=dept_id, viewer_role=role, viewer_subjects=subjects,
    )
    low_sample = payload.get('low_sample_widgets', [])

    from levels import LEVEL_LABELS
    level_label = LEVEL_LABELS.get(level, level)
    dept_label = 'All departments'
    if dept_id is not None:
        d = Department.query.get(dept_id)
        if d:
            dept_label = d.short_name or d.name
    prompt_body = (
        f"DEPARTMENT: {dept_label}\n"
        f"LEVEL: {level_label}\n"
        f"AS OF: {payload.get('as_of')}\n"
        f"TERM WINDOW: {payload.get('term_window', {}).get('days')} days\n\n"
        f"DATA:\n{json.dumps(payload, default=str)}"
    )
    system_prompt = (
        "You are an analyst for a school department's marking insights page. "
        "The viewer is the HOD or a subject lead. Your job is to help them "
        "make systemic decisions about curriculum, shared resources, "
        "professional development, and scheduling.\n\n"
        "ABSOLUTE RULES:\n"
        "1. Open with `## Important caveats`. List low-sample data exclusions "
        "and any teacher-supplied known-issue annotations (none in v1). If "
        "there are no caveats, write 'None for this view.'\n"
        "2. NEVER name an individual teacher, class, or student as a cause "
        "or as the target of an action. If the data implies one, talk about "
        "the level as a whole.\n"
        "3. Tag every recommendation with exactly one of: [Curriculum] "
        "[Resource] [PD] [Scheduling]. Reject the temptation to invent "
        "other tags.\n"
        "4. End with the literal sentence: 'These are hypotheses for "
        "department discussion, not conclusions.'\n\n"
        "RESPOND in JSON only:\n"
        "{\n"
        '  "caveats": ["..."],\n'
        '  "summary": "2-3 sentence overview, no names.",\n'
        '  "action_items": [\n'
        '    {"tag": "Curriculum|Resource|PD|Scheduling", "text": "..."}\n'
        "  ],\n"
        '  "closing": "These are hypotheses for department discussion, not conclusions."\n'
        "}"
    )

    def _call_ai(user_text):
        if prov_type == 'anthropic':
            response = client.messages.create(
                model=model_name, max_tokens=1500,
                system=system_prompt,
                messages=[{'role': 'user', 'content': user_text}],
            )
            return response.content[0].text
        response = client.chat.completions.create(
            model=model_name, max_tokens=1500,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_text},
            ],
        )
        return response.choices[0].message.content

    def _parse(text):
        m = re.search(r'\{[\s\S]*\}', text)
        if not m:
            return {'caveats': [], 'summary': text, 'action_items': [],
                    'closing': 'These are hypotheses for department discussion, not conclusions.'}
        try:
            return json.loads(m.group())
        except Exception:
            return {'caveats': [], 'summary': text, 'action_items': [],
                    'closing': 'These are hypotheses for department discussion, not conclusions.'}

    # Build the forbidden-names regex so we can sweep for teacher / class names.
    classes_in_slice = []
    teachers_in_slice = []
    try:
        from levels import classes_in_level as _cls_in_level, LEVEL_ALL
        all_cls = Class.query.all()
        if level == LEVEL_ALL:
            relevant = all_cls
        else:
            relevant = _cls_in_level(all_cls, level)
        classes_in_slice = [c.name for c in relevant if c.name]
        teacher_ids = set()
        for c in relevant:
            for tc in TeacherClass.query.filter_by(class_id=c.id).all():
                teacher_ids.add(tc.teacher_id)
        if teacher_ids:
            teachers_in_slice = [
                t.name for t in
                Teacher.query.filter(Teacher.id.in_(teacher_ids)).all()
                if t.name
            ]
    except Exception:
        pass

    forbidden = [n for n in (classes_in_slice + teachers_in_slice) if n]
    forbidden_pattern = None
    if forbidden:
        forbidden_pattern = re.compile(
            r'\b(' + '|'.join(re.escape(n) for n in forbidden) + r')\b',
            re.IGNORECASE,
        )

    try:
        text = _call_ai(prompt_body)
    except Exception as e:
        logger.error(f'Dept level AI analysis failed: {e}')
        return jsonify({'success': False, 'error': 'AI analysis failed.'}), 500

    sweep_warning = False
    if forbidden_pattern and forbidden_pattern.search(text):
        stronger = prompt_body + (
            "\n\nSTRICT REMINDER: the previous response named a teacher or "
            "class. Rewrite without any proper nouns referring to people or "
            "classes. Replace with level-aggregate statements."
        )
        try:
            text2 = _call_ai(stronger)
            if not (forbidden_pattern and forbidden_pattern.search(text2)):
                text = text2
            else:
                sweep_warning = True
        except Exception:
            sweep_warning = True

    parsed = _parse(text)

    items = []
    valid_tags = {'Curriculum', 'Resource', 'PD', 'Scheduling'}
    for raw in (parsed.get('action_items') or []):
        if isinstance(raw, dict):
            tag = (raw.get('tag') or '').strip()
            txt = (raw.get('text') or '').strip()
        else:
            tag, txt = '', str(raw).strip()
        if tag not in valid_tags:
            tag = ''
        if txt:
            items.append({'tag': tag, 'text': txt})

    saved = {
        'caveats': parsed.get('caveats') or [],
        'summary': parsed.get('summary') or '',
        'action_items': items,
        'closing': parsed.get('closing') or
                   'These are hypotheses for department discussion, not conclusions.',
        'low_sample_widgets': low_sample,
        'sweep_warning': sweep_warning,
        'provider': provider,
        'model': model_name,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'level': level,
        'dept_id': dept_id,
    }

    cache_key = _ai_analysis_cache_key(level, dept_id)
    cfg = DepartmentConfig.query.filter_by(key=cache_key).first()
    if cfg:
        cfg.value = json.dumps(saved)
    else:
        cfg = DepartmentConfig(key=cache_key, value=json.dumps(saved))
        db.session.add(cfg)
    db.session.commit()

    return jsonify({'success': True, **saved})


# ---------------------------------------------------------------------------
# Class-level insights (heatmap, item analysis, AI summary, chat)
# ---------------------------------------------------------------------------

def _build_class_performance_data(assignment_id):
    """Gather per-student, per-question performance data for a single assignment.

    Returns dict with heatmap, item_analysis, score_distribution, student_list,
    and summary stats.  Shared by the data, analyze, and chat routes.
    """
    asn = Assignment.query.get(assignment_id)
    if not asn or not asn.class_id:
        return None

    cls = Class.query.get(asn.class_id)
    students = Student.query.filter_by(class_id=asn.class_id)\
        .order_by(Student.index_number).all()
    # UP-10: defer large blob columns — class-performance analysis touches
    # result_json only, never script bytes or extracted text.
    subs = {s.student_id: s for s in
            Submission.query_no_blobs().filter_by(assignment_id=assignment_id, is_final=True).all()}

    heatmap = []
    all_scores = []  # (student_id, total_pct)
    q_accum = {}     # qnum -> list of score ratios (0-1)
    q_marks_total = {}  # qnum -> marks_total (from first submission that has it)

    for stu in students:
        sub = subs.get(stu.id)
        row = {
            'student_name': stu.name,
            'student_index': stu.index_number,
            'student_id': stu.id,
            'submitted': False,
            'total_pct': None,
            'questions': {},
        }
        if not sub or sub.status != 'done':
            heatmap.append(row)
            continue

        result = sub.get_result()
        questions = result.get('questions', [])
        if not questions:
            heatmap.append(row)
            continue

        row['submitted'] = True
        has_marks = any(q.get('marks_awarded') is not None for q in questions)

        q_pcts = {}
        total_awarded = 0
        total_possible = 0
        correct_count = 0

        for i, q in enumerate(questions):
            qnum = str(q.get('question_number', q.get('question_num', i + 1)))
            if has_marks:
                awarded = (q.get('marks_awarded') or 0)
                possible = (q.get('marks_total') or 0)
                ratio = (awarded / possible) if possible > 0 else 0
                total_awarded += awarded
                total_possible += possible
                q_pcts[qnum] = round(ratio * 100, 1)
            else:
                status = q.get('status', '')
                if status == 'correct':
                    ratio = 1.0
                    correct_count += 1
                elif status == 'partially_correct':
                    ratio = 0.5
                else:
                    ratio = 0.0
                q_pcts[qnum] = round(ratio * 100, 1)

            q_accum.setdefault(qnum, []).append(ratio)
            if qnum not in q_marks_total and has_marks and q.get('marks_total') is not None:
                q_marks_total[qnum] = q['marks_total']

        if has_marks:
            total_pct = (total_awarded / total_possible * 100) if total_possible > 0 else 0
        else:
            total_pct = (correct_count / len(questions) * 100) if questions else 0

        row['total_pct'] = round(total_pct, 1)
        row['questions'] = q_pcts
        all_scores.append((stu.id, total_pct))
        heatmap.append(row)

    # --- Item analysis (FI, DI) ---
    sorted_by_total = sorted(all_scores, key=lambda x: x[1], reverse=True)
    n = len(sorted_by_total)
    top_n = max(1, int(n * 0.27))
    top_ids = {s[0] for s in sorted_by_total[:top_n]}
    bot_ids = {s[0] for s in sorted_by_total[-top_n:]}

    # Build per-student ratios keyed by student_id for DI lookup
    student_q_ratios = {}
    for row in heatmap:
        if not row['submitted']:
            continue
        sid = row['student_id']
        for qnum, pct in row['questions'].items():
            student_q_ratios.setdefault(sid, {})[qnum] = pct / 100.0

    item_analysis = []
    for qnum in sorted(q_accum.keys(), key=lambda x: int(x) if x.isdigit() else x):
        ratios = q_accum[qnum]
        fi = sum(ratios) / len(ratios) if ratios else 0
        attempts = len(ratios)

        # DI
        top_ratios = [student_q_ratios[sid][qnum]
                      for sid in top_ids if sid in student_q_ratios and qnum in student_q_ratios[sid]]
        bot_ratios = [student_q_ratios[sid][qnum]
                      for sid in bot_ids if sid in student_q_ratios and qnum in student_q_ratios[sid]]
        top_mean = (sum(top_ratios) / len(top_ratios)) if top_ratios else 0
        bot_mean = (sum(bot_ratios) / len(bot_ratios)) if bot_ratios else 0
        di = round(top_mean - bot_mean, 2)

        if fi >= 0.7:
            difficulty = 'Easy'
        elif fi >= 0.4:
            difficulty = 'Moderate'
        else:
            difficulty = 'Hard'

        # Interpretation
        if fi >= 0.7 and di >= 0.3:
            interp = 'Good item \u2014 moderate difficulty with strong discrimination'
        elif fi >= 0.7 and di >= 0.2:
            interp = 'Acceptable item \u2014 moderate difficulty and discrimination'
        elif fi >= 0.4 and di >= 0.3:
            interp = 'Good item \u2014 appropriate difficulty with strong discrimination'
        elif fi >= 0.4 and di >= 0.2:
            interp = 'Acceptable item \u2014 moderate difficulty and discrimination'
        elif fi < 0.4 and di >= 0.2:
            interp = 'Item needs review \u2014 difficult with acceptable discrimination'
        elif di < 0.2:
            interp = 'Easy but poor discrimination \u2014 review needed' if fi >= 0.7 \
                else 'Item needs review \u2014 moderate difficulty but poor discrimination' if fi >= 0.4 \
                else 'Item needs review \u2014 difficult and poor discrimination'
        else:
            interp = 'Insufficient data'

        # Mean score text
        if q_accum[qnum]:
            sample_total = q_marks_total.get(qnum)
            if sample_total is not None:
                mean_score = f"{round(fi * sample_total, 1)}/{sample_total}"
            else:
                mean_score = f"{round(fi * 100)}%"
        else:
            mean_score = 'N/A'

        item_analysis.append({
            'question_num': qnum,
            'fi': round(fi, 2),
            'di': di,
            'difficulty': difficulty,
            'mean_score': mean_score,
            'attempts': attempts,
            'interpretation': interp,
        })

    # --- Determine scoring mode ---
    scoring_mode = asn.scoring_mode or 'status'

    # --- Status distribution (for status-based assignments) ---
    status_dist = {'correct': 0, 'partially_correct': 0, 'incorrect': 0}
    per_question_status = {}  # qnum -> {correct, partially_correct, incorrect}
    if scoring_mode == 'status':
        for sub_obj in subs.values():
            if sub_obj.status != 'done':
                continue
            result = sub_obj.get_result()
            for q in result.get('questions', []):
                qnum = str(q.get('question_number', q.get('question_num', '')))
                st = q.get('status', 'incorrect')
                if st in status_dist:
                    status_dist[st] += 1
                per_question_status.setdefault(qnum, {'correct': 0, 'partially_correct': 0, 'incorrect': 0})
                if st in per_question_status[qnum]:
                    per_question_status[qnum][st] += 1

    # --- Score distribution (A/B/C/D) ---
    dist = {
        'A': {'label': 'A (80\u2013100%)', 'count': 0, 'pct': 0, 'students': []},
        'B': {'label': 'B (60\u201379%)', 'count': 0, 'pct': 0, 'students': []},
        'C': {'label': 'C (40\u201359%)', 'count': 0, 'pct': 0, 'students': []},
        'D': {'label': 'D (0\u201339%)', 'count': 0, 'pct': 0, 'students': []},
    }
    for row in heatmap:
        if row['total_pct'] is None:
            continue
        p = row['total_pct']
        if p >= 80:
            band = 'A'
        elif p >= 60:
            band = 'B'
        elif p >= 40:
            band = 'C'
        else:
            band = 'D'
        dist[band]['count'] += 1
        dist[band]['students'].append(row['student_name'])

    submitted_count = sum(1 for r in heatmap if r['submitted'])
    if submitted_count:
        for band in dist.values():
            band['pct'] = round(band['count'] / submitted_count * 100)

    # --- Student list ---
    student_list = []
    for row in heatmap:
        sub = subs.get(row['student_id'])
        if not sub:
            status = 'not_submitted'
        elif sub.status == 'done':
            status = 'done'
        elif sub.status in ('processing', 'extracting', 'preview'):
            status = 'processing'
        else:
            status = 'pending'
        student_list.append({
            'name': row['student_name'],
            'index': row['student_index'],
            'score': row['total_pct'],
            'status': status,
        })

    # --- Summary stats ---
    scores_only = [s[1] for s in all_scores]
    overall_avg = round(sum(scores_only) / len(scores_only), 1) if scores_only else 0
    pass_rate = round(sum(1 for s in scores_only if s >= 50) / len(scores_only) * 100, 1) if scores_only else 0
    question_nums = sorted(q_accum.keys(), key=lambda x: int(x) if x.isdigit() else x)

    return {
        'assignment_title': asn.title or asn.subject or 'Untitled',
        'class_name': cls.name if cls else 'Unknown',
        'subject': asn.subject or '',
        'scoring_mode': scoring_mode,
        'total_students': len(students),
        'submitted_count': submitted_count,
        'overall_avg': overall_avg,
        'pass_rate': pass_rate,
        'question_count': len(question_nums),
        'question_nums': question_nums,
        'heatmap': heatmap,
        'item_analysis': item_analysis,
        'score_distribution': dist,
        'status_distribution': status_dist,
        'per_question_status': per_question_status,
        'student_list': student_list,
    }


@app.route('/department/insights/class/<int:assignment_id>')
def class_insights_page(assignment_id):
    """Render class-level insights page for a single assignment."""
    err = _require_insights_access()
    if err:
        return redirect(url_for('hub'))

    asn = Assignment.query.get_or_404(assignment_id)
    cls = Class.query.get(asn.class_id) if asn.class_id else None

    from ai_marking import get_available_providers, PROVIDERS
    dept_keys = _get_dept_keys()
    ai_providers = get_available_providers(dept_keys) if dept_keys else get_available_providers()
    if not ai_providers:
        ai_providers = PROVIDERS

    teacher = _current_teacher()
    return render_template('class_insights.html',
                           assignment=asn,
                           cls=cls,
                           teacher=teacher,
                           ai_providers=ai_providers,
                           demo_mode=is_demo_mode(),
                           dept_mode=is_dept_mode())


@app.route('/department/insights/class/<int:assignment_id>/data')
def class_insights_data(assignment_id):
    """JSON data for class-level insights (heatmap, item analysis, etc.)."""
    err = _require_insights_access()
    if err:
        return err

    perf = _build_class_performance_data(assignment_id)
    if not perf:
        return jsonify({'success': False, 'error': 'Assignment not found or no class linked'}), 404

    return jsonify({'success': True, **perf})


@app.route('/department/insights/class/<int:assignment_id>/analysis')
def class_insights_get_analysis(assignment_id):
    """Return cached AI class analysis if available."""
    err = _require_insights_access()
    if err:
        return err

    cfg = DepartmentConfig.query.filter_by(key=f'class_insight_analysis:{assignment_id}').first()
    if cfg and cfg.value:
        try:
            saved = json.loads(cfg.value)
            return jsonify({'success': True, 'exists': True, **saved})
        except (json.JSONDecodeError, TypeError):
            pass
    return jsonify({'success': True, 'exists': False})


@app.route('/department/insights/class/<int:assignment_id>/analyze', methods=['POST'])
def class_insights_analyze(assignment_id):
    """Generate AI class summary with structured analysis."""
    err = _require_insights_access()
    if err:
        return err

    data = request.get_json()
    provider = data.get('provider')
    model = data.get('model')
    if not provider:
        return jsonify({'success': False, 'error': 'No provider selected'}), 400

    perf = _build_class_performance_data(assignment_id)
    if not perf:
        return jsonify({'success': False, 'error': 'No data'}), 404

    # Resolve AI client
    dept_keys = _get_dept_keys()
    if not dept_keys:
        from db import _get_fernet
        for prov in ('anthropic', 'openai', 'qwen'):
            cfg_row = DepartmentConfig.query.filter_by(key=f'api_key_{prov}').first()
            if cfg_row and cfg_row.value:
                f = _get_fernet()
                if f:
                    try:
                        dept_keys[prov] = f.decrypt(cfg_row.value.encode()).decode()
                        continue
                    except Exception:
                        pass
                dept_keys[prov] = cfg_row.value
    from ai_marking import get_ai_client
    client, model_name, prov_type = get_ai_client(provider, model, dept_keys if dept_keys else None)
    if not client:
        return jsonify({'success': False, 'error': f'No API key for {provider}'}), 400

    # Collect sample student answers for pattern analysis (up to 20 per question)
    asn = Assignment.query.get(assignment_id)
    subs = Submission.query.filter_by(assignment_id=assignment_id, status='done', is_final=True).all()
    answer_samples = {}
    for sub in subs:
        result = sub.get_result()
        for q in result.get('questions', []):
            qnum = str(q.get('question_number', q.get('question_num', '')))
            if qnum not in answer_samples:
                answer_samples[qnum] = []
            if len(answer_samples[qnum]) < 20:
                answer_samples[qnum].append({
                    'answer': (q.get('student_answer', '') or '')[:200],
                    'status': q.get('status', ''),
                    'feedback': (q.get('feedback', '') or '')[:200],
                    'marks': f"{q.get('marks_awarded', '?')}/{q.get('marks_total', '?')}" if q.get('marks_awarded') is not None else q.get('status', ''),
                })

    # Build prompt
    item_summary = '\n'.join(
        f"  Q{ia['question_num']}: FI={ia['fi']}, DI={ia['di']}, {ia['difficulty']}, "
        f"Mean={ia['mean_score']}, {ia['attempts']} attempts"
        for ia in perf['item_analysis']
    )

    answer_section = ''
    for qnum in sorted(answer_samples.keys(), key=lambda x: int(x) if x.isdigit() else x):
        samples = answer_samples[qnum]
        answer_section += f"\nQ{qnum} student responses ({len(samples)} samples):\n"
        for s in samples:
            answer_section += f"  - [{s['marks']}] {s['answer'][:100]}\n"
            if s['feedback']:
                answer_section += f"    Feedback: {s['feedback'][:100]}\n"

    if perf.get('scoring_mode') == 'status':
        sd = perf.get('status_distribution', {})
        total_answers = sum(sd.values())
        pqs = perf.get('per_question_status', {})
        pqs_summary = '\n'.join(
            f"  Q{qn}: Correct={pqs[qn].get('correct',0)}, Partial={pqs[qn].get('partially_correct',0)}, Incorrect={pqs[qn].get('incorrect',0)}"
            for qn in sorted(pqs.keys(), key=lambda x: int(x) if x.isdigit() else x)
        )
        prompt_data = f"""Class: {perf['class_name']}
Assignment: {perf['assignment_title']}
Subject: {perf['subject']}
Scoring Mode: Status-based (correct / partially correct / incorrect — no numerical marks)
Total students: {perf['total_students']}, Submitted: {perf['submitted_count']}

Overall Status Distribution ({total_answers} total answers):
  Correct: {sd.get('correct', 0)}
  Partially Correct: {sd.get('partially_correct', 0)}
  Incorrect: {sd.get('incorrect', 0)}

Per-Question Status:
{pqs_summary}

Item Analysis:
{item_summary}

Student Answers & Feedback:
{answer_section}"""
    else:
        prompt_data = f"""Class: {perf['class_name']}
Assignment: {perf['assignment_title']}
Subject: {perf['subject']}
Scoring Mode: Marks-based (numerical scores)
Total students: {perf['total_students']}, Submitted: {perf['submitted_count']}
Overall average: {perf['overall_avg']}%, Pass rate: {perf['pass_rate']}%

Item Analysis:
{item_summary}

Score Distribution:
  A (80-100%): {perf['score_distribution']['A']['count']} students ({perf['score_distribution']['A']['pct']}%)
  B (60-79%): {perf['score_distribution']['B']['count']} students ({perf['score_distribution']['B']['pct']}%)
  C (40-59%): {perf['score_distribution']['C']['count']} students ({perf['score_distribution']['C']['pct']}%)
  D (0-39%): {perf['score_distribution']['D']['count']} students ({perf['score_distribution']['D']['pct']}%)

Student Answers & Feedback:
{answer_section}"""

    system_prompt = """You are an education analytics assistant analyzing a class's performance on a specific assignment. Given the data below, provide a structured analysis in JSON format.

Be specific: reference question numbers, score ranges, and student counts. Focus on patterns in student responses and actionable insights for the teacher.

Respond ONLY with valid JSON:
{
  "concepts_grasped": [
    "Description of a well-understood concept with evidence (e.g., question numbers, FI scores)"
  ],
  "misconceptions": [
    "Description of a common misconception with evidence from student answers and question references"
  ],
  "areas_needing_clarification": [
    "Description of a borderline topic where performance was mixed"
  ],
  "recommended_actions": [
    "Specific, actionable teaching suggestion referencing questions and student groups"
  ],
  "per_question_notes": [
    {
      "question_num": "1",
      "summary": "Brief performance summary for this question",
      "common_errors": "What students got wrong and why, based on answer patterns",
      "teaching_suggestion": "How to address this in class"
    }
  ]
}"""

    try:
        if prov_type == 'anthropic':
            response = client.messages.create(
                model=model_name,
                max_tokens=4096,
                system=system_prompt,
                messages=[{'role': 'user', 'content': prompt_data}],
            )
            text = response.content[0].text
        else:
            response = client.chat.completions.create(
                model=model_name,
                max_tokens=4096,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': prompt_data},
                ],
            )
            text = response.choices[0].message.content

        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            parsed = {'concepts_grasped': [], 'misconceptions': [],
                      'areas_needing_clarification': [], 'recommended_actions': [],
                      'per_question_notes': []}

    except Exception:
        logger.exception('Class AI analysis failed')
        return jsonify({'success': False, 'error': 'AI analysis failed. Check server logs.'}), 500

    saved = {
        **parsed,
        'provider': provider,
        'model': model_name,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }

    config_key = f'class_insight_analysis:{assignment_id}'
    cfg = DepartmentConfig.query.filter_by(key=config_key).first()
    if cfg:
        cfg.value = json.dumps(saved, ensure_ascii=False)
    else:
        cfg = DepartmentConfig(key=config_key, value=json.dumps(saved, ensure_ascii=False))
        db.session.add(cfg)
    db.session.commit()

    return jsonify({'success': True, **saved})


@app.route('/department/insights/class/<int:assignment_id>/chat', methods=['POST'])
def class_insights_chat(assignment_id):
    """Streaming chat about class performance data using SSE."""
    err = _require_insights_access()
    if err:
        return err

    data = request.get_json()
    provider = data.get('provider')
    model = data.get('model')
    messages = data.get('messages', [])[-20:]  # limit to last 20 messages
    if not provider or not messages:
        return jsonify({'success': False, 'error': 'Missing provider or messages'}), 400

    perf = _build_class_performance_data(assignment_id)
    if not perf:
        return jsonify({'success': False, 'error': 'No data'}), 404

    # Resolve AI client
    dept_keys = _get_dept_keys()
    if not dept_keys:
        from db import _get_fernet
        for prov in ('anthropic', 'openai', 'qwen'):
            cfg_row = DepartmentConfig.query.filter_by(key=f'api_key_{prov}').first()
            if cfg_row and cfg_row.value:
                f = _get_fernet()
                if f:
                    try:
                        dept_keys[prov] = f.decrypt(cfg_row.value.encode()).decode()
                        continue
                    except Exception:
                        pass
                dept_keys[prov] = cfg_row.value
    from ai_marking import get_ai_client
    client, model_name, prov_type = get_ai_client(provider, model, dept_keys if dept_keys else None)
    if not client:
        return jsonify({'success': False, 'error': f'No API key for {provider}'}), 400

    # Build compact context for chat system prompt
    item_summary = ', '.join(
        f"Q{ia['question_num']}(FI={ia['fi']},DI={ia['di']},{ia['difficulty']},Mean={ia['mean_score']})"
        for ia in perf['item_analysis']
    )
    heatmap_summary = []
    for row in perf['heatmap']:
        if row['submitted']:
            heatmap_summary.append(f"{row['student_name']}({row['student_index']}): {row['total_pct']}%")

    system_prompt = f"""You are an education analytics assistant. You have access to the following class performance data.

Class: {perf['class_name']}
Assignment: {perf['assignment_title']} ({perf['subject']})
Students: {perf['total_students']} total, {perf['submitted_count']} submitted
Overall average: {perf['overall_avg']}%, Pass rate: {perf['pass_rate']}%

Item Analysis: {item_summary}

Score Distribution: A({perf['score_distribution']['A']['count']}), B({perf['score_distribution']['B']['count']}), C({perf['score_distribution']['C']['count']}), D({perf['score_distribution']['D']['count']})

Student Scores: {'; '.join(heatmap_summary[:50])}

Answer the teacher's questions about this data. Be conversational, specific, and actionable. Reference specific question numbers and student performance patterns when relevant. Use LaTeX in $ delimiters for any math."""

    def generate():
        try:
            if prov_type == 'anthropic':
                with client.messages.stream(
                    model=model_name,
                    max_tokens=2048,
                    system=system_prompt,
                    messages=messages,
                ) as stream:
                    for text in stream.text_stream:
                        yield f"data: {json.dumps({'text': text}, ensure_ascii=False)}\n\n"
            else:
                stream = client.chat.completions.create(
                    model=model_name,
                    max_tokens=2048,
                    stream=True,
                    messages=[{'role': 'system', 'content': system_prompt}] + messages,
                )
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield f"data: {json.dumps({'text': chunk.choices[0].delta.content}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception:
            logger.exception('Chat stream error')
            yield f"data: {json.dumps({'error': 'Chat failed. Please try again.'})}\n\n"

    return app.response_class(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


@app.route('/department/export/csv')
def department_export_csv():
    """Export results as CSV."""
    err = _require_insights_access()
    if err:
        return err
    if is_demo_mode() and not is_dept_mode():
        return jsonify({'success': False, 'error': 'Not available in demo mode'}), 403

    assignment_id = request.args.get('assignment_id')
    class_id = request.args.get('class_id')

    # UP-10: defer blob columns — analytics path only reads result_json.
    query = Submission.query_no_blobs().filter_by(status='done', is_final=True)
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
            ta = sum((q.get('marks_awarded') or 0) for q in questions)
            tp = sum((q.get('marks_total') or 0) for q in questions)
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

        _finalise_login_session()
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

        # UP-28: rotate session ID at the actual authentication boundary
        # (account creation). `pending_setup` is dropped implicitly by
        # session.clear() inside the helper.
        _finalise_login_session()
        session['teacher_id'] = teacher.id
        session['teacher_name'] = teacher.name
        return jsonify({'success': True, 'redirect': '/'})

    return render_template('teacher_setup.html')


# ---------------------------------------------------------------------------
# Teacher Dashboard
# ---------------------------------------------------------------------------

@app.route('/dashboard')
def teacher_dashboard():
    if not _is_authenticated():
        return redirect(url_for('hub'))

    teacher = _current_teacher()
    if not teacher:
        return redirect(url_for('hub'))

    # Cross-class roles (HOD / SH / Lead / Manager / Owner) get the
    # teacher-filter dropdown and can pick any teacher's roster. Everyone
    # else just sees their own assigned classes.
    is_senior = teacher.role in ROLES_CAN_VIEW_INSIGHTS
    all_teachers = []
    # Distinguish "param missing" (first visit) from "param present but
    # empty" (user explicitly chose 'All teachers' in the dropdown). For
    # any role that has the dropdown, first visit defaults to filtering
    # by themselves so they see only their own classes at a glance; they
    # can pick another teacher — or 'All teachers' — to widen the view.
    raw_teacher_id = request.args.get('teacher_id')
    if raw_teacher_id is None:
        filter_teacher_id = teacher.id if is_senior else ''
    else:
        filter_teacher_id = raw_teacher_id.strip()

    if is_senior:
        all_teachers = Teacher.query.order_by(Teacher.name).all()
        if filter_teacher_id:
            # Filter classes assigned to a specific teacher
            filter_teacher = Teacher.query.get(filter_teacher_id)
            if filter_teacher:
                teacher_classes = filter_teacher.classes
            else:
                teacher_classes = Class.query.all()
        else:
            teacher_classes = Class.query.all()
    else:
        teacher_classes = teacher.classes

    teacher_class_ids = [cls.id for cls in teacher_classes]
    if teacher_class_ids:
        from perf import light_assignment_query
        q = light_assignment_query().filter(Assignment.class_id.in_(teacher_class_ids))
        # Co-teaching: a teacher viewing their own roster sees every
        # assignment in those classes, not just the ones they authored.
        # The creator filter only kicks in when a senior uses the dropdown
        # to inspect ANOTHER teacher's authored work — viewing yourself
        # (default first visit) keeps the full co-teaching view.
        if is_senior and filter_teacher_id and filter_teacher_id != teacher.id:
            q = q.filter(Assignment.teacher_id == filter_teacher_id)
        all_assignments = q.order_by(Assignment.created_at.desc()).all()
    else:
        all_assignments = []

    # Resolve creator names for the "Created by …" label on each card.
    author_ids = {a.teacher_id for a in all_assignments if a.teacher_id}
    teacher_name_by_id = (
        {t.id: t.name for t in Teacher.query.filter(Teacher.id.in_(author_ids)).all()}
        if author_ids else {}
    )
    assignments_by_class = {}
    for a in all_assignments:
        assignments_by_class.setdefault(a.class_id, []).append(a)

    # UP-49: bulk load student counts by class in a single GROUP BY query.
    # Previously this loop fired one `COUNT(*)` per class — N round trips
    # for a teacher with N assigned classes.
    student_counts_by_class = {cls.id: 0 for cls in teacher_classes}
    if teacher_class_ids:
        from sqlalchemy import func as _func
        rows = (
            db.session.query(Student.class_id, _func.count(Student.id))
            .filter(Student.class_id.in_(teacher_class_ids))
            .group_by(Student.class_id)
            .all()
        )
        for cid, n in rows:
            student_counts_by_class[cid] = n

    # Bulk load all submissions for these assignments via the perf
    # helper — defers script_bytes / per-page image base64 / extracted
    # + student text JSON (none read here). result_json stays eager
    # because the avg-score loop below reads it via s.get_result().
    from perf import light_submission_query
    all_asn_ids = [a.id for a in all_assignments]
    all_subs = (
        light_submission_query()
        .filter(Submission.assignment_id.in_(all_asn_ids))
        .all()
    ) if all_asn_ids else []
    subs_by_assignment = {}
    for s in all_subs:
        subs_by_assignment.setdefault(s.assignment_id, []).append(s)

    class_data = []
    for cls in teacher_classes:
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
                            total_a = sum((q.get('marks_awarded') or 0) for q in qs)
                            total_p = sum((q.get('marks_total') or 0) for q in qs)
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
                'created_by_id': asn.teacher_id,
                'created_by_name': teacher_name_by_id.get(asn.teacher_id, ''),
            })

        class_data.append({
            'id': cls.id,
            'name': cls.name,
            'level': cls.level,
            'student_count': student_counts_by_class.get(cls.id, 0),
            'assignments': asn_data,
        })

    return render_template('dashboard.html',
                           teacher=teacher,
                           classes=class_data,
                           dept_mode=is_dept_mode(),
                           demo_mode=is_demo_mode(),
                           all_teachers=all_teachers,
                           filter_teacher_id=filter_teacher_id)


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
    """Parse class list from CSV or Excel. Returns list of {index, name} dicts."""
    ext = (filename or '').rsplit('.', 1)[-1].lower() if filename else ''

    # Handle Excel files
    if ext in ('xlsx', 'xls'):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True)
            ws = wb.active
            students = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(c).strip() if c is not None else '' for c in row]
                if not any(cells):
                    continue
                if cells[0].lower() in ('index', 'no', 'no.', 's/n', 'sn', '#', 'number', 'name'):
                    continue
                # UP-47: NFC-normalise at the ingest boundary so a Tamil /
                # CJK name typed in NFD form (from MOE class lists) and a
                # precomposed-NFC form (from manual entry) collapse to the
                # same string for dedup, sort, and dict lookups.
                if len(cells) >= 2 and cells[1]:
                    students.append({'index': cells[0], 'name': normalize_name(cells[1])})
                elif cells[0]:
                    students.append({'index': str(len(students) + 1), 'name': normalize_name(cells[0])})
            wb.close()
            return students
        except ImportError:
            raise ValueError('Excel support requires openpyxl. Please upload a CSV file instead.')

    # Handle CSV with encoding fallback
    text = None
    for encoding in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1252'):
        try:
            text = file_bytes.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        raise ValueError('Could not decode file. Please save as UTF-8 CSV and try again.')

    reader = csv.reader(io.StringIO(text))
    students = []
    for row in reader:
        if not row or not any(cell.strip() for cell in row):
            continue
        # Skip header row
        if row[0].strip().lower() in ('index', 'no', 'no.', 's/n', 'sn', '#', 'number'):
            continue
        # UP-47: NFC at ingest — see Excel branch above for rationale.
        if len(row) >= 2:
            students.append({'index': row[0].strip(), 'name': normalize_name(row[1])})
        else:
            students.append({'index': str(len(students) + 1), 'name': normalize_name(row[0])})
    return students


def run_bulk_marking_job(job_id, provider, model, question_paper_pages, answer_key_pages,
                         rubrics_pages, reference_pages, student_scripts, students,
                         subject, review_instructions, marking_instructions,
                         assign_type, scoring_mode, total_marks, session_keys,
                         assignment_id=None, student_id_map=None, submission_id_map=None,
                         pinyin_mode='off'):
    """Background thread for bulk marking — marks each student sequentially."""
    results = []
    total = len(students)
    processed_indices = set()

    # UP-08: resolve calibration once for the whole bulk run. The block is
    # per-assignment (subject + teacher), not per-student. Computing it
    # inside the loop would be the same answer N times and N× the cost.
    calibration_block = ''
    if assignment_id:
        try:
            with app.app_context():
                asn_for_cal = Assignment.query.get(assignment_id)
                if asn_for_cal:
                    calibration_block = _build_calibration_block_for(asn_for_cal)
        except Exception as cal_err:
            logger.warning(
                f"Bulk job {job_id}: calibration pre-fetch failed, "
                f"proceeding without it: {cal_err}"
            )
            calibration_block = ''

    try:
        for i, (student, script_bytes) in enumerate(zip(students, student_scripts)):
            # UP-15: progress writes go to the bulk_jobs table so the
            # `/status` poller stays accurate across a redeploy.
            try:
                with app.app_context():
                    _bulk_job_update(job_id, progress={
                        'current': i + 1,
                        'total': total,
                        'current_name': student['name'],
                    })
            except Exception as _pe:
                logger.debug(f'bulk progress write skipped: {_pe}')

            # UP-12: reset per-iteration so a `mark_script` raise can't leak
            # the previous student's usage onto the next DB write.
            bulk_usage_entry = None
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
                    pinyin_mode=pinyin_mode,
                    calibration_block=calibration_block,
                )
                # UP-12: usage will be appended to the submission row below.
                bulk_usage_entry = consume_last_usage()
            except Exception as e:
                # UP-35: `MarkingError` is a subclass of `Exception`, so the
                # typed raise from `mark_script` still flows here — we just
                # downgrade the log level for it (the typed path is already
                # logged inside `mark_script`; only genuinely unexpected
                # exceptions warrant a fresh `logger.exception`).
                if isinstance(e, MarkingError):
                    logger.info(
                        "Bulk job %s, student %s: marking error: %s",
                        job_id, student['name'], e,
                    )
                else:
                    logger.exception(
                        "Bulk job %s, student %s failed", job_id, student['name'],
                    )
                result = {'error': str(e)}
                # UP-46: capture a per-student error entry on the persistent
                # BulkJob so the class page can surface "3 failed — retry
                # these" affordance after the job completes. `retryable=True`
                # for transient AI/network failures (AIProviderError covers
                # rate limit / timeout / connection exhausted by retry);
                # `False` for ResponseParseError and unexpected exceptions
                # — those need a real fix (rubric tweak, prompt edit, manual
                # remark), not a blind retry.
                if isinstance(e, AIProviderError):
                    err_class = 'provider'
                    retryable = True
                elif isinstance(e, ResponseParseError):
                    err_class = 'parse'
                    retryable = False
                elif isinstance(e, MarkingError):
                    err_class = 'marking'
                    retryable = False
                else:
                    err_class = 'unexpected'
                    retryable = False
                try:
                    with app.app_context():
                        student_db_id = (student_id_map or {}).get(student['index'])
                        _bulk_job_update(job_id, errors_append={
                            'student_id': student_db_id,
                            'student_index': student['index'],
                            'student_name': student['name'],
                            'error_class': err_class,
                            'error_msg': str(e)[:500],  # cap so a giant traceback can't bloat the JSON column
                            'retryable': retryable,
                            'at': datetime.now(timezone.utc).isoformat(),
                        })
                except Exception:
                    logger.exception(
                        "Bulk job %s: failed to record per-student error for %s",
                        job_id, student['name'],
                    )
                # Ensure the pre-created row is finalized as 'error' so it doesn't
                # linger as 'pending' and count against the student's draft cap.
                if assignment_id and submission_id_map:
                    sub_id = submission_id_map.get(student['index'])
                    if sub_id:
                        try:
                            with app.app_context():
                                sub = Submission.query.get(sub_id)
                                if sub and sub.status == 'pending':
                                    sub.status = 'error'
                                    sub.set_result({'error': str(e)})
                                    sub.marked_at = datetime.now(timezone.utc)
                                    db.session.commit()
                        except Exception:
                            db.session.rollback()
                            logger.exception(
                                "Failed to finalize errored submission for %s",
                                student['name'],
                            )

            results.append({
                'index': student['index'],
                'name': student['name'],
                'result': result,
            })
            processed_indices.add(student['index'])

            # Save to DB if in dept mode
            if assignment_id and (submission_id_map or student_id_map):
                try:
                    with app.app_context():
                        sub_id = (submission_id_map or {}).get(student['index'])
                        if sub_id:
                            # Pre-created draft: update result + status in place
                            sub = Submission.query.get(sub_id)
                            if sub:
                                sub.status = 'error' if result.get('error') else 'done'
                                sub.set_result(result)
                                sub.marked_at = datetime.now(timezone.utc)
                                # UP-12: persist this student's AI call usage.
                                if bulk_usage_entry:
                                    try:
                                        sub.append_usage(bulk_usage_entry)
                                    except Exception:
                                        pass
                                db.session.commit()
                                # Parity with single-marking: kick categorisation
                                # so mistake_type lands on result_json. Without this,
                                # bulk-marked submissions stay categorisation_status='pending'
                                # until the teacher first opens each one — and any
                                # FeedbackEdit / SubjectStandard promoted before that
                                # inherits mistake_type=None.
                                if sub.status == 'done':
                                    try:
                                        if _count_lost_criteria((result or {}).get('questions')) >= 2:
                                            _kick_categorisation_worker(sub.id)
                                        else:
                                            sub.categorisation_status = 'done'
                                            db.session.commit()
                                    except Exception as cat_err:
                                        db.session.rollback()
                                        logger.warning(
                                            f"Bulk job {job_id}: categorisation kick "
                                            f"failed for sub {sub.id}: {cat_err}"
                                        )
                        else:
                            # Legacy path retained for safety; currently unreachable
                            # Legacy fallback (no pre-created row) — create a new Submission
                            student_db_id = (student_id_map or {}).get(student['index'])
                            if student_db_id:
                                sub = Submission(
                                    student_id=student_db_id,
                                    assignment_id=assignment_id,
                                    script_bytes=script_bytes,
                                    status='error' if result.get('error') else 'done',
                                    submitted_at=datetime.now(timezone.utc),
                                )
                                sub.set_result(result)
                                sub.marked_at = datetime.now(timezone.utc)
                                db.session.add(sub)
                                db.session.commit()
                except Exception:
                    db.session.rollback()
                    logger.exception("Failed to save submission for %s", student['name'])

        # UP-15: final results + status to the bulk_jobs row.
        try:
            with app.app_context():
                _bulk_job_update(
                    job_id,
                    results=results,
                    status='done',
                    progress={'current': total, 'total': total, 'current_name': 'Complete'},
                    finished=True,
                )
        except Exception:
            logger.exception('bulk final write failed')

        # Clear the "needs re-mark" flag on the assignment now that bulk-mark finished.
        if assignment_id:
            try:
                with app.app_context():
                    asn = Assignment.query.get(assignment_id)
                    if asn and asn.needs_remark:
                        asn.needs_remark = False
                        db.session.commit()
            except Exception:
                db.session.rollback()
                logger.exception("Failed to clear needs_remark for assignment %s", assignment_id)
    except Exception as job_err:
        # Top-level safety: if any unexpected exception escapes the per-student
        # handler, finalize any remaining pre-created rows so they don't stay
        # 'pending' forever and count against students' draft caps.
        logger.exception("Bulk job %s interrupted by unexpected error", job_id)
        if assignment_id and submission_id_map:
            remaining_ids = [
                sid for idx, sid in submission_id_map.items()
                if idx not in processed_indices
            ]
            for sid in remaining_ids:
                try:
                    with app.app_context():
                        sub = Submission.query.get(sid)
                        if sub and sub.status == 'pending':
                            sub.status = 'error'
                            sub.set_result({'error': 'bulk job interrupted'})
                            sub.marked_at = datetime.now(timezone.utc)
                            db.session.commit()
                except Exception:
                    db.session.rollback()
                    logger.exception("Failed to finalize interrupted submission %s", sid)
        try:
            with app.app_context():
                _bulk_job_update(
                    job_id,
                    results=results,
                    status='error',
                    error_message=str(job_err),
                    progress={
                        'current': len(processed_indices),
                        'total': total,
                        'current_name': 'Interrupted',
                    },
                    finished=True,
                )
        except Exception:
            logger.exception('bulk error write failed')


# UP-41: `bulk_page` moved to `routes/bulk.py`. Heavier `/bulk/mark`,
# `/bulk/download/<job_id>`, `/bulk/overview/<job_id>` remain on the
# monolith until the bulk-job helpers (`_bulk_job_*`) stabilise enough
# to move with their routes.


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
    # UP-17: must be a PDF (magic-byte verified). Bulk only accepts PDFs by
    # design — `_split_pdf_variable` can't split an image stack.
    if _detect_mime(bulk_pdf) != 'application/pdf':
        return jsonify({'success': False, 'error': 'Bulk upload must be a PDF file.'}), 415

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

    # Prepare a new submission row per student via _prepare_new_submission.
    # This honours allow_drafts + max_drafts: students at cap are skipped and surfaced.
    skipped = []
    filtered_students = []
    filtered_scripts = []
    submission_id_map = {}  # student index -> pre-created submission id
    for s, script_bytes in zip(students_to_mark, student_scripts):
        student_obj = Student.query.get(s['db_id'])
        if student_obj is None:
            skipped.append({
                'index': s.get('index'),
                'name': s.get('name'),
                'reason': 'Student not found',
            })
            continue
        new_sub, err = _prepare_new_submission(student_obj, asn)
        if err:
            skipped.append({
                'index': s.get('index'),
                'name': s.get('name'),
                'reason': err,
            })
            continue
        new_sub.script_bytes = script_bytes
        new_sub.status = 'pending'
        new_sub.set_script_pages([script_bytes])
        db.session.add(new_sub)
        db.session.commit()
        submission_id_map[s['index']] = new_sub.id
        filtered_students.append(s)
        filtered_scripts.append(script_bytes)

    students_to_mark = filtered_students
    student_scripts = filtered_scripts

    if not students_to_mark:
        return jsonify({
            'success': False,
            'error': 'No students eligible for marking (all at draft cap or no students selected).',
            'skipped': skipped,
        }), 400

    # Use assignment's stored settings
    session_keys = _resolve_api_keys(asn)

    # Build student_id_map for the background thread (kept for backward-compat logging)
    student_id_map = {s['index']: s['db_id'] for s in students_to_mark}

    # Get files from assignment record
    question_paper_pages = [asn.question_paper] if asn.question_paper else []
    answer_key_pages = [asn.answer_key] if asn.answer_key else []
    rubrics_pages = [asn.rubrics] if asn.rubrics else []
    reference_pages = [asn.reference] if asn.reference else []

    cleanup_old_jobs()

    job_id = str(uuid.uuid4())
    # UP-15: persist bulk-job state to DB so a Railway redeploy mid-mark
    # doesn't lose progress. The status route now reads this row.
    _bulk_job_create(
        job_id,
        assignment_id=assignment_id,
        subject=asn.subject,
        total=len(students_to_mark),
        skipped=skipped,
    )

    thread = threading.Thread(
        target=run_bulk_marking_job,
        kwargs={
            'job_id': job_id, 'provider': asn.provider, 'model': asn.model,
            'question_paper_pages': question_paper_pages, 'answer_key_pages': answer_key_pages,
            'rubrics_pages': rubrics_pages, 'reference_pages': reference_pages,
            'student_scripts': student_scripts, 'students': students_to_mark,
            'subject': asn.subject,
            'review_instructions': asn.review_instructions,
            'marking_instructions': asn.marking_instructions,
            'assign_type': asn.assign_type, 'scoring_mode': asn.scoring_mode,
            'total_marks': asn.total_marks, 'session_keys': session_keys,
            'assignment_id': assignment_id, 'student_id_map': student_id_map,
            'submission_id_map': submission_id_map,
            'pinyin_mode': getattr(asn, 'pinyin_mode', 'off'),
        },
        daemon=True
    )
    thread.start()

    return jsonify({'success': True, 'job_id': job_id, 'skipped': skipped})


@app.route('/bulk/download/<job_id>')
def bulk_download(job_id):
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    # UP-15: read from persistent BulkJob with in-memory fallback for any
    # in-flight job created before this code shipped.
    # UP-30: shallow-copy under lock to avoid racing the bulk worker.
    job = _bulk_job_load(job_id) or _jobs_get(job_id)
    if not job or job['status'] != 'done' or not job.get('results'):
        return jsonify({'success': False, 'error': 'No results available'}), 404
    if job.get('_persistent') and job.get('assignment_id'):
        asn = Assignment.query.get(job['assignment_id'])
        if asn is not None:
            err = _check_assignment_ownership(asn)
            if err:
                return err

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in job['results']:
            if item['result'].get('error'):
                continue
            pdf_bytes = generate_report_pdf(
                item['result'],
                subject=job.get('subject', ''),
                app_title=get_app_title(),
                assignment_name=job.get('assignment_name', '') or job.get('title', ''),
                student_name=item.get('name', ''),
            )
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

    # UP-15: read from persistent BulkJob with in-memory fallback.
    # UP-30: shallow-copy under lock to avoid racing the bulk worker.
    job = _bulk_job_load(job_id) or _jobs_get(job_id)
    if not job or job['status'] != 'done' or not job.get('results'):
        return jsonify({'success': False, 'error': 'No results available'}), 404
    if job.get('_persistent') and job.get('assignment_id'):
        asn = Assignment.query.get(job['assignment_id'])
        if asn is not None:
            err = _check_assignment_ownership(asn)
            if err:
                return err

    student_results = [
        {'name': item['name'], 'index': item['index'], 'result': item['result']}
        for item in job['results']
    ]
    pdf_bytes = generate_overview_pdf(
        student_results,
        subject=job.get('subject', ''),
        app_title=get_app_title(),
        assignment_name=job.get('assignment_name', '') or job.get('title', ''),
    )

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


def _run_submission_extraction(app_obj, submission_id, assignment_id):
    """Background thread: extract answers from student script (preview step)."""
    with app_obj.app_context():
        sub = Submission.query.get(submission_id)
        asn = Assignment.query.get(assignment_id)
        if not sub or not asn:
            return

        try:
            from ai_marking import extract_answers
            qp = [asn.question_paper] if asn.question_paper else []
            script = sub.get_script_pages()

            result = extract_answers(
                provider=asn.provider,
                question_paper_pages=qp,
                script_pages=script,
                subject=asn.subject,
                assign_type=asn.assign_type,
                model=asn.model,
                session_keys=_resolve_api_keys(asn),
            )

            # UP-12: capture extraction-call usage too.
            try:
                usage = consume_last_usage()
                if usage:
                    sub.append_usage(usage)
            except Exception as _u:
                logger.debug(f"usage append skipped for sub {submission_id} (extract): {_u}")

            if result.get('error'):
                sub.set_result({'error': result['error']})
                sub.status = 'error'
            else:
                answers = result.get('answers', [])
                if not answers:
                    sub.set_result({'error': 'Could not extract any answers from your script. Please re-upload a clearer image.'})
                    sub.status = 'error'
                else:
                    sub.set_extracted_text(answers)
                    sub.status = 'preview'
        except Exception as e:
            db.session.rollback()
            logger.exception("Submission %s extraction failed", submission_id)
            sub.set_result({'error': str(e)})
            sub.status = 'error'

        db.session.commit()


def _run_submission_marking(app_obj, submission_id, assignment_id, band_overrides=None):
    """Background thread: mark a student submission.

    band_overrides: optional {criterion_name: band_label} dict. When set,
        the AI is told to keep those bands fixed and anchor all per-band
        descriptors / improvement examples to the teacher's chosen band.
        After marking, we also clobber the AI's `band` field for those
        criteria back to the teacher's choice (defence-in-depth in case
        the model ignored the prompt instruction).
    """
    with app_obj.app_context():
        sub = Submission.query.get(submission_id)
        asn = Assignment.query.get(assignment_id)
        if not sub or not asn:
            return

        sub.status = 'processing'
        db.session.commit()

        logger.info(
            f"Marking submission {submission_id} for assignment {assignment_id} — "
            f"scoring_mode={asn.scoring_mode!r} total_marks={asn.total_marks!r}"
        )

        try:
            qp = [asn.question_paper] if asn.question_paper else []
            ak = [asn.answer_key] if asn.answer_key else []
            rub = [asn.rubrics] if asn.rubrics else []
            ref = [asn.reference] if asn.reference else []
            script = sub.get_script_pages()

            # Calibration injection. Tiered behaviour — raw examples below
            # the principles threshold, shared markdown principles at/above
            # — everything keyed on assignments.subject (canonical dropdown
            # string). Centralised in `_build_calibration_block_for` (UP-08)
            # so the bulk path applies the same calibration.
            calibration_block = _build_calibration_block_for(asn, sub=sub)

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
                calibration_block=calibration_block,
                pinyin_mode=getattr(asn, 'pinyin_mode', 'off'),
                band_overrides=band_overrides,
            )

            # UP-12: persist per-call usage on the submission. Best-effort —
            # a None means provider didn't expose usage on this attempt.
            try:
                usage = consume_last_usage()
                if usage:
                    sub.append_usage(usage)
            except Exception as _u:
                logger.debug(f"usage append skipped for sub {submission_id}: {_u}")

            # Defence-in-depth: even with the prompt instruction, the model
            # could drift on the band field. Clobber the AI's band back to
            # the teacher's choice for any criterion in the override map,
            # and persist band_ai_original so the modal still tracks where
            # the AI would have placed the student.
            if band_overrides and isinstance(result, dict) and not result.get('error'):
                qs = result.get('questions') or []
                for q in qs:
                    crit = (q.get('criterion_name') or '').strip()
                    forced = band_overrides.get(crit)
                    if forced and (q.get('band') or '').strip() != forced:
                        q['band_ai_original'] = (q.get('band') or '').strip()
                        q['band'] = forced

            # Safety net: in marks mode, every question must have marks_total.
            # If the AI missed a bracket and left it blank, fill the gaps by
            # distributing whatever's unaccounted for in the assignment total
            # across the questions missing a total. Clamp marks_awarded to
            # [0, marks_total] so downstream math never goes negative.
            if asn.scoring_mode == 'marks' and isinstance(result, dict) and not result.get('error'):
                qs = result.get('questions') or []
                try:
                    asn_total = int(asn.total_marks) if asn.total_marks else 0
                except (TypeError, ValueError):
                    asn_total = 0
                if qs:
                    known_total = sum(int(q['marks_total']) for q in qs
                                      if isinstance(q.get('marks_total'), (int, float)) and q.get('marks_total') is not None and q.get('marks_total') > 0)
                    missing = [q for q in qs if not (isinstance(q.get('marks_total'), (int, float)) and q.get('marks_total') is not None and q.get('marks_total') > 0)]
                    if missing:
                        remaining = max(0, asn_total - known_total)
                        if remaining <= 0 or asn_total <= 0:
                            # Fallback when total isn't set: give every unassigned part 1 mark
                            per_q = 1
                        else:
                            per_q = max(1, remaining // len(missing))
                        for q in missing:
                            q['marks_total'] = per_q
                            if q.get('marks_awarded') is None:
                                # Infer from status if present, otherwise 0
                                status = (q.get('status') or '').lower()
                                if status == 'correct':
                                    q['marks_awarded'] = per_q
                                elif status == 'partially_correct':
                                    q['marks_awarded'] = per_q / 2
                                else:
                                    q['marks_awarded'] = 0
                        logger.warning(
                            f"Submission {submission_id}: AI left marks_total blank on "
                            f"{len(missing)}/{len(qs)} questions; filled with {per_q} each"
                        )
                    # Clamp awarded to [0, total] for any question, and fill missing awarded.
                    for q in qs:
                        mt = q.get('marks_total')
                        if isinstance(mt, (int, float)) and mt > 0:
                            ma = q.get('marks_awarded')
                            if ma is None:
                                status = (q.get('status') or '').lower()
                                if status == 'correct':
                                    q['marks_awarded'] = mt
                                elif status == 'partially_correct':
                                    q['marks_awarded'] = mt / 2
                                else:
                                    q['marks_awarded'] = 0
                            elif isinstance(ma, (int, float)):
                                if ma < 0:
                                    q['marks_awarded'] = 0
                                elif ma > mt:
                                    q['marks_awarded'] = mt

            sub.set_result(result)
            # UP-37: typed status (str-Enum stays compatible with the
            # `sub.status == 'done'` comparison below + Jinja templates).
            sub.status = SubmissionStatus.ERROR if result.get('error') else SubmissionStatus.DONE
            sub.marked_at = datetime.now(timezone.utc)
            if sub.status == 'done':
                qs = (result or {}).get('questions') or []
                ta = sum((q.get('marks_awarded') or 0) for q in qs)
                tp = sum((q.get('marks_total') or 0) for q in qs)
                logger.info(
                    f"Submission {submission_id} marked → {ta}/{tp} "
                    f"(total_marks requested={asn.total_marks!r})"
                )
        except Exception as e:
            db.session.rollback()
            # UP-35: typed MarkingError already logged in `mark_script`;
            # only genuinely unexpected exceptions need a fresh traceback.
            if isinstance(e, MarkingError):
                logger.info(
                    "Submission %s: marking error: %s", submission_id, e,
                )
            else:
                logger.exception("Submission %s marking failed", submission_id)
            sub.set_result({'error': str(e)})
            sub.status = 'error'
            sub.marked_at = datetime.now(timezone.utc)

        db.session.commit()

        # Auto-clear needs_remark once every done submission for this assignment
        # has been marked after the last edit. No-op when stale submissions remain
        # or when the flag is already False.
        try:
            asn_refreshed = Assignment.query.get(assignment_id)
            if asn_refreshed and asn_refreshed.needs_remark and asn_refreshed.last_edited_at:
                stale_exists = db.session.query(Submission.id).filter(
                    Submission.assignment_id == assignment_id,
                    Submission.status == 'done',
                    Submission.marked_at < asn_refreshed.last_edited_at,
                ).first() is not None
                if not stale_exists:
                    asn_refreshed.needs_remark = False
                    db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Failed to auto-clear needs_remark for assignment %s", assignment_id)

        # Log the AI-generated originals to feedback_log (v1 rows). Synchronous
        # but best-effort: any error is logged and swallowed.
        _log_ai_originals(submission_id)

        # Kick off the "Group by Mistake Type" categorisation in a background
        # thread. Only when the mark succeeded AND ≥ 2 criteria lost marks —
        # a single criterion can't form a group, so the 3-pass AI cycle
        # would just produce one standalone entry at AI-call cost.
        try:
            sub_fresh = Submission.query.get(submission_id)
            if sub_fresh and sub_fresh.status == 'done':
                result = sub_fresh.get_result() or {}
                if _count_lost_criteria(result.get('questions')) >= 2:
                    _kick_categorisation_worker(submission_id)
                else:
                    sub_fresh.categorisation_status = 'done'  # nothing to group; render shows no toggle
                    db.session.commit()
        except Exception as cat_err:
            db.session.rollback()
            logger.warning(f"Could not kick off categorisation for submission {submission_id}: {cat_err}")


def _log_ai_originals(submission_id):
    """Write feedback_log v1 rows for the AI-generated feedback and improvement
    of every criterion in this submission. Idempotent via the unique constraint
    on (submission_id, criterion_id, field, version) — re-marks skip silently.

    Best-effort: failures are logged and swallowed so the student-facing flow
    is never blocked. Only logs for submissions that successfully marked
    (status == 'done').
    """
    from db import FeedbackLog
    try:
        sub = Submission.query.get(submission_id)
        if not sub or sub.status != 'done':
            return
        result = sub.get_result() or {}
        questions = result.get('questions') or []
        added = 0
        for q in questions:
            qn = q.get('question_num')
            if qn is None:
                continue
            cid = str(qn)
            for field in ('feedback', 'improvement'):
                text_val = q.get(field) or ''
                if not text_val:
                    continue
                exists = FeedbackLog.query.filter_by(
                    submission_id=sub.id,
                    criterion_id=cid,
                    field=field,
                    version=1,
                ).first()
                if exists:
                    continue
                db.session.add(FeedbackLog(
                    submission_id=sub.id,
                    criterion_id=cid,
                    field=field,
                    version=1,
                    feedback_text=text_val,
                    author_type='ai',
                    author_id=None,
                ))
                added += 1
        if added:
            db.session.commit()
            logger.info(f"Logged {added} AI-original feedback rows for submission {submission_id}")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not log AI originals for submission {submission_id}: {e}")


def _find_propagation_candidates(edit, asn):
    """Synchronous lookup. Returns the list of submission_ids in the same
    assignment where the same criterion lost marks AND feedback_source is
    not yet 'teacher_edit'. Each entry includes student_name, marks, and
    the current feedback / improvement text so the 'Review individually'
    panel can render without a second fetch.
    """
    from db import Submission, Student
    out = []
    submissions = (
        db.session.query(Submission, Student)
        .outerjoin(Student, Submission.student_id == Student.id)
        .filter(
            Submission.assignment_id == asn.id,
            Submission.id != edit.submission_id,
            Submission.status == 'done',
        )
        .order_by(Submission.id)
        .all()
    )
    for sub, student in submissions:
        try:
            result = sub.get_result() or {}
        except Exception:
            continue
        questions = result.get('questions') or []
        target_q = None
        for q in questions:
            if str(q.get('question_num')) == edit.criterion_id:
                target_q = q
                break
        if not target_q:
            continue
        ma = target_q.get('marks_awarded')
        mt = target_q.get('marks_total')
        lost_by_marks = (mt and ma is not None and mt > 0 and ma < mt)
        lost_by_status = (not lost_by_marks
                          and target_q.get('status')
                          and target_q.get('status') != 'correct')
        if not (lost_by_marks or lost_by_status):
            continue
        source = target_q.get('feedback_source')
        if source not in (None, 'original_ai', 'propagated'):
            continue
        out.append({
            'submission_id': sub.id,
            'student_name': (student.name if student else f"Student #{sub.student_id}"),
            'marks_awarded': ma,
            'marks_total': mt,
            'current_feedback': (target_q.get('feedback') or ''),
            'current_improvement': (target_q.get('improvement') or ''),
        })
    return {
        'edit_id': edit.id,
        'criterion_name': edit.criterion_id,
        'candidate_count': len(out),
        'candidates': out,
    }


def _run_insight_extraction_worker(app_obj, edit_id):
    """Background thread: extract a structured insight from a calibration
    edit and write the three fields back. Best-effort; never blocks the
    teacher's save flow.
    """
    from db import FeedbackEdit
    with app_obj.app_context():
        try:
            edit = FeedbackEdit.query.get(edit_id)
            if not edit:
                return
            asn = Assignment.query.get(edit.assignment_id)
            if not asn:
                return
            from ai_marking import extract_correction_insight
            insight = extract_correction_insight(
                provider=asn.provider,
                model=asn.model,
                session_keys=_resolve_api_keys(asn),
                subject=(asn.subject or ''),
                mistake_type=edit.mistake_type,
                criterion_name=edit.criterion_id,
                original_text=edit.original_text,
                edited_text=edit.edited_text,
            )
            if not insight:
                return
            edit.mistake_pattern = insight.get('mistake_pattern')
            edit.correction_principle = insight.get('correction_principle')
            edit.transferability = insight.get('transferability')
            db.session.commit()
            logger.info(f"Insight extracted for edit {edit_id}: "
                        f"pattern={edit.mistake_pattern!r} "
                        f"transferability={edit.transferability!r}")
        except Exception as e:
            logger.warning(f"Insight worker failed for edit {edit_id}: {e}")
            try:
                db.session.rollback()
            except Exception:
                pass


def _run_propagation_worker(app_obj, edit_id, target_ids):
    """Background thread: refresh feedback for each candidate submission in
    sequence (never parallel — avoids DB contention). Updates result_json
    in place per submission, logs failures, and stamps the originating
    feedback_edit row with the final propagation_status + propagated_to.
    """
    from db import FeedbackEdit, Submission
    import json as _json

    with app_obj.app_context():
        try:
            edit = FeedbackEdit.query.get(edit_id)
            if not edit:
                logger.warning(f"propagation worker: edit {edit_id} not found")
                return
            asn = Assignment.query.get(edit.assignment_id)
            if not asn:
                logger.warning(f"propagation worker: assignment for edit {edit_id} not found")
                return

            # Seed propagated_to with pending entries so the progress poll
            # has the full list visible from the very first poll.
            seeded = [{'submission_id': int(sid), 'status': 'pending'} for sid in target_ids]
            edit.propagated_to = _json.dumps(seeded, ensure_ascii=False)
            edit.propagation_status = 'pending'
            db.session.commit()

            from ai_marking import refresh_criterion_feedback
            results = []
            for sid in target_ids:
                entry = {'submission_id': int(sid), 'status': 'pending'}
                try:
                    sub = Submission.query.get(int(sid))
                    if not sub:
                        entry = {'submission_id': int(sid), 'status': 'failed', 'error': 'submission not found'}
                        results.append(entry)
                        continue
                    result = sub.get_result() or {}
                    target_q = None
                    for q in (result.get('questions') or []):
                        if str(q.get('question_num')) == edit.criterion_id:
                            target_q = q
                            break
                    if not target_q:
                        entry = {'submission_id': int(sid), 'status': 'failed', 'error': 'criterion not found on this submission'}
                        results.append(entry)
                        continue
                    # Field-aware re-mark: pass the teacher's edited field
                    # through; refresh_criterion_feedback returns only that
                    # field + an optional marks_awarded override.
                    target_field = edit.field
                    if target_field not in ('feedback', 'improvement'):
                        # Defensive: server-side validation should already
                        # prevent this, but if it slipped through, skip the
                        # candidate with a clear error rather than corrupting
                        # the result.
                        entry = {'submission_id': int(sid), 'status': 'failed',
                                 'error': f'unsupported field for propagation: {target_field!r}'}
                        results.append(entry)
                        continue

                    refreshed = refresh_criterion_feedback(
                        provider=asn.provider,
                        model=asn.model,
                        session_keys=_resolve_api_keys(asn),
                        subject=asn.subject or '',
                        criterion_name=edit.criterion_id,
                        student_answer=target_q.get('student_answer') or '',
                        correct_answer=target_q.get('correct_answer') or '',
                        marks_awarded=target_q.get('marks_awarded'),
                        marks_total=target_q.get('marks_total'),
                        calibration_edit=edit,
                        target_field=target_field,
                    )
                    new_text = (refreshed.get(target_field) or target_q.get(target_field) or '')
                    target_q[target_field] = new_text
                    new_marks = refreshed.get('marks_awarded')
                    if new_marks is not None:
                        target_q['marks_awarded'] = new_marks
                        # Re-derive status to stay consistent with marks.
                        mt = target_q.get('marks_total')
                        if mt and mt > 0:
                            ratio = (new_marks or 0) / mt
                            if ratio >= 0.99:
                                target_q['status'] = 'correct'
                            elif ratio > 0:
                                target_q['status'] = 'partially_correct'
                            else:
                                target_q['status'] = 'incorrect'
                    target_q['feedback_source'] = 'propagated'
                    target_q['propagated_from_edit'] = edit.id
                    sub.set_result(result)
                    db.session.commit()
                    entry = {'submission_id': int(sid), 'status': 'done'}
                    results.append(entry)
                except Exception as e:
                    db.session.rollback()
                    err = str(e)[:200]
                    logger.warning(f"propagation refresh failed sub={sid} edit={edit_id}: {e}")
                    entry = {'submission_id': int(sid), 'status': 'failed', 'error': err}
                    results.append(entry)

                # Persist running state after each iteration so the progress
                # poll reflects partial progress.
                try:
                    edit_fresh = FeedbackEdit.query.get(edit_id)
                    current = _json.loads(edit_fresh.propagated_to or '[]')
                    for i, c in enumerate(current):
                        if int(c.get('submission_id')) == int(sid):
                            current[i] = entry
                            break
                    edit_fresh.propagated_to = _json.dumps(current, ensure_ascii=False)
                    db.session.commit()
                except Exception as persist_err:
                    db.session.rollback()
                    logger.warning(f"propagation progress persist failed: {persist_err}")

            # Final state.
            try:
                failed_n = sum(1 for r in results if r.get('status') == 'failed')
                final_status = 'complete' if failed_n == 0 else 'partial'
                edit_final = FeedbackEdit.query.get(edit_id)
                edit_final.propagation_status = final_status
                edit_final.propagated_at = datetime.now(timezone.utc)
                db.session.commit()
                logger.info(f"propagation finished edit={edit_id} status={final_status} "
                            f"done={len(results) - failed_n} failed={failed_n}")
            except Exception:
                db.session.rollback()
                logger.exception("propagation final-status persist failed")
        except Exception:
            logger.exception("propagation worker crashed for edit %s", edit_id)
            try:
                edit_err = FeedbackEdit.query.get(edit_id)
                if edit_err and edit_err.propagation_status == 'pending':
                    edit_err.propagation_status = 'partial'
                    db.session.commit()
            except Exception:
                db.session.rollback()


def _count_lost_criteria(questions):
    """Count criteria where the student lost marks (by-marks comparison
    OR by-status if marks aren't tracked). Used by both the post-marking
    kickoff gate and the feedback-view resilience auto-relaunch — same
    logic in both places."""
    return sum(
        1 for q in (questions or [])
        if ((q.get('marks_total') or 0) > 0 and (q.get('marks_awarded') or 0) < (q.get('marks_total') or 0))
        or (q.get('status') and q.get('status') != 'correct')
    )


def _kick_categorisation_worker(submission_id):
    """Mark the submission as 'pending' and spawn the background worker.
    Caller is responsible for ensuring the worker SHOULD run (lost-marks
    gate satisfied). Commits in this helper; rolls back on failure.
    Returns True on success.

    Skipped for rubrics-mode submissions: the rubrics-redesign view has no
    Mistake Category UI and no per-question correction_prompt — bands are
    the primary axis. Running categorisation would do useless DB writes
    and waste an AI call."""
    try:
        sub = Submission.query.get(submission_id)
        if not sub:
            return False
        asn = Assignment.query.get(sub.assignment_id) if sub.assignment_id else None
        if asn and getattr(asn, 'assign_type', 'short_answer') == 'rubrics':
            return False
        sub.categorisation_status = 'pending'
        db.session.commit()
        _submit_marking(_run_categorisation_worker, app, submission_id)
        return True
    except Exception as kick_err:
        db.session.rollback()
        logger.warning(f"Could not kick categorisation for sub {submission_id}: {kick_err}")
        return False


def _run_categorisation_worker(app_obj, submission_id):
    """Background thread: run the "Group by Mistake Type" AI categorisation and
    write results back into result_json. Opens its own app context so the
    original request/session is free to close.
    """
    with app_obj.app_context():
        try:
            sub = Submission.query.get(submission_id)
            if not sub or sub.status != 'done':
                return
            asn = Assignment.query.get(sub.assignment_id)
            if not asn:
                return
            result = sub.get_result() or {}
            questions = result.get('questions') or []

            # Build per-criterion payload: only criteria where marks were lost.
            payload = []
            for q in questions:
                ma = q.get('marks_awarded')
                mt = q.get('marks_total')
                lost_by_marks = (mt and ma is not None and mt > 0 and ma < mt)
                lost_by_status = (not lost_by_marks and q.get('status') and q.get('status') != 'correct')
                if not (lost_by_marks or lost_by_status):
                    continue
                cid = q.get('question_num')
                if cid is None:
                    continue
                payload.append({
                    'criterion_id': str(cid),
                    'criterion_name': q.get('criterion_name') or f"Question {cid}",
                    'student_answer': q.get('student_answer') or '',
                    'feedback': q.get('feedback') or '',
                    'marks_awarded': ma,
                    'marks_total': mt,
                    'marks_lost': max(0, (mt or 0) - (ma or 0)) if (mt and ma is not None) else None,
                })
            if not payload:
                sub.categorisation_status = 'done'
                db.session.commit()
                return

            from config.mistake_themes import themes_for
            from ai_marking import (
                categorise_mistakes,
                fetch_recent_categorisation_corrections,
                format_categorisation_corrections_block,
            )
            THEMES = themes_for(asn.subject or '')

            # Few-shot teacher corrections: pull recent CategorisationCorrection
            # rows for this assignment's subject (canonical dropdown string)
            # and inject them into the categorisation prompt. NO additional
            # AI call — same single-pass categorise_mistakes call now sees
            # the corrections as in-prompt examples.
            try:
                _corr = fetch_recent_categorisation_corrections(
                    subject=(asn.subject or ''), limit=5
                )
                corrections_block = format_categorisation_corrections_block(_corr)
                if _corr:
                    logger.info(
                        f"Categorisation for sub {submission_id}: "
                        f"injecting {len(_corr)} past teacher correction(s) for "
                        f"subject {asn.subject!r}"
                    )
            except Exception as _corr_err:
                logger.warning(
                    f"Could not fetch categorisation corrections for sub {submission_id}: {_corr_err}"
                )
                corrections_block = ''

            parsed = categorise_mistakes(
                provider=asn.provider,
                model=asn.model,
                session_keys=_resolve_api_keys(asn),
                subject=asn.subject or '',
                themes=THEMES,
                questions_data=payload,
                corrections_block=corrections_block,
            )

            # Merge the per-criterion fields into result_json.questions, keyed
            # by criterion_id (question_num). Store group habits in the tiered
            # namespace so the existing teacher PATCH merge stays compatible.
            #
            # Skip the overwrite for criteria the teacher has already corrected
            # — preserve the human value instead of letting the AI undo it on
            # the next categorisation run.
            cats_by_id = {c['criterion_id']: c for c in (parsed.get('categorisation') or [])}
            for q in questions:
                cid = str(q.get('question_num')) if q.get('question_num') is not None else None
                c = cats_by_id.get(cid) if cid else None
                if not c:
                    continue
                if q.get('mistake_type_corrected'):
                    # Teacher has already chosen a mistake_type for this criterion;
                    # don't let the AI clobber it.
                    continue
                q['mistake_type'] = c['mistake_type']
                q['specific_label'] = c['specific_label']
                q['low_confidence'] = bool(c.get('low_confidence'))
                if c.get('themed_correction_prompt'):
                    pmap = q.get('correction_prompts_by_theme') or {}
                    pmap[c['mistake_type']] = c['themed_correction_prompt']
                    q['correction_prompts_by_theme'] = pmap

            tiered = result.get('_tiered') or {}
            if not isinstance(tiered, dict):
                tiered = {}
            tiered['group_habits'] = parsed.get('group_habits') or []
            result['_tiered'] = tiered
            result['questions'] = questions

            sub.set_result(result)
            sub.categorisation_status = 'done'
            db.session.commit()
            logger.info(f"Categorisation done for submission {submission_id}: "
                        f"{len(cats_by_id)} criteria, {len(tiered['group_habits'])} group habits")
        except Exception:
            logger.exception("Categorisation failed for submission %s", submission_id)
            try:
                sub = Submission.query.get(submission_id)
                if sub:
                    sub.categorisation_status = 'failed'
                    db.session.commit()
            except Exception:
                db.session.rollback()


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
    qp_bytes_new = qp_files[0].read()

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

    # UP-17: validate teacher-supplied files at the boundary. They flow into
    # Pillow (image-mode AI calls) and LuaLaTeX (PDF generation); a malicious
    # rename-to-PDF could trigger a Pillow crash or worse.
    err = _validate_upload_blobs(
        [b for b in (qp_bytes_new, ak_bytes, rub_bytes, ref_bytes) if b],
        label='assignment file',
    )
    if err:
        return err

    provider = request.form.get('provider', '')
    if provider not in api_keys:
        return jsonify({'success': False, 'error': 'Selected provider has no API key'}), 400

    # Pinyin mode is only meaningful for Chinese subjects. Server-side,
    # validate against the allowed values and zero it out for non-Chinese
    # subjects so we don't store accidental UI state.
    raw_pinyin = (request.form.get('pinyin_mode', 'off') or 'off').lower()
    if raw_pinyin not in ('off', 'vocab', 'advanced', 'full'):
        raw_pinyin = 'off'
    from subjects import resolve_subject_key, canonicalise_subject
    # Coerce the typed subject to its canonical display form ('maths' →
    # 'Mathematics', 'hcl' → 'Chinese', etc.) so the canonical pool
    # actually pools cross-assignment. Freeform input passes through
    # unchanged and stays intra-assignment-only via is_canonical_subject.
    canon_subject = canonicalise_subject(request.form.get('subject', ''))
    if resolve_subject_key(canon_subject) != 'chinese':
        raw_pinyin = 'off'

    asn = Assignment(
        id=str(uuid.uuid4()),
        classroom_code=_generate_classroom_code(),
        title=request.form.get('title', ''),
        subject=canon_subject,
        assign_type=assign_type,
        scoring_mode=request.form.get('scoring_mode', 'marks'),
        total_marks=request.form.get('total_marks', ''),
        provider=provider,
        model=request.form.get('model', ''),
        show_results=request.form.get('show_results') == 'on',
        allow_drafts=request.form.get('allow_drafts') == 'on',
        max_drafts=_parse_max_drafts(request.form.get('max_drafts')),
        review_instructions=request.form.get('review_instructions', ''),
        marking_instructions=request.form.get('marking_instructions', ''),
        question_paper=qp_bytes_new,
        answer_key=ak_bytes,
        rubrics=rub_bytes,
        reference=ref_bytes,
        class_id=class_id,
        teacher_id=teacher_obj.id if teacher_obj else None,
        pinyin_mode=raw_pinyin,
    )
    # Only store user-provided keys
    user_keys = {}
    for prov in ('anthropic', 'openai', 'qwen'):
        val = request.form.get(f'api_key_{prov}', '').strip()
        if val:
            user_keys[prov] = val
    asn.set_api_keys(user_keys)

    db.session.add(asn)

    # Optionally add to bank
    if request.form.get('add_to_bank') == 'on':
        bank_item = AssignmentBank(
            id=str(uuid.uuid4()),
            title=asn.title,
            subject=asn.subject,
            level=request.form.get('bank_level', ''),
            tags=request.form.get('bank_tags', ''),
            assign_type=asn.assign_type,
            scoring_mode=asn.scoring_mode,
            total_marks=asn.total_marks,
            review_instructions=asn.review_instructions,
            marking_instructions=asn.marking_instructions,
            question_paper=asn.question_paper,
            answer_key=asn.answer_key,
            rubrics=asn.rubrics,
            reference=asn.reference,
            created_by=teacher_obj.id if teacher_obj else None,
        )
        db.session.add(bank_item)

    db.session.commit()

    return jsonify({
        'success': True,
        'assignment_id': asn.id,
        'classroom_code': asn.classroom_code,
    })


@app.route('/teacher/assignment/<assignment_id>/edit', methods=['POST'])
def teacher_edit(assignment_id):
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err

    # Resolve API keys (assignment → dept → env). Provider must have a key.
    api_keys = _resolve_api_keys(asn) or {}
    # Also accept fresh user-provided keys from the request (rare; usually omitted in edit)
    for prov in ('anthropic', 'openai', 'qwen'):
        val = request.form.get(f'api_key_{prov}', '').strip()
        if val:
            api_keys[prov] = val

    # Fall back to env-var keys for any provider not yet covered (parity with teacher_create)
    from ai_marking import PROVIDER_KEY_MAP
    for prov, env_name in PROVIDER_KEY_MAP.items():
        if prov not in api_keys:
            env_val = os.getenv(env_name, '')
            if env_val:
                api_keys[prov] = env_val

    new_provider = request.form.get('provider', asn.provider)
    new_model = request.form.get('model', asn.model)

    if new_provider not in api_keys:
        return jsonify({'success': False, 'error': 'Selected provider has no API key configured'}), 400

    # Parse incoming text/scalar fields (default to current value if missing)
    new_title = request.form.get('title', asn.title or '')
    from subjects import resolve_subject_key as _rsk, canonicalise_subject as _canon
    # Coerce on edit too, so changing the typed subject from 'maths' to
    # 'Mathematics' (or vice-versa) lands on the same canonical row.
    new_subject = _canon(request.form.get('subject', asn.subject or ''))
    # scoring_mode is locked after creation — changing it would invalidate
    # already-marked submissions. Always pin to the current value regardless
    # of what the form posts.
    new_scoring_mode = asn.scoring_mode or 'status'
    new_total_marks = request.form.get('total_marks', asn.total_marks or '')
    new_show_results = request.form.get('show_results') == 'on'
    new_allow_drafts = request.form.get('allow_drafts') == 'on'
    new_max_drafts = _parse_max_drafts(request.form.get('max_drafts')) if request.form.get('max_drafts') is not None else asn.max_drafts
    new_review = request.form.get('review_instructions', asn.review_instructions or '')
    new_marking = request.form.get('marking_instructions', asn.marking_instructions or '')
    # pinyin_mode: validate, then zero-out for non-Chinese subjects.
    prior_pinyin = (asn.pinyin_mode or 'off')
    new_pinyin = (request.form.get('pinyin_mode', prior_pinyin) or 'off').lower()
    if new_pinyin not in ('off', 'vocab', 'advanced', 'full'):
        new_pinyin = 'off'
    if _rsk(new_subject) != 'chinese':
        new_pinyin = 'off'

    # File handling: new upload replaces; empty input keeps existing.
    def _maybe_read(field_name):
        files = request.files.getlist(field_name)
        if files and files[0].filename:
            return files[0].read(), True
        return None, False

    qp_bytes, qp_changed = _maybe_read('question_paper')
    ak_bytes, ak_changed = _maybe_read('answer_key')
    rub_bytes, rub_changed = _maybe_read('rubrics')
    ref_bytes, ref_changed = _maybe_read('reference')

    # Type-specific required-file invariant: don't allow ending up with no answer_key
    # for short_answer or no rubrics for rubrics. Replacement is fine; removal is not allowed
    # via this endpoint (no "delete file" UI).
    if asn.assign_type == 'rubrics' and rub_changed and not rub_bytes:
        return jsonify({'success': False, 'error': 'Rubrics file cannot be empty for essay type'}), 400
    if asn.assign_type != 'rubrics' and ak_changed and not ak_bytes:
        return jsonify({'success': False, 'error': 'Answer key cannot be empty for short answer type'}), 400

    # UP-17: validate any replaced file before persisting it.
    _changed_blobs = [b for b, changed in
                      ((qp_bytes, qp_changed), (ak_bytes, ak_changed),
                       (rub_bytes, rub_changed), (ref_bytes, ref_changed))
                      if changed and b]
    if _changed_blobs:
        err = _validate_upload_blobs(_changed_blobs, label='assignment file')
        if err:
            return err

    # Detect major change BEFORE applying writes
    major_change = (
        qp_changed or ak_changed or rub_changed or ref_changed
        or (new_marking.strip() != (asn.marking_instructions or '').strip())
        or (new_review.strip() != (asn.review_instructions or '').strip())
        or (new_provider != asn.provider)
        or (new_model != asn.model)
        or (new_total_marks.strip() != (asn.total_marks or '').strip())
    )

    # Apply updates
    asn.title = new_title
    asn.subject = new_subject
    asn.scoring_mode = new_scoring_mode
    asn.total_marks = new_total_marks
    asn.show_results = new_show_results
    asn.allow_drafts = new_allow_drafts
    asn.max_drafts = new_max_drafts
    asn.review_instructions = new_review
    asn.marking_instructions = new_marking
    asn.provider = new_provider
    asn.model = new_model
    asn.pinyin_mode = new_pinyin
    if qp_changed:
        asn.question_paper = qp_bytes
    if ak_changed:
        asn.answer_key = ak_bytes
    if rub_changed:
        asn.rubrics = rub_bytes
    if ref_changed:
        asn.reference = ref_bytes

    asn.last_edited_at = datetime.now(timezone.utc)
    if major_change:
        asn.needs_remark = True

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to save edits for assignment %s", assignment_id)
        return jsonify({'success': False, 'error': 'Failed to save changes. Please try again.'}), 500

    # ── Calibration-intent design 2026-05-13 §4.8 ──
    # If rubric or answer_key changed, re-pin active amend_answer_key edits
    # from the old rubric_version to the new one. This keeps amendments
    # "alive" through a typo-fix style re-upload without requiring the
    # teacher to re-create them on the new version.
    carried_amendments = 0
    try:
        if rub_changed or ak_changed:
            from ai_marking import _rubric_version_hash
            from db import FeedbackEdit as _FE
            new_rv = _rubric_version_hash(asn)
            edits = _FE.query.filter_by(
                assignment_id=asn.id, active=True, amend_answer_key=True,
            ).all()
            for fe in edits:
                if fe.rubric_version != new_rv:
                    fe.rubric_version = new_rv
                    carried_amendments += 1
            if carried_amendments:
                db.session.commit()
    except Exception as carry_err:
        logger.warning(
            f'amendment carry-over failed for {asn.id}: {carry_err}'
        )

    # If pinyin_mode flipped (off→on, on→off, or between vocab/full), re-derive
    # the *_html siblings on every existing submission's result_json so the
    # change shows up immediately on the feedback view + PDF, without forcing
    # the teacher to re-mark every student. Idempotent and safe to skip on
    # any single submission — we just log and move on.
    pinyin_changed = (prior_pinyin != new_pinyin)
    if pinyin_changed:
        try:
            from pinyin_annotate import annotate_result_for_pinyin
            subs_done = (
                Submission.query
                .filter_by(assignment_id=asn.id)
                .filter(Submission.result_json.isnot(None))
                .all()
            )
            updated = 0
            for sub_iter in subs_done:
                try:
                    res = sub_iter.get_result()
                    if not isinstance(res, dict) or res.get('error'):
                        continue
                    if new_pinyin == 'off':
                        # Strip every *_html sibling so the renderer falls
                        # back to plain text (and any in-process PDF cache
                        # entries get a fresh key from the dict change).
                        keys_to_drop = [k for k in list(res.keys()) if k.endswith('_html')]
                        for k in keys_to_drop:
                            res.pop(k, None)
                        for q in (res.get('questions') or []):
                            if not isinstance(q, dict):
                                continue
                            for k in [k for k in list(q.keys()) if k.endswith('_html')]:
                                q.pop(k, None)
                        res.pop('pinyin_mode', None)
                    else:
                        annotate_result_for_pinyin(res, new_pinyin)
                        res['pinyin_mode'] = new_pinyin
                    sub_iter.set_result(res)
                    updated += 1
                except Exception as _re:
                    logger.warning(
                        f'pinyin re-annotation skipped for sub {sub_iter.id}: {_re}'
                    )
            if updated:
                db.session.commit()
            logger.info(
                f"Re-annotated {updated} submission(s) for assignment "
                f"{asn.id} after pinyin_mode change {prior_pinyin}→{new_pinyin}"
            )
        except Exception as _e:
            db.session.rollback()
            logger.warning(f'pinyin sweep on assignment edit skipped: {_e}')

    return jsonify({
        'success': True,
        'major_change': major_change,
        'needs_remark': asn.needs_remark,
        'last_edited_at': asn.last_edited_at.isoformat(),
        'pinyin_resweep': pinyin_changed,
        'carried_amendments': carried_amendments,
    })


@app.route('/teacher/assignment/<assignment_id>/issue-feedback', methods=['POST'])
def teacher_assignment_issue_feedback(assignment_id):
    """Flip show_results to True so students can view AI feedback in the
    browser and download PDFs. Used when an assignment was created with
    show_results=False so the teacher could review/edit before students
    saw anything. Idempotent — re-posting on an already-issued assignment
    is a no-op."""
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    if not asn.show_results:
        asn.show_results = True
        db.session.commit()
        logger.info(f"Issued AI feedback to students for assignment {assignment_id}")
    return jsonify({'success': True, 'show_results': True})


@app.route('/teacher/assignment/<assignment_id>/set-view-mode', methods=['POST'])
def teacher_assignment_set_view_mode(assignment_id):
    """Toggle the student-facing feedback layout between the
    question-by-question breakdown (with corrections) and the
    feedback-modal single-card view. Short-answer assignments only —
    rubrics assignments always use the band-first modal regardless."""
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    mode = (payload.get('mode') or '').strip().lower()
    if mode not in ('breakdown', 'modal'):
        return jsonify({'success': False, 'error': 'invalid mode'}), 400
    if asn.student_view_mode != mode:
        asn.student_view_mode = mode
        db.session.commit()
        logger.info(f"Set student_view_mode={mode} for assignment {assignment_id}")
    return jsonify({'success': True, 'student_view_mode': mode})


def _bands_for_assignment(asn):
    """Build a {criterion_name: [band_label, ...]} dict for the modal's
    band-edit dropdown.

    Mines mark-range-bearing band labels (e.g. "Band 3 (5–6)") from the
    cohort's done submissions so the teacher sees the rubric's exact
    phrasing where it's known. Then INTERPOLATES ranges for the bands
    the cohort hasn't covered yet, using the average band-width across
    mined ranges and the criterion's marks_total — so every band 1–5 in
    the dropdown carries a usable mark range.

    Returns {} if no done submission exists. The client falls back to a
    free-text input in that case.
    """
    out = {}
    crit_marks_total = {}
    subs = (Submission.query
            .filter_by(assignment_id=asn.id, is_final=True, status='done')
            .all())
    for sub in subs:
        r = sub.get_result() or {}
        for q in (r.get('questions') or []):
            crit = (q.get('criterion_name') or '').strip()
            band = (q.get('band') or '').strip()
            mt = q.get('marks_total')
            if crit and band:
                out.setdefault(crit, set()).add(band)
            if crit and isinstance(mt, (int, float)) and mt > 0:
                crit_marks_total[crit] = int(mt)

    def band_num(b):
        m = re.search(r'Band\s+(\d+)', b, re.IGNORECASE)
        return int(m.group(1)) if m else 0

    def parse_range(b):
        m = re.search(r'\((\d+)\s*[\-–—]\s*(\d+)', b)
        if not m:
            return None
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return (lo, hi)

    final = {}
    for crit, mined in out.items():
        by_num_label = {}
        ranges_by_num = {}
        for b in mined:
            n = band_num(b)
            if not n:
                continue
            r = parse_range(b)
            if r:
                ranges_by_num[n] = r
                by_num_label[n] = b
            else:
                by_num_label.setdefault(n, b)

        widths = [hi - lo + 1 for (lo, hi) in ranges_by_num.values()]
        avg_w = round(sum(widths) / len(widths)) if widths else 0
        marks_total = crit_marks_total.get(crit, 0)

        for n in range(1, 6):
            if n in ranges_by_num:
                continue
            new_lo = new_hi = None
            if avg_w > 0 and ranges_by_num:
                anchor_n = min(ranges_by_num.keys(), key=lambda k: abs(k - n))
                a_lo, a_hi = ranges_by_num[anchor_n]
                delta = n - anchor_n
                new_lo = a_lo + delta * avg_w
                new_hi = a_hi + delta * avg_w
                if n == 1:
                    new_lo = max(0, new_lo)
                if marks_total:
                    new_hi = min(new_hi, marks_total)
                    new_lo = max(0, min(new_lo, marks_total))
                if new_lo > new_hi:
                    new_lo = new_hi = None
            if new_lo is not None and new_hi is not None:
                by_num_label[n] = f"Band {n} ({new_lo}–{new_hi})"
                ranges_by_num[n] = (new_lo, new_hi)
            else:
                by_num_label.setdefault(n, f"Band {n}")
        final[crit] = [by_num_label[k] for k in sorted(by_num_label)]
    return final


def _rubric_pills_for_questions(questions):
    """Build the table-cell band pills for a rubrics submission.

    Returns a list of {abbrev, band_num, criterion_name, band_label} or [] if
    the data isn't a marked rubrics submission.
    """
    pills = []
    for q in questions or []:
        crit = (q.get('criterion_name') or '').strip()
        band = (q.get('band') or '').strip()
        if not crit or not band:
            continue
        m = re.search(r'Band\s+(\d+)', band, re.IGNORECASE)
        if not m:
            continue
        band_num = int(m.group(1))
        # Abbreviation: first 2 alpha chars of the first word with ≥2 alpha chars
        # (so "Content (Addressing the Task)" → CO, "Language" → LA). If no word
        # qualifies, fall back to first 2 alpha chars of the whole string.
        abbrev = ''
        for word in crit.split():
            clean = ''.join(c for c in word if c.isalpha())
            if len(clean) >= 2:
                abbrev = clean[:2].upper()
                break
        if not abbrev:
            letters = ''.join(c for c in crit if c.isalpha())
            abbrev = letters[:2].upper() if letters else '??'
        marks_awarded = q.get('marks_awarded')
        marks_total = q.get('marks_total')
        pills.append({
            'abbrev': abbrev,
            'band_num': band_num,
            'criterion_name': crit,
            'band_label': band,
            'marks_awarded': marks_awarded,
            'marks_total': marks_total,
        })
    return pills


@app.route('/teacher/assignment/<assignment_id>/answer-key-preview')
def teacher_assignment_answer_key_preview(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    original_ak_text = ''
    try:
        if asn.answer_key:
            from ai_marking import _decode_answer_key_text
            original_ak_text = _decode_answer_key_text(asn.answer_key) or ''
    except Exception as e:
        logger.warning(f'answer-key preview: decode failed: {e}')
    from subject_standards import build_effective_answer_key
    merged = build_effective_answer_key(asn, original_ak_text)
    return jsonify({
        'success': True,
        'effective_answer_key': merged,
        'has_original_text': bool(original_ak_text),
    })


@app.route('/teacher/assignment/<assignment_id>/push-amendments-to-bank', methods=['POST'])
def teacher_push_amendments_to_bank(assignment_id):
    """Push the merged effective-answer-key text into the linked bank's answer_key blob.

    Uses optimistic concurrency: the client sends the `last_known_bank_pushed_at`
    timestamp it saw when loading the page. If the server's `bank_pushed_at` is
    newer, return 409 so the client knows someone else pushed in the meantime.
    """
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    bank_id = data.get('bank_id')
    if not bank_id:
        return jsonify({'success': False, 'error': 'bank_id required'}), 400
    bank = AssignmentBank.query.get(bank_id)
    if not bank:
        return jsonify({'success': False, 'error': 'bank not found'}), 404

    client_ts_raw = data.get('last_known_bank_pushed_at')
    client_ts = None
    if client_ts_raw:
        try:
            client_ts = datetime.fromisoformat(str(client_ts_raw).replace('Z', '+00:00'))
        except ValueError:
            return jsonify({'success': False, 'error': 'invalid timestamp'}), 400
        # SQLite drops tz info on round-trip, so the isoformat we sent to the
        # client may have come back naive. Treat naive timestamps as UTC to
        # match the server-side normalization below.
        if client_ts.tzinfo is None:
            client_ts = client_ts.replace(tzinfo=timezone.utc)

    server_ts = asn.bank_pushed_at
    if server_ts is not None and server_ts.tzinfo is None:
        server_ts = server_ts.replace(tzinfo=timezone.utc)

    if server_ts and client_ts and server_ts > client_ts:
        return jsonify({
            'success': False,
            'error': 'concurrent_write',
            'server_bank_pushed_at': server_ts.isoformat(),
        }), 409
    if server_ts and not client_ts:
        return jsonify({
            'success': False,
            'error': 'concurrent_write_no_client_ts',
            'server_bank_pushed_at': server_ts.isoformat(),
        }), 409

    from subject_standards import build_effective_answer_key
    # Preserve the original file in `bank.answer_key` (so the bank preview
    # still renders the PDF/image natively) and persist the amendments-only
    # text into the dedicated `bank.answer_key_amendments` column for the
    # right-pane dropdown. Pass '' as the original so the helper returns just
    # the "Teacher clarifications" appendix.
    bank.answer_key = asn.answer_key
    amendments_text = build_effective_answer_key(asn, '') or ''
    bank.answer_key_amendments = amendments_text.lstrip('\n')

    now = datetime.now(timezone.utc)
    asn.bank_pushed_at = now
    db.session.commit()
    return jsonify({'success': True, 'bank_pushed_at': now.isoformat()})


@app.route('/teacher/assignment/<assignment_id>')
def teacher_assignment_detail(assignment_id):
    # Defer the four LargeBinary file blobs — the assignment-detail page
    # doesn't render the raw bytes (file preview routes load them
    # separately on demand). Keeps `api_keys_json` and the
    # review/marking instructions eager since the edit form + AI
    # client setup need them.
    from sqlalchemy.orm import defer as _defer_asn_blobs
    asn = (Assignment.query
           .options(_defer_asn_blobs(Assignment.question_paper),
                    _defer_asn_blobs(Assignment.answer_key),
                    _defer_asn_blobs(Assignment.rubrics),
                    _defer_asn_blobs(Assignment.reference))
           .filter_by(id=assignment_id)
           .first())
    if asn is None:
        return abort(404)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    students = _sort_by_index(Student.query.filter_by(class_id=asn.class_id).all()) if asn.class_id else _sort_by_index(Student.query.filter_by(assignment_id=assignment_id).all())
    is_rubrics = (getattr(asn, 'assign_type', 'short_answer') == 'rubrics')
    eligible_remark_count = 0

    # Stuck-submission detection: submissions that entered an in-progress
    # status more than 5 minutes ago without finishing are surfaced as
    # 'stuck' so the teacher can retry them. The marking worker can die
    # silently (deploy mid-flight, transient API error during extraction),
    # leaving submissions in 'extracting' or 'processing' forever otherwise.
    STUCK_THRESHOLD_SECONDS = 300
    IN_PROGRESS = ('pending', 'processing', 'extracting', 'preview')
    now_utc = datetime.now(timezone.utc)

    # UP-49: batch-load every final Submission for this assignment in
    # ONE query, then dict-lookup inside the per-student loop. The
    # previous `.first()` inside the loop was one round trip per
    # student (40 students = 40 SELECTs). The perf helper defers heavy
    # cols; `result_json` stays eager since `sub.get_result()` below
    # reads it.
    from perf import light_submission_query
    _final_subs = (
        light_submission_query()
        .filter_by(assignment_id=assignment_id, is_final=True)
        .all()
    )
    subs_by_student_id = {s.student_id: s for s in _final_subs}

    student_data = []
    for s in students:
        sub = subs_by_student_id.get(s.id)
        result = sub.get_result() if sub else {}
        questions = result.get('questions', [])
        has_marks = any(q.get('marks_awarded') is not None for q in questions)

        score = None
        if sub and sub.status == 'done' and not result.get('error'):
            if has_marks:
                ta = sum((q.get('marks_awarded') or 0) for q in questions)
                tp = sum((q.get('marks_total') or 0) for q in questions)
                score = f"{ta}/{tp}"
            else:
                correct = sum(1 for q in questions if q.get('status') == 'correct')
                score = f"{correct}/{len(questions)}"

        # Tiered-feedback engagement status:
        #   not_opened      — no submission yet, or student hasn't opened their feedback page
        #   opened          — student has opened the feedback page at least once
        #   corrections_done — student has also submitted at least one "Now You Try" correction
        if sub and getattr(sub, 'correction_submitted_at', None):
            feedback_status = 'corrections_done'
        elif sub and getattr(sub, 'feedback_opened_at', None):
            feedback_status = 'opened'
        else:
            feedback_status = 'not_opened'

        # Per-student feedback source rollup. Aggregates result_json.questions[*].feedback_source.
        # Priority: any teacher_edit > any propagated > else original.
        sources = [(q.get('feedback_source') or 'original_ai') for q in questions]
        if not sub or sub.status != 'done':
            source_icon = ''
            source_label = 'No feedback yet'
        elif any(src == 'teacher_edit' for src in sources):
            source_icon = '✎'
            source_label = 'Teacher edited directly'
        elif any(src == 'propagated' for src in sources):
            source_icon = '↻'
            source_label = 'Propagated from another student'
        else:
            source_icon = '○'
            source_label = 'Original AI feedback'

        stuck = False
        if sub and sub.status in IN_PROGRESS and sub.submitted_at:
            submitted = sub.submitted_at
            if submitted.tzinfo is None:
                submitted = submitted.replace(tzinfo=timezone.utc)
            if (now_utc - submitted).total_seconds() > STUCK_THRESHOLD_SECONDS:
                stuck = True

        # Re-mark All eligibility: submissions that finished (done) or errored
        # AND have stored script pages we can re-feed to the AI. Mirrors the
        # /remark-all endpoint's filter so the button count matches reality.
        if sub and sub.status in ('done', 'error') and sub.get_script_pages():
            eligible_remark_count += 1

        rubric_pills = (
            _rubric_pills_for_questions(questions)
            if (is_rubrics and sub and sub.status == 'done' and not result.get('error'))
            else []
        )

        student_data.append({
            'student_id': s.id,
            'index': s.index_number,
            'name': s.name,
            'status': sub.status if sub else 'not_submitted',
            'stuck': stuck,
            'score': score,
            'rubric_pills': rubric_pills,
            'submitted_at': sub.submitted_at.strftime('%d %b %H:%M') if sub and sub.submitted_at else None,
            'student_amended': sub.student_amended if sub else False,
            'submission_id': sub.id if sub else None,
            'feedback_status': feedback_status,
            'feedback_opened_at': sub.feedback_opened_at.strftime('%d %b %H:%M') if sub and getattr(sub, 'feedback_opened_at', None) else None,
            'correction_submitted_at': sub.correction_submitted_at.strftime('%d %b %H:%M') if sub and getattr(sub, 'correction_submitted_at', None) else None,
            'source_icon': source_icon,
            'source_label': source_label,
        })

    # Compute which providers have a usable key for this assignment (assignment → dept → env).
    edit_api_keys = _resolve_api_keys(asn) or {}
    from ai_marking import PROVIDER_KEY_MAP
    for prov, env_name in PROVIDER_KEY_MAP.items():
        if prov not in edit_api_keys:
            env_val = os.getenv(env_name, '')
            if env_val:
                edit_api_keys[prov] = env_val
    available_providers = sorted(edit_api_keys.keys())

    from subjects import SUBJECT_DISPLAY_NAMES, resolve_subject_key as _resolve_subj
    rubric_bands_by_criterion = _bands_for_assignment(asn) if is_rubrics else {}
    assignment_has_canonical_subject = _resolve_subj(asn.subject or '') is not None

    # Linked bank (if assignment was created from or pushed to a bank). Cheap
    # lookup: bank items share the rubric/AK byte content with the assignment;
    # we don't currently store an explicit asn→bank FK, so fall back to a
    # title-based heuristic. If absent, the bank-push UI is hidden.
    linked_bank_id = None
    try:
        linked_bank = AssignmentBank.query.filter_by(
            title=asn.title or '', subject=asn.subject or ''
        ).first()
        if linked_bank:
            linked_bank_id = linked_bank.id
    except Exception:
        linked_bank_id = None

    resp = make_response(render_template('teacher_detail.html',
                           assignment=asn,
                           students=student_data,
                           all_providers=PROVIDERS,
                           available_providers=available_providers,
                           canonical_subjects=SUBJECT_DISPLAY_NAMES,
                           is_rubrics=is_rubrics,
                           eligible_remark_count=eligible_remark_count,
                           rubric_bands_by_criterion=rubric_bands_by_criterion,
                           assignment_has_canonical_subject=assignment_has_canonical_subject,
                           linked_bank_id=linked_bank_id,
                           bank_pushed_at_iso=(asn.bank_pushed_at.isoformat() if asn.bank_pushed_at else None)))
    # Prevent the browser/proxy from caching the score cells — a stale cache here
    # makes post-remark reloads show old marks even though the DB is fresh.
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@app.route('/teacher/assignment/<assignment_id>/download')
def teacher_download_all(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    # UP-10: the zip writer only needs `result_json` (via get_result) plus
    # `student_id` for the filename — defer all four blob columns.
    submissions = Submission.query_no_blobs().filter_by(assignment_id=assignment_id, status='done', is_final=True).all()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for sub in submissions:
            result = sub.get_result()
            if result.get('error'):
                continue
            student = Student.query.get(sub.student_id)
            if not student:
                continue
            pdf_bytes = generate_report_pdf(
                result, subject=asn.subject, app_title=get_app_title(),
                assignment_name=asn.title or '',
                student_name=student.name or '',
            )
            safe_name = student.name.replace('/', '_').replace('\\', '_')
            zf.writestr(f"{student.index_number}_{safe_name}_report.pdf", pdf_bytes)
    buf.seek(0)

    return send_file(buf, mimetype='application/zip', as_attachment=True,
                     download_name=f'{asn.classroom_code}_reports.zip')


# Background-job state for "Print All Reports". Each entry is a dict:
#   {'status': 'running'|'done'|'error', 'phase': str, 'current': int,
#    'total': int, 'pdf_bytes': bytes|None, 'error': str|None,
#    'created_at': float, 'asn_id': str, 'classroom_code': str}
# Entries are removed lazily — once the wrapper consumes the result via
# /result/<job_id>, we drop the bytes (keep a tiny stub so a duplicate
# fetch still gets a 410 instead of a 404 noise).
_PRINT_JOBS = {}
_PRINT_JOBS_LOCK = threading.Lock()
_PRINT_JOB_TTL_SECONDS = 30 * 60  # 30 min — long enough to print, short enough not to leak


def _print_job_set(job_id, **fields):
    with _PRINT_JOBS_LOCK:
        if job_id not in _PRINT_JOBS:
            return
        _PRINT_JOBS[job_id].update(fields)


def _print_job_get(job_id):
    with _PRINT_JOBS_LOCK:
        return dict(_PRINT_JOBS.get(job_id) or {})


def _print_job_evict_stale():
    """Drop jobs older than TTL. Cheap; called on each /start."""
    now = time.time()
    with _PRINT_JOBS_LOCK:
        for jid in list(_PRINT_JOBS.keys()):
            if now - _PRINT_JOBS[jid].get('created_at', now) > _PRINT_JOB_TTL_SECONDS:
                _PRINT_JOBS.pop(jid, None)


def _run_print_all_reports_job(app_obj, job_id, assignment_id):
    """Background worker: regenerate every done submission's PDF, merge
    into one with pypdf, store result bytes on the job. Updates progress
    on each student so the wrapper page can render a progress bar."""
    from pypdf import PdfReader, PdfWriter

    with app_obj.app_context():
        try:
            asn = Assignment.query.get(assignment_id)
            if not asn:
                _print_job_set(job_id, status='error', error='Assignment not found')
                return

            rows = (
                db.session.query(Submission, Student)
                .join(Student, Submission.student_id == Student.id)
                .filter(
                    Submission.assignment_id == assignment_id,
                    Submission.status == 'done',
                    Submission.is_final.is_(True),
                )
                .all()
            )

            def _sort_key(pair):
                student = pair[1]
                idx = (student.index_number or '').strip()
                return (0, int(idx)) if idx.isdigit() else (1, idx.lower(), student.name or '')
            rows = sorted(rows, key=_sort_key)

            total = len(rows)
            _print_job_set(job_id, total=total, phase='preparing', current=0)

            if total == 0:
                _print_job_set(job_id, status='error', error='No marked submissions to print.')
                return

            writer = PdfWriter()
            merged_count = 0
            for i, (sub, student) in enumerate(rows, start=1):
                _print_job_set(job_id, current=i, phase='preparing')
                try:
                    result = sub.get_result() or {}
                    if result.get('error'):
                        continue
                    pdf_bytes = generate_report_pdf(
                        result,
                        subject=asn.subject,
                        app_title=get_app_title(),
                        assignment_name=asn.title or '',
                        student_name=student.name or '',
                    )
                    reader = PdfReader(io.BytesIO(pdf_bytes))
                    pages = list(reader.pages)
                    for page in pages:
                        writer.add_page(page)
                    # Pad odd-page reports with a blank page so duplex
                    # printing keeps each student on their own sheets —
                    # without this, an odd-page student would land their
                    # last page on the front of a sheet whose back gets
                    # the next student's first page.
                    if len(pages) % 2 == 1:
                        try:
                            mb = pages[-1].mediabox
                            writer.add_blank_page(width=float(mb.width),
                                                  height=float(mb.height))
                        except Exception:
                            # A4 fallback (595 × 842 pt) if mediabox is unreadable.
                            writer.add_blank_page(width=595, height=842)
                    merged_count += 1
                except Exception as e:
                    logger.warning(
                        f"print-all-reports[{job_id}]: skipping student "
                        f"{student.name!r}: {e}"
                    )
                    continue

            if merged_count == 0:
                _print_job_set(job_id, status='error', error='No printable reports could be generated.')
                return

            _print_job_set(job_id, phase='merging')
            buf = io.BytesIO()
            writer.write(buf)
            buf.seek(0)

            _print_job_set(
                job_id, status='done', phase='done',
                pdf_bytes=buf.read(),
                merged_count=merged_count,
            )
        except Exception as e:
            logger.exception(f"print-all-reports[{job_id}] failed: {e}")
            _print_job_set(job_id, status='error', error=str(e))


@app.route('/teacher/assignment/<assignment_id>/print-all-reports')
def teacher_print_all_reports(assignment_id):
    """HTML wrapper. Loads the progress UI; the page's JS kicks off the
    background job, polls progress, then loads the merged PDF in an
    iframe and fires window.print()."""
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    return render_template(
        'print_all_reports.html',
        assignment=asn,
        app_title=get_app_title(),
    )


@app.route('/teacher/assignment/<assignment_id>/print-all-reports/start',
           methods=['POST'])
def teacher_print_all_reports_start(assignment_id):
    """Spawn a background worker that builds the merged PDF. Returns
    {job_id} immediately so the wrapper page can begin polling progress."""
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err

    _print_job_evict_stale()

    job_id = str(uuid.uuid4())
    with _PRINT_JOBS_LOCK:
        _PRINT_JOBS[job_id] = {
            'status': 'running',
            'phase': 'preparing',
            'current': 0,
            'total': 0,
            'pdf_bytes': None,
            'error': None,
            'created_at': time.time(),
            'asn_id': assignment_id,
            'classroom_code': asn.classroom_code,
        }

    threading.Thread(
        target=_run_print_all_reports_job,
        args=(app, job_id, assignment_id),
        daemon=True,
    ).start()

    return jsonify({'success': True, 'job_id': job_id})


@app.route('/teacher/assignment/<assignment_id>/print-all-reports/progress/<job_id>')
def teacher_print_all_reports_progress(assignment_id, job_id):
    """Polled by the wrapper page. Returns the job's current state:
       status: running | done | error
       phase:  preparing | merging | done
       current / total: per-student progress counter"""
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err

    job = _print_job_get(job_id)
    if not job or job.get('asn_id') != assignment_id:
        return jsonify({'success': False, 'error': 'Job not found'}), 404

    return jsonify({
        'success': True,
        'status': job.get('status'),
        'phase': job.get('phase'),
        'current': job.get('current', 0),
        'total': job.get('total', 0),
        'error': job.get('error'),
    })


@app.route('/teacher/assignment/<assignment_id>/print-all-reports/result/<job_id>')
def teacher_print_all_reports_result(assignment_id, job_id):
    """Serve the merged PDF. The wrapper page sets the iframe src to
    this URL once /progress reports status='done'. SAMEORIGIN override
    on X-Frame-Options lets the wrapper iframe embed it (the global
    default is DENY)."""
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err

    job = _print_job_get(job_id)
    if not job or job.get('asn_id') != assignment_id:
        return ('Job not found.', 404, {'Content-Type': 'text/plain; charset=utf-8'})
    if job.get('status') != 'done':
        return ('Job not ready.', 409, {'Content-Type': 'text/plain; charset=utf-8'})
    pdf_bytes = job.get('pdf_bytes')
    if not pdf_bytes:
        return ('Result expired.', 410, {'Content-Type': 'text/plain; charset=utf-8'})

    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = (
        f'inline; filename="{asn.classroom_code}_all_reports.pdf"'
    )
    # Override the global X-Frame-Options: DENY default so the wrapper
    # iframe can embed this same-origin PDF.
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return response


@app.route('/teacher/assignment/<assignment_id>/overview')
def teacher_overview(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    submissions = Submission.query.filter_by(assignment_id=assignment_id, status='done', is_final=True).all()

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

    pdf_bytes = generate_overview_pdf(
        student_results, subject=asn.subject, app_title=get_app_title(),
        assignment_name=asn.title or '',
    )

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=False,
        download_name=f'{asn.classroom_code}_overview.pdf'
    )


@app.route('/teacher/assignment/<assignment_id>/item-analysis')
def teacher_item_analysis(assignment_id):
    """Visual per-criterion item analysis for rubrics assignments.

    Aggregates band counts, lowest-band student names, and common-gap
    feedback so a teacher can see at a glance where the class struggles.

    Short-answer assignments fall through to the existing PDF overview
    — the visual treatment is rubrics-specific.
    """
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err

    if getattr(asn, 'assign_type', 'short_answer') != 'rubrics':
        return redirect(url_for('teacher_overview', assignment_id=assignment_id))

    from perf import light_submission_query
    subs = (
        light_submission_query()
        .filter_by(assignment_id=assignment_id, status='done', is_final=True)
        .all()
    )
    student_ids = {s.student_id for s in subs if s.student_id}
    students = {
        st.id: st for st in Student.query.filter(Student.id.in_(student_ids)).all()
    } if student_ids else {}

    total_students = (
        Student.query.filter_by(class_id=asn.class_id).count()
        if asn.class_id else
        Student.query.filter_by(assignment_id=assignment_id).count()
    )
    done_count = len(subs)

    # Aggregate per-criterion data. The keyed dict preserves criterion
    # ordering by first appearance across submissions; rubrics keep the
    # same criterion ordering across all marked scripts, so this is
    # stable.
    from collections import OrderedDict
    crit_data = OrderedDict()
    total_awarded = 0
    total_possible = 0

    band_re = re.compile(r'Band\s+(\d+)', re.IGNORECASE)

    for sub in subs:
        result = sub.get_result() or {}
        if result.get('error'):
            continue
        student = students.get(sub.student_id)
        student_name = student.name if student else 'Unknown'
        student_index = student.index_number if student else ''

        for q in result.get('questions') or []:
            crit = (q.get('criterion_name') or '').strip()
            if not crit:
                continue
            band_raw = (q.get('band') or '').strip()
            m = band_re.search(band_raw)
            if not m:
                continue
            band_num = int(m.group(1))
            marks_awarded = q.get('marks_awarded')
            marks_total = q.get('marks_total')
            feedback = (q.get('feedback') or '').strip()

            data = crit_data.setdefault(crit, {
                'name': crit,
                # Bands populated on demand — rubrics range from 3 to 6
                # bands in practice, so the global max is computed once
                # below rather than hard-coded.
                'band_counts': {},
                'band_students': {},
                'feedback_by_band': {},
                'marks_awarded_sum': 0,
                'marks_awarded_count': 0,
                'marks_total': None,
                'band_sum': 0,
                'total': 0,
            })
            data['band_counts'][band_num] = data['band_counts'].get(band_num, 0) + 1
            data['band_students'].setdefault(band_num, [])
            data['feedback_by_band'].setdefault(band_num, [])
            data['band_students'][band_num].append({
                'name': student_name,
                'index': student_index,
                'submission_id': sub.id,
            })
            if feedback:
                data['feedback_by_band'][band_num].append(feedback)
            if marks_awarded is not None:
                data['marks_awarded_sum'] += marks_awarded
                data['marks_awarded_count'] += 1
                total_awarded += marks_awarded
            if marks_total is not None:
                data['marks_total'] = marks_total
                total_possible += marks_total
            data['band_sum'] += band_num
            data['total'] += 1

    # Cluster feedback by a normalized 50-char prefix so paraphrased
    # variants of the same complaint collapse into one row.
    def _norm(s):
        s = re.sub(r'\s+', ' ', s.lower()).strip()
        s = re.sub(r'[^a-z0-9 ]', '', s)
        return s[:50]

    # Detect the band scale this assignment actually uses. Rubrics in
    # real use range from 3 to 6 bands; one fixed scale across the
    # whole page keeps every criterion's bar the same length so the
    # eye can compare them at a glance.
    global_max_band = 1
    for data in crit_data.values():
        if data['band_counts']:
            global_max_band = max(global_max_band, max(data['band_counts'].keys()))

    def _ramp_slot(band_num, max_band):
        """1-indexed slot on the red→green ramp (1=red, 5+=green).

        Spreads the ramp across whatever band scale the rubric uses so
        a 4-band rubric still reads red→green end-to-end. Identity for
        a 5-band rubric.
        """
        if max_band <= 1:
            return 5
        target = max(5, max_band)
        return 1 + round((band_num - 1) / (max_band - 1) * (target - 1))

    criteria = []
    for crit_name, data in crit_data.items():
        # Show the full ramp 1..global_max_band even if a band is empty
        # so the teacher can see "no one reached the top band" at a
        # glance — and every criterion's bar has the same number of
        # segments for easy side-by-side comparison.
        segments = []
        for bn in range(1, global_max_band + 1):
            count = data['band_counts'].get(bn, 0)
            names = [s['name'] for s in data['band_students'].get(bn, [])]
            segments.append({
                'num': bn,
                'count': count,
                'names': names,
                'slot': _ramp_slot(bn, global_max_band),
            })

        lowest_band_num = None
        for bn in range(1, global_max_band + 1):
            if data['band_counts'].get(bn, 0) > 0:
                lowest_band_num = bn
                break

        # Surface a "needs attention" block whenever the lowest occupied
        # band sits in the bottom 40% of the scale. For a 5-band rubric
        # this is B1–B2; for a 4-band rubric it's B1; for 6 it's B1–B2.
        attention_threshold = max(2, round(global_max_band * 0.4))
        lowest_band_students = []
        lowest_band_slot = None
        if lowest_band_num is not None and lowest_band_num <= attention_threshold:
            lowest_band_students = data['band_students'].get(lowest_band_num, [])
            lowest_band_slot = _ramp_slot(lowest_band_num, global_max_band)

        # Common gaps: pool feedback from bands in the bottom 40% of the
        # scale, then cluster by 50-char prefix.
        gap_pool = []
        for bn in range(1, attention_threshold + 1):
            gap_pool.extend(data['feedback_by_band'].get(bn, []))
        clusters = {}
        for fb in gap_pool:
            key = _norm(fb)
            if not key:
                continue
            entry = clusters.setdefault(key, {'text': fb, 'count': 0})
            entry['count'] += 1
            if len(fb) > len(entry['text']):
                entry['text'] = fb
        gaps_sorted = sorted(clusters.values(), key=lambda g: -g['count'])
        common_gaps = [g for g in gaps_sorted if g['count'] >= 2][:4]
        if not common_gaps:
            common_gaps = gaps_sorted[:3]

        total = data['total'] or 1
        avg_band = round(data['band_sum'] / total, 1)
        avg_marks = (
            round(data['marks_awarded_sum'] / data['marks_awarded_count'], 1)
            if data['marks_awarded_count'] > 0 else None
        )

        criteria.append({
            'name': crit_name,
            'total': data['total'],
            'avg_band': avg_band,
            'avg_marks': avg_marks,
            'marks_total': data['marks_total'],
            'band_segments': segments,
            'max_band': global_max_band,
            'lowest_band_num': lowest_band_num,
            'lowest_band_slot': lowest_band_slot,
            'lowest_band_students': lowest_band_students,
            'common_gaps': common_gaps,
        })

    criteria.sort(key=lambda c: (c['avg_band'], c['name']))

    avg_pct = round(total_awarded / total_possible * 100, 1) if total_possible > 0 else None
    low_band_student_ids = set()
    for c in criteria:
        if c['lowest_band_num'] is not None and c['lowest_band_students']:
            for s in c['lowest_band_students']:
                low_band_student_ids.add(s['submission_id'])
    low_band_count = len(low_band_student_ids)

    return render_template(
        'item_analysis.html',
        assignment=asn,
        total_students=total_students,
        done_count=done_count,
        avg_pct=avg_pct,
        low_band_count=low_band_count,
        num_criteria=len(criteria),
        criteria=criteria,
    )


@app.route('/teacher/assignment/<assignment_id>/exemplars')
def teacher_exemplars_page(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err

    total = Student.query.filter_by(class_id=asn.class_id).count()
    done_count = Submission.query.filter_by(
        assignment_id=assignment_id, status='done', is_final=True,
    ).count()
    gate_pct = int((done_count / total) * 100) if total > 0 else 0
    can_generate = total > 0 and gate_pct >= 20

    analysis = None
    student_names = {}
    if asn.exemplar_analysis_json:
        try:
            analysis = json.loads(asn.exemplar_analysis_json)
        except Exception:
            analysis = None
    area_rates = []
    area_order = []
    if analysis and isinstance(analysis.get('areas'), list):
        ids = set()
        for area in analysis['areas']:
            for key in ('needs_work_examples', 'strong_examples'):
                for ex in area.get(key) or []:
                    if isinstance(ex.get('submission_id'), int):
                        ids.add(ex['submission_id'])
        if ids:
            rows = (
                db.session.query(Submission, Student)
                .join(Student, Submission.student_id == Student.id)
                .filter(Submission.id.in_(ids))
                .all()
            )
            student_names = {sub.id: st.name for (sub, st) in rows}
        area_rates = _compute_area_wrong_rates(asn.id, analysis['areas'])
        area_order = _area_display_order(area_rates)

    return render_template(
        'exemplars.html',
        assignment=asn,
        total_students=total,
        done_count=done_count,
        gate_pct=gate_pct,
        can_generate=can_generate,
        analysis=analysis,
        analyzed_at=asn.exemplar_analyzed_at,
        student_names=student_names,
        area_rates=area_rates,
        area_order=area_order,
    )


@app.route('/teacher/assignment/<assignment_id>/exemplars/generate', methods=['POST'])
def teacher_exemplars_generate(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err

    total = Student.query.filter_by(class_id=asn.class_id).count()
    done_subs = (
        Submission.query
        .filter_by(assignment_id=assignment_id, status='done', is_final=True)
        .all()
    )
    if total == 0 or (len(done_subs) / total) * 100 < 20:
        return jsonify({'success': False, 'error': 'At least 20% of the class must have done submissions.'}), 400

    # Cap to 40 submissions, sampled evenly across mark buckets if we have more.
    MAX_SUBS = 40
    selected = done_subs
    if len(done_subs) > MAX_SUBS:
        def _score(sub):
            r = sub.get_result() or {}
            qs = r.get('questions') or []
            awarded = sum((q.get('marks_awarded') or 0) for q in qs)
            total_m = sum((q.get('marks_total') or 0) for q in qs)
            return (awarded / total_m) if total_m > 0 else 0.5
        scored = sorted(done_subs, key=_score)
        step = len(scored) / MAX_SUBS
        selected = [scored[int(i * step)] for i in range(MAX_SUBS)]

    # Build per-submission payload for the AI.
    student_by_id = {
        st.id: st.name for st in Student.query.filter_by(class_id=asn.class_id).all()
    }
    submissions_data = []
    valid_subs = {}
    for sub in selected:
        result = sub.get_result() or {}
        pages = sub.get_script_pages() or []
        submissions_data.append({
            'submission_id': sub.id,
            'student_name': student_by_id.get(sub.student_id, ''),
            'marks_awarded': sum((q.get('marks_awarded') or 0) for q in (result.get('questions') or [])) or None,
            'marks_total': sum((q.get('marks_total') or 0) for q in (result.get('questions') or [])) or None,
            'questions': result.get('questions') or [],
            'overall_feedback': result.get('overall_feedback') or '',
            'page_count': len(pages),
        })
        valid_subs[sub.id] = len(pages)

    try:
        parsed = generate_exemplar_analysis(
            provider=asn.provider,
            model=asn.model,
            session_keys=_resolve_api_keys(asn),
            subject=asn.subject or '',
            submissions_data=submissions_data,
        )
    except Exception as e:
        logger.exception("Exemplar analysis failed for assignment %s", assignment_id)
        return jsonify({'success': False, 'error': f'AI analysis failed: {e}'}), 502

    # Validate + sanitise AI output.
    areas_in = parsed.get('areas') or []
    areas_out = []
    for area in areas_in:
        if not isinstance(area, dict):
            continue
        def _clean_examples(lst):
            out = []
            seen = set()
            for ex in (lst or []):
                if not isinstance(ex, dict):
                    continue
                sid = ex.get('submission_id')
                pidx = ex.get('page_index')
                note = (ex.get('note') or '').strip()
                if not isinstance(sid, int) or sid not in valid_subs:
                    continue
                if not isinstance(pidx, int) or pidx < 0 or pidx >= valid_subs[sid]:
                    continue
                if sid in seen:
                    continue
                seen.add(sid)
                out.append({'submission_id': sid, 'page_index': pidx, 'note': note})
                if len(out) >= 2:
                    break
            return out
        needs = _clean_examples(area.get('needs_work_examples'))
        strong = _clean_examples(area.get('strong_examples'))
        if len(needs) < 2 or len(strong) < 2:
            continue
        areas_out.append({
            'question_part': (area.get('question_part') or '').strip() or 'Area',
            'label': (area.get('label') or '').strip() or 'Discussion area',
            'description': (area.get('description') or '').strip(),
            'needs_work_examples': needs,
            'strong_examples': strong,
        })

    if not areas_out:
        return jsonify({'success': False, 'error': 'AI analysis could not produce valid exemplars. Try regenerating.'}), 502

    sanitised = {'areas': areas_out}
    asn.exemplar_analysis_json = json.dumps(sanitised, ensure_ascii=False)
    asn.exemplar_analyzed_at = datetime.now(timezone.utc)

    # Mark every prior log row for this assignment as superseded, then
    # append the new latest row. This way the history is preserved
    # (audit / drift study) while clustering rollups can simply filter
    # `superseded_at IS NULL` to count only one analysis per assignment.
    from db import ExemplarAnalysisLog
    ExemplarAnalysisLog.query.filter_by(
        assignment_id=asn.id, superseded_at=None,
    ).update({'superseded_at': asn.exemplar_analyzed_at}, synchronize_session=False)
    db.session.add(ExemplarAnalysisLog(
        assignment_id=asn.id,
        submissions_count=len(submissions_data),
        roster_size=total,
        areas_json=json.dumps(sanitised, ensure_ascii=False),
        created_at=asn.exemplar_analyzed_at,
    ))
    db.session.commit()

    # Build student-name map for the response.
    ids = set()
    for area in areas_out:
        for ex in area['needs_work_examples'] + area['strong_examples']:
            ids.add(ex['submission_id'])
    rows = (
        db.session.query(Submission, Student)
        .join(Student, Submission.student_id == Student.id)
        .filter(Submission.id.in_(ids))
        .all()
    )
    student_names = {sub.id: st.name for (sub, st) in rows}

    area_rates = _compute_area_wrong_rates(asn.id, areas_out)
    area_order = _area_display_order(area_rates)

    return jsonify({
        'success': True,
        'analysis': sanitised,
        'student_names': student_names,
        'analyzed_at': asn.exemplar_analyzed_at.isoformat(),
        'area_rates': area_rates,
        'area_order': area_order,
    })


@app.route('/teacher/assignment/<assignment_id>/submit/<int:student_id>', methods=['POST'])
def teacher_submit_for_student(assignment_id, student_id):
    """Teacher uploads a script on behalf of a student."""
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    student = Student.query.get(int(student_id))
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
    if len(script_files) > 20:
        return jsonify({'success': False, 'error': 'Maximum 20 files'}), 400

    script_pages = [f.read() for f in script_files if f.filename]
    err = _validate_upload_blobs(script_pages, label='script')
    if err:
        return err

    sub, err = _prepare_new_submission(student, asn)
    if err:
        return jsonify({'success': False, 'error': err}), 400
    sub.script_bytes = script_pages[0] if script_pages else None
    sub.status = 'pending'
    sub.set_script_pages(script_pages)
    db.session.add(sub)
    db.session.commit()

    _submit_marking(_run_submission_marking, app, sub.id, assignment_id)

    return jsonify({'success': True})


@app.route('/teacher/assignment/<assignment_id>/submission/<int:submission_id>/set-final', methods=['POST'])
def teacher_set_final(assignment_id, submission_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Invalid submission'}), 400
    Submission.query.filter_by(
        student_id=sub.student_id,
        assignment_id=assignment_id,
        is_final=True,
    ).update({'is_final': False})
    sub.is_final = True
    db.session.commit()
    return jsonify({'success': True})


@app.route('/teacher/assignment/<assignment_id>/submission/<int:submission_id>/delete', methods=['POST'])
def teacher_delete_draft(assignment_id, submission_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Invalid submission'}), 400
    was_final = sub.is_final
    student_id = sub.student_id
    db.session.delete(sub)
    db.session.flush()
    if was_final:
        latest = Submission.query.filter_by(
            student_id=student_id,
            assignment_id=assignment_id,
        ).order_by(Submission.draft_number.desc()).first()
        if latest:
            latest.is_final = True
    db.session.commit()
    return jsonify({'success': True})


@app.route('/teacher/assignment/<assignment_id>/student/<int:student_id>/drafts')
def teacher_student_drafts(assignment_id, student_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    subs = Submission.query.filter_by(
        student_id=student_id,
        assignment_id=assignment_id,
    ).order_by(Submission.draft_number).all()
    return jsonify({
        'success': True,
        'drafts': [
            {
                'id': s.id,
                'draft_number': s.draft_number,
                'is_final': s.is_final,
                'status': s.status,
                'submitted_at': s.submitted_at.strftime('%d %b %I:%M%p') if s.submitted_at else None,
            }
            for s in subs
        ],
    })


def _detect_mime(data):
    """Infer MIME type from the first bytes of a blob. Falls back to octet-stream.

    UP-17: must be magic-byte based, not extension based — student uploads
    arrive from iOS/Android and renaming .jpg → .pdf is trivial. Keep this
    list in lock-step with `_ALLOWED_UPLOAD_MIMES`.
    """
    if not data:
        return 'application/octet-stream'
    b = bytes(data[:16])
    if b.startswith(b'%PDF'):
        return 'application/pdf'
    if b.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if b.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if b[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if b.startswith(b'RIFF') and b[8:12] == b'WEBP':
        return 'image/webp'
    # HEIC/HEIF: ISO-BMFF with `ftyp` box. The brand at bytes 8:12 tells us
    # heic vs heif. iOS photos default to .heic; many "heif" brands also
    # work via pillow-heif.
    if len(b) >= 12 and b[4:8] == b'ftyp':
        brand = b[8:12]
        if brand in (b'heic', b'heix', b'hevc', b'hevx', b'heim', b'heis', b'hevm', b'hevs', b'mif1', b'msf1'):
            return 'image/heic'
        if brand == b'heif':
            return 'image/heif'
    return 'application/octet-stream'


# UP-17: allow-list for student/teacher script uploads. PDFs and the
# image formats Pillow/pillow-heif can decode. Anything else (Word docs,
# zip-bombs disguised with a .pdf rename, Pillow-vulnerable old formats
# we don't need) is rejected at the boundary.
_ALLOWED_UPLOAD_MIMES = frozenset({
    'application/pdf',
    'image/jpeg',
    'image/png',
    'image/heic',
    'image/heif',
})


def _validate_upload_blobs(blobs, label='script'):
    """Validate a list of uploaded file bytes against `_ALLOWED_UPLOAD_MIMES`.

    Returns `None` on success, or a Flask JSON response tuple on failure.
    Use at every external-facing upload entry point (single, bulk,
    student-submit, demo).
    """
    for idx, data in enumerate(blobs or ()):
        if not data:
            continue
        mime = _detect_mime(data)
        if mime not in _ALLOWED_UPLOAD_MIMES:
            return jsonify({
                'success': False,
                'error': f'Unsupported file type for {label} page {idx + 1}. Accepted: PDF, JPG, PNG, HEIC.',
            }), 415
    return None


def _build_text_edit_meta(submission_id, teacher_id=None):
    """Per (criterion_id, field), the latest teacher version + whether an
    active feedback_edit (calibration bank) row exists, plus its edit_id
    so the client can wire the Retire link.

    When `teacher_id` is provided, the `calibrated` flag and `edit_id`
    reflect only that teacher's active edits — important in department
    mode where several teachers may calibrate the same submission.

    Shape: {criterion_id: {field: {'version': N, 'calibrated': bool,
                                    'edit_id': N (when calibrated)}}}

    Best-effort — wrapped in try/except so a partial schema or other DB
    hiccup never blocks the feedback modal from rendering.
    """
    from db import FeedbackLog, FeedbackEdit
    from sqlalchemy import func as _func

    out = {}
    try:
        log_rows = db.session.query(
            FeedbackLog.criterion_id,
            FeedbackLog.field,
            _func.max(FeedbackLog.version).label('latest_version'),
        ).filter(
            FeedbackLog.submission_id == submission_id,
            FeedbackLog.author_type == 'teacher',
        ).group_by(
            FeedbackLog.criterion_id, FeedbackLog.field,
        ).all()

        edit_q = FeedbackEdit.query.filter_by(
            submission_id=submission_id,
            active=True,
        )
        if teacher_id:
            edit_q = edit_q.filter_by(edited_by=teacher_id)
        active_edits = edit_q.all()
        active_by_key = {(e.criterion_id, e.field): e for e in active_edits}

        for row in log_rows:
            ed = active_by_key.get((row.criterion_id, row.field))
            entry = {
                'version': int(row.latest_version),
                'calibrated': ed is not None,
            }
            if ed:
                entry['edit_id'] = ed.id
                entry['amend_answer_key'] = bool(ed.amend_answer_key)
            out.setdefault(row.criterion_id, {})[row.field] = entry

        # Calibration rows without a corresponding FeedbackLog (e.g. legacy
        # data from staging which didn't write FeedbackLog) — emit them too
        # so the indicator still renders for those.
        for (cid, fld), ed in active_by_key.items():
            if cid in out and fld in out[cid]:
                continue
            out.setdefault(cid, {})[fld] = {
                'version': 0,
                'calibrated': True,
                'edit_id': ed.id,
                'amend_answer_key': bool(ed.amend_answer_key),
            }
    except Exception as _meta_err:
        logger.warning(f"text_edit_meta lookup failed for sub {submission_id}: {_meta_err}")
        try:
            db.session.rollback()
        except Exception:
            pass
        return {}
    return out


@app.route('/teacher/assignment/<assignment_id>/submission/<int:submission_id>/result')
def teacher_submission_result(assignment_id, submission_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Invalid submission'}), 400
    teacher = _current_teacher()
    teacher_id = teacher.id if teacher else None
    # Available categories for the inline "Mistake Category" dropdown in
    # the per-question card. Strict (non-display) variant — teachers pick
    # from canonical taxonomy keys only; deprecated legacy keys never
    # appear as a choice for new categorisations.
    from config.mistake_themes import themes_for, themes_meta_list
    return jsonify({
        'success': True,
        'result': sub.get_result(),
        'status': sub.status,
        'draft_number': sub.draft_number,
        'is_final': sub.is_final,
        'text_edit_meta': _build_text_edit_meta(sub.id, teacher_id=teacher_id),
        'current_teacher_id': teacher_id,
        'available_themes': themes_meta_list(themes_for(asn.subject or '')),
    })


def _process_text_edit(submission, criterion_id, field, edited_text,
                      teacher_id, assignment, calibrate, current_text):
    """Log a teacher edit to feedback_log; if `calibrate`, also (a) deactivate
    any prior active feedback_edit row for (this teacher, assignment, criterion,
    field) and (b) insert a new feedback_edit row.

    Returns {'version': N, 'calibrated': bool} on a real change, or None when
    edited_text equals current_text (no-op).

    Caller is responsible for db.session.commit().
    """
    from db import FeedbackLog, FeedbackEdit
    from sqlalchemy import func as _func

    if (edited_text or '') == (current_text or ''):
        return None  # no change → no log row, no edit row

    max_v = db.session.query(_func.max(FeedbackLog.version)).filter(
        FeedbackLog.submission_id == submission.id,
        FeedbackLog.criterion_id == criterion_id,
        FeedbackLog.field == field,
    ).scalar() or 0
    new_version = max_v + 1

    db.session.add(FeedbackLog(
        submission_id=submission.id,
        criterion_id=criterion_id,
        field=field,
        version=new_version,
        feedback_text=edited_text or '',
        author_type='teacher',
        author_id=teacher_id,
    ))

    calibrated = False
    if calibrate:
        # Read or back-fill the v1 (AI original) row. Legacy submissions
        # marked before Task 2 was deployed may lack one; back-fill from
        # current_text (the best AI-original we still have visible).
        v1 = FeedbackLog.query.filter_by(
            submission_id=submission.id,
            criterion_id=criterion_id,
            field=field,
            version=1,
        ).first()
        if not v1:
            v1 = FeedbackLog(
                submission_id=submission.id,
                criterion_id=criterion_id,
                field=field,
                version=1,
                feedback_text=current_text or '',
                author_type='ai',
                author_id=None,
            )
            db.session.add(v1)
            db.session.flush()  # so v1.feedback_text is queryable below
        original_text = v1.feedback_text or (current_text or '')

        # One active bank row per (teacher, assignment, criterion, field).
        FeedbackEdit.query.filter_by(
            edited_by=teacher_id,
            assignment_id=assignment.id,
            criterion_id=criterion_id,
            field=field,
            active=True,
        ).update({'active': False})

        # Look up the current criterion's mistake_type from result_json (may be
        # NULL if categorisation hasn't run for this submission).
        mistake_type = None
        result_for_theme = submission.get_result() or {}
        for q in (result_for_theme.get('questions') or []):
            if str(q.get('question_num')) == criterion_id:
                mistake_type = q.get('mistake_type')
                break

        from ai_marking import _rubric_version_hash
        db.session.add(FeedbackEdit(
            submission_id=submission.id,
            criterion_id=criterion_id,
            field=field,
            original_text=original_text,
            edited_text=edited_text or '',
            edited_by=teacher_id,
            mistake_type=mistake_type,
            assignment_id=assignment.id,
            rubric_version=_rubric_version_hash(assignment),
            active=True,
        ))
        calibrated = True

    return {'version': new_version, 'calibrated': calibrated}


@app.route('/teacher/assignment/<assignment_id>/submission/<int:submission_id>/result', methods=['PATCH'])
def teacher_submission_result_patch(assignment_id, submission_id):
    """Teacher edits AI-generated feedback. Overwrites fields in sub.result_json.
    Editable: overall_feedback + per-question marks_awarded/marks_total/feedback/
    improvement/status. Per-question entries may carry calibrate=true to also
    write a feedback_edit row + trigger propagation candidate detection.
    """
    from db import FeedbackEdit
    from ai_marking import _rubric_version_hash

    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Invalid submission'}), 400

    # The authoring teacher for log/edit rows. _current_teacher() returns the
    # logged-in Teacher in dept mode; in legacy single-teacher mode it may
    # return None — fall back to the assignment's owning teacher_id.
    teacher = _current_teacher()
    editor_id = teacher.id if teacher else (asn.teacher_id or None)

    payload = request.get_json(silent=True) or {}
    edit_meta = {}
    result = sub.get_result() or {}
    questions = result.get('questions') or []

    if 'overall_feedback' in payload:
        val = payload.get('overall_feedback')
        if val is not None and not isinstance(val, str):
            return jsonify({'success': False, 'error': 'overall_feedback must be a string'}), 400
        result['overall_feedback'] = (val or '').strip()

    edit_meta = {}            # per-criterion summary returned to the client
    fresh_calibration_edits = []  # FeedbackEdit rows written this request
    calibration_write_errors = []  # surfaced to client so the toast can warn

    incoming_qs = payload.get('questions')
    if incoming_qs is not None:
        if not isinstance(incoming_qs, list):
            return jsonify({'success': False, 'error': 'questions must be a list'}), 400
        by_num = {}
        for idx, q in enumerate(questions):
            qn = q.get('question_num')
            key = str(qn) if qn is not None else str(idx)
            by_num[key] = q
        for edit in incoming_qs:
            if not isinstance(edit, dict):
                continue
            qn = edit.get('question_num')
            if qn is None:
                continue
            target = by_num.get(str(qn))
            if target is None:
                continue

            # Snapshot pre-edit text BEFORE applying writes, so the
            # _process_text_edit helper sees the actual diff and the
            # calibration row can pin original_text correctly even if the
            # same criterion is edited multiple times.
            old_text_by_field = {
                'feedback': (target.get('feedback') or '').strip(),
                'improvement': (target.get('improvement') or '').strip(),
            }
            # Validate text length before applying in-place updates.
            for _field in ('feedback', 'improvement'):
                if _field in edit:
                    _val = edit.get(_field) or ''
                    if len(_val) > 2000:
                        return jsonify({'success': False, 'error': f'{_field} too long (max 2000 chars)'}), 400

            # Mark this question teacher_edited so propagation never overwrites it.
            if 'feedback' in edit or 'improvement' in edit:
                target['feedback_source'] = 'teacher_edit'

            for field in ('feedback', 'improvement'):
                if field in edit:
                    v = edit[field]
                    if v is not None and not isinstance(v, str):
                        return jsonify({'success': False, 'error': f'{field} must be a string'}), 400
                    target[field] = (v or '').strip()
            # Rubrics-redesign editable string fields. Plain string writes —
            # no calibration plumbing yet (these fields aren't on the
            # short_answer track that calibration was built around).
            for field in (
                'current_band_oneliner',
                'next_band_oneliner',
                'evidence_quote',
                'improvement_target',
                'improvement_rewrite',
                'improvement_target_2',
                'improvement_rewrite_2',
                'maintain_advice',
            ):
                if field in edit:
                    v = edit[field]
                    if v is not None and not isinstance(v, str):
                        return jsonify({'success': False, 'error': f'{field} must be a string'}), 400
                    if v is not None and len(v) > 2000:
                        return jsonify({'success': False, 'error': f'{field} too long (max 2000 chars)'}), 400
                    target[field] = (v or '').strip()
            # Rubrics band override: capture band_ai_original on the FIRST
            # change so the modal can show "AI placed at Band X" and the
            # "Re-mark for tailored text" link can fire if the teacher
            # wants the AI to redo descriptors at the new band.
            if 'band' in edit:
                v = edit['band']
                if v is not None and not isinstance(v, str):
                    return jsonify({'success': False, 'error': 'band must be a string'}), 400
                new_band = (v or '').strip()
                current_band = (target.get('band') or '').strip()
                if new_band and new_band != current_band:
                    if not target.get('band_ai_original'):
                        target['band_ai_original'] = current_band
                    target['band'] = new_band
            for field in ('marks_awarded', 'marks_total'):
                if field in edit:
                    v = edit[field]
                    if v is None or v == '':
                        target[field] = None
                        continue
                    try:
                        target[field] = float(v) if isinstance(v, float) or (isinstance(v, str) and '.' in v) else int(v)
                    except (TypeError, ValueError):
                        return jsonify({'success': False, 'error': f'{field} must be a number'}), 400
            if 'status' in edit:
                v = edit['status']
                if v in ('correct', 'partially_correct', 'incorrect'):
                    target['status'] = v
                elif v is not None:
                    return jsonify({'success': False, 'error': 'status must be correct | partially_correct | incorrect'}), 400

            # Categorisation correction — feed_forward_beta inline editable
            # category line above the feedback textarea. Validate mistake_type
            # against THEMES; on valid change, update the in-memory result
            # AND write a CategorisationCorrection audit row IF the
            # assignment's subject is a canonical-taxonomy entry. Freeform
            # subjects skip the audit-row write — they're intra-assignment-
            # only, so cross-assignment few-shot learning never sees them.
            # The corrected mistake_type still flows into the FeedbackEdit
            # row below if the teacher also calibrates.
            if 'mistake_type' in edit or 'specific_label' in edit:
                try:
                    from config.mistake_themes import themes_for as _themes_for
                    from db import CategorisationCorrection
                    from subjects import is_canonical_subject as _is_canonical
                    _THEMES = _themes_for(asn.subject or '')
                    proposed_tk = (edit.get('mistake_type') or '').strip() or None
                    proposed_label_raw = edit.get('specific_label')
                    proposed_label = (proposed_label_raw or '').strip() or None
                    if proposed_label and len(proposed_label) > 80:
                        proposed_label = proposed_label[:80]
                    current_tk = target.get('mistake_type')
                    current_label = target.get('specific_label')
                    if (proposed_tk
                            and proposed_tk in _THEMES
                            and (proposed_tk != current_tk
                                 or (proposed_label or '') != (current_label or ''))):
                        target['mistake_type'] = proposed_tk
                        target['specific_label'] = proposed_label or ''
                        target['mistake_type_corrected'] = True
                        if _is_canonical(asn.subject or ''):
                            try:
                                db.session.add(CategorisationCorrection(
                                    submission_id=sub.id,
                                    criterion_id=str(qn),
                                    field='mistake_type',
                                    original_mistake_type=current_tk,
                                    original_specific_label=current_label,
                                    corrected_mistake_type=proposed_tk,
                                    corrected_specific_label=proposed_label,
                                    corrected_by=editor_id,
                                    assignment_id=asn.id,
                                ))
                            except Exception as cat_err:
                                logger.warning(
                                    f"CategorisationCorrection insert failed "
                                    f"(sub={sub.id}, crit={qn}): {cat_err}")
                except Exception as outer_cat_err:
                    logger.warning(f"categorisation correction handling failed: {outer_cat_err}")

            # Calibration bank + audit log:
            #   • Always log text changes to FeedbackLog (audit trail).
            #   • amend_answer_key + new text → write a FeedbackEdit row.
            #   • Idempotent re-affirm: text unchanged + prior matches +
            #     flag unchanged → no new row, but emit edit_meta so the
            #     indicator still shows.
            #   • Checkbox unticked → deactivate any prior FeedbackEdit
            #     row so unchecking actually removes it from the bank.

            # Single-intent calibration (spec 2026-05-16): only amend_answer_key.
            amend_flag = bool(edit.get('amend_answer_key'))
            cal_flag = amend_flag

            if editor_id and ('feedback' in edit or 'improvement' in edit):
                rubric_hash = _rubric_version_hash(asn)
                for _field in ('feedback', 'improvement'):
                    if _field not in edit:
                        continue
                    new_text = (edit.get(_field) or '').strip()
                    old_text = old_text_by_field.get(_field, '')

                    sp = db.session.begin_nested()
                    try:
                        prior = (FeedbackEdit.query
                                 .filter_by(edited_by=editor_id,
                                            assignment_id=asn.id,
                                            criterion_id=str(qn),
                                            field=_field,
                                            active=True)
                                 .order_by(FeedbackEdit.id.desc())
                                 .first())

                        # Always log to FeedbackLog when the text actually
                        # changed, regardless of intent flags — audit trail.
                        log_meta = None
                        if new_text != old_text:
                            log_meta = _process_text_edit(
                                submission=sub,
                                criterion_id=str(qn),
                                field=_field,
                                edited_text=new_text,
                                teacher_id=editor_id,
                                assignment=asn,
                                calibrate=False,  # FeedbackEdit handled separately below
                                current_text=old_text,
                            )

                        # Uncheck path: deactivate any prior bank row.
                        if not cal_flag:
                            if prior:
                                prior.active = False
                                db.session.flush()
                            entry = {'amend_answer_key': False,
                                     'calibrated': False}
                            if log_meta and log_meta.get('version'):
                                entry['version'] = log_meta['version']
                            edit_meta.setdefault(str(qn), {})[_field] = entry
                            sp.commit()
                            continue

                        # Idempotent re-affirm: text unchanged AND prior text
                        # matches AND amend_answer_key flag matches the prior
                        # row. Otherwise fall through to the "write new row"
                        # path which deactivates the prior row.
                        prior_amend = bool(prior.amend_answer_key) if prior else False
                        if (new_text == old_text and prior
                                and (prior.edited_text or '') == new_text
                                and prior_amend == amend_flag):
                            entry = {
                                'edit_id': prior.id,
                                'amend_answer_key': prior_amend,
                                'calibrated': True,  # back-compat
                            }
                            edit_meta.setdefault(str(qn), {})[_field] = entry
                            sp.rollback()
                            continue

                        # Write a new FeedbackEdit row. Anchor original_text
                        # to the AI original (prior row's original_text if it
                        # exists; pre-edit text otherwise).
                        original_text = (prior.original_text if prior else old_text) or old_text
                        if prior:
                            prior.active = False
                        new_edit = FeedbackEdit(
                            submission_id=sub.id,
                            criterion_id=str(qn),
                            field=_field,
                            original_text=original_text,
                            edited_text=new_text,
                            edited_by=editor_id,
                            assignment_id=asn.id,
                            rubric_version=rubric_hash,
                            mistake_type=target.get('mistake_type'),
                            amend_answer_key=amend_flag,
                            active=True,
                            propagation_status='none',
                        )
                        db.session.add(new_edit)
                        db.session.flush()
                        sp.commit()
                        fresh_calibration_edits.append(new_edit)
                        entry = {
                            'edit_id': new_edit.id,
                            'amend_answer_key': amend_flag,
                            'calibrated': True,  # back-compat with old JS
                        }
                        if log_meta and log_meta.get('version'):
                            entry['version'] = log_meta['version']
                        edit_meta.setdefault(str(qn), {})[_field] = entry
                    except Exception:
                        sp.rollback()
                        logger.exception(
                            "feedback_edit write failed (sub=%s, crit=%s, field=%s)",
                            sub.id, qn, _field,
                        )
                        calibration_write_errors.append(
                            f"feedback_edit write failed for {_field}"
                        )
                        target[_field] = new_text  # ensure in-memory change survives the rollback

        # Recompute per-question status from marks if both are present
        for q in questions:
            a = q.get('marks_awarded')
            t = q.get('marks_total')
            if a is not None and t is not None and t > 0:
                ratio = (a or 0) / t
                if ratio >= 0.99:
                    q['status'] = 'correct'
                elif ratio > 0:
                    q['status'] = 'partially_correct'
                else:
                    q['status'] = 'incorrect'

    # Re-derive pinyin _html siblings from the (possibly edited) raw text so
    # the next render reflects the teacher's prose changes. No-op for
    # non-Chinese assignments and when pinyin_mode is 'off'.
    pmode = getattr(asn, 'pinyin_mode', 'off') or 'off'
    if pmode != 'off':
        try:
            from subjects import resolve_subject_key
            if resolve_subject_key(asn.subject or '') == 'chinese':
                from pinyin_annotate import annotate_result_for_pinyin
                annotate_result_for_pinyin(result, pmode)
                result['pinyin_mode'] = pmode
        except Exception as _e:
            logger.warning(f'pinyin re-annotation on edit skipped: {_e}')

    sub.set_result(result)
    db.session.commit()
    logger.info(f"Teacher edited feedback for submission {submission_id} on assignment {assignment_id}")

    # Calibration follow-ups for fresh FeedbackEdit rows written this request:
    #   1) Spawn an insight-extraction worker per fresh edit (mistake_pattern,
    #      correction_principle, transferability — background, non-blocking).
    #   2) Auto-fire propagation: same teacher standard applied to every
    #      matching candidate without prompting the teacher (banner UX
    #      deferred). Always echo auto_propagation to the client (even with
    #      0 candidates) so the toast can confirm the save landed.
    auto_propagation = None
    if fresh_calibration_edits:
        # Spawn one insight worker per fresh edit. UP-16: bounded pool.
        for fe in fresh_calibration_edits:
            try:
                _submit_marking(_run_insight_extraction_worker, app, fe.id)
            except Exception as worker_err:
                logger.warning(f"Could not spawn insight worker for edit {fe.id}: {worker_err}")

        # Auto-fire propagation worker on the most-recent fresh edit.
        try:
            anchor = fresh_calibration_edits[-1]
            cands = _find_propagation_candidates(anchor, asn)
            target_ids = [c['submission_id'] for c in cands.get('candidates') or []]
            logger.info(
                f"Auto-propagation: edit_id={anchor.id} crit={anchor.criterion_id} "
                f"field={anchor.field} candidates={len(target_ids)}"
            )
            auto_propagation = {
                'edit_id': anchor.id,
                'candidate_count': len(target_ids),
            }
            if target_ids:
                anchor.propagation_status = 'pending'
                db.session.commit()
                _submit_marking(_run_propagation_worker, app, anchor.id, target_ids)
                logger.info(f"Auto-propagation worker started for edit_id={anchor.id}")
        except Exception:
            logger.exception("Auto-propagation kickoff failed")
            auto_propagation = None

    response = {'success': True, 'result': sub.get_result()}
    if edit_meta:
        response['edit_meta'] = edit_meta
    if auto_propagation:
        response['auto_propagation'] = auto_propagation
    if calibration_write_errors:
        response['calibration_warning'] = (
            f'Calibration save failed for {len(calibration_write_errors)} field(s). '
            f'First error: {calibration_write_errors[0]}'
        )
    return jsonify(response)


@app.route('/teacher/assignment/<assignment_id>/submission/<int:submission_id>/result/pinyin', methods=['PATCH'])
def teacher_submission_pinyin_patch(assignment_id, submission_id):
    """Per-ruby pinyin edit. Accepts a single override:
        {
          "question_num": 1 | null,           # null => top-level (well_done, main_gap, overall_feedback)
          "field": "feedback",                # which field hosts the word
          "old_word": "成语",                 # current Chinese to edit (used for replace if changing)
          "new_word": "成语",                 # may equal old_word if only pinyin changes
          "new_pinyin": "chéngyǔ"             # new pinyin for new_word; empty string removes override
        }
    Mutates result_json in place: if new_word != old_word, the raw text
    field is updated by string-replacing the first occurrence; the per-field
    pinyin_overrides map is updated; the result is re-annotated; saved.
    Returns the updated result so the client can re-render."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Invalid submission'}), 400

    pmode = getattr(asn, 'pinyin_mode', 'off') or 'off'
    if pmode == 'off':
        return jsonify({'success': False, 'error': 'Pinyin is not enabled on this assignment'}), 400

    payload = request.get_json(silent=True) or {}
    field = (payload.get('field') or '').strip()
    if field not in (
        'feedback', 'improvement', 'idea', 'correction_prompt',
        'student_answer', 'correct_answer',
        'well_done', 'main_gap', 'overall_feedback',
    ):
        return jsonify({'success': False, 'error': 'Unknown or non-editable field'}), 400
    old_word = (payload.get('old_word') or '').strip()
    new_word = (payload.get('new_word') or old_word).strip()
    new_pinyin = (payload.get('new_pinyin') or '').strip()
    if not old_word:
        return jsonify({'success': False, 'error': 'old_word required'}), 400
    if len(new_word) > 80 or len(new_pinyin) > 120:
        return jsonify({'success': False, 'error': 'Edited values are too long'}), 400

    result = sub.get_result() or {}
    qnum = payload.get('question_num')

    if qnum is None:
        target = result
    else:
        target = None
        for q in (result.get('questions') or []):
            if str(q.get('question_num')) == str(qnum):
                target = q
                break
        if target is None:
            return jsonify({'success': False, 'error': 'Question not found'}), 400

    if not isinstance(target.get(field), str):
        return jsonify({'success': False, 'error': f'Field {field!r} not present'}), 400

    # Replace the old word in the raw text if the teacher changed the
    # Chinese (replace first occurrence only — multi-occurrence cases
    # would need richer addressing). If only the pinyin changed, leave
    # the prose alone.
    if new_word != old_word:
        target[field] = target[field].replace(old_word, new_word, 1)

    # Update overrides map. Empty pinyin clears the override for that word.
    ov_key = field + '_pinyin_overrides'
    overrides = target.get(ov_key)
    if not isinstance(overrides, dict):
        overrides = {}
    # Remove the old override key if the word actually changed and the old
    # value is no longer present in the prose, otherwise it dangles.
    if new_word != old_word and old_word in overrides:
        if old_word not in target.get(field, ''):
            overrides.pop(old_word, None)
    if new_pinyin:
        overrides[new_word] = new_pinyin
    else:
        overrides.pop(new_word, None)
    if overrides:
        target[ov_key] = overrides
    else:
        target.pop(ov_key, None)

    # Re-annotate the whole result so all _html fields stay consistent.
    try:
        from pinyin_annotate import annotate_result_for_pinyin
        annotate_result_for_pinyin(result, pmode)
        result['pinyin_mode'] = pmode
    except Exception as _e:
        logger.warning(f'pinyin re-annotation on per-ruby edit skipped: {_e}')

    sub.set_result(result)
    db.session.commit()
    return jsonify({'success': True, 'result': sub.get_result()})


@app.route('/teacher/assignment/<assignment_id>/submission/<int:submission_id>/remark', methods=['POST'])
def teacher_submission_remark(assignment_id, submission_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Invalid submission'}), 400
    if sub.status in ('pending', 'processing', 'extracting', 'preview'):
        return jsonify({'success': False, 'error': 'Already in progress'}), 409
    if not sub.get_script_pages():
        return jsonify({'success': False, 'error': 'No stored script available to re-mark'}), 400

    # Optional teacher band overrides — locks bands across the re-mark so
    # the AI produces fresh per-band descriptors / improvement examples
    # anchored at the teacher's chosen band. Used by the modal's
    # "Re-mark for tailored text" link in the band-stale notice.
    payload = request.get_json(silent=True) or {}
    raw_overrides = payload.get('band_overrides') if isinstance(payload, dict) else None
    band_overrides = None
    if isinstance(raw_overrides, dict):
        cleaned = {}
        for crit, band in raw_overrides.items():
            if isinstance(crit, str) and isinstance(band, str) and crit.strip() and band.strip():
                cleaned[crit.strip()] = band.strip()
        if cleaned:
            band_overrides = cleaned

    sub.status = 'pending'
    sub.result_json = None
    sub.marked_at = None
    sub.submitted_at = datetime.now(timezone.utc)  # reset so 'stuck' clears
    db.session.commit()

    _submit_marking(_run_submission_marking, app, sub.id, assignment_id, band_overrides=band_overrides)

    return jsonify({'success': True})


@app.route('/teacher/assignment/<assignment_id>/remark-all', methods=['POST'])
def teacher_remark_all_submissions(assignment_id):
    """Re-mark every already-submitted script for this assignment using the
    current answer key / rubric. Used when the teacher updates marking
    materials after students have submitted. Skips submissions still
    in-flight ('pending' / 'processing' / 'extracting' / 'preview') and
    submissions without a stored script.

    Returns: { success: True, queued: N, skipped: M }
    """
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err

    subs = (Submission.query
            .filter_by(assignment_id=assignment_id, is_final=True)
            .filter(Submission.status.in_(['done', 'error']))
            .all())
    if not subs:
        return jsonify({'success': False, 'error': 'No submitted scripts to re-mark.'}), 400

    queued_ids = []
    skipped = 0
    now_utc = datetime.now(timezone.utc)
    for sub in subs:
        if not sub.get_script_pages():
            skipped += 1
            continue
        sub.status = 'pending'
        sub.result_json = None
        sub.marked_at = None
        sub.submitted_at = now_utc  # reset so 'stuck' clears
        queued_ids.append(sub.id)
    db.session.commit()

    if not queued_ids:
        return jsonify({'success': False, 'error': 'No re-markable scripts (all are missing stored script pages).'}), 400

    # UP-16: re-mark-all is the fan-out we explicitly want to bound. Going
    # through `_submit_marking` caps concurrent provider calls at 4 even
    # for a 40-student class.
    for sub_id in queued_ids:
        _submit_marking(_run_submission_marking, app, sub_id, assignment_id)

    logger.info(
        f"Re-mark-all kicked off for assignment={assignment_id}: "
        f"queued={len(queued_ids)} skipped={skipped}"
    )
    return jsonify({'success': True, 'queued': len(queued_ids), 'skipped': skipped})


@app.route('/teacher/assignment/<assignment_id>/submission/<int:submission_id>/force-remark', methods=['POST'])
def teacher_submission_force_remark(assignment_id, submission_id):
    """Re-kick the marking worker for a stuck submission. Bypasses the
    'already in progress' guard that the regular /remark endpoint enforces,
    because the whole point of this route is to recover from a status that
    never advanced (worker died mid-extraction, etc.)."""
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Invalid submission'}), 400
    if not sub.get_script_pages():
        return jsonify({'success': False, 'error': 'No stored script available to retry'}), 400

    logger.warning(
        f"Force-remark stuck submission: assignment={assignment_id} "
        f"submission={submission_id} prior_status={sub.status} "
        f"submitted_at={sub.submitted_at}"
    )
    sub.status = 'pending'
    sub.result_json = None
    sub.marked_at = None
    sub.submitted_at = datetime.now(timezone.utc)  # reset so 'stuck' clears
    db.session.commit()

    _submit_marking(_run_submission_marking, app, sub.id, assignment_id)

    return jsonify({'success': True})


@app.route('/teacher/assignment/<assignment_id>/submission/<int:submission_id>/script/manifest')
def teacher_submission_script_manifest(assignment_id, submission_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Invalid submission'}), 400
    pages = sub.get_script_pages() or []
    return jsonify({
        'success': True,
        'pages': [{'index': i, 'mime': _detect_mime(p)} for i, p in enumerate(pages)],
    })


@app.route('/teacher/assignment/<assignment_id>/submission/<int:submission_id>/script/page/<int:page_idx>')
def teacher_submission_script_page(assignment_id, submission_id, page_idx):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Invalid submission'}), 400
    pages = sub.get_script_pages() or []
    if page_idx < 0 or page_idx >= len(pages):
        return jsonify({'success': False, 'error': 'Page out of range'}), 404
    data = pages[page_idx]
    resp = send_file(
        io.BytesIO(data),
        mimetype=_detect_mime(data),
        as_attachment=False,
    )
    resp.cache_control.private = True
    resp.cache_control.no_store = True
    return resp


@app.route('/teacher/assignment/<assignment_id>/answer-key')
def teacher_assignment_answer_key(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    if not asn.answer_key:
        return jsonify({'success': False, 'error': 'No answer key available'}), 404
    data = asn.answer_key
    resp = send_file(
        io.BytesIO(data),
        mimetype=_detect_mime(data),
        as_attachment=False,
    )
    resp.cache_control.private = True
    resp.cache_control.no_store = True
    return resp


@app.route('/teacher/assignment/<assignment_id>/submission/<int:submission_id>/review')
def teacher_submission_review(assignment_id, submission_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id or sub.status != 'done':
        abort(404)

    student = Student.query.get(sub.student_id)
    pages = sub.get_script_pages() or []
    manifest = [{'index': i, 'mime': _detect_mime(p)} for i, p in enumerate(pages)]

    # Build list of OTHER students on this assignment with done submissions
    other_subs = (
        db.session.query(Submission, Student)
        .join(Student, Submission.student_id == Student.id)
        .filter(
            Submission.assignment_id == assignment_id,
            Submission.status == 'done',
            Submission.id != submission_id,
            Submission.is_final == True,  # noqa: E712
        )
        .order_by(Student.index_number)
        .all()
    )
    other_students = [
        {'submission_id': s.id, 'name': st.name, 'index': st.index_number}
        for (s, st) in other_subs
    ]

    from subjects import resolve_subject_key as _resolve_subj_r
    assignment_has_canonical_subject = _resolve_subj_r(asn.subject or '') is not None
    return render_template(
        'review.html',
        assignment=asn,
        submission=sub,
        student=student,
        manifest=manifest,
        other_students=other_students,
        has_answer_key=bool(asn.answer_key),
        assignment_has_canonical_subject=assignment_has_canonical_subject,
    )


@app.route('/teacher/assignment/<assignment_id>/delete', methods=['POST'])
def teacher_delete_assignment(assignment_id):
    from db import FeedbackEdit
    from sqlalchemy import inspect as sa_inspect, text, bindparam
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    # Two FK rabbit holes block the Assignment.submissions cascade-delete:
    #   1. feedback_edit.assignment_id  — model exists, no cascade rule.
    #   2. feedback_log.submission_id   — model retired but table is still
    #      live in production with rows from the calibration-bank rollout.
    # Clear both before the cascade fires.
    FeedbackEdit.query.filter_by(assignment_id=asn.id).delete(synchronize_session=False)
    sub_ids = [sid for (sid,) in db.session.query(Submission.id)
               .filter_by(assignment_id=asn.id).all()]
    if sub_ids and 'feedback_log' in sa_inspect(db.engine).get_table_names():
        stmt = text('DELETE FROM feedback_log WHERE submission_id IN :sids') \
            .bindparams(bindparam('sids', expanding=True))
        db.session.execute(stmt, {'sids': sub_ids})
    db.session.delete(asn)
    db.session.commit()
    return jsonify({'success': True})


# --- Student submission ---

# UP-39: `student_page` and `student_question_paper` moved to
# `routes/student.py` (registered via `app.register_blueprint(student_bp)`
# at boot). Other `/submit/*` routes follow on a per-route basis as the
# helpers they depend on get stable enough to lazy-import.


@app.route('/submit/<assignment_id>/verify', methods=['POST'])
@csrf.exempt  # UP-04: student-facing route, auth by classroom code (no session)
def student_verify(assignment_id):
    if is_demo_mode():
        return jsonify({'success': False, 'error': 'Submissions are disabled in demo mode'}), 403
    if not _check_rate_limit(f'student_verify:{request.remote_addr}'):
        return jsonify({'success': False, 'error': 'Too many attempts. Please wait.'}), 429
    asn = Assignment.query.get_or_404(assignment_id)
    data = request.get_json()
    code = (data.get('code') or '').strip().upper()
    if not secrets.compare_digest(str(code), str(asn.classroom_code or '')):
        return jsonify({'success': False, 'error': 'Invalid classroom code'}), 401

    students = _sort_by_index(Student.query.filter_by(class_id=asn.class_id).all()) if asn.class_id else _sort_by_index(Student.query.filter_by(assignment_id=assignment_id).all())

    # Include submission status so students can see/review previous work
    all_subs = Submission.query.filter_by(assignment_id=assignment_id).all()
    subs_by_student = {}
    for sub in all_subs:
        subs_by_student.setdefault(sub.student_id, []).append(sub)
    student_list = []
    for s in students:
        student_subs = sorted(subs_by_student.get(s.id, []), key=lambda x: x.draft_number)
        drafts = [
            {
                'id': sub.id,
                'draft_number': sub.draft_number,
                'is_final': sub.is_final,
                'status': sub.status,
                'submitted_at': sub.submitted_at.strftime('%d %b %I:%M%p') if sub.submitted_at else None,
            }
            for sub in student_subs
            if sub.status == 'done'
        ]
        entry = {
            'id': s.id,
            'index': s.index_number,
            'name': s.name,
            'drafts': drafts,
            'draft_count': len(student_subs),
        }
        latest_done = drafts[-1] if drafts else None
        if latest_done:
            entry['has_submission'] = True
            entry['submission_id'] = latest_done['id']
        student_list.append(entry)

    session[f'student_auth_{assignment_id}'] = True
    # UP-05: re-authenticating with the classroom code releases any
    # previous student binding so the user can pick a different name.
    session.pop(f'student_id_{assignment_id}', None)
    return jsonify({
        'success': True,
        'students': student_list,
        'show_results': asn.show_results,
        'allow_drafts': asn.allow_drafts,
        'max_drafts': asn.max_drafts,
        'has_question_paper': bool(asn.question_paper),
    })


@app.route('/submit/<assignment_id>/switch-student', methods=['POST'])
@csrf.exempt  # UP-04: student-facing route, auth-gated by classroom code
def student_switch(assignment_id):
    """UP-05: free switch to a name that has no submission yet; require
    classroom-code re-entry to switch to a name that already has one
    (closes the snooping vector — see plan acceptance (c))."""
    if not session.get(f'student_auth_{assignment_id}'):
        return jsonify({'success': False, 'error': 'Not authenticated', 'auth_required': True}), 401
    asn = Assignment.query.get_or_404(assignment_id)
    data = request.get_json(silent=True) or {}
    try:
        new_sid = int(data.get('student_id'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'student_id required'}), 400

    student = Student.query.get(new_sid)
    if not student:
        return jsonify({'success': False, 'error': 'Invalid student'}), 400
    # Roster check (same shape as student_upload)
    if asn.class_id:
        if not student.class_id or student.class_id != asn.class_id:
            return jsonify({'success': False, 'error': 'Invalid student'}), 400
    elif student.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Invalid student'}), 400

    # If the target name already has any submission, force re-auth so an
    # opportunist with the classroom code can't enumerate other students'
    # work just by rotating the name dropdown.
    has_sub = Submission.query.filter_by(
        student_id=new_sid, assignment_id=assignment_id,
    ).first() is not None
    if has_sub:
        session.pop(f'student_auth_{assignment_id}', None)
        session.pop(f'student_id_{assignment_id}', None)
        return jsonify({
            'success': False,
            'error': 'This name already has a submission — please enter the classroom code to continue.',
            'auth_required': True,
        }), 403

    # Otherwise: free switch.
    session[f'student_id_{assignment_id}'] = new_sid
    return jsonify({'success': True})


@app.route('/submit/<assignment_id>/undo/<int:submission_id>', methods=['POST'])
@csrf.exempt  # UP-04: student-facing route, auth-gated by classroom code
def student_undo(assignment_id, submission_id):
    """UP-05: 30-minute undo window after submit. Deletes the new
    submission and, if it had bumped a prior final to is_final=False,
    flips that prior row back. Clears the session student binding so the
    re-pick is unconstrained."""
    if is_demo_mode():
        return jsonify({'success': False, 'error': 'Undo disabled in demo mode'}), 403
    if not session.get(f'student_auth_{assignment_id}'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    # UP-05 (review fix): bound_sid MUST be set and match. The previous
    # `if not None and !=` shape skipped the check when the binding was
    # absent — e.g. directly after `/verify` clears it — letting anyone
    # with the classroom code delete any roster submission within 30 min.
    bound_sid = session.get(f'student_id_{assignment_id}')
    if bound_sid is None or int(bound_sid) != sub.student_id:
        return jsonify({'success': False, 'error': 'Not your submission'}), 403

    # 30-minute window. submitted_at may be tz-naive (depends on column type).
    submitted_at = sub.submitted_at
    if submitted_at is None:
        return jsonify({'success': False, 'error': 'Submission has no timestamp'}), 400
    if submitted_at.tzinfo is None:
        submitted_at = submitted_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - submitted_at
    if age.total_seconds() > 30 * 60:
        return jsonify({
            'success': False,
            'error': 'Undo window has closed (30 minutes). Ask your teacher to delete this submission.',
        }), 410

    try:
        student_id = sub.student_id
        sub_asn_id = sub.assignment_id
        db.session.delete(sub)
        # Restore the most recent prior submission as final, if any.
        prior = (Submission.query
                 .filter_by(student_id=student_id, assignment_id=sub_asn_id)
                 .order_by(Submission.draft_number.desc())
                 .first())
        if prior:
            prior.is_final = True
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception('UP-05 undo failed')
        return jsonify({'success': False, 'error': 'Could not undo submission'}), 500

    session.pop(f'student_id_{assignment_id}', None)
    return jsonify({'success': True})


# UP-39: `student_review_submission` moved to `routes/student.py`.


# ---------------------------------------------------------------------------
# Tiered feedback ("Unpack My Feedback" + "Now You Try") — student-facing
# ---------------------------------------------------------------------------

def _student_feedback_auth(assignment_id, submission_id):
    """Shared auth guard for student-facing feedback routes.

    Returns (assignment, submission, None) on success, or (None, None, error_response).
    Accepts either the student's classroom-code session OR a logged-in teacher
    (so teachers can preview and debug). Requires asn.show_results.
    """
    is_student = session.get(f'student_auth_{assignment_id}')
    is_teacher = _is_authenticated()
    if not is_student and not is_teacher:
        return None, None, (jsonify({'success': False, 'error': 'Not authenticated'}), 401)
    asn = Assignment.query.get_or_404(assignment_id)
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id or sub.status != 'done':
        return None, None, (jsonify({'success': False, 'error': 'Not found'}), 404)
    if not asn.show_results and not is_teacher:
        return None, None, (jsonify({'success': False, 'error': 'Results are not available for this assignment'}), 403)
    return asn, sub, None


def _tiered_bucket(result):
    """Ensure result['_tiered'] exists and return it (mutating result)."""
    if '_tiered' not in result or not isinstance(result.get('_tiered'), dict):
        result['_tiered'] = {}
    return result['_tiered']


@app.route('/feedback/<assignment_id>/<int:submission_id>')
def student_feedback_page(assignment_id, submission_id):
    """Student-facing tiered feedback page ("Unpack My Feedback")."""
    asn, sub, err = _student_feedback_auth(assignment_id, submission_id)
    if err:
        return err

    is_teacher = _is_authenticated()

    # First-open stamp — only for genuine student visits, not teacher previews.
    if sub.feedback_opened_at is None and not is_teacher:
        try:
            sub.feedback_opened_at = datetime.now(timezone.utc)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.warning(f"Could not stamp feedback_opened_at on submission {submission_id}: {e}")

    student = Student.query.get(sub.student_id)
    result = sub.get_result() or {}

    # Score summary (marks if present, correct-count otherwise).
    questions = result.get('questions') or []
    has_marks = any(q.get('marks_awarded') is not None for q in questions)
    if has_marks:
        ta = sum((q.get('marks_awarded') or 0) for q in questions)
        tp = sum((q.get('marks_total') or 0) for q in questions)
        score_pill = f"{ta} / {tp}"
    else:
        correct = sum(1 for q in questions if q.get('status') == 'correct')
        score_pill = f"{correct} / {len(questions)}"

    from config.mistake_themes import themes_for_display, themes_meta_dict
    # Serialisable theme metadata for the template + JS (never hardcoded).
    # Includes deprecated legacy keys so old submissions render with clean
    # labels; the categorisation worker + correction dropdown still use
    # the strict (legacy-free) themes_for() variant.
    THEMES = themes_for_display(asn.subject or '')
    theme_meta = themes_meta_dict(THEMES)

    grouping_data = _compute_grouping_payload(sub, result, THEMES)

    # Resilience: auto-relaunch categorisation for legacy submissions (NULL —
    # marked before this feature shipped) and for submissions stuck in
    # 'pending' for longer than the worker should ever need (worker died
    # silently, e.g. dyno restart). Mirrors the >= 2 lost-criteria gate used
    # at marking time — a single criterion can't form a group anyway.
    cat_state = sub.categorisation_status
    if cat_state in (None, 'pending'):
        if _count_lost_criteria(questions) >= 2:
            stale = False
            if cat_state is None:
                stale = True
            elif sub.marked_at:
                marked_at = sub.marked_at
                if marked_at.tzinfo is None:
                    marked_at = marked_at.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - marked_at).total_seconds() > 90:
                    stale = True
            if stale and _kick_categorisation_worker(sub.id):
                logger.info(f"Re-kicked categorisation for stale/legacy submission {sub.id}")
        elif cat_state is None:
            # Fewer than 2 lost criteria → nothing to group; mark done so the page stops polling.
            try:
                sub.categorisation_status = 'done'
                db.session.commit()
            except Exception:
                db.session.rollback()

    # Student-facing grouping UI is gated behind a feature flag — when off,
    # the toggle, polling indicator, and grouped view are hidden completely
    # (the pipeline still runs in the background; only the student surface
    # is suppressed).
    if _ENV_STUDENT_GROUPING_UI_ENABLED:
        cat_status_for_view = sub.categorisation_status or 'pending'
        grouping_data_for_view = grouping_data
    else:
        cat_status_for_view = 'disabled'  # never matches 'pending' so polling never starts
        grouping_data_for_view = None

    # Rubrics-mode students see the same redesigned modal teachers see —
    # band-first cards, improvement examples, etc. — rendered read-only via
    # FeedbackRender. The Layer 1/2/3 + Corrections layout below is
    # short-answer-only.
    #
    # Short-answer teachers can opt their cohort into the same modal-style
    # render via `Assignment.student_view_mode == 'modal'`. Default
    # 'breakdown' keeps the existing Layer1/2/3 + corrections experience.
    is_rubrics = getattr(asn, 'assign_type', 'short_answer') == 'rubrics'
    teacher_picked_modal = getattr(asn, 'student_view_mode', 'breakdown') == 'modal'
    if is_rubrics or teacher_picked_modal:
        return render_template(
            'feedback_view_rubrics.html',
            assignment=asn,
            submission=sub,
            student=student,
            result=result,
            score_pill=score_pill,
            download_url=url_for('student.download_submission_pdf', assignment_id=assignment_id, submission_id=submission_id),
        )

    return render_template(
        'feedback_view.html',
        assignment=asn,
        submission=sub,
        student=student,
        result=result,
        score_pill=score_pill,
        download_url=url_for('student.download_submission_pdf', assignment_id=assignment_id, submission_id=submission_id),
        themes=theme_meta,
        categorisation_status=cat_status_for_view,
        grouping_data=grouping_data_for_view,
        grouping_ui_enabled=_ENV_STUDENT_GROUPING_UI_ENABLED,
    )


def _compute_grouping_payload(sub, result, themes):
    """Build the student-facing "By Mistake Type" payload from stored values.

    The same shape is returned by the polling endpoint — the template renders
    from this server-side copy on initial page load when categorisation is
    already 'done', and the client patches in the fresh copy if it was still
    'pending' when the page first rendered.
    """
    if not sub or sub.categorisation_status != 'done':
        return None

    questions = result.get('questions') or []
    tiered = result.get('_tiered') or {}
    habits_by_theme = {h.get('mistake_type'): h.get('habit') for h in (tiered.get('group_habits') or []) if h.get('mistake_type')}
    reviewed_keys = set(tiered.get('reviewed_mistake_types') or [])
    correction_attempts = tiered.get('corrections') or []
    correction_question_nums = {str(a.get('question_num')) for a in correction_attempts if a.get('question_num') is not None}

    # Collect per-theme criteria (only those with marks lost AND a mistake_type).
    by_theme = {}
    standalone = []
    for q in questions:
        ma = q.get('marks_awarded')
        mt = q.get('marks_total')
        lost_by_marks = (mt and ma is not None and mt > 0 and ma < mt)
        lost_by_status = (not lost_by_marks and q.get('status') and q.get('status') != 'correct')
        if not (lost_by_marks or lost_by_status):
            continue
        tk = q.get('mistake_type')
        if not tk or tk not in themes:
            continue  # uncategorised (rare) — do not show in grouped view
        marks_lost = max(0, (mt or 0) - (ma or 0)) if (mt and ma is not None) else 0
        entry = {
            'criterion_id': str(q.get('question_num')),
            'criterion_name': q.get('criterion_name') or f"Question {q.get('question_num')}",
            'specific_label': q.get('specific_label') or themes[tk].get('label', tk),
            'marks_lost': marks_lost,
            'low_confidence': bool(q.get('low_confidence')),
            'mistake_type': tk,
        }
        if themes[tk].get('never_group'):
            standalone.append(entry)
            continue
        by_theme.setdefault(tk, []).append(entry)

    groups = []
    # A theme forms a group only if it has ≥ 2 criteria; otherwise its one
    # criterion renders standalone.
    for tk, crits in by_theme.items():
        if len(crits) >= 2:
            crits_sorted = sorted(crits, key=lambda e: e.get('marks_lost') or 0, reverse=True)
            total = sum((e.get('marks_lost') or 0) for e in crits_sorted)
            groups.append({
                'mistake_type': tk,
                'theme_label': themes[tk].get('label', tk),
                'specific_labels': [e['specific_label'] for e in crits_sorted],
                'habit': habits_by_theme.get(tk, ''),
                'total_marks_lost': total,
                'criteria': crits_sorted,
            })
        else:
            standalone.extend(crits)

    groups.sort(key=lambda g: g['total_marks_lost'], reverse=True)
    # Standalone: content_gap (never_group) plus orphan single-criterion themes.
    standalone_sorted = sorted(standalone, key=lambda e: e.get('marks_lost') or 0, reverse=True)

    # Annotate reviewed state + pick the first unreviewed group (the one the
    # student "left off" at) so the client can render a Done badge / dim and
    # a "You left off here" label + auto-scroll.
    marked_first = False
    for g in groups:
        g['reviewed'] = g['mistake_type'] in reviewed_keys
        g['left_off_here'] = False
        if not g['reviewed'] and not marked_first:
            g['left_off_here'] = True
            marked_first = True

    return {
        'groups': groups,
        'standalone': standalone_sorted,
        'reviewed_mistake_types': sorted(reviewed_keys),
        'total_groups': len(groups),
        'reviewed_count': sum(1 for g in groups if g['reviewed']),
        'correction_count': len(correction_question_nums),
    }


@app.route('/feedback/<assignment_id>/<int:submission_id>/explain', methods=['POST'])
@csrf.exempt  # UP-04: student-facing route, auth-gated by classroom code
def student_feedback_explain(assignment_id, submission_id):
    """Layer 3 on-demand: "The idea" for one criterion. Cached on result_json.

    "Next time" is populated client-side from the criterion's improvement
    field, so this route only needs to return the AI-generated idea.
    """
    asn, sub, err = _student_feedback_auth(assignment_id, submission_id)
    if err:
        return err

    payload = request.get_json(silent=True) or {}
    qnum = payload.get('question_num')
    if qnum is None:
        return jsonify({'success': False, 'error': 'question_num required'}), 400
    qkey = str(qnum)

    result = sub.get_result() or {}
    tiered = _tiered_bucket(result)
    cache = tiered.setdefault('layer3_cache', {})

    if qkey in cache and isinstance(cache[qkey], dict):
        # Older cache entries may carry next_time too — strip on the way out
        # so the response shape stays clean.
        return jsonify({'success': True, 'cached': True, 'idea': cache[qkey].get('idea', '')})

    # Find the target question.
    q = next((x for x in (result.get('questions') or []) if str(x.get('question_num')) == qkey), None)
    if not q:
        return jsonify({'success': False, 'error': 'Question not found'}), 404

    # Inlined "idea" path: marking now produces idea per criterion in
    # result_json (see ai_marking.IDEA_RULES). If present, return it
    # directly — no AI round-trip. Falls through to a live AI call only for
    # legacy submissions marked before this optimisation.
    inlined_idea = (q.get('idea') or '').strip()
    if inlined_idea:
        return jsonify({'success': True, 'cached': True, 'idea': inlined_idea})

    criterion_name = q.get('criterion_name') or f"Question {q.get('question_num') or qkey}"
    try:
        explanation = explain_criterion(
            provider=asn.provider,
            model=asn.model,
            session_keys=_resolve_api_keys(asn),
            subject=asn.subject or '',
            criterion_name=criterion_name,
            student_answer=q.get('student_answer') or '',
            expected_answer=q.get('correct_answer') or '',
            feedback_sentence=q.get('feedback') or '',
        )
    except Exception as e:
        logger.exception("Layer 3 explain failed for sub %s q %s", submission_id, qkey)
        return jsonify({'success': False, 'error': f'Could not generate explanation: {e}'}), 502

    cache[qkey] = explanation
    sub.set_result(result)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not cache layer3 for sub {submission_id}: {e}")

    return jsonify({'success': True, 'cached': False, **explanation})


@app.route('/feedback/<assignment_id>/<int:submission_id>/correction', methods=['POST'])
@csrf.exempt  # UP-04: student-facing route, auth-gated by classroom code
def student_feedback_correction(assignment_id, submission_id):
    """Evaluate a "Now You Try" correction attempt. Stores the attempt."""
    asn, sub, err = _student_feedback_auth(assignment_id, submission_id)
    if err:
        return err

    payload = request.get_json(silent=True) or {}
    qnum = payload.get('question_num')
    attempt_text = (payload.get('text') or '').strip()
    if qnum is None or not attempt_text:
        return jsonify({'success': False, 'error': 'question_num and text required'}), 400
    qkey = str(qnum)
    if len(attempt_text) > 2000:
        attempt_text = attempt_text[:2000]

    result = sub.get_result() or {}
    q = next((x for x in (result.get('questions') or []) if str(x.get('question_num')) == qkey), None)
    if not q:
        return jsonify({'success': False, 'error': 'Question not found'}), 404

    criterion_name = q.get('criterion_name') or f"Question {q.get('question_num') or qkey}"
    try:
        verdict = evaluate_correction(
            provider=asn.provider,
            model=asn.model,
            session_keys=_resolve_api_keys(asn),
            subject=asn.subject or '',
            criterion_name=criterion_name,
            expected_answer=q.get('correct_answer') or '',
            feedback_sentence=q.get('feedback') or '',
            attempt_text=attempt_text,
        )
    except Exception as e:
        logger.exception("Correction eval failed for sub %s q %s", submission_id, qkey)
        return jsonify({'success': False, 'error': f'Could not evaluate: {e}'}), 502

    # Store the attempt on the submission's tiered bucket.
    tiered = _tiered_bucket(result)
    attempts = tiered.setdefault('corrections', [])
    attempts.append({
        'question_num': qkey,
        'text': attempt_text,
        'verdict': verdict['verdict'],
        'message': verdict['message'],
        'mistake_type': (payload.get('mistake_type') or None),
        'submitted_at': datetime.now(timezone.utc).isoformat(),
    })
    sub.set_result(result)
    # Stamp completion timestamp on first successful attempt.
    if sub.correction_submitted_at is None:
        sub.correction_submitted_at = datetime.now(timezone.utc)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"Could not persist correction for sub {submission_id}: {e}")

    return jsonify({'success': True, **verdict})


@app.route('/feedback/<assignment_id>/<int:submission_id>/mark-reviewed', methods=['POST'])
@csrf.exempt  # UP-04: student-facing route, auth-gated by classroom code
def student_feedback_mark_reviewed(assignment_id, submission_id):
    """Record that the student has expanded a group — powers the "Where was I?"
    return-visit landing. Idempotent: calling twice for the same mistake_type is
    a no-op."""
    asn, sub, err = _student_feedback_auth(assignment_id, submission_id)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    tk = (data.get('mistake_type') or '').strip()
    if not tk:
        return jsonify({'success': False, 'error': 'mistake_type required'}), 400
    result = sub.get_result() or {}
    tiered = _tiered_bucket(result)
    reviewed = list(tiered.get('reviewed_mistake_types') or [])
    if tk not in reviewed:
        reviewed.append(tk)
    tiered['reviewed_mistake_types'] = reviewed
    sub.set_result(result)
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"mark_reviewed failed for sub {submission_id}: {e}")
        return jsonify({'success': False, 'error': 'Could not save'}), 500
    return jsonify({'success': True, 'reviewed_mistake_types': reviewed})


@app.route('/feedback/grouping-status/<int:submission_id>')
def student_feedback_grouping_status(submission_id):
    """Poll endpoint for the "Group by Mistake Type" async categorisation.

    Returns one of:
      {"status": "pending"}
      {"status": "failed"}
      {"status": "done", "groups": [...], "standalone": [...]}

    Auth: same as the other /feedback/... routes — requires a student
    session for THIS assignment (or a teacher login for preview).
    """
    sub = Submission.query.get_or_404(submission_id)
    asn, sub, err = _student_feedback_auth(sub.assignment_id, submission_id)
    if err:
        return err

    state = sub.categorisation_status or 'pending'
    if state != 'done':
        return jsonify({'status': state})

    from config.mistake_themes import themes_for_display
    payload = _compute_grouping_payload(sub, sub.get_result() or {}, themes_for_display(asn.subject or ''))
    if not payload:
        return jsonify({'status': 'pending'})  # defensive — don't flip UI yet
    return jsonify({'status': 'done', **payload})


@app.route('/feedback/deprecate-edit', methods=['POST'])
def feedback_deprecate_edit():
    """Soft-delete a feedback_edit row. Only the original editor may retire."""
    from db import FeedbackEdit
    if not _is_authenticated():
        return jsonify({'status': 'error', 'message': 'Not authenticated'}), 401
    teacher = _current_teacher()
    teacher_id = teacher.id if teacher else None
    if not teacher_id:
        return jsonify({'status': 'error', 'message': 'Not authenticated'}), 401
    data = request.get_json(silent=True) or {}
    edit_id = data.get('edit_id')
    if not isinstance(edit_id, int):
        return jsonify({'status': 'error', 'message': 'edit_id (int) required'}), 400
    edit = FeedbackEdit.query.get(edit_id)
    if not edit:
        return jsonify({'status': 'error', 'message': 'Edit not found'}), 404
    if edit.edited_by != teacher_id:
        return jsonify({'status': 'error', 'message': 'Forbidden'}), 403
    if edit.active:
        edit.active = False
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("Could not retire edit %s", edit_id)
            return jsonify({'status': 'error', 'message': 'Could not save'}), 500
    return jsonify({'status': 'ok'})


@app.route('/feedback/edit-history/<assignment_id>/<int:submission_id>/<criterion_id>')
def feedback_edit_history(assignment_id, submission_id, criterion_id):
    """Combined history of versions for both feedback and improvement
    on one criterion. Auth: assignment owner (or HOD/lead in dept mode).
    """
    from db import FeedbackLog, FeedbackEdit, Teacher as _Teacher
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'error': 'submission does not belong to this assignment'}), 404

    log_rows = FeedbackLog.query.filter_by(
        submission_id=submission_id,
        criterion_id=criterion_id,
    ).order_by(FeedbackLog.field.asc(), FeedbackLog.version.asc()).all()

    edit_rows = FeedbackEdit.query.filter_by(
        submission_id=submission_id,
        criterion_id=criterion_id,
    ).all()
    edits_by_key = {(e.field, e.edited_by, e.edited_text): e for e in edit_rows}

    teacher_ids = {r.author_id for r in log_rows if r.author_id}
    teachers = {}
    if teacher_ids:
        for tt in _Teacher.query.filter(_Teacher.id.in_(teacher_ids)).all():
            teachers[tt.id] = tt

    def _author_name(row):
        if row.author_type == 'ai':
            return 'AI'
        tt = teachers.get(row.author_id)
        if not tt:
            return f'Teacher #{row.author_id}'
        return getattr(tt, 'name', None) or f'Teacher #{row.author_id}'

    def _fmt_date(dt):
        if not dt:
            return ''
        try:
            return f"{dt.day} {dt.strftime('%b %Y')}"
        except Exception:
            return dt.strftime('%d %b %Y')

    out = {'feedback': [], 'improvement': []}
    for r in log_rows:
        if r.field not in out:
            continue
        edit = edits_by_key.get((r.field, r.author_id, r.feedback_text)) if r.author_type == 'teacher' else None
        out[r.field].append({
            'version': r.version,
            'author_type': r.author_type,
            'author_name': _author_name(r),
            'author_id': r.author_id,
            'feedback_text': r.feedback_text,
            'created_at': _fmt_date(r.created_at),
            'edit_id': edit.id if edit else None,
            'active': edit.active if edit else None,
        })
    return jsonify(out)


def _check_edit_owner(edit_id):
    """Helper: load FeedbackEdit + verify the current teacher is the
    original editor. Returns (edit, None) on success or (None, error_response)."""
    from db import FeedbackEdit
    if not _is_authenticated():
        return None, (jsonify({'status': 'error', 'message': 'Not authenticated'}), 401)
    teacher = _current_teacher()
    teacher_id = teacher.id if teacher else None
    if not teacher_id:
        return None, (jsonify({'status': 'error', 'message': 'Not authenticated'}), 401)
    edit = FeedbackEdit.query.get(edit_id)
    if not edit:
        return None, (jsonify({'status': 'error', 'message': 'Edit not found'}), 404)
    if edit.edited_by != teacher_id:
        return None, (jsonify({'status': 'error', 'message': 'Forbidden'}), 403)
    return edit, None


@app.route('/feedback/propagation-candidates/<int:edit_id>')
def feedback_propagation_candidates(edit_id):
    edit, err = _check_edit_owner(edit_id)
    if err:
        return err
    asn = Assignment.query.get(edit.assignment_id)
    if not asn:
        return jsonify({'status': 'error', 'message': 'Assignment not found'}), 404
    return jsonify(_find_propagation_candidates(edit, asn))


@app.route('/feedback/propagate', methods=['POST'])
def feedback_propagate():
    data = request.get_json(silent=True) or {}
    edit_id = data.get('edit_id')
    if not isinstance(edit_id, int):
        return jsonify({'status': 'error', 'message': 'edit_id (int) required'}), 400
    edit, err = _check_edit_owner(edit_id)
    if err:
        return err
    asn = Assignment.query.get(edit.assignment_id)
    if not asn:
        return jsonify({'status': 'error', 'message': 'Assignment not found'}), 404

    mode = (data.get('mode') or '').strip().lower()
    if mode not in ('all', 'selected'):
        return jsonify({'status': 'error', 'message': 'mode must be "all" or "selected"'}), 400

    candidates = _find_propagation_candidates(edit, asn)
    candidate_ids = [c['submission_id'] for c in candidates['candidates']]

    if mode == 'all':
        target_ids = candidate_ids
    else:
        provided = data.get('submission_ids') or []
        if not isinstance(provided, list) or not all(isinstance(x, int) for x in provided):
            return jsonify({'status': 'error', 'message': 'submission_ids must be a list of integers'}), 400
        legit = set(candidate_ids)
        invalid = [x for x in provided if x not in legit]
        if invalid:
            return jsonify({'status': 'error', 'message': f'invalid candidates: {invalid}'}), 400
        target_ids = provided

    if not target_ids:
        return jsonify({'status': 'started', 'edit_id': edit_id, 'candidate_count': 0})

    edit.propagation_status = 'pending'
    db.session.commit()
    _submit_marking(_run_propagation_worker, app, edit_id, target_ids)
    return jsonify({'status': 'started', 'edit_id': edit_id, 'candidate_count': len(target_ids)})


@app.route('/feedback/propagate-skip', methods=['POST'])
def feedback_propagate_skip():
    data = request.get_json(silent=True) or {}
    edit_id = data.get('edit_id')
    if not isinstance(edit_id, int):
        return jsonify({'status': 'error', 'message': 'edit_id (int) required'}), 400
    edit, err = _check_edit_owner(edit_id)
    if err:
        return err
    edit.propagation_status = 'skipped'
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': 'Could not save'}), 500
    return jsonify({'status': 'ok'})


@app.route('/feedback/propagation-progress/<int:edit_id>')
def feedback_propagation_progress(edit_id):
    import json as _json
    edit, err = _check_edit_owner(edit_id)
    if err:
        return err
    propagated = []
    try:
        propagated = _json.loads(edit.propagated_to or '[]')
        if not isinstance(propagated, list):
            propagated = []
    except Exception:
        propagated = []
    total = len(propagated)
    done = sum(1 for r in propagated if r.get('status') == 'done')
    failed = sum(1 for r in propagated if r.get('status') == 'failed')
    return jsonify({
        'edit_id': edit_id,
        'propagation_status': edit.propagation_status or 'none',
        'total': total,
        'done': done,
        'failed': failed,
        'propagated_to': propagated,
    })


@app.route('/submit/<assignment_id>/upload', methods=['POST'])
@csrf.exempt  # UP-04: student-facing route, auth-gated by classroom code
def student_upload(assignment_id):
    """Upload script and start AI extraction (preview step before marking)."""
    if is_demo_mode():
        return jsonify({'success': False, 'error': 'Submissions are disabled in demo mode'}), 403
    if not session.get(f'student_auth_{assignment_id}'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    asn = Assignment.query.get_or_404(assignment_id)

    student_id = request.form.get('student_id')
    if not student_id:
        return jsonify({'success': False, 'error': 'Please select your name'}), 400

    student = Student.query.get(int(student_id))
    if not student:
        return jsonify({'success': False, 'error': 'Invalid student'}), 400
    # Validate student belongs to this assignment's class or assignment
    if asn.class_id:
        if not student.class_id or student.class_id != asn.class_id:
            return jsonify({'success': False, 'error': 'Invalid student'}), 400
    elif student.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Invalid student'}), 400

    # UP-05: write-side IDOR fix. Without this, anyone with the classroom
    # code can post an upload as any other student in the class. We bind
    # the chosen student_id to the session on first upload; subsequent
    # attempts as a different student are rejected and the client is told
    # to go through the switch-student flow (which re-auths if needed).
    bound_sid = session.get(f'student_id_{assignment_id}')
    form_sid = int(student_id)
    if bound_sid is None:
        session[f'student_id_{assignment_id}'] = form_sid
    elif int(bound_sid) != form_sid:
        return jsonify({
            'success': False,
            'error': 'You are bound to a different name in this session. Use "Not me?" to switch.',
            'auth_required': True,
        }), 403

    script_files = request.files.getlist('script')
    if not script_files or not script_files[0].filename:
        return jsonify({'success': False, 'error': 'Please upload your script'}), 400
    if len(script_files) > 20:
        return jsonify({'success': False, 'error': 'Maximum 20 files per submission'}), 400

    MAX_IMAGE_SIZE = 5 * 1024 * 1024   # 5MB per image
    # UP-17: MIME validation happens after size-bounded `f.read()` below.
    MAX_PDF_SIZE = 20 * 1024 * 1024    # 20MB per PDF
    MAX_TOTAL_SIZE = 30 * 1024 * 1024  # 30MB total

    script_pages = []
    total_size = 0
    for f in script_files:
        if not f.filename:
            continue
        data = f.read()
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext == 'pdf' and len(data) > MAX_PDF_SIZE:
            # Try server-side downscale before rejecting
            original_size = len(data)
            data = _compress_pdf(data, MAX_PDF_SIZE)
            if len(data) > MAX_PDF_SIZE:
                return jsonify({
                    'success': False,
                    'error': f'PDF too large even after auto-compression ({original_size // (1024*1024)}MB original, {len(data) // (1024*1024)}MB compressed). Maximum is 20MB. Try splitting into multiple files.',
                }), 400
        file_size = len(data)
        total_size += file_size
        if ext != 'pdf' and file_size > MAX_IMAGE_SIZE:
            return jsonify({'success': False, 'error': f'Image "{f.filename}" too large ({file_size // (1024*1024)}MB). Maximum is 5MB per image.'}), 400
        if total_size > MAX_TOTAL_SIZE:
            return jsonify({'success': False, 'error': 'Total upload too large. Maximum is 30MB combined.'}), 400
        script_pages.append(data)

    err = _validate_upload_blobs(script_pages, label='script')
    if err:
        return err

    sub, err = _prepare_new_submission(student, asn)
    if err:
        return jsonify({'success': False, 'error': err}), 400
    sub.script_bytes = script_pages[0] if script_pages else None
    sub.status = 'extracting'
    sub.set_script_pages(script_pages)
    db.session.add(sub)
    db.session.commit()

    # Start extraction in background
    _submit_marking(_run_submission_extraction, app, sub.id, assignment_id)

    return jsonify({
        'success': True,
        'submission_id': sub.id,
        'show_results': asn.show_results,
    })


@app.route('/submit/<assignment_id>/confirm/<int:submission_id>', methods=['POST'])
@csrf.exempt  # UP-04: student-facing route, auth-gated by classroom code
def student_confirm(assignment_id, submission_id):
    """Student confirms (possibly edited) extracted text, then start marking."""
    if is_demo_mode():
        return jsonify({'success': False, 'error': 'Submissions are disabled in demo mode'}), 403
    if not session.get(f'student_auth_{assignment_id}'):
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    sub = Submission.query.get_or_404(submission_id)
    if sub.assignment_id != assignment_id:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    if sub.status != 'preview':
        return jsonify({'success': False, 'error': 'Submission is not ready for confirmation'}), 400

    data = request.get_json()
    student_answers = data.get('answers', [])

    # Store student-confirmed text
    sub.set_student_text(student_answers)

    # Check if student amended any answers
    original = sub.get_extracted_text()
    amended = False
    for orig, student in zip(original, student_answers):
        if orig.get('extracted_text', '').strip() != student.get('extracted_text', '').strip():
            amended = True
            break
    sub.student_amended = amended

    sub.status = 'pending'
    db.session.commit()

    # Start marking in background
    _submit_marking(_run_submission_marking, app, sub.id, assignment_id)

    return jsonify({'success': True})


# UP-39: `student_submission_status` and `download_submission_pdf`
# moved to `routes/student.py`.


@app.route('/api/class/<class_id>/assignments')
def api_class_assignments(class_id):
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'unauthenticated'}), 401
    # UP-02: ownership check. In dept mode a teacher with no roster entry
    # for this class can otherwise enumerate any class's assignment list.
    # Senior roles (hod/subject_head/lead/owner) keep cross-class access.
    teacher = _current_teacher()
    if teacher and teacher.role not in ROLES_CAN_VIEW_INSIGHTS:
        tc = TeacherClass.query.filter_by(teacher_id=teacher.id, class_id=class_id).first()
        if not tc:
            return jsonify({'success': False, 'error': 'Class not in your roster'}), 403
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
    # UP-02: anyone authenticated could otherwise fetch any roster + per-
    # student submission status by URL enumeration.
    err = _check_assignment_ownership(asn)
    if err:
        return err

    # Get students from class level
    if asn.class_id:
        students = _sort_by_index(Student.query.filter_by(class_id=asn.class_id).all())
    else:
        students = _sort_by_index(Student.query.filter_by(assignment_id=assignment_id).all())

    result = []
    for s in students:
        sub = Submission.query.filter_by(student_id=s.id, assignment_id=assignment_id, is_final=True).first()
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


@app.route('/api/submission/<int:submission_id>/extracted')
def api_submission_extracted(submission_id):
    """Teacher endpoint: view extracted vs student-amended text for a submission."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    sub = Submission.query.get_or_404(submission_id)
    # UP-02: submission_id is an integer URL parameter (1..N) — easy to
    # enumerate. Resolve to the assignment and run the ownership check so
    # teacher A can't read teacher B's student OCR text.
    asn = Assignment.query.get(sub.assignment_id)
    if asn is None:
        return jsonify({'success': False, 'error': 'Assignment not found'}), 404
    err = _check_assignment_ownership(asn)
    if err:
        return err
    return jsonify({
        'success': True,
        'extracted': sub.get_extracted_text(),
        'student_text': sub.get_student_text(),
        'student_amended': sub.student_amended or False,
    })


# ---------------------------------------------------------------------------
# Assignment Bank
# ---------------------------------------------------------------------------

@app.route('/bank')
def bank_page():
    if not _is_authenticated():
        return redirect(url_for('hub'))
    teacher = _current_teacher()

    # Search/filter params
    q = request.args.get('q', '').strip()
    level = request.args.get('level', '').strip()

    query = AssignmentBank.query
    if q:
        like = f'%{q}%'
        query = query.filter(
            db.or_(
                AssignmentBank.title.ilike(like),
                AssignmentBank.subject.ilike(like),
                AssignmentBank.tags.ilike(like),
            )
        )
    # Level is filtered client-side (so it can combine with subject without
    # a page reload). The URL `?level=` still pre-selects the initial pill.

    items = query.order_by(AssignmentBank.created_at.desc()).all()

    # Subject pills show the subjects actually present in the bank
    # (across the whole bank, not just the current `q` results — pills are
    # a stable UI control, not a content-dependent list).
    # Dedupe case-insensitively since subjects are mostly canonical but can
    # be free-text; pick the most-frequent casing per subject.
    from collections import Counter
    subj_counter = Counter()
    for (s,) in db.session.query(AssignmentBank.subject).all():
        s = (s or '').strip()
        if s:
            subj_counter[s] += 1
    by_lower = {}
    for s, n in subj_counter.items():
        key = s.lower()
        if key not in by_lower or subj_counter[by_lower[key]] < n:
            by_lower[key] = s
    available_subjects = sorted(by_lower.values(), key=str.lower)

    # Get classes for the "Use" modal
    if teacher and teacher.role in ('hod', 'owner'):
        classes = Class.query.order_by(Class.name).all()
    elif teacher:
        tc_ids = [tc.class_id for tc in TeacherClass.query.filter_by(teacher_id=teacher.id).all()]
        classes = Class.query.filter(Class.id.in_(tc_ids)).order_by(Class.name).all() if tc_ids else []
    else:
        classes = Class.query.order_by(Class.name).all()

    from subjects import SUBJECT_DISPLAY_NAMES
    sentinel_bank_item = type('Sentinel', (), {
        'id': '__SENTINEL__',
        'title': '', 'subject': '', 'level': '', 'tags': '',
        'review_instructions': '', 'marking_instructions': '',
        'assign_type': 'short_answer', 'scoring_mode': 'marks',
        'total_marks': '', 'provider': '', 'model': '',
        'pinyin_mode': 'off', 'show_results': True,
        'allow_drafts': False, 'max_drafts': 3,
        'question_paper': None, 'answer_key': None,
        'rubrics': None, 'reference': None,
    })()
    sk = _get_session_keys()
    providers = get_available_providers(session_keys=sk)
    return render_template('bank.html', items=items, classes=classes, q=q, level=level, teacher=teacher,
                           canonical_subjects=SUBJECT_DISPLAY_NAMES,
                           available_subjects=available_subjects,
                           sentinel_bank_item=sentinel_bank_item,
                           providers=providers,
                           all_providers=PROVIDERS)


@app.route('/bank/publish', methods=['POST'])
def bank_publish():
    """Publish an existing assignment to the bank."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    teacher = _current_teacher()

    assignment_id = request.form.get('assignment_id')
    if not assignment_id:
        return jsonify({'success': False, 'error': 'Missing assignment'}), 400

    asn = Assignment.query.get(assignment_id)
    if not asn:
        return jsonify({'success': False, 'error': 'Assignment not found'}), 404

    # Ownership check
    err = _check_assignment_ownership(asn)
    if err:
        return jsonify({'success': False, 'error': 'Not authorized'}), 403

    title = request.form.get('title', '').strip() or asn.title or asn.subject
    level = request.form.get('level', '').strip()
    tags = request.form.get('tags', '').strip()

    item = AssignmentBank(
        id=str(uuid.uuid4()),
        title=title,
        subject=asn.subject,
        level=level,
        tags=tags,
        assign_type=asn.assign_type,
        scoring_mode=asn.scoring_mode,
        total_marks=asn.total_marks,
        review_instructions=asn.review_instructions,
        marking_instructions=asn.marking_instructions,
        question_paper=asn.question_paper,
        answer_key=asn.answer_key,
        rubrics=asn.rubrics,
        reference=asn.reference,
        created_by=teacher.id if teacher else None,
    )
    db.session.add(item)
    db.session.commit()
    return jsonify({'success': True, 'id': item.id})


@app.route('/bank/use', methods=['POST'])
def bank_use():
    """Clone a bank item into one or more classes as live assignments."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    teacher = _current_teacher()

    data = request.get_json()
    bank_id = data.get('bank_id')
    class_ids = data.get('class_ids', [])

    if not bank_id or not class_ids:
        return jsonify({'success': False, 'error': 'Select a bank item and at least one class'}), 400

    item = AssignmentBank.query.get(bank_id)
    if not item:
        return jsonify({'success': False, 'error': 'Bank item not found'}), 404

    # Resolve API keys
    api_keys = {}
    from ai_marking import PROVIDER_KEY_MAP
    for prov, env_name in PROVIDER_KEY_MAP.items():
        val = os.getenv(env_name, '')
        if val:
            api_keys[prov] = val
    if not api_keys and is_dept_mode():
        dept_keys = _get_dept_keys()
        if dept_keys:
            api_keys = dept_keys
    if not api_keys:
        # Try wizard keys
        from db import _get_fernet
        f = _get_fernet()
        for prov in ('anthropic', 'openai', 'qwen'):
            cfg = DepartmentConfig.query.filter_by(key=f'api_key_{prov}').first()
            if cfg and cfg.value:
                if f:
                    try:
                        api_keys[prov] = f.decrypt(cfg.value.encode()).decode()
                        continue
                    except Exception:
                        pass
                api_keys[prov] = cfg.value

    if not api_keys:
        return jsonify({'success': False, 'error': 'No API keys configured'}), 400

    created = []
    skipped = []
    for cid in class_ids:
        cls = Class.query.get(cid)
        if not cls:
            continue
        student_count = Student.query.filter_by(class_id=cid).count()
        if student_count == 0:
            skipped.append(cls.name)
            continue

        # Resolve provider preference: bank value if set + key available, else first available.
        bank_provider = item.provider or ''
        if bank_provider and bank_provider in api_keys:
            chosen_provider = bank_provider
        else:
            chosen_provider = next(iter(api_keys))

        asn = Assignment(
            id=str(uuid.uuid4()),
            classroom_code=_generate_classroom_code(),
            title=item.title,
            subject=item.subject,
            assign_type=item.assign_type,
            scoring_mode=item.scoring_mode,
            total_marks=item.total_marks,
            provider=chosen_provider,
            model=item.model or '',
            pinyin_mode=item.pinyin_mode or 'off',
            show_results=item.show_results if item.show_results is not None else True,
            allow_drafts=item.allow_drafts if item.allow_drafts is not None else False,
            max_drafts=item.max_drafts if item.max_drafts is not None else 3,
            review_instructions=item.review_instructions,
            marking_instructions=item.marking_instructions,
            question_paper=item.question_paper,
            answer_key=item.answer_key,
            rubrics=item.rubrics,
            reference=item.reference,
            class_id=cid,
            teacher_id=teacher.id if teacher else None,
        )
        db.session.add(asn)
        created.append({'class': cls.name, 'code': asn.classroom_code})

    db.session.commit()
    return jsonify({'success': True, 'created': created, 'count': len(created), 'skipped': skipped})


@app.route('/bank/upload', methods=['POST'])
def bank_bulk_upload():
    """Bulk upload assignments via CSV + ZIP bundle. Manager, lead, HOD, subject head only."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    teacher = _current_teacher()
    if teacher and teacher.role not in ('hod', 'subject_head', 'lead', 'manager', 'owner'):
        return jsonify({'success': False, 'error': 'Not authorized'}), 403

    csv_file = request.files.get('csv')
    zip_file = request.files.get('zip')
    if not csv_file or not csv_file.filename:
        return jsonify({'success': False, 'error': 'Please upload a CSV file'}), 400
    if not zip_file or not zip_file.filename:
        return jsonify({'success': False, 'error': 'Please upload a ZIP file'}), 400

    # Parse CSV
    try:
        csv_text = csv_file.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
    except Exception as e:
        return jsonify({'success': False, 'error': f'CSV parse error: {e}'}), 400

    if not rows:
        return jsonify({'success': False, 'error': 'CSV is empty'}), 400

    # Required columns
    required = {'title', 'folder'}
    headers = set(rows[0].keys()) if rows else set()
    missing = required - headers
    if missing:
        return jsonify({'success': False, 'error': f'CSV missing columns: {", ".join(missing)}. Required: title, folder. Optional: subject, level, tags, type, scoring, marks, review_instructions, marking_instructions'}), 400

    # Parse ZIP
    try:
        zip_bytes = zip_file.read()
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except Exception as e:
        return jsonify({'success': False, 'error': f'ZIP error: {e}'}), 400

    # Build lookup of zip contents by folder
    zip_names = zf.namelist()

    created = 0
    errors = []
    for i, row in enumerate(rows, 1):
        title = (row.get('title') or '').strip()
        folder = (row.get('folder') or '').strip().strip('/')
        if not title or not folder:
            errors.append(f'Row {i}: missing title or folder')
            continue

        # Find files in this folder
        def find_file(prefix, folder_name=folder):
            for name in zip_names:
                # Match folder/prefix.* (case-insensitive, any nesting depth)
                parts = name.split('/')
                if len(parts) >= 2:
                    parent = parts[-2]
                    fname = parts[-1].lower()
                    if parent == folder_name and fname.startswith(prefix.lower()) and not fname.startswith('.'):
                        return zf.read(name)
                # Also try flat: folder_prefix.*
                basename = parts[-1].lower()
                if basename.startswith(f'{folder_name.lower()}_{prefix.lower()}') and not basename.startswith('.'):
                    return zf.read(name)
            return None

        qp = find_file('question')
        if not qp:
            errors.append(f'Row {i} "{title}": no question paper found in folder "{folder}"')
            continue

        ak = find_file('answer')
        rub = find_file('rubric')
        ref = find_file('reference')

        assign_type = (row.get('type') or 'short_answer').strip()
        if assign_type not in ('short_answer', 'rubrics'):
            assign_type = 'short_answer'

        if assign_type == 'short_answer' and not ak:
            errors.append(f'Row {i} "{title}": short_answer type requires answer key in folder "{folder}"')
            continue
        if assign_type == 'rubrics' and not rub:
            errors.append(f'Row {i} "{title}": rubrics type requires rubrics file in folder "{folder}"')
            continue

        scoring = (row.get('scoring') or 'status').strip()
        if scoring not in ('status', 'marks'):
            scoring = 'status'

        from subjects import canonicalise_subject as _canon
        item = AssignmentBank(
            id=str(uuid.uuid4()),
            title=title,
            subject=_canon(row.get('subject') or ''),
            level=(row.get('level') or '').strip(),
            tags=(row.get('tags') or '').strip(),
            assign_type=assign_type,
            scoring_mode=scoring,
            total_marks=(row.get('marks') or '').strip(),
            review_instructions=(row.get('review_instructions') or '').strip(),
            marking_instructions=(row.get('marking_instructions') or '').strip(),
            question_paper=qp,
            answer_key=ak,
            rubrics=rub,
            reference=ref,
            created_by=teacher.id if teacher else None,
        )
        db.session.add(item)
        created += 1

    db.session.commit()
    zf.close()
    return jsonify({'success': True, 'created': created, 'errors': errors})


@app.route('/bank/delete/<bank_id>', methods=['POST'])
def bank_delete(bank_id):
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    item = AssignmentBank.query.get_or_404(bank_id)
    db.session.delete(item)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/bank/edit/<bank_id>', methods=['POST'])
def bank_edit(bank_id):
    """Edit a bank item. Any authenticated teacher can edit."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    item = AssignmentBank.query.get_or_404(bank_id)

    # Multipart form (PDFs) — fall back to JSON body for backwards-compat callers.
    if request.content_type and 'multipart' in request.content_type:
        form = request.form
        files = request.files
    else:
        form = request.get_json() or {}
        files = {}

    def _f(key, default=''):
        val = form.get(key, default)
        return val.strip() if isinstance(val, str) else val

    # Text fields
    if 'title' in form:
        item.title = _f('title')
    if 'subject' in form:
        from subjects import canonicalise_subject as _canon
        item.subject = _canon(_f('subject'))
    if 'level' in form:
        item.level = _f('level')
    if 'tags' in form:
        # Normalise via the model helper so tags always have leading '#'.
        raw_tags = _f('tags')
        tag_list = [t.strip() for t in raw_tags.split(',') if t.strip()]
        item.set_tags_list(tag_list)
    if 'total_marks' in form:
        item.total_marks = _f('total_marks')
    if 'review_instructions' in form:
        item.review_instructions = _f('review_instructions')
    if 'marking_instructions' in form:
        item.marking_instructions = _f('marking_instructions')

    # New default-settings fields
    if 'provider' in form:
        item.provider = _f('provider')
    if 'model' in form:
        item.model = _f('model')
    if 'pinyin_mode' in form:
        new_pin = (_f('pinyin_mode') or 'off').lower()
        if new_pin not in ('off', 'vocab', 'advanced', 'full'):
            new_pin = 'off'
        # Subject-conditional: zero out pinyin for non-Chinese subjects.
        from subjects import resolve_subject_key as _rsk
        if _rsk(item.subject or '') != 'chinese':
            new_pin = 'off'
        item.pinyin_mode = new_pin
    if 'show_results' in form:
        item.show_results = (form.get('show_results') == 'on')
    if 'allow_drafts' in form:
        item.allow_drafts = (form.get('allow_drafts') == 'on')
    if 'max_drafts' in form:
        try:
            md = int(_f('max_drafts') or 3)
            item.max_drafts = max(2, min(10, md))
        except (TypeError, ValueError):
            pass

    # NOTE: assign_type and scoring_mode are intentionally NOT updated here.
    # They are locked after creation because changing them would invalidate
    # already-marked submissions on class assignments cloned from this bank item.

    # PDF replacement: only when a non-empty file is provided. Empty input keeps existing.
    def _maybe_read(field_name):
        if not files:
            return None, False
        f_list = files.getlist(field_name) if hasattr(files, 'getlist') else []
        if f_list and f_list[0].filename:
            return f_list[0].read(), True
        return None, False

    qp_bytes, qp_changed = _maybe_read('question_paper')
    ak_bytes, ak_changed = _maybe_read('answer_key')
    rub_bytes, rub_changed = _maybe_read('rubrics')
    ref_bytes, ref_changed = _maybe_read('reference')

    # Required-file invariants: don't end up with no answer_key for short_answer
    # or no rubrics for rubrics. Replacement is fine; removal is not allowed.
    if item.assign_type == 'rubrics' and rub_changed and not rub_bytes:
        return jsonify({'success': False, 'error': 'Rubrics file cannot be empty for essay type'}), 400
    if item.assign_type != 'rubrics' and ak_changed and not ak_bytes:
        return jsonify({'success': False, 'error': 'Answer key cannot be empty for short answer type'}), 400

    if qp_changed:
        item.question_paper = qp_bytes
    if ak_changed:
        item.answer_key = ak_bytes
    if rub_changed:
        item.rubrics = rub_bytes
    if ref_changed:
        item.reference = ref_bytes

    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/bank/search')
def bank_search_api():
    """Search bank items for the assignment creation picker."""
    if not _is_authenticated():
        return jsonify([])
    q = request.args.get('q', '').strip()
    query = AssignmentBank.query
    if q:
        like = f'%{q}%'
        query = query.filter(
            db.or_(
                AssignmentBank.title.ilike(like),
                AssignmentBank.subject.ilike(like),
                AssignmentBank.tags.ilike(like),
            )
        )
    items = query.order_by(AssignmentBank.created_at.desc()).limit(20).all()
    return jsonify([{
        'id': it.id,
        'title': it.title,
        'subject': it.subject,
        'level': it.level,
        'assign_type': it.assign_type,
        'scoring_mode': it.scoring_mode,
        'total_marks': it.total_marks,
        'tags': it.tags,
        'has_question_paper': bool(it.question_paper),
        'has_answer_key': bool(it.answer_key),
        'has_rubrics': bool(it.rubrics),
        'has_reference': bool(it.reference),
        'review_instructions': it.review_instructions or '',
        'marking_instructions': it.marking_instructions or '',
    } for it in items])


@app.route('/api/bank/<bank_id>/file/<file_type>')
def bank_file_download(bank_id, file_type):
    """Download a file from a bank item to pre-fill assignment creation."""
    if not _is_authenticated():
        return 'Not authenticated', 401
    item = AssignmentBank.query.get_or_404(bank_id)
    file_map = {
        'question_paper': item.question_paper,
        'answer_key': item.answer_key,
        'rubrics': item.rubrics,
        'reference': item.reference,
    }
    data = file_map.get(file_type)
    if not data:
        return 'File not found', 404
    return Response(data, mimetype='application/pdf',
                    headers={'Content-Disposition': f'attachment; filename={file_type}.pdf'})


@app.route('/bank/<bank_id>/file-inline/<file_type>')
def bank_file_inline(bank_id, file_type):
    """Inline-display version of a bank item's file (used by the preview page)."""
    if not _is_authenticated():
        return 'Not authenticated', 401
    item = AssignmentBank.query.get_or_404(bank_id)
    file_map = {
        'question_paper': item.question_paper,
        'answer_key': item.answer_key,
        'rubrics': item.rubrics,
        'reference': item.reference,
    }
    data = file_map.get(file_type)
    if not data:
        return 'File not found', 404
    resp = send_file(io.BytesIO(data), mimetype=_detect_mime(data), as_attachment=False)
    resp.cache_control.private = True
    resp.cache_control.no_store = True
    return resp


@app.route('/teacher/assignment/<assignment_id>/file-inline/<file_type>')
def teacher_file_inline(assignment_id, file_type):
    """Inline-display version of an assignment's uploaded PDF (used by edit modal preview links)."""
    if not _is_authenticated():
        return 'Not authenticated', 401
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        # _check_assignment_ownership returns a JSON tuple; for this raw-stream route, return a plain 403
        return 'Not authorized', 403
    file_map = {
        'question_paper': asn.question_paper,
        'answer_key': asn.answer_key,
        'rubrics': asn.rubrics,
        'reference': asn.reference,
    }
    data = file_map.get(file_type)
    if not data:
        return 'File not found', 404
    resp = send_file(io.BytesIO(data), mimetype=_detect_mime(data), as_attachment=False)
    resp.cache_control.private = True
    resp.cache_control.no_store = True
    return resp


@app.route('/bank/<bank_id>/preview')
def bank_preview(bank_id):
    """Split-screen preview of a bank item: question paper (left) vs answer key (right)."""
    if not _is_authenticated():
        return redirect(url_for('hub'))
    item = AssignmentBank.query.get_or_404(bank_id)
    return render_template(
        'bank_preview.html',
        item=item,
        has_question_paper=bool(item.question_paper),
        has_answer_key=bool(item.answer_key),
        has_rubrics=bool(item.rubrics),
        has_amendments=bool((item.answer_key_amendments or '').strip()),
    )


@app.route('/bank/<bank_id>/answer-key-amendments')
def bank_answer_key_amendments(bank_id):
    """Return the textual "Teacher clarifications" appended to a bank item's
    answer key. Surfaced via the right-pane dropdown in bank_preview.html."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'unauthenticated'}), 401
    item = AssignmentBank.query.get_or_404(bank_id)
    return jsonify({
        'success': True,
        'amendments': item.answer_key_amendments or '',
    })


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

    # Check which API keys are already set via env vars
    from ai_marking import PROVIDER_KEY_MAP
    env_keys = {}
    for prov, env_name in PROVIDER_KEY_MAP.items():
        env_keys[prov] = bool(os.getenv(env_name, ''))

    has_postgres = bool(os.getenv('DATABASE_URL', ''))
    env_teacher_code = _ENV_TEACHER_CODE or ''
    return render_template('setup_wizard.html', env_keys=env_keys, has_postgres=has_postgres, env_teacher_code=env_teacher_code)


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


@app.route('/settings/go-live', methods=['POST'])
def settings_go_live():
    """One-way switch from demo mode to live. Purges all demo data."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    teacher = _current_teacher()
    # Allow in demo mode even without a teacher record
    if teacher and teacher.role not in ('owner', 'hod'):
        return jsonify({'success': False, 'error': 'Access denied'}), 403

    current_mode = get_app_mode()
    if current_mode not in ('demo', 'demo_department'):
        return jsonify({'success': False, 'error': 'Already in live mode'}), 400

    data = request.get_json()
    target_mode = data.get('mode', 'normal')
    if target_mode not in ('normal', 'department'):
        return jsonify({'success': False, 'error': 'Target mode must be normal or department'}), 400

    name = (data.get('name', '') or '').strip()
    code = (data.get('code', '') or '').strip()
    if not name or not code:
        return jsonify({'success': False, 'error': 'Name and access code are required'}), 400

    # --- Purge all user data ---
    Submission.query.delete()
    Student.query.delete()
    Assignment.query.delete()
    TeacherClass.query.delete()
    Class.query.delete()
    Teacher.query.delete()

    # Clear cached analyses from DepartmentConfig but keep system config
    keep_keys = {'app_mode', 'app_title', 'teacher_code', 'setup_complete',
                 'flask_secret_key', 'api_key_anthropic', 'api_key_openai', 'api_key_qwen'}
    DepartmentConfig.query.filter(~DepartmentConfig.key.in_(keep_keys)).delete(synchronize_session='fetch')
    db.session.commit()

    # --- Set new mode and create the admin teacher ---
    _set_config('app_mode', target_mode)
    _set_config('teacher_code', code)

    role = 'hod' if target_mode == 'department' else 'owner'
    new_teacher = Teacher(name=name, code=code, role=role, active=True)
    db.session.add(new_teacher)
    db.session.commit()

    # Update API keys if provided
    from db import _get_fernet
    f = _get_fernet()
    for prov in ('anthropic', 'openai', 'qwen'):
        key_field = f'api_key_{prov}'
        if key_field in data:
            val = (data[key_field] or '').strip()
            if val:
                encrypted = f.encrypt(val.encode()).decode() if f else val
                _set_config(f'api_key_{prov}', encrypted)

    # Clear session so user re-logs with new credentials
    session.clear()

    return jsonify({'success': True, 'message': 'Switched to live mode. All demo data has been removed.'})


# ---------------------------------------------------------------------------
# Calibration bank — propagation
# ---------------------------------------------------------------------------

def _find_propagation_candidates(edit, asn):
    """Synchronous: list other students in the same assignment who have a
    matching criterion with marks lost AND haven't already been teacher-
    edited / propagated. Returns the shape consumed by the banner."""
    from db import Submission, Student
    out = []
    rows = (
        db.session.query(Submission, Student)
        .outerjoin(Student, Submission.student_id == Student.id)
        .filter(
            Submission.assignment_id == asn.id,
            Submission.id != edit.submission_id,
            Submission.status == 'done',
        )
        .order_by(Submission.id)
        .all()
    )
    for sub, student in rows:
        try:
            result = sub.get_result() or {}
        except Exception:
            continue
        target_q = None
        for q in (result.get('questions') or []):
            if str(q.get('question_num')) == edit.criterion_id:
                target_q = q
                break
        if not target_q:
            continue
        ma = target_q.get('marks_awarded')
        mt = target_q.get('marks_total')
        lost_by_marks = (mt and ma is not None and mt > 0 and ma < mt)
        lost_by_status = (not lost_by_marks
                          and target_q.get('status')
                          and target_q.get('status') != 'correct')
        if not (lost_by_marks or lost_by_status):
            continue
        source = target_q.get('feedback_source')
        if source not in (None, 'original_ai', 'propagated'):
            continue  # whitelist — never propagate over teacher_edit
        out.append({
            'submission_id': sub.id,
            'student_name': (student.name if student else f"Student #{sub.student_id}"),
            'marks_awarded': ma,
            'marks_total': mt,
            'student_answer': (target_q.get('student_answer') or ''),
            'current_feedback': (target_q.get('feedback') or ''),
            'current_improvement': (target_q.get('improvement') or ''),
        })
    return {
        'edit_id': edit.id,
        'criterion_id': edit.criterion_id,
        'field': edit.field,
        'candidate_count': len(out),
        'candidates': out,
    }


def _check_edit_owner(edit_id):
    """Helper: load FeedbackEdit + verify the current teacher owns it.
    Returns (edit, None) on success or (None, error_response_tuple)."""
    from db import FeedbackEdit
    if not _is_authenticated():
        return None, (jsonify({'status': 'error', 'message': 'Not authenticated'}), 401)
    teacher = _current_teacher()
    teacher_id = teacher.id if teacher else None
    if not teacher_id:
        return None, (jsonify({'status': 'error', 'message': 'Not authenticated'}), 401)
    edit = FeedbackEdit.query.get(edit_id)
    if not edit:
        return None, (jsonify({'status': 'error', 'message': 'Edit not found'}), 404)
    if edit.edited_by != teacher_id:
        return None, (jsonify({'status': 'error', 'message': 'Forbidden'}), 403)
    return edit, None


def _run_propagation_worker(app_obj, edit_id, target_ids):
    """Background thread: refresh feedback for each candidate submission in
    sequence (never parallel — avoids DB contention). Updates result_json
    in place per submission, logs failures, and stamps the originating
    feedback_edit row with the final propagation_status + propagated_to."""
    from db import FeedbackEdit, Submission
    import json as _json

    with app_obj.app_context():
        try:
            edit = FeedbackEdit.query.get(edit_id)
            if not edit:
                logger.warning(f"propagation worker: edit {edit_id} not found")
                return
            asn = Assignment.query.get(edit.assignment_id)
            if not asn:
                logger.warning(f"propagation worker: assignment for edit {edit_id} not found")
                return

            # Seed propagated_to with pending entries so the progress poll has
            # the full target list visible from the very first poll.
            seeded = [{'submission_id': int(sid), 'status': 'pending'} for sid in target_ids]
            edit.propagated_to = _json.dumps(seeded, ensure_ascii=False)
            edit.propagation_status = 'pending'
            db.session.commit()

            from ai_marking import refresh_criterion_feedback
            results = []
            for sid in target_ids:
                entry = {'submission_id': int(sid), 'status': 'pending'}
                try:
                    sub = Submission.query.get(int(sid))
                    if not sub:
                        entry = {'submission_id': int(sid), 'status': 'failed', 'error': 'submission not found'}
                        results.append(entry)
                    else:
                        result = sub.get_result() or {}
                        target_q = None
                        for q in (result.get('questions') or []):
                            if str(q.get('question_num')) == edit.criterion_id:
                                target_q = q
                                break
                        if not target_q:
                            entry = {'submission_id': int(sid), 'status': 'failed', 'error': 'criterion not found on this submission'}
                            results.append(entry)
                        else:
                            # Field-aware re-mark: pass the teacher's edited field
                            # through; refresh_criterion_feedback returns only that
                            # field + an optional marks_awarded override.
                            target_field = edit.field
                            if target_field not in ('feedback', 'improvement'):
                                # Defensive: server-side validation should already
                                # prevent this, but if it slipped through, skip the
                                # candidate with a clear error rather than corrupting
                                # the result.
                                entry = {'submission_id': int(sid), 'status': 'failed',
                                         'error': f'unsupported field for propagation: {target_field!r}'}
                                results.append(entry)
                            else:
                                refreshed = refresh_criterion_feedback(
                                    provider=asn.provider,
                                    model=asn.model,
                                    session_keys=_resolve_api_keys(asn),
                                    subject=asn.subject or '',
                                    criterion_name=edit.criterion_id,
                                    student_answer=target_q.get('student_answer') or '',
                                    correct_answer=target_q.get('correct_answer') or '',
                                    marks_awarded=target_q.get('marks_awarded'),
                                    marks_total=target_q.get('marks_total'),
                                    calibration_edit=edit,
                                    target_field=target_field,
                                )
                                new_text = (refreshed.get(target_field) or target_q.get(target_field) or '')
                                target_q[target_field] = new_text
                                new_marks = refreshed.get('marks_awarded')
                                if new_marks is not None:
                                    target_q['marks_awarded'] = new_marks
                                    # Re-derive status to stay consistent with marks.
                                    mt = target_q.get('marks_total')
                                    if mt and mt > 0:
                                        ratio = (new_marks or 0) / mt
                                        if ratio >= 0.99:
                                            target_q['status'] = 'correct'
                                        elif ratio > 0:
                                            target_q['status'] = 'partially_correct'
                                        else:
                                            target_q['status'] = 'incorrect'
                                target_q['feedback_source'] = 'propagated'
                                target_q['propagated_from_edit'] = edit.id
                                sub.set_result(result)
                                db.session.commit()
                                entry = {'submission_id': int(sid), 'status': 'done'}
                                results.append(entry)
                except Exception as e:
                    db.session.rollback()
                    err = str(e)[:200]
                    logger.warning(f"propagation refresh failed sub={sid} edit={edit_id}: {e}")
                    entry = {'submission_id': int(sid), 'status': 'failed', 'error': err}
                    results.append(entry)

                # Persist running state after each iteration so the progress
                # poll reflects partial progress.
                try:
                    edit_fresh = FeedbackEdit.query.get(edit_id)
                    current = _json.loads(edit_fresh.propagated_to or '[]')
                    for i, c in enumerate(current):
                        if int(c.get('submission_id')) == int(sid):
                            current[i] = entry
                            break
                    edit_fresh.propagated_to = _json.dumps(current, ensure_ascii=False)
                    db.session.commit()
                except Exception as persist_err:
                    db.session.rollback()
                    logger.warning(f"propagation progress persist failed: {persist_err}")

            # Final state.
            try:
                failed_n = sum(1 for r in results if r.get('status') == 'failed')
                final_status = 'complete' if failed_n == 0 else 'partial'
                edit_final = FeedbackEdit.query.get(edit_id)
                edit_final.propagation_status = final_status
                edit_final.propagated_at = datetime.now(timezone.utc)
                db.session.commit()
                logger.info(f"propagation finished edit={edit_id} status={final_status} "
                            f"done={len(results) - failed_n} failed={failed_n}")
            except Exception:
                db.session.rollback()
                logger.exception("propagation final-status persist failed")
        except Exception:
            logger.exception("propagation worker crashed for edit %s", edit_id)
            try:
                edit_err = FeedbackEdit.query.get(edit_id)
                if edit_err and edit_err.propagation_status == 'pending':
                    edit_err.propagation_status = 'partial'
                    db.session.commit()
            except Exception:
                db.session.rollback()




# UP-39 / UP-40 / UP-41: register Flask Blueprints AFTER all top-level
# definitions so the deferred imports inside blueprint route bodies
# (`from app import ...`) resolve to fully-constructed symbols at
# request time.
from routes.student import bp as _student_bp  # noqa: E402
from routes.insights import bp as _insights_bp  # noqa: E402
from routes.bulk import bp as _bulk_bp  # noqa: E402

app.register_blueprint(_student_bp)
app.register_blueprint(_insights_bp)
app.register_blueprint(_bulk_bp)


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    # UP-28: Werkzeug's debug console is a remote-code-execution surface.
    # Require both FLASK_DEBUG=true AND FLASK_ENV=development to enable it,
    # and bind to loopback in that mode so it can't be reached from the LAN
    # by accident. Production (gunicorn) doesn't hit this code path at all.
    _debug = (
        os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
        and os.getenv('FLASK_ENV') == 'development'
    )
    app.run(host='127.0.0.1' if _debug else '0.0.0.0', port=port, debug=_debug)
