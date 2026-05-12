# Upgrade Plan — `sandbox_upgraded`

> **Source of truth for systematic improvements.** Generated 2026-05-12 from an 8-agent code audit (security ×2, backend perf, AI integration, frontend, ops, Python quality, general review).
>
> **How to use in a future Claude session:**
> 1. Reference this file: "Work on UP-XX from `.claude/UPGRADE_PLAN.md`".
> 2. Each task is self-contained — what, why, where (file:line), how, acceptance.
> 3. Update `Status:` in this file as you complete items. Commit alongside the change.
> 4. Tasks within a phase are mostly independent — pick by appetite. Phases are roughly ordered by impact-per-effort.
>
> **Verification reality:** This codebase has zero tests. Every change needs manual smoke-test until UP-31 (test scaffold) lands. Until then: import the module, hit the affected route in a local Flask, eyeball the rendered page.

---

## Phase 1 — Critical (Security + Data Integrity)

These are the items where the cost of doing nothing is "real data loss / real breach". Do these first.

---

### UP-01 — Split `_require_hod()` from `_can_manage_accounts()`
**Status:** DONE — `_require_hod()` is now strict (role == 'hod' only). Added loose `_require_management()` for class/teacher CRUD. Switched 10 LOOSE callsites; kept STRICT for `/department/keys`, `/department/teacher/<id>/purge`, `/department/teacher/<id>/reset-code`.
**Why:** `_require_hod()` at `app.py:511` actually delegates to `_can_manage_accounts()` which admits roles `{hod, subject_head, manager}` (`app.py:319`). A `manager` can POST to `/department/keys` and overwrite the org's Anthropic/OpenAI/Qwen keys (`app.py:1583-1603`) — instant billing hijack. They can also purge any teacher's data.
**Where:** `app.py:511` (helper), and audit every `err = _require_hod()` callsite: `1042, 1113, 1168, 1211, 1236, 1251, 1275, 1289, 1317, 1338, 1547, 1569, 1585`.
**Fix:** Define two helpers:
```python
def _require_hod():
    """Strict: only role == 'hod' (or TEACHER_CODE master)."""
def _require_management():
    """Loose: any role with management rights (hod, subject_head, manager)."""
```
Use `_require_hod` for `/department/keys`, `/department/teacher/*/purge`, `/department/teacher/*/reset-code`. Use `_require_management` for class CRUD.
**Acceptance:** A teacher account with `role='manager'` gets 403 from `/department/keys` and `/department/teacher/<id>/purge`.

---

### UP-02 — Add ownership checks on `/api/*` JSON endpoints
**Status:** DONE — `/api/class/<id>/assignments` (TeacherClass roster check, senior roles bypass); `/api/assignment/<id>/students` and `/api/submission/<int:id>/extracted` (resolve to Assignment and call `_check_assignment_ownership`). Submission endpoint additionally resolves int ID → assignment first.
**Why:** Three endpoints check only authentication, not ownership: any logged-in teacher can fetch any class's roster or any submission's OCR'd text by URL enumeration.
**Where:**
- `app.py:8189` — `/api/class/<class_id>/assignments`
- `app.py:8203` — `/api/assignment/<assignment_id>/students`
- `app.py:8230` — `/api/submission/<int:submission_id>/extracted` (integer-enumerable! 1..N)
**Fix:** After `get_or_404(...)`, call `_check_class_access_for_teacher(...)` / `_check_assignment_ownership(...)`. For the submission endpoint, resolve `sub.assignment_id → assignment` and run the assignment ownership check.
**Acceptance:** Logged in as teacher A, hitting an endpoint for teacher B's class/assignment returns 403.

---

### UP-03 — Close default-open auth gate
**Status:** DONE — default-deny in `_is_authenticated()`; demo mode kept open via explicit short-circuit so demo (non-dept) routes still work. Legacy ACCESS_CODE only honored when env var is set.
**Why:** `_is_authenticated()` returns `True` for any visitor when no codes are configured (the literal first-boot state before setup wizard). Anyone with the Railway URL gets full owner access.
**Where:** `app.py:290-304`.
**Fix:** Change `if not _ENV_ACCESS_CODE: return True` to `return False`. Setup wizard already has its own bypass via `pending_setup` session, so first-run still works.
**Acceptance:** Fresh deployment with no `TEACHER_CODE` set, no DB teacher_code, no dept_mode — `GET /hub` redirects to setup wizard, not directly into the app.

---

### UP-04 — Add CSRF protection
**Status:** DONE — `flask-wtf>=1.2` added to `requirements.txt`; `CSRFProtect(app)` initialised in `app.py` with a JSON-friendly `CSRFError` handler. `base.html` carries the `<meta name="csrf-token">` tag and a `window.fetch` shim that auto-attaches `X-CSRFToken` to every same-origin non-GET call. One classic form (`marking_patterns.html#dismiss-conflict`) has `csrf_token` hidden input. Student-facing routes (`/submit/*` upload/confirm/verify, `/feedback/<asn>/<sub>/{explain,correction,mark-reviewed}`) are `@csrf.exempt` because they're auth-gated by the classroom code, not a teacher session.
**Why:** No CSRF anywhere. A logged-in teacher clicking a malicious link can be made to delete classes, create fake teachers, or rotate API keys via cross-origin POST.
**Where:** `app.py` (boot path), all `<form>` and `fetch()` POSTs.
**Fix:** `pip install flask-wtf`, add `requirements.txt` entry. In `app.py` after `app = Flask(__name__)`:
```python
from flask_wtf.csrf import CSRFProtect
csrf = CSRFProtect(app)
```
For HTML forms: `{{ csrf_token() }}` hidden field. For `fetch()` calls: send `X-CSRFToken` header from a meta tag in `base.html`. Exempt the student-submission flow if you keep it CSRF-light (already auth-gated by classroom code) — but ideally include it.
**Acceptance:** A POST without `csrf_token` returns 400. The setup-wizard, login, and at least one teacher-write flow still work.

---

### UP-05 — Student name-selection recovery flow (switch + undo + confirm)
**Status:** DONE — server: (1) `student_upload` binds `session['student_id_<asn>']` on first upload and rejects mismatched form student_id (write-side IDOR fix); (2) `student_verify` clears the binding so re-auth lets you pick freely; (3) new `/submit/<asn>/switch-student` allows a free switch to a name with no submission, forces classroom-code re-auth otherwise; (4) new `/submit/<asn>/undo/<int:sub>` deletes a fresh submission within 30 min and clears the binding — guard requires the session binding to be PRESENT and match (not "if present, must match"), so a `/verify`-then-undo cannot delete another student's submission. Client: confirm dialog already in place; added "Not me?" bar across post-gate sections and "Undo & re-pick" card with countdown on the success state.
**Why:** The IDOR fix (UP-12) binds the session to a chosen student. If a student picks the wrong name OR uploads to the wrong student, they're stuck. Also: anyone with the classroom code can currently `student_upload` as any other student (write-side IDOR — `app.py:7996`).
**Where:** Student routes `app.py:7351, 8149, 7996`, `templates/submit.html`.
**Fix — three layers:**
1. **Confirm dialog on name-pick**: "You picked Alice — continue as Alice?" Catches fast clicks.
2. **"Not [Alice]?" switch link** on every page of the submit flow. Clicking clears `session['student_id_<asn>']` and returns to name picker. **No classroom-code re-entry needed** when switching to a name with no submission yet. **Require classroom-code re-entry** when switching to a name that already has a submission (closes the snooping vector).
3. **Post-upload undo window**: For ~30 minutes after upload, show "Submitted as [Alice] — wrong? Undo and re-pick." Undo soft-deletes the submission (or moves it to a `teacher_review` state) and clears the session.
**Also enforce on the write side**: `student_upload` (`app.py:7996`) must check `session['student_id_<asn>'] == form_student_id`. Without this, anyone with the classroom code can upload as anyone in the class.
**Acceptance:** (a) Mis-clicking a name on submit page never traps you; (b) URL-fuzzing `submission_id` returns 403; (c) Mid-flow rotation between two names with no submissions is free, but flipping to a name that has a submission requires the classroom code.

---

### UP-06 — Boot-time stuck-submission sweeper
**Status:** DONE — `_sweep_stuck_submissions(app)` in `db.py`, called from `init_db()` after `_migrate_add_columns`. Idempotent; flips in-flight submissions >10 min old to `error` with a clear message and sets `marked_at`.
**Why:** The job system is in-memory (`jobs = {}` at `app.py:271`). Every Railway redeploy mid-bulk-mark leaves submissions stuck at `status='pending'` forever — they're invisible to the bulk loop's exception handler because the process died.
**Where:** `db.py:_migrate_add_columns` (or a new boot-path function called from there). Add ~15 lines.
**Fix:**
```python
# In _migrate_add_columns or a new _sweep_stuck_submissions()
stale_threshold = datetime.utcnow() - timedelta(minutes=10)
stuck = Submission.query.filter(
    Submission.status.in_(['pending','processing','extracting','preview']),
    Submission.submitted_at < stale_threshold,
).all()
for s in stuck:
    s.status = 'error'
    s.set_result({'error': 'Marking worker died during deploy — please retry.'})
db.session.commit()
```
**Acceptance:** After a forced kill mid-bulk-mark, on next boot the stuck rows flip to `error` with a clear message.

---

### UP-07 — Daily DB backups to cloud storage
**Status:** PARTIAL — `scripts/backup_db.sh` (pg_dump → gzip → s3/b2), `scripts/restore_db.sh` (TARGET_DATABASE_URL-gated, asks YES before write), README section with cron + restore-drill instructions. **Operator action required**: schedule the cron job (Railway / GitHub Actions), provision the bucket, and run a real restore drill before declaring DONE.
**Why:** No backup story. If Railway PG is your DB, snapshot policy is on you; if it's still SQLite on a Railway volume, a redeploy without mount = total data loss. "I lost a term's marks" is the only unrecoverable failure in the audit.
**Where:** New `scripts/backup_db.sh` + Railway Cron (or a GitHub Actions cron).
**Fix:** Daily `pg_dump | gzip | aws s3 cp s3://.../$(date +%F).sql.gz` (or Backblaze B2 — cheaper). 30-day retention. Document a one-command restore in `README.md`. **Run a real restore drill once** before declaring it done — backups you haven't restored are theatre.
**Acceptance:** A backup file appears in S3 daily; you've restored one to a scratch DB successfully.

---

### UP-08 — Fix `mark_script` callsite drift (calibration in all 3 paths)
**Status:** DONE — extracted `_build_calibration_block_for(asn, sub=None)` in `app.py`. `_run_submission_marking` now uses the helper; `run_bulk_marking_job` pre-resolves the block once per job (same for every student in the assignment) and passes it to `mark_script`. `run_marking_job` is demo-only (no Assignment/teacher) so no calibration applies.
**Why:** `mark_script` has kwargs `calibration_block` and `band_overrides`. Only `_run_submission_marking` (`app.py:4364`) passes them. The single-marking path (`run_marking_job`, `app.py:591`) and the bulk path (`run_bulk_marking_job`, `app.py:3867`) silently use defaults. So calibration only fires when re-marking — not first-marking or bulk-marking. Direct backwards-compat policy violation (`CLAUDE.md:169`).
**Where:** Extract `build_calibration_block(...)` from `app.py:4323-4351` into a helper. Call it from `run_marking_job` (~line 590) and `run_bulk_marking_job` (~line 3866).
**Acceptance:** Set a calibration record, mark a fresh submission, verify calibration is reflected. Repeat for bulk. Both currently fail; both should pass.

---

### UP-09 — `ensure_ascii=False` on JSON dumps
**Status:** DONE — set_script_pages / set_result / set_extracted_text / set_student_text (db.py), chat SSE stream (app.py:3470-3488), exemplar_analysis_json (app.py:6140,6155), propagated_to (app.py:4691,4751,9156,9216), department-insight + class-insight DepartmentConfig saves (app.py:2976/2978, 3485/3487 — added after Python reviewer flagged missed AI-text serialization sites).
**Why:** Every Chinese/Tamil/Malay-diacritic character in stored AI feedback or student names gets serialised to `\uXXXX`. Storage works but DB inspection is unreadable, byte cost is ~5× per CJK char, and CSV exports are garbled. This is Singaporean school context — direct impact.
**Where:** `db.py:706, 715, 724, 733` (all 4 `set_*` methods on Submission). Also `app.py` — search for `json.dumps(` and audit the 3 nearest `result_json` adjacent dumps.
**Fix:** `json.dumps(data, ensure_ascii=False)` everywhere.
**Acceptance:** Insert a submission with a name like "黃志强" or "முருகன்"; `SELECT result_json FROM submissions` shows the character, not `黃`.

---

## Phase 2 — Reliability & Performance

---

### UP-10 — `defer()` blob columns on insights queries
**Status:** DONE — Added `Submission.query_no_blobs()` classmethod (db.py) deferring `script_bytes`, `script_pages_json`, `extracted_text_json`, `student_text_json`. Migrated `_missed_dots_for_class`, `teacher_widget_performance_trend`, `teacher_widget_consultation`, `teacher_widget_encourage`, `teacher_widget_weak_questions`, `teacher_widget_submission_rate_trend`, `department_insights_data`, `_build_class_performance_data`, `teacher_download_all` to use the helper. Skipped `teacher_remark_all_submissions` (legitimately needs `get_script_pages()`).
**Why:** Single biggest perf win. Every analytics route loads full student PDFs into RAM (`script_bytes`, `script_pages_json`, `extracted_text_json`, `student_text_json`). 40-student class = 100-300 MB per request. Single 100-thread gunicorn worker → 5-10 concurrent HOD insight requests = OOM-kill on 512 MB Railway dyno.
**Where:** Fix these 12 routes (copy pattern from `app.py:3705` which already does it right):
- `app.py:2541` `department_insights_data`
- `app.py:2910` `_build_class_performance_data`
- `app.py:1778, 1916, 2058, 2170, 2311, 2379, 2458` (the `teacher_widget_*` family)
- `app.py:7070` `teacher_remark_all`
- `app.py:5685` `teacher_download_all`
- `app.py:773` `class_page` for HOD
**Fix:**
```python
from sqlalchemy.orm import defer
.options(
    defer(Submission.script_bytes),
    defer(Submission.script_pages_json),
    defer(Submission.extracted_text_json),
    defer(Submission.student_text_json),
)
```
Even better: add a `Submission.query_no_blobs()` classmethod in `db.py` so future callers can't forget.
**Acceptance:** HOD insights page render time drops dramatically (measure before/after). RAM per request <50 MB.

---

### UP-11 — Reorder system prompt for OpenAI/Qwen prefix caching
**Status:** DONE — `_build_rubrics_prompt` and `_build_short_answer_prompt` (ai_marking.py) now lead with static rules ("You are an experienced teacher..." + task list + FEEDBACK/CORRECTION/IDEA rules + JSON schema). Per-assignment variables (`subject`, `calibration_block`, `review_section`, `marking_section`, `language_block`, `rubrics_section`/`reference_section`) appended at the bottom under a `--- PER-ASSIGNMENT CONTEXT ---` header. The per-STUDENT variable `overrides_section` (band_overrides) moved out of the system prompt entirely and is appended to the user message before the question paper. Anthropic cache_control still wraps the whole system prompt.
**Why:** Anthropic gets ~80% input-token discount via `cache_control`. OpenAI/Qwen also cache automatically on prefixes >1024 tokens — but `_build_*_prompt` puts variable per-student text (`subject`, `band_overrides`) **above** the static rules in the system prompt, breaking prefix match.
**Where:** `ai_marking.py:874, 1080` (where `system_prompt` is built).
**Fix:** Reorder so static content (rules, rubric extraction instructions, JSON schema) comes first; move `band_overrides` into the user message, not the system prompt. Estimated dollar savings: same ~80% input-cost cut as Anthropic, only applies when teacher picks OpenAI/Qwen.
**Acceptance:** After UP-12 (usage logging) is in, verify OpenAI bulk-mark shows cached-token reads on calls 2..N.

---

### UP-12 — Per-AI-call usage logging
**Status:** DONE — New `Submission.usage_json` column + `get_usage()` / `append_usage()` methods (db.py); migration with idempotent guard. Anthropic capture via `stream.get_final_message().usage` (input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens); OpenAI/Qwen capture via `response.usage` + `prompt_tokens_details.cached_tokens`. Module-level price table `_PROVIDER_PRICES_PER_M`; thread-local `_call_state.last_usage` plus `consume_last_usage()` exported helper (cleared per-call so ThreadPoolExecutor re-use can't leak across runs). Structured `logger.info` line `ai_call provider=… in=… out=… cache_read=… ms=… cost=…` on every call. Persisted in `_run_submission_marking`, `_run_submission_extraction`, and the bulk loop (`bulk_usage_entry` initialised per iteration).
**Why:** Zero token/cost/latency observability. Cannot verify prompt caching is hitting, cannot compare providers, cannot detect a prompt edit that silently breaks caching.
**Where:** `ai_marking.py:285-365` (`make_ai_api_call`).
**Fix:** Switch Anthropic from `get_final_text()` to `get_final_message()`; capture `usage.input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`. For OpenAI/Qwen, capture `response.usage`. Persist on `Submission.usage_json` (new column, lazy-fill at write). Log one structured line per call:
```python
logger.info(
    "ai_call provider=%s model=%s in=%d out=%d cache_read=%d ms=%d cost=%.4f",
    provider, model, in_tok, out_tok, cache_read_tok, ms, cost_usd,
)
```
Add a price table dict in `ai_marking.py` keyed on model.
**Acceptance:** Tail Railway logs during a bulk-mark; each call emits a line. After 5 calls on same assignment, `cache_read` should jump on Anthropic.

---

### UP-13 — Retry + backoff on transient AI failures
**Status:** DONE — `tenacity>=8.2` added to requirements.txt; `_ai_retry` decorator (stop_after_attempt=3, wait_exponential 2-30s, before_sleep_log) applied to `make_ai_api_call` (ai_marking.py). Retry filter `_TRANSIENT_AI_ERRORS` covers Anthropic + OpenAI `RateLimitError`/`APIConnectionError`/`APITimeoutError`/`InternalServerError`. New `_openai_chat_create` wrapper (also decorated) used by `generate_exemplar_analysis`, `_run_text_completion`, `_run_feedback_helper` so the helper paths share retry semantics.
**Why:** Zero retry. A single 429/500/connection-reset mid-bulk-mark marks that student `error`. Estimated bulk-row error rate drops from ~3% to <0.3%.
**Where:** `ai_marking.py:298, 355`. Wrap `client.messages.stream(...)` and `client.chat.completions.create(...)`.
**Fix:** `pip install tenacity`, then:
```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)),
)
def _call_anthropic(...): ...
```
**Acceptance:** Simulate a 429 by setting `ANTHROPIC_BASE_URL` to a 429-returning endpoint mid-mark; the call retries 3 times before giving up.

---

### UP-14 — Encrypt API keys in session
**Status:** DONE — `_set_session_keys()` encrypts the keys dict with Fernet (via existing `db._get_fernet()`) and stores the ciphertext string in `session['api_keys']`. `_get_session_keys()` decrypts on read; legacy dict-shape sessions still accepted for graceful rollover; decrypt failures return `{}` with a `logger.warning` so operators notice key-rotation/tamper events. `save_keys` route migrated to `_set_session_keys`.
**Why:** Flask cookies are signed (tamper-proof) but not encrypted. Anyone reading the cookie (browser extension, shared device) gets the API keys in base64.
**Where:** `app.py:846` (and the corresponding read).
**Fix:** Encrypt with existing `_get_fernet()` before storing in `session['api_keys']`; decrypt on read. Cheap.
**Acceptance:** `Set-Cookie` value decoded shows ciphertext, not the API key.

---

### UP-15 — Persist bulk-job state to DB
**Status:** DONE — New `BulkJob` model (`db.py`: id PK, kind, status, assignment_id, subject, progress_json, results_json, skipped_json, errors_json, error_message, started_at, finished_at) with `get_progress` / `set_progress` / `get_results` / `set_results` / `append_error` helpers. Boot-time sweeper `_sweep_stuck_bulk_jobs` flips processing>30min to `error` (tz-aware comparison). New `_bulk_job_create` / `_bulk_job_load` / `_bulk_job_update` helpers in app.py. `bulk_mark` route creates a row instead of dict entry; `run_bulk_marking_job` writes progress + final results to DB; `/status/<job_id>`, `/bulk/download/<job_id>`, `/bulk/overview/<job_id>` load from DB first with in-memory fallback for single-marking demo jobs. Added `_check_assignment_ownership` after load on persistent jobs (closes a side IDOR).
**Why:** Full fix for in-memory `jobs` dict loss on restart. UP-06 sweeper papers over symptoms; this fixes the cause. Also unblocks a sane bulk blueprint split (UP-43).
**Where:** New `BulkJob` model in `db.py` (id, assignment_id, status, progress_json, errors_json, started_at, finished_at). Migrate `jobs[]` reads/writes in `app.py` to query/update this table. The status poller (`/status/<job_id>`) reads from DB.
**Effort:** ~half day.
**Acceptance:** Kill the process mid-bulk-mark, restart, the job's status reflects partial progress (not just "stuck").

---

### UP-16 — Bounded ThreadPoolExecutor for fan-outs
**Status:** DONE — Module-level `_MARK_EXEC = ThreadPoolExecutor(max_workers=4, thread_name_prefix='mark')` + `_submit_marking()` helper. Converted: `run_marking_job` (demo path), all `_run_submission_marking` thread starts (single mark, force-remark, re-mark-all loop, student_confirm), `_run_submission_extraction`, `_run_categorisation_worker`, `_run_insight_extraction_worker`, both `_run_propagation_worker` paths. Re-mark-all on a 40-student class now caps at 4 concurrent provider calls. Boot warmup, bulk dispatcher, and print-all-reports stay as raw threads (long-running orchestrators that manage their own concurrency).
**Why:** Re-marking 40 students currently spawns 40 simultaneous outbound API calls — hits rate limits, double-bills on retries.
**Where:** `app.py:7094, 6859, 6883, 7944, 8064, 8111` (raw `threading.Thread(...).start()`).
**Fix:** One module-level `_MARK_EXEC = ThreadPoolExecutor(max_workers=4)`. Replace `threading.Thread(target=_run_x, args=(...)).start()` with `_MARK_EXEC.submit(_run_x, ...)`.
**Acceptance:** Re-mark a 40-student class; concurrent AI calls capped at 4.

---

### UP-17 — File upload MIME validation
**Status:** DONE — Extended `_detect_mime` to recognise HEIC/HEIF brands. Added `_ALLOWED_UPLOAD_MIMES` allow-list (PDF, JPEG, PNG, HEIC, HEIF) and `_validate_upload_blobs(blobs, label)` helper. Wired into student_upload, teacher_assignment_submit, teacher single-mark, demo mark, teacher_create (assignment files), teacher_assignment_edit (replaced files). Bulk PDF path adds a strict `_detect_mime == 'application/pdf'` check (415 if not). requirements.txt floors bumped to `Pillow>=10.3.0,<12`, `pillow-heif>=0.18`, `pdf2image>=1.17` for Pillow CVE coverage.
**Why:** Pillow has had RCE CVEs (e.g. CVE-2023-50447). Currently extension-checked, not magic-byte-checked.
**Where:** `app.py:8020, 5112, 4041` (student/single/bulk upload paths). The helper exists at `_detect_mime` — just call it.
**Fix:**
```python
mime = _detect_mime(data)
if mime not in {'application/pdf', 'image/jpeg', 'image/png', 'image/heic', 'image/heif'}:
    return jsonify({'error': 'Unsupported file type'}), 415
```
Also pin in `requirements.txt`: `Pillow>=10.3.0,<12`, `pillow-heif>=0.18`, `pdf2image>=1.17`.
**Acceptance:** Upload a `.jpg` renamed to `.pdf` → rejected. Upload a real PDF → accepted.

---

### UP-18 — ProxyFix middleware
**Status:** DONE — `app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)` immediately after `Flask(__name__)` (app.py). One trusted hop matches Railway's single reverse-proxy layer.
**Why:** Rate limiter sees only Railway's load-balancer IP. One teacher hitting refresh trips it for everyone.
**Where:** `app.py` near boot.
**Fix:**
```python
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
```
**Acceptance:** Logs show real client IPs in rate-limit checks, not Railway LB IPs.

---

### UP-19 — `secrets.compare_digest` for code comparisons
**Status:** DONE — Replaced `==` with `secrets.compare_digest(str(...), str(...))` at: `verify_code` TEACHER_CODE branch, legacy ACCESS_CODE branch, and `student_verify` classroom_code. The `if _tc:` falsy guard prevents `compare_digest('', '')` ever firing; `or ''` on `asn.classroom_code` keeps None-safety.
**Why:** Theoretical timing attack on 8-char codes. Cheap to close.
**Where:** `app.py:811, 829, 7303, 797`.
**Fix:** Replace `code == _tc` with `secrets.compare_digest(code, _tc)`. Wrap both args in `str(...)` if they might be None.
**Acceptance:** Codes still verify correctly; eyeball-check no regressions on login + student submit verify.

---

## Phase 3 — UX Recovery

(UP-05 above is the biggest UX item; these are the rest.)

---

### UP-20 — Modal accessibility helper + standardisation
**Status:** TODO
**Why:** ~20 modals across `teacher_detail.html`, `class.html`, `bank.html` have no `role="dialog"`, no `aria-modal`, no focus trap, no Escape on most of them. Teachers using keyboard or screen-magnifier have no idea they exist as dialogs.
**Where:** Create `templates/_modal.html` macro + `static/js/modal.js`.
**Fix:** Macro:
```jinja
{% macro modal(id, title) %}
<div class="modal" id="{{ id }}" role="dialog" aria-modal="true" aria-labelledby="{{ id }}-title" hidden>
  <div class="modal-content">
    <h3 id="{{ id }}-title">{{ title }}</h3>
    {{ caller() }}
  </div>
</div>
{% endmacro %}
```
JS: focus trap, Escape to close, click-outside to close. Migrate existing modals one PR per page.
**Acceptance:** Tab-cycle inside an open modal doesn't escape; Escape closes; VoiceOver announces as dialog.

---

### UP-21 — `teacher_insights.html` mobile layout
**Status:** TODO
**Why:** `width=1280` viewport forces 3.3× horizontal scroll on a 390 px phone. The "lock to desktop" was a workaround for gridstack having no mobile layout.
**Where:** `templates/teacher_insights.html:8, 1288`.
**Fix:** Either enable gridstack's `disableOneColumnMode: false` (default), or — simpler — hide the gridstack on `<=600px` and show a "Open on desktop for the full dashboard" card with the 2-3 most useful summary numbers.
**Acceptance:** Open the page on a 390 px viewport; no horizontal scroll.

---

### UP-22 — `class.html` mobile `@media` rules
**Status:** TODO
**Why:** Zero `@media` rules. Creating an assignment on phone is a crowded mess.
**Where:** `templates/class.html`, `templates/_assignment_form_fields.html`.
**Fix:** Add 3-4 `@media (max-width: 600px)` rules to collapse multi-column form grids to single column. Copy the pattern from `templates/dashboard.html` which already has good responsive grids.
**Acceptance:** Assignment creation form usable on 390 px without horizontal scroll.

---

### UP-23 — Submissions table mobile-stack view
**Status:** TODO
**Why:** Table is ~700 px wide; 390 px phone forces constant horizontal swipe.
**Where:** `templates/teacher_detail.html:58, 68-76`.
**Fix:** `@media (max-width: 600px)`: hide the table, render each row as a stacked card (name | status badge | score | action button). Keep `overflow-x:auto` as fallback.
**Acceptance:** Submissions list scannable on phone without horizontal swipe.

---

### UP-24 — Extract `teacher_detail.html` inline JS/CSS
**Status:** TODO
**Why:** 1909-line template, 1090 lines of inline JS, 345 of inline CSS. Re-downloaded on every assignment view.
**Where:** `templates/teacher_detail.html`.
**Fix:** Extract:
- CSS lines 6-351 → `static/css/teacher_detail.css`
- JS lines 1090-1313 (bulk-marking module) → `static/js/bulk_marking.js`
- JS lines 1410-1647 (re-mark polling) → `static/js/remark_polling.js`
Pass `ASSIGNMENT_ID` once via a `<meta>` tag or `data-` attr; collapse the 4 duplicate `ASSIGNMENT_ID / FB_ASSIGNMENT_ID / REMARK_ASSIGNMENT_ID / ASSIGNMENT_ID_FOR_DRAFTS` globals.
**Acceptance:** Template drops to ~600 lines of pure markup; browser caches the JS+CSS across class views.

---

### UP-25 — Gate `screen_pet.js` on localStorage
**Status:** TODO
**Why:** 45 KB script loads on every authenticated page even when the turtle is opt-in. ~1.35 MB extra per teacher per day on a 30-page-load workday.
**Where:** `templates/base.html:118-120`.
**Fix:** Replace `<script defer src="...screen_pet.js"></script>` with an inline 3-liner:
```html
<script>
  if (localStorage.getItem('screen_pet_enabled') === 'true') {
    document.head.appendChild(Object.assign(document.createElement('script'), {src: '/static/js/screen_pet.js', defer: true}));
  }
</script>
```
**Acceptance:** Network tab shows no `screen_pet.js` request when disabled.

---

### UP-26 — Replace inline `onclick=` with `data-action` delegation
**Status:** TODO
**Why:** 178 inline handlers across templates leak ~40 globals to `window`. Blocks CSP tightening; a name collision silently breaks a button.
**Where:** `templates/teacher_detail.html` (37), `templates/index.html` (27), `templates/class.html` (23), `templates/setup_wizard.html` (22), others.
**Fix:** Existing kebab handler at `teacher_detail.html:994` is the template — copy that pattern. One page at a time.
**Acceptance:** `grep -rn 'onclick="' templates/` shrinks page by page; no functional change.

---

## Phase 4 — Code Health

---

### UP-27 — Convert `logger.error(f"... {e}")` to `logger.exception(...)`
**Status:** TODO
**Why:** `logger.exception` is used **once** in the codebase. `logger.error(f"... {e}")` is used 30+ times — every catch site loses the traceback. Bulk-mark failure logs read "Bulk job xyz, student Tan Wei failed: 'NoneType' object has no attribute 'get'" with no stack, no line. Single most leveraged fix in the audit.
**Where:** Every `except Exception as e:` block in `app.py` (~30 sites) and `ai_marking.py` (~5 sites).
**Fix:** sed-like replacement. `logger.error(f"... failed: {e}")` → `logger.exception("... failed")`.
**Acceptance:** Force a marking error in dev; logs show full stack trace.

---

### UP-28 — Session hardening (clear on login, lifetime, debug guard)
**Status:** TODO
**Why:** Three related session issues:
- No `session.clear()` on login (`app.py:802, 817, 823, 830, 3595, 3633`) — session fixation possible.
- No `PERMANENT_SESSION_LIFETIME` — sessions live forever.
- Werkzeug debug console enabled by `FLASK_DEBUG=true` env var on `0.0.0.0` — RCE risk if PIN brute-forced.
**Where:** `app.py` (boot + verify-code routes + `app.run(...)` at bottom).
**Fix:**
```python
# In app.config block:
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
# In each /verify-code success branch, before assigning teacher_id:
session.clear()
session.permanent = True
session['teacher_id'] = teacher.id
# In app.run:
debug = os.getenv('FLASK_DEBUG','false')=='true' and os.getenv('FLASK_ENV')=='development'
app.run(host='127.0.0.1' if debug else '0.0.0.0', debug=debug)
```
**Acceptance:** Login regenerates session ID; 8 h idle logs out; `FLASK_DEBUG=true` on `0.0.0.0` refuses to boot.

---

### UP-29 — CSP + HSTS + SRI on CDN scripts
**Status:** TODO
**Why:** No CSP, no HSTS, no Subresource Integrity on 7 CDN-loaded scripts (KaTeX, mhchem, auto-render, gridstack, Chart.js, pdfjs-dist). A jsdelivr/cdnjs compromise lands a malicious build in every teacher session — full session takeover + key exfil from `session['api_keys']`.
**Where:** `app.py:228` (`add_security_headers`); `templates/base.html:7, 17-20`; `teacher_insights.html:11, 408, 409`; `review.html:155`; `bank_preview.html:102, 104, 105`; `exemplars.html:106, 108, 109`.
**Fix:** Add headers:
```python
resp.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: blob:; ..."
resp.headers['Strict-Transport-Security'] = 'max-age=63072000; includeSubDomains'
```
Pin SRI on each CDN `<script integrity="sha384-..." crossorigin="anonymous">`. Use jsdelivr's "Copy SRI" button.
**Acceptance:** Browser blocks an inline `eval()` from a CDN script if injected; SRI mismatch causes browser to refuse the file.

---

### UP-30 — `_jobs_lock` mutex on `jobs` dict
**Status:** TODO
**Why:** Module-level `jobs = {}` mutated from foreground requests + background threads with no lock. `_PRINT_JOBS` already has `_PRINT_JOBS_LOCK` — same pattern needed here. Likely cause of observed "stuck at 99%" half-built progress reads.
**Where:** `app.py:271, 608-613, 910, 3860, 3946-3948`.
**Fix:** Add `_jobs_lock = threading.Lock()`. Wrap every read-modify-write on `jobs[k]`. Replace bare `jobs[k] = v` with `with _jobs_lock: jobs[k] = v`.
**Acceptance:** No functional change visible; eliminate iteration-during-mutation risk. (Becomes moot after UP-15 lands.)

---

### UP-31 — Smoke test scaffold (5-7 tests)
**Status:** TODO
**Why:** Zero tests. Every change is unverified. These 5-7 catch ~80% of "broke main on Friday evening" regressions.
**Where:** New `tests/` directory + `pytest.ini` + `requirements-dev.txt`.
**Fix:** `pip install pytest pytest-flask`, write:
1. `test_boot.py` — `GET /` returns 200, `db._migrate_add_columns` is idempotent (run twice).
2. `test_auth.py` — `POST /gate` with `TEACHER_CODE` sets session; bad code returns 401.
3. `test_class_crud.py` — Create class → list → delete round trip.
4. `test_ai_parse.py` — `ai_marking.parse_ai_response` against malformed JSON, truncated JSON, smart quotes, `<think>` blocks.
5. `test_pdf_compile.py` — `pdf_generator.generate_report_pdf({...minimal...})` returns non-empty bytes.
6. `test_error_paths.py` — bad submission ID 404s; oversize upload 413s.
7. `test_idor.py` (after UP-02/UP-05): teacher A cannot fetch teacher B's class assignments.
**Acceptance:** `pytest` green locally; add to CI.

---

### UP-32 — Sentry free tier
**Status:** TODO
**Why:** Plain `logging.INFO` to stderr → Railway logs (ephemeral, no search). When a teacher says "marking failed for X", you can't grep for it.
**Where:** `app.py` boot path.
**Fix:** `pip install sentry-sdk[flask]`; in `app.py`:
```python
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
if os.getenv('SENTRY_DSN'):
    sentry_sdk.init(dsn=os.getenv('SENTRY_DSN'), integrations=[FlaskIntegration()], traces_sample_rate=0.1)
```
Free tier covers ~5k events/month. Pair with UP-27.
**Acceptance:** Force a 500 in dev with `SENTRY_DSN` set; event appears in Sentry within seconds.

---

### UP-33 — Pin `requirements.txt`
**Status:** TODO
**Why:** All entries use `>=` floors with no upper bounds. Every Railway rebuild can pull a newer minor that broke something. "Worked yesterday, broken today" deploys with no code change.
**Where:** `requirements.txt`.
**Fix:** Run `pip freeze > requirements.txt` once on a known-good build. Add `runtime.txt` or `.python-version` with `python-3.11.10` for local parity with Dockerfile. Optionally add Dependabot weekly.
**Acceptance:** `pip install -r requirements.txt` produces a deterministic environment.

---

### UP-34 — `gunicorn` config hardening
**Status:** TODO
**Why:** `-w 1 --threads 100` means one bad LuaLaTeX OOM kills everything mid-bulk-mark. No worker recycling — long-running process bloats.
**Where:** `Procfile` and `Dockerfile`.
**Fix:** `gunicorn -w 2 --threads 50 --worker-class gthread --timeout 300 --max-requests 1000 --max-requests-jitter 100 --bind 0.0.0.0:$PORT app:app`. Optionally add `/healthz` route that returns 200 without DB touch.
**Acceptance:** One worker dying doesn't kill the other; deploy logs show worker recycling after ~1000 reqs.

---

## Phase 5 — Refactor

---

### UP-35 — Custom exception classes
**Status:** TODO
**Why:** `mark_script` returns `dict | {'error': str}`. Every caller must remember to check `.get('error')` before reading `.get('questions')`. No grep ever surfaces "marking failure paths" because they're not exceptions.
**Where:** `ai_marking.py` (define + raise), `app.py:584, 4292` (catch at boundary).
**Fix:**
```python
class MarkingError(Exception): pass
class AIProviderError(MarkingError): pass
class ResponseParseError(MarkingError): pass
```
Raise from `mark_script`. Catch in `run_marking_job` / `_run_submission_marking` exactly once and convert to the persisted `{'error': ...}` shape there. Pairs naturally with UP-27 (`logger.exception`).
**Acceptance:** `grep "result.get('error')" app.py` drops dramatically.

---

### UP-36 — Type hints on top 3 functions
**Status:** TODO
**Why:** Zero return-type annotations across 14,350 lines. Future Claude sessions burn context re-reading bodies to learn shapes.
**Where:** Start with three highest-leverage:
- `Submission.get_result() -> dict[str, Any]` (`db.py:708`)
- `mark_script(...) -> MarkResult | ErrorResult` (`ai_marking.py:1240`) — pairs with UP-35
- `_resolve_api_keys(...) -> dict[str, str]` (`app.py:552`)
**Fix:** Add `from typing import Any` (Python 3.9+ has built-in generics, no `List[...]` needed). Annotate top 3. Optionally `pip install mypy --user` and run on those files to catch real type bugs.
**Acceptance:** IDE / mypy can flag a caller treating the result wrong.

---

### UP-37 — Status `Enum` + `_utc(dt)` helper
**Status:** TODO
**Why:** Status strings (`'pending', 'processing', 'done', 'error', 'extracting', 'preview'`) are scattered as raw literals in 430 places — a typo silently breaks status filters. Timezone-aware coercion (`if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)`) is duplicated 9× — one missed location and prod gets `TypeError`.
**Where:** `db.py` (new Enum + helper).
**Fix:**
```python
class SubmissionStatus(str, Enum):
    PENDING = 'pending'
    EXTRACTING = 'extracting'
    PREVIEW = 'preview'
    PROCESSING = 'processing'
    DONE = 'done'
    ERROR = 'error'

def utc(dt: datetime | None) -> datetime | None:
    if dt is None: return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
```
Migrate progressively — `str` subclass means Jinja templates keep working.
**Acceptance:** First migration site uses `SubmissionStatus.DONE`; rest can follow at leisure.

---

### UP-38 — Centralise provider dispatch in `ai_marking.py`
**Status:** TODO
**Why:** Three near-identical provider-branching blocks: `generate_exemplar_analysis` (`ai_marking.py:1412-1440`), `_run_text_completion` (`1467-1494`), `_run_feedback_helper` (`1508-1536`). Plus `make_ai_api_call`. Every model change is a 4-place edit. Already drifted: helper paths skip prompt caching.
**Where:** `ai_marking.py`.
**Fix:** Extract `_simple_completion(provider: str, model: str, system: str, user: str, max_tokens: int) -> str` shared by all four. Single source of truth for `max_completion_tokens` vs `max_tokens`, Qwen base_url, cache headers.
**Acceptance:** All four helpers route through one function; model-string change is one edit.

---

### UP-39 — Split `app.py`: student blueprint
**Status:** TODO
**Why:** `app.py` is 9,250 lines. Student routes (`/submit/*`, `/feedback/*`) are the cleanest split — fully self-contained, distinct URL prefix, no shared module-level state besides `jobs` (which goes away with UP-15).
**Where:** Extract `app.py:7272-8189` (~900 lines) → `routes/student.py`.
**Fix:** Create Flask Blueprint, register in `app.py`. Helpers (`_require_student_auth_for`, etc.) move with it. **Do this AFTER UP-05** (student recovery flow) and **AFTER UP-15** (persistent job state) — otherwise it's a refactor + behavior change in one PR.
**Acceptance:** `app.py` drops by 900 lines; all `/submit/*` routes still work.

---

### UP-40 — Split `app.py`: insights blueprint
**Status:** TODO
**Why:** 1900 lines of `teacher_widget_*` and analytics. Second-largest cohesive section.
**Where:** Extract `app.py:1612-3500` → `routes/insights.py` + `insights_helpers.py`.
**Fix:** Extract shared helpers (`_check_class_access_for_teacher`, `_submission_percent`) to a separate module first; then move routes. **Do AFTER UP-10** (defer blob columns) so the move is structural, not behavioral.
**Acceptance:** All insights pages still render.

---

### UP-41 — Split `app.py`: bulk blueprint
**Status:** TODO
**Why:** 450 lines tightly coupled to `jobs` dict.
**Where:** Extract `app.py:3777-4226` → `routes/bulk.py`.
**Fix:** **Do AFTER UP-15** (persistent job state).
**Acceptance:** Bulk-mark + status poll + download all still work.

---

### UP-42 — `MarkResult` and `BulkMarkingContext` dataclasses
**Status:** TODO
**Why:** Marking `result` dict has ~14 keys, passed by structural convention only. `run_bulk_marking_job` has 18+ positional params — already a reason calibration kwargs weren't threaded through (UP-08).
**Where:** New `ai_marking.py` types.
**Fix:**
```python
@dataclass(frozen=True)
class MarkResult:
    questions: list[Question]
    overall_feedback: str = ''
    recommended_actions: list[str] = field(default_factory=list)
    ...
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, d: dict) -> 'MarkResult': ...  # tolerates missing fields per backwards-compat policy
```
Backwards-compat: keep `to_dict()` for JSON persistence. Pairs with UP-35 + UP-36.
**Acceptance:** Type-checker can flag a writer that drops a load-bearing field.

---

## Phase 6 — Polish

---

### UP-43 — Refresh `CLAUDE.md`
**Status:** TODO
**Why:** `CLAUDE.md:121, 137, 192` still references `subject_family` and `classify_subject_family`, which `db.py:200, 333, 406` explicitly drops. The schema-evolution policy IS the contract — when it's stale, every future Claude session is misled.
**Where:** `CLAUDE.md`.
**Fix:**
- Strip `subject_family` mentions from "load-bearing fields" list; replace with the actual current set (`theme_key`, `categorisation_status`).
- Document the `STUDENT_GROUPING_UI_ENABLED` env var that's referenced in `app.py:46` but not in the env-var table.
- Add a "branch workflow" note: `staging` and `sandbox_*` branches diverge — never merge between them (per user's memory).
**Acceptance:** A fresh Claude session reading CLAUDE.md doesn't reference any dropped column.

---

### UP-44 — Generic error templates (404, 413, 429, 500)
**Status:** TODO
**Why:** Generic 500 returns plain text "Internal server error. Check server logs." No 404 handler (Flask default). LaTeX error log can leak tmpdir paths.
**Where:** New `templates/_error.html`; register handlers in `app.py:240, 245`. Also fix `pdf_generator.py:1006` — drop log tail from the public `RuntimeError`, keep it in `logger.exception` only.
**Fix:** Branded error page with a "Report to Joe" mailto link. Register handlers for 404, 413, 429, 500.
**Acceptance:** Forcing a 500 in dev shows the branded page; LaTeX log doesn't appear in HTTP response.

---

### UP-45 — `.gitignore` cleanup
**Status:** TODO
**Why:** 5-line `.gitignore` misses `.DS_Store` (already tracked!), `.venv/`, `.idea/`, `.vscode/`, `*.log`.
**Where:** `.gitignore`.
**Fix:** Append GitHub's standard Python `.gitignore`. Then `git rm --cached .DS_Store` to untrack the existing file. Also add `.env` if you haven't already (grep showed no leaked secrets — keep it that way).
**Acceptance:** `git status` no longer surfaces `.DS_Store` or local env files.

---

### UP-46 — Bulk-mark per-student error surfacing
**Status:** TODO
**Why:** Bulk-mark failures only land in server logs. Teacher sees "done" with N missing students, no reason. They re-click force-remark per student to discover what failed.
**Where:** `app.py:3885` and the bulk completion path.
**Fix:** Append `{student_id, student_name, error_class, error_msg, retryable: bool}` to `jobs[job_id]['errors']` (or `BulkJob.errors_json` after UP-15). Surface a "3 failed — retry these" affordance on the class page after bulk-mark completes.
**Acceptance:** Force a fake error mid-bulk; UI lists which students failed and offers a one-click retry.

---

### UP-47 — Unicode `NFC` normalization on student names
**Status:** TODO
**Why:** No normalization anywhere. A Tamil name in NFD form vs NFC form will compare unequal in dict lookups, sort wrong, produce two cache keys for the same student. Real risk in Singaporean context (CSV from MOE vs typed names).
**Where:** Every place that ingests student names: `app.py:1322, 1437` (add_students-style endpoints), CSV parsing paths.
**Fix:** `import unicodedata; name = unicodedata.normalize('NFC', name.strip())` at write boundaries.
**Acceptance:** Importing the same student via two different sources produces one row, not two.

---

### UP-48 — Race condition on "single final" submission
**Status:** TODO
**Why:** `_prepare_new_submission` (`app.py:463-508`) does count → update → insert without a row lock. Two simultaneous submissions interleave: both pass the cap check, both flip prior drafts, both insert `is_final=True`. Result: two finals or cap+1 drafts.
**Where:** `app.py:463-508`, `db.py:669` (Submission model).
**Fix:** Either:
- Wrap in `with db.session.begin_nested(): db.session.query(Submission).filter_by(student_id=..., assignment_id=...).with_for_update().all()` — row lock, simple.
- Add a partial unique index: `CREATE UNIQUE INDEX uniq_final_submission ON submissions (student_id, assignment_id) WHERE is_final = TRUE` — DB-layer guarantee.
**Acceptance:** Two parallel `/student_upload` calls for the same student produce exactly one final submission.

---

### UP-49 — N+1 fixes (teacher_assignment_detail + dashboard)
**Status:** TODO
**Why:** `teacher_assignment_detail` (`app.py:5571`) does one `Submission.query.filter_by(student_id=s.id, ...).first()` inside `for s in students` — 40 students = 40 round trips per page render. Teacher dashboard does one `COUNT(*)` per class.
**Where:** `app.py:5571, 3688-3689`.
**Fix:**
```python
# Replace per-student lookup with one batch query, then dict lookup in loop
subs = (Submission.query
        .filter_by(assignment_id=assignment_id, is_final=True)
        .options(defer(Submission.script_bytes), defer(Submission.script_pages_json),
                 defer(Submission.extracted_text_json), defer(Submission.student_text_json))
        .all())
subs_by_student_id = {s.student_id: s for s in subs}
# in loop: sub = subs_by_student_id.get(s.id)
```
Dashboard: `db.session.query(Student.class_id, func.count()).filter(Student.class_id.in_(ids)).group_by(Student.class_id).all()` — one query.
**Acceptance:** Page render time drops ~3 s on a 40-student class.

---

## Cross-cutting principles

- **Old data is real data.** Schema evolution policy (CLAUDE.md) is mandatory. Lazy-fill at write + one-shot backfill on boot. Never `if x is None: skip` in readers.
- **Public function signatures are stable.** Adding a kwarg → audit every callsite + plumb through. The `mark_script` drift (UP-08) is exactly the bug this rule prevents.
- **Trust model is teacher-level, not student-level.** Universal edit/delete for teacher resources is fine ("our teachers are responsible"). Student-facing routes need stricter checks — that's the IDOR work in UP-02 + UP-05.
- **Friction for destructive UI** is preferred over always-visible danger buttons (kebab / confirm / two-step).
- **Structural reasoning ≠ verification.** Until UP-31 lands, every change needs a real local smoke test before being marked DONE.
- **No tests = no auto-deletion.** Refactor-clean and similar workflows that require revert-on-test-failure are inoperative until UP-31. Use them analysis-only.

---

## Quick reference: file index

| File | Size | Notes |
|---|---|---|
| `app.py` | ~9 250 lines | Will split into 3 blueprints in Phase 5 |
| `ai_marking.py` | ~3 500 lines | Provider dispatch consolidation in UP-38 |
| `db.py` | ~1 400 lines | New Enum + helpers + dataclasses in Phase 4-5 |
| `pdf_generator.py` | ~1 100 lines | Mostly healthy; UP-44 leak fix |
| `templates/teacher_detail.html` | 1909 lines | UP-24 extract |
| `templates/teacher_insights.html` | 1345 lines | UP-21 mobile |
| `templates/class.html` | 811 lines | UP-22 mobile |
| `templates/submit.html` | 815 lines | UP-05 recovery flow |

## Status legend

- `TODO` — not started
- `IN PROGRESS` — currently being worked
- `DONE` — merged + smoke-tested
- `BLOCKED` — waiting on dependency (note what)
- `WONTFIX` — intentionally not doing (note why)
