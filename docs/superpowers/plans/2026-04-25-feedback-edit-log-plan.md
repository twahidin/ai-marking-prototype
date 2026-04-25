# Feedback Edit Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture every AI-generated feedback string and every teacher edit to it, then use the teacher's prior corrections to calibrate future AI marking prompts.

**Architecture:** Two new SQLAlchemy tables (`feedback_log` for every-version history; `feedback_edit` for the calibration bank). The teacher's existing inline-edit gets a single "Save to calibration bank" checkbox below the textarea (default off). On save, every text edit logs a new version; only opted-in edits also write a bank row. At marking time, the worker looks up the teacher's relevant prior bank rows (by rubric hash and theme key) and prepends them as a calibration block to the AI system prompt. Soft-delete via `active` flag; staleness via `rubric_version` match. No age-based filter anywhere.

**Tech Stack:** Flask + SQLAlchemy on PostgreSQL (Railway production) and SQLite (local dev). Vanilla JS in `static/js/feedback_render.js`. No new Python deps.

**Spec:** `docs/superpowers/specs/2026-04-25-feedback-edit-log-design.md`

**Verification approach:** This repository has no pytest infrastructure (verified — no `tests/`, no `test_*.py`, no `pytest` in `requirements.txt`). Existing plans (e.g. `2026-04-24-exemplar-analysis-plan.md`) verify with `python3 -m py_compile`, `python3 -c` smoke tests, direct `sqlite3` inspection, and `curl` against a running dev server. This plan follows the same pattern. **Do not introduce pytest scaffolding** — it's out of scope.

**Branch:** `feedback_edit_log`, branched off `feed_forward_beta`. Confirm with `git branch --show-current` before starting.

**Type cheat sheet** (verified in `db.py`):

| Existing table | id type |
|---|---|
| `teachers` | `db.String(36)` (UUID) |
| `assignments` | `db.String(36)` (UUID) |
| `submissions` | `db.Integer` autoincrement |

The new FKs must match: `submission_id` → `INTEGER`, `assignment_id` → `VARCHAR(36)`, `edited_by` → `VARCHAR(36)`, `promoted_by` → `VARCHAR(36)`.

---

## Task 1: Schema — `FeedbackLog` and `FeedbackEdit` models

**Files:**
- Modify: `db.py` (append two new model classes near other models; no `_migrate_add_columns` change needed because these are entirely new tables created via `db.create_all()`)

- [ ] **Step 1: Add `FeedbackLog` and `FeedbackEdit` model classes**

Open `db.py`. Find the `Submission` class (around line 312) and the end of the `DepartmentConfig` class (around line 380, the last model in the file). Append the two new models *immediately after* the last model class but *before* any module-level functions. (If unsure, use `grep -n "^class " db.py` to find the class boundaries; insert after the last `class ...:` block.)

```python
class FeedbackLog(db.Model):
    __tablename__ = 'feedback_log'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    submission_id = db.Column(db.Integer, db.ForeignKey('submissions.id'), nullable=False, index=True)
    criterion_id = db.Column(db.String(64), nullable=False)
    field = db.Column(db.String(20), nullable=False)  # 'feedback' | 'improvement'
    version = db.Column(db.Integer, nullable=False)   # 1 = AI original, 2+ = teacher edits
    feedback_text = db.Column(db.Text, nullable=False, default='')
    author_type = db.Column(db.String(10), nullable=False)  # 'ai' | 'teacher'
    author_id = db.Column(db.String(36), nullable=True)     # NULL for AI; teacher.id otherwise
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint('submission_id', 'criterion_id', 'field', 'version',
                            name='uq_feedback_log_sub_crit_field_ver'),
    )


class FeedbackEdit(db.Model):
    __tablename__ = 'feedback_edit'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    submission_id = db.Column(db.Integer, db.ForeignKey('submissions.id'), nullable=False, index=True)
    criterion_id = db.Column(db.String(64), nullable=False)
    field = db.Column(db.String(20), nullable=False)  # 'feedback' | 'improvement'
    original_text = db.Column(db.Text, nullable=False, default='')
    edited_text = db.Column(db.Text, nullable=False, default='')
    edited_by = db.Column(db.String(36), db.ForeignKey('teachers.id'), nullable=False, index=True)
    subject_family = db.Column(db.String(40), nullable=True)
    theme_key = db.Column(db.String(40), nullable=True)
    assignment_id = db.Column(db.String(36), db.ForeignKey('assignments.id'), nullable=False, index=True)
    rubric_version = db.Column(db.String(64), nullable=False, default='')
    # FUTURE: department-level promotion logic goes here.
    scope = db.Column(db.String(20), nullable=False, default='individual')
    promoted_by = db.Column(db.String(36), nullable=True)
    promoted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.Index('ix_feedback_edit_lookup', 'edited_by', 'active', 'subject_family', 'theme_key'),
        db.Index('ix_feedback_edit_assignment', 'assignment_id', 'rubric_version'),
    )
```

- [ ] **Step 2: Compile check**

```bash
python3 -m py_compile db.py
```

Expected: no output.

- [ ] **Step 3: Smoke-test that the tables get created on a fresh database**

Run this from the repo root:

```bash
rm -f /tmp/edit_log_smoke.db
python3 -c "
import os
os.environ['DATABASE_URL'] = 'sqlite:////tmp/edit_log_smoke.db'
os.environ['FLASK_SECRET_KEY'] = 'smoke-test-key-do-not-use-in-prod'
os.environ['ANTHROPIC_API_KEY'] = 'smoke-fake'
os.environ['TEACHER_CODE'] = 'smoke'
import app  # triggers init_db
import sqlite3
conn = sqlite3.connect('/tmp/edit_log_smoke.db')
c = conn.cursor()
c.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")
tables = [r[0] for r in c.fetchall()]
assert 'feedback_log' in tables, f'feedback_log not in {tables}'
assert 'feedback_edit' in tables, f'feedback_edit not in {tables}'
c.execute('PRAGMA table_info(feedback_log)')
log_cols = sorted(r[1] for r in c.fetchall())
assert log_cols == sorted(['id','submission_id','criterion_id','field','version','feedback_text','author_type','author_id','created_at']), f'feedback_log cols: {log_cols}'
c.execute('PRAGMA table_info(feedback_edit)')
edit_cols = sorted(r[1] for r in c.fetchall())
expected = sorted(['id','submission_id','criterion_id','field','original_text','edited_text','edited_by','subject_family','theme_key','assignment_id','rubric_version','scope','promoted_by','promoted_at','active','created_at'])
assert edit_cols == expected, f'feedback_edit cols: {edit_cols}'
print('OK: both tables created with correct columns')
"
```

Expected: `OK: both tables created with correct columns`

- [ ] **Step 4: Commit**

```bash
git add db.py
git commit -m "$(cat <<'EOF'
feat(db): add feedback_log and feedback_edit tables

feedback_log records every version of feedback/improvement text per
criterion (v1 = AI original, v2+ = teacher edits). feedback_edit is
the calibration bank — only opted-in edits land here, with snapshot
columns (subject_family, theme_key, rubric_version) for relevance
matching at future marking time.

Forward-compatible columns scope/promoted_by/promoted_at exist for
future department-level promotion but are never written with
non-default values in this implementation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Hash helper + AI original logging

**Files:**
- Modify: `ai_marking.py` (add `_rubric_version_hash`)
- Modify: `app.py` (add `_log_ai_originals` helper; call from the marking worker after `db.session.commit()` for the marked submission)

- [ ] **Step 1: Add the rubric hash helper to `ai_marking.py`**

In `ai_marking.py`, find the existing `import hashlib` line (or add one near the top imports if absent — there is currently no hashlib import, so add `import hashlib` to the top of the imports block). Then append this function at the end of the file, before any `if __name__ == '__main__'` block:

```python
def _rubric_version_hash(asn):
    """MD5 hex over the assignment's raw rubric or answer_key bytes.

    rubrics and answer_key are LargeBinary blobs (uploaded files), not
    text. Hash the raw bytes — the spec's `.encode()` formulation
    doesn't apply to the actual columns. Empty/missing blobs hash the
    empty bytes string consistently, which is fine: such an
    assignment will only ever match other empty-blob assignments.
    """
    blob = (getattr(asn, 'rubrics', None) or getattr(asn, 'answer_key', None) or b'')
    if isinstance(blob, str):  # defensive — should be bytes from LargeBinary, but stay safe
        blob = blob.encode('utf-8')
    return hashlib.md5(blob).hexdigest()
```

- [ ] **Step 2: Smoke-test the hash helper**

```bash
python3 -c "
from ai_marking import _rubric_version_hash
class A: pass
a = A(); a.rubrics = b''; a.answer_key = None
h_empty = _rubric_version_hash(a)
a.rubrics = b'hello world'
h_hello = _rubric_version_hash(a)
a.rubrics = None; a.answer_key = b'fallback'
h_fallback = _rubric_version_hash(a)
assert h_empty != h_hello, 'empty and hello must differ'
assert h_fallback == _rubric_version_hash(type('B',(),{'rubrics':None,'answer_key':b'fallback'})), 'fallback must match'
assert len(h_empty) == 32 and all(c in '0123456789abcdef' for c in h_empty), f'bad hash: {h_empty}'
print('hash OK:', h_empty[:8], h_hello[:8], h_fallback[:8])
"
```

Expected: `hash OK: <8hex> <8hex> <8hex>` with three different prefixes.

- [ ] **Step 3: Add `_log_ai_originals` helper to `app.py`**

In `app.py`, find the existing helper `_run_categorisation_worker` (search: `def _run_categorisation_worker`). Insert the new helper *immediately above* it (so the two related background helpers stay together):

```python
def _log_ai_originals(submission_id):
    """Write feedback_log v1 rows for the AI-generated feedback and improvement
    of every criterion in this submission. Idempotent via the unique constraint
    on (submission_id, criterion_id, field, version) — re-marks skip silently.

    Best-effort: failures are logged and swallowed so the student-facing flow
    is never blocked.
    """
    from db import FeedbackLog
    try:
        sub = Submission.query.get(submission_id)
        if not sub:
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
```

- [ ] **Step 4: Call `_log_ai_originals` from the marking worker**

In `app.py`, find the block that kicks off the categorisation worker (search: `Kick off the "Group by Mistake Type" categorisation`). It looks like:

```python
        # Kick off the "Group by Mistake Type" categorisation in a background
        # thread. Only if the mark actually succeeded and there is at least
        # one lost-mark criterion to categorise. The thread opens its own
        # app context and does not rely on this request/worker's session.
        try:
            sub_fresh = Submission.query.get(submission_id)
            if sub_fresh and sub_fresh.status == 'done':
```

Insert this block *immediately before* the `# Kick off the "Group by Mistake Type"...` comment (i.e. between the `db.session.commit()` for the marked submission and the categorisation kickoff):

```python
        # Log the AI-generated originals to feedback_log (v1 rows). Synchronous
        # but best-effort: any error is logged and swallowed.
        _log_ai_originals(submission_id)

```

- [ ] **Step 5: Compile check**

```bash
python3 -m py_compile ai_marking.py app.py
```

Expected: no output.

- [ ] **Step 6: Smoke-test the AI-original logging end-to-end**

```bash
rm -f /tmp/edit_log_smoke.db
python3 -c "
import os
os.environ['DATABASE_URL'] = 'sqlite:////tmp/edit_log_smoke.db'
os.environ['FLASK_SECRET_KEY'] = 'smoke-test-key-do-not-use-in-prod'
os.environ['ANTHROPIC_API_KEY'] = 'smoke-fake'
os.environ['TEACHER_CODE'] = 'smoke'
import app as A
from db import db, Submission, Assignment, Class, Teacher, FeedbackLog
import json, uuid
with A.app.app_context():
    # Build a minimal submission with two criteria
    cls = Class(id=str(uuid.uuid4()), name='X')
    db.session.add(cls); db.session.commit()
    asn = Assignment(id=str(uuid.uuid4()), title='T', subject='Sci', class_id=cls.id, provider='anthropic', model='m', assign_type='short_answer', show_results=True)
    db.session.add(asn); db.session.commit()
    sub = Submission(student_id=None, assignment_id=asn.id, status='done', result_json=json.dumps({'questions':[{'question_num':'1','feedback':'AI fb 1','improvement':'AI imp 1'},{'question_num':'2','feedback':'AI fb 2','improvement':''}]}))
    db.session.add(sub); db.session.commit()
    A._log_ai_originals(sub.id)
    rows = FeedbackLog.query.filter_by(submission_id=sub.id, version=1).order_by(FeedbackLog.criterion_id, FeedbackLog.field).all()
    seen = [(r.criterion_id, r.field, r.author_type, r.feedback_text) for r in rows]
    assert seen == [('1','feedback','ai','AI fb 1'),('1','improvement','ai','AI imp 1'),('2','feedback','ai','AI fb 2')], seen
    # Idempotency: a second call must not duplicate
    A._log_ai_originals(sub.id)
    n = FeedbackLog.query.filter_by(submission_id=sub.id, version=1).count()
    assert n == 3, f'expected 3 rows, got {n}'
    print('AI logging + idempotency OK')
"
```

Expected: `AI logging + idempotency OK`

- [ ] **Step 7: Commit**

```bash
git add ai_marking.py app.py
git commit -m "$(cat <<'EOF'
feat(edit-log): hash rubric bytes + log AI originals to feedback_log

_rubric_version_hash hashes the raw rubric / answer_key blob bytes
(LargeBinary columns) — the spec's text/.encode() form doesn't match
the schema. _log_ai_originals writes one v1 feedback_log row per
criterion × {feedback, improvement} after the marking worker
commits. Idempotent via the table's unique constraint, so re-marks
don't duplicate v1 rows; preserves the FIRST AI output as the
calibration bank's original_text anchor.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Calibration lookup, formatting, and prompt injection

**Files:**
- Modify: `ai_marking.py` (add `fetch_calibration_examples`, `_truncate_at_word`, `format_calibration_block`; extend `_build_rubrics_prompt` and `_build_short_answer_prompt` to accept and prepend a calibration block)
- Modify: `app.py` (call `fetch_calibration_examples` + `format_calibration_block` from the marking worker before the AI call, pass through to the builders)

- [ ] **Step 1: Add the lookup helper**

Append to `ai_marking.py` (after `_rubric_version_hash` from Task 2):

```python
def fetch_calibration_examples(teacher_id, assignment, theme_keys, limit=10):
    """Return up to `limit` of this teacher's prior active edits relevant to
    the current marking. Two tiers, merged then deduped:

      Tier 0: same assignment + same rubric_version (no theme filter).
      Tier 1: per theme_key — different assignment, same subject_family,
              theme_key matches.

    `theme_keys` is the iterable of theme_keys from the current submission's
    lost-mark criteria. May be empty (first mark of a fresh submission, or
    submission not yet categorised) — only Tier 0 returns rows in that case.

    All queries use bound parameters via SQLAlchemy text(). Never f-string
    interpolation.
    """
    from sqlalchemy import text as _sql_text
    from db import db
    if not teacher_id or not assignment:
        return []

    rubric_hash = _rubric_version_hash(assignment)
    rows_by_id = {}

    tier0_sql = _sql_text(
        "SELECT id, original_text, edited_text, theme_key, assignment_id, "
        "rubric_version, created_at, criterion_id, field, "
        "0 AS match_tier "
        "FROM feedback_edit "
        "WHERE edited_by = :teacher_id "
        "  AND active = true "
        "  AND assignment_id = :aid "
        "  AND rubric_version = :rubric_hash "
        "ORDER BY created_at DESC"
    )
    for r in db.session.execute(tier0_sql, {
        'teacher_id': teacher_id,
        'aid': assignment.id,
        'rubric_hash': rubric_hash,
    }).mappings().all():
        rows_by_id[r['id']] = dict(r)

    sf = getattr(assignment, 'subject_family', None) or ''
    if sf and theme_keys:
        tier1_sql = _sql_text(
            "SELECT id, original_text, edited_text, theme_key, assignment_id, "
            "rubric_version, created_at, criterion_id, field, "
            "1 AS match_tier "
            "FROM feedback_edit "
            "WHERE edited_by = :teacher_id "
            "  AND active = true "
            "  AND assignment_id != :aid "
            "  AND subject_family = :sf "
            "  AND theme_key IS NOT NULL "
            "  AND theme_key = :tk "
            "ORDER BY created_at DESC"
        )
        seen_themes = set()
        for tk in theme_keys:
            if not tk or tk in seen_themes:
                continue
            seen_themes.add(tk)
            for r in db.session.execute(tier1_sql, {
                'teacher_id': teacher_id,
                'aid': assignment.id,
                'sf': sf,
                'tk': tk,
            }).mappings().all():
                # Tier 0 wins over Tier 1 for the same edit id.
                if r['id'] not in rows_by_id:
                    rows_by_id[r['id']] = dict(r)

    # Sort: Tier 0 first, newest first within each tier.
    def _ts(d):
        ca = d.get('created_at')
        if ca is None:
            return 0
        try:
            return ca.timestamp()
        except Exception:
            return 0

    sorted_rows = sorted(
        rows_by_id.values(),
        key=lambda d: (d['match_tier'], -_ts(d)),
    )

    # Collapse to most-recent per (criterion_id, field), then truncate.
    seen_keys = set()
    out = []
    for d in sorted_rows:
        key = (d['criterion_id'], d['field'])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(d)
        if len(out) >= limit:
            break
    return out
```

- [ ] **Step 2: Add formatting helpers**

Append to `ai_marking.py` (after `fetch_calibration_examples`):

```python
def _truncate_at_word(s, max_chars=200):
    """Truncate to <= max_chars at the nearest word boundary, append '...'."""
    if not s:
        return ''
    s = s.replace('\n', ' ').replace('\r', ' ').strip()
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    last_space = cut.rfind(' ')
    if last_space > max_chars * 0.5:
        cut = cut[:last_space]
    return cut.rstrip(' .,;:!?') + '...'


def format_calibration_block(examples):
    """Render `examples` (output of fetch_calibration_examples) as the
    MARKING CALIBRATION block per spec Part 3. Returns '' when empty so the
    caller can simply prepend without checking.
    """
    if not examples:
        return ''
    lines = [
        '---',
        'MARKING CALIBRATION',
        '',
        'This teacher has previously edited AI-generated feedback on '
        'this or similar assignments. Use these examples only to '
        'calibrate your tone, length, and marking standard. Do not '
        'reference them in your output. Apply the same corrections '
        'silently to any similar criteria in this submission.',
        '',
    ]
    for ex in examples:
        orig = _truncate_at_word(ex.get('original_text') or '', 200)
        edited = _truncate_at_word(ex.get('edited_text') or '', 200)
        lines.append(f'Original AI feedback: "{orig}"')
        lines.append(f'Teacher changed it to: "{edited}"')
        if ex.get('theme_key'):
            lines.append(f"Mistake type: {ex['theme_key']}")
        if ex.get('match_tier') == 0:
            lines.append('Context: same assignment, same rubric')
        else:
            lines.append('Context: different assignment, same subject and mistake type')
        lines.append('')
    lines.append('---')
    lines.append('')
    return '\n'.join(lines)
```

- [ ] **Step 3: Smoke-test the lookup + formatting**

```bash
rm -f /tmp/edit_log_smoke.db
python3 -c "
import os
os.environ['DATABASE_URL'] = 'sqlite:////tmp/edit_log_smoke.db'
os.environ['FLASK_SECRET_KEY'] = 'smoke-test-key-do-not-use-in-prod'
os.environ['ANTHROPIC_API_KEY'] = 'smoke-fake'
os.environ['TEACHER_CODE'] = 'smoke'
import app as A
from db import db, Teacher, Assignment, Class, FeedbackEdit
from datetime import datetime, timezone
import uuid
from ai_marking import fetch_calibration_examples, format_calibration_block, _rubric_version_hash
with A.app.app_context():
    t = Teacher(id=str(uuid.uuid4()), name='Mx Smoke', code='SMK')
    cls = Class(id=str(uuid.uuid4()), name='X')
    db.session.add_all([t, cls]); db.session.commit()
    a1 = Assignment(id=str(uuid.uuid4()), title='A1', subject='Sci', class_id=cls.id, provider='p', model='m', assign_type='short_answer', show_results=True, subject_family='science', rubrics=b'rubric-bytes-A1')
    a2 = Assignment(id=str(uuid.uuid4()), title='A2', subject='Sci', class_id=cls.id, provider='p', model='m', assign_type='short_answer', show_results=True, subject_family='science', rubrics=b'rubric-bytes-A2')
    db.session.add_all([a1, a2]); db.session.commit()
    h1 = _rubric_version_hash(a1)
    # 1 same-assignment edit (tier 0)
    db.session.add(FeedbackEdit(submission_id=999, criterion_id='1', field='feedback', original_text='AI long original about reasoning', edited_text='Teacher rewrite naming the gap', edited_by=t.id, subject_family='science', theme_key='reasoning_gap', assignment_id=a1.id, rubric_version=h1, scope='individual', active=True))
    # 1 different-assignment same-theme edit (tier 1)
    db.session.add(FeedbackEdit(submission_id=998, criterion_id='2', field='improvement', original_text='Long AI suggested improvement', edited_text='Concise teacher rewrite', edited_by=t.id, subject_family='science', theme_key='reasoning_gap', assignment_id=a2.id, rubric_version='other-hash', scope='individual', active=True))
    # 1 retired (must be excluded)
    db.session.add(FeedbackEdit(submission_id=997, criterion_id='3', field='feedback', original_text='X', edited_text='Y', edited_by=t.id, subject_family='science', theme_key='reasoning_gap', assignment_id=a2.id, rubric_version='other-hash', scope='individual', active=False))
    db.session.commit()
    examples = fetch_calibration_examples(t.id, a1, ['reasoning_gap'])
    assert len(examples) == 2, f'expected 2 (retired excluded), got {len(examples)}'
    # Tier 0 first
    assert examples[0]['match_tier'] == 0 and examples[1]['match_tier'] == 1, [e['match_tier'] for e in examples]
    # Empty theme list: only tier 0
    examples2 = fetch_calibration_examples(t.id, a1, [])
    assert len(examples2) == 1 and examples2[0]['match_tier'] == 0
    block = format_calibration_block(examples)
    assert 'MARKING CALIBRATION' in block
    assert 'Teacher rewrite naming the gap' in block
    assert 'same assignment, same rubric' in block
    assert 'different assignment, same subject and mistake type' in block
    print('lookup + formatting OK')
"
```

Expected: `lookup + formatting OK`

- [ ] **Step 4: Wire calibration block into `_build_rubrics_prompt`**

In `ai_marking.py`, find the function `_build_rubrics_prompt`. Find its signature line (something like `def _build_rubrics_prompt(subject, rubrics_pages, reference_pages, question_paper_pages, script_pages, review_section, marking_section, total_marks):`).

Add a new keyword argument `calibration_block=''` at the end of the signature:

```python
def _build_rubrics_prompt(subject, rubrics_pages, reference_pages, question_paper_pages,
                          script_pages, review_section, marking_section, total_marks,
                          calibration_block=''):
```

Find the line where `system_prompt` is assigned (the multi-line `f"""You are an experienced teacher marking...`). At the very top of that f-string, immediately after the opening `"""`, prepend `{calibration_block}` so the rendered prompt begins with the calibration block (or an empty string when there's nothing to inject):

```python
    system_prompt = f"""{calibration_block}You are an experienced teacher marking a student's essay/extended response using rubrics.
```

(Notice the lack of newline between `{calibration_block}` and `You are`. The `format_calibration_block` output already ends with a trailing `---\n\n` so the spacing is correct when populated, and a no-op when empty.)

- [ ] **Step 5: Wire calibration block into `_build_short_answer_prompt`**

Same pattern. Find `def _build_short_answer_prompt(...)`. Add `calibration_block=''` to the end of the signature. Find the multi-line f-string for `system_prompt` (`You are an experienced teacher marking a student's assignment script.`). Prepend `{calibration_block}` at the top of that f-string the same way:

```python
    system_prompt = f"""{calibration_block}You are an experienced teacher marking a student's assignment script.
```

- [ ] **Step 6: Find the marking dispatch site in `app.py`**

In `app.py`, search for the place where `_build_rubrics_prompt` and `_build_short_answer_prompt` are invoked. There is exactly one dispatcher (search: `_build_rubrics_prompt(`). Around it you will find `_build_short_answer_prompt(`. They are typically called from a wrapper like `_run_marking_job` or similar.

Identify the local variables in scope at that call site:
- `asn` (the Assignment) — confirm by reading 5–10 lines above
- `submission_id` — the current submission being marked
- The current teacher's id — search for `teacher_id` or `_resolve_marking_teacher_id` upstream; if not present, derive it from `asn.teacher_id` (which is set on the assignment).

- [ ] **Step 7: Inject the calibration block at the marking dispatch site**

Just **before** the call to `_build_rubrics_prompt(...)` / `_build_short_answer_prompt(...)`, compute the calibration block and pass it through. Add this code:

```python
        # Calibration: pull this teacher's prior relevant edits and prepend
        # them to the system prompt. Best-effort — never blocks marking.
        calibration_block = ''
        try:
            from ai_marking import fetch_calibration_examples, format_calibration_block
            sub_for_themes = Submission.query.get(submission_id)
            theme_keys = []
            if sub_for_themes:
                prior = sub_for_themes.get_result() or {}
                for q in (prior.get('questions') or []):
                    tk = q.get('theme_key')
                    if tk:
                        theme_keys.append(tk)
            calibration_examples = fetch_calibration_examples(
                teacher_id=asn.teacher_id,
                assignment=asn,
                theme_keys=theme_keys,
            )
            calibration_block = format_calibration_block(calibration_examples)
            if calibration_examples:
                logger.info(f"Marking sub {submission_id}: prepending {len(calibration_examples)} calibration examples")
        except Exception as cal_err:
            logger.warning(f"Calibration lookup failed for sub {submission_id}, marking with no calibration: {cal_err}")
            calibration_block = ''
```

Then update the *both* of the actual `_build_rubrics_prompt(...)` and `_build_short_answer_prompt(...)` invocation sites to pass `calibration_block=calibration_block` as a keyword argument at the end of their argument lists. Example:

```python
            system_prompt, content = _build_rubrics_prompt(
                ..., total_marks,
                calibration_block=calibration_block,
            )
```

and:

```python
            system_prompt, content = _build_short_answer_prompt(
                ..., scoring_mode, total_marks,
                calibration_block=calibration_block,
            )
```

- [ ] **Step 8: Compile check**

```bash
python3 -m py_compile ai_marking.py app.py
```

Expected: no output.

- [ ] **Step 9: End-to-end smoke test of the prompt assembly**

```bash
python3 -c "
from ai_marking import _build_short_answer_prompt, format_calibration_block
ex = [{'original_text':'AI verbose original','edited_text':'Teacher concise rewrite','theme_key':'reasoning_gap','match_tier':0}]
block = format_calibration_block(ex)
sp, content = _build_short_answer_prompt('Sci', [], [], [], [], '', '', 'marks', 10, calibration_block=block)
assert sp.startswith('---'), 'system prompt should start with calibration block (---)'
assert 'MARKING CALIBRATION' in sp
assert 'You are an experienced teacher' in sp
print('prompt assembly OK; first 200 chars:')
print(sp[:200])
# And without calibration:
sp2, _ = _build_short_answer_prompt('Sci', [], [], [], [], '', '', 'marks', 10, calibration_block='')
assert sp2.startswith('You are an experienced teacher'), 'no-calibration prompt must start with the original opener'
print('no-calibration baseline also OK')
"
```

Expected: prints first 200 chars showing `--- MARKING CALIBRATION` then the original `You are an experienced teacher` opener; then `no-calibration baseline also OK`.

- [ ] **Step 10: Commit**

```bash
git add ai_marking.py app.py
git commit -m "$(cat <<'EOF'
feat(edit-log): calibration lookup + prompt prepend

fetch_calibration_examples runs two-tier query against feedback_edit
(same-assignment+rubric, then per-theme cross-assignment),
deduplicated to most-recent per (criterion, field), capped at 10.
format_calibration_block renders the MARKING CALIBRATION text per
spec, truncating at the nearest word boundary <= 200 chars.

Both rubrics and short-answer prompt builders accept a
calibration_block kwarg (default ''), prepended verbatim to the top
of the system prompt — empty when there's no signal, and never
referenced in any other instruction text. Marking-dispatch site
calls the lookup before the AI call and falls back silently on any
error.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: PATCH extension — log teacher edits, write bank rows, return edit_meta

**Files:**
- Modify: `app.py` (extend the existing PATCH handler at `/teacher/assignment/<aid>/submission/<sid>/result`; add a single helper `_process_text_edit`)

- [ ] **Step 1: Locate the existing PATCH handler**

In `app.py`, search for `def patch_submission_result` (or grep `'/teacher/assignment/<assignment_id>/submission/<int:submission_id>/result'`). The handler is around line 4103. Read the entire function carefully — you'll be inserting logic between the existing in-place `result_json` updates and the existing `db.session.commit()`.

Note the locals available there:
- `asn` — the Assignment
- `sub` — the Submission
- `teacher` — `_current_teacher()` result (may be `None` in non-dept mode; if `None`, use `asn.teacher_id` as the author for log/edit, **but only when `_is_authenticated()` is True** — anonymous saves in that path don't happen because `_is_authenticated()` is the gate)
- `result` — the dict from `sub.get_result()`, modified in place
- `data` — the parsed request JSON

- [ ] **Step 2: Add the helper `_process_text_edit`**

Insert this helper *immediately above* the PATCH handler (so it's visually close to its only caller):

```python
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

        # Look up the current criterion's theme_key from result_json (may be
        # NULL if categorisation hasn't run for this submission).
        theme_key = None
        result_for_theme = submission.get_result() or {}
        for q in (result_for_theme.get('questions') or []):
            if str(q.get('question_num')) == criterion_id:
                theme_key = q.get('theme_key')
                break

        from ai_marking import _rubric_version_hash
        db.session.add(FeedbackEdit(
            submission_id=submission.id,
            criterion_id=criterion_id,
            field=field,
            original_text=original_text,
            edited_text=edited_text or '',
            edited_by=teacher_id,
            subject_family=getattr(assignment, 'subject_family', None),
            theme_key=theme_key,
            assignment_id=assignment.id,
            rubric_version=_rubric_version_hash(assignment),
            scope='individual',  # FUTURE: department-level promotion logic goes here
            promoted_by=None,
            promoted_at=None,
            active=True,
        ))
        calibrated = True

    return {'version': new_version, 'calibrated': calibrated}
```

- [ ] **Step 3: Capture old text values inside the PATCH handler before in-place updates**

Inside the PATCH handler, find the loop that iterates `data.get('questions', [])` and applies field updates (around line 4136 onward). Right at the top of that for-loop, *before* any `target[field] = ...` assignment, insert:

```python
        # Capture old values so the edit-log helper can detect actual changes.
        old_text_by_field = {
            'feedback': (target.get('feedback') or ''),
            'improvement': (target.get('improvement') or ''),
        }
```

(Place it *inside* the for-loop, immediately after `target` has been resolved to the question dict for this iteration — i.e. after `target = ...` and before any `if 'feedback' in q_data: target['feedback'] = ...`.)

- [ ] **Step 4: Resolve the editing teacher's id**

Inside the PATCH handler, near the top (after `_check_assignment_ownership`), add:

```python
    # The authoring teacher for log/edit rows. _current_teacher() returns the
    # logged-in Teacher in dept mode; in legacy single-teacher mode it may
    # return None — fall back to the assignment's owning teacher_id.
    _editor = _current_teacher()
    editor_id = (_editor.id if _editor else asn.teacher_id)
```

- [ ] **Step 5: Call `_process_text_edit` after the in-place updates and before commit**

At the *end of the for-loop body* (still inside the for-loop, after all in-place `target[field] = ...` assignments and any status-recompute logic for that iteration), add:

```python
        # Edit-log integration — only acts on feedback/improvement text changes.
        cal_flag = bool(q_data.get('calibrate'))
        for _field in ('feedback', 'improvement'):
            if _field not in q_data:
                continue
            new_text = q_data.get(_field) or ''
            old_text = old_text_by_field.get(_field, '')
            try:
                meta = _process_text_edit(
                    submission=sub,
                    criterion_id=str(qn) if qn is not None else str(idx),
                    field=_field,
                    edited_text=new_text,
                    teacher_id=editor_id,
                    assignment=asn,
                    calibrate=cal_flag,
                    current_text=old_text,
                )
                if meta:
                    edit_meta.setdefault(str(qn) if qn is not None else str(idx), {})[_field] = meta
            except Exception as log_err:
                # Best-effort: log/edit failures must not block the user-facing PATCH.
                logger.warning(f"feedback log/edit write failed (sub={sub.id}, crit={qn}, field={_field}): {log_err}")
                db.session.rollback()  # roll back failed log/edit writes; in-memory result_json change is re-applied below
                target[_field] = new_text  # re-apply in-place (rollback discarded the SQLAlchemy session's view of result_json too)
```

(Pay attention to the variable names `qn` and `idx` — they are the existing loop's question-num and index. Use whichever name the existing loop uses; do not invent new ones. If the existing loop names them differently, use those names.)

- [ ] **Step 6: Initialize `edit_meta` at the top of the handler**

Near the top of the PATCH handler, after `data = request.get_json(...)`, add:

```python
    edit_meta = {}
```

- [ ] **Step 7: Return edit_meta in the response**

Find the existing `return jsonify({'success': True, 'result': result})` line at the end of the PATCH handler. Replace it with:

```python
    response = {'success': True, 'result': result}
    if edit_meta:
        response['edit_meta'] = edit_meta
    return jsonify(response)
```

- [ ] **Step 8: Validate text length**

Inside the for-loop, *before* applying the in-place `target['feedback'] = ...` / `target['improvement'] = ...`, add:

```python
        for _field in ('feedback', 'improvement'):
            if _field in q_data:
                _val = q_data.get(_field) or ''
                if len(_val) > 2000:
                    return jsonify({'success': False, 'error': f'{_field} too long (max 2000 chars)'}), 400
```

(This pairs the spec's 2000-char limit with the inline edit. Place it before the existing `target[_field] = ...` lines; do not duplicate the validation in the helper.)

- [ ] **Step 9: Compile check**

```bash
python3 -m py_compile app.py
```

Expected: no output.

- [ ] **Step 10: End-to-end smoke test of the PATCH extension**

```bash
rm -f /tmp/edit_log_smoke.db
python3 -c "
import os
os.environ['DATABASE_URL'] = 'sqlite:////tmp/edit_log_smoke.db'
os.environ['FLASK_SECRET_KEY'] = 'smoke-test-key-do-not-use-in-prod'
os.environ['ANTHROPIC_API_KEY'] = 'smoke-fake'
os.environ['TEACHER_CODE'] = 'smoke'
import app as A
from db import db, Submission, Assignment, Class, Teacher, FeedbackLog, FeedbackEdit
import json, uuid
with A.app.app_context():
    t = Teacher(id=str(uuid.uuid4()), name='Mx Smoke', code='SMK')
    cls = Class(id=str(uuid.uuid4()), name='X')
    db.session.add_all([t, cls]); db.session.commit()
    asn = Assignment(id=str(uuid.uuid4()), title='T', subject='Sci', class_id=cls.id, teacher_id=t.id, provider='anthropic', model='m', assign_type='short_answer', show_results=True, subject_family='science', rubrics=b'rubric-bytes')
    db.session.add(asn); db.session.commit()
    sub = Submission(student_id=None, assignment_id=asn.id, status='done', result_json=json.dumps({'questions':[{'question_num':'1','feedback':'AI fb 1','improvement':'AI imp 1','theme_key':'reasoning_gap'}]}))
    db.session.add(sub); db.session.commit()
    A._log_ai_originals(sub.id)  # seed v1 row
    # First call: edit feedback as workflow note
    r = A._process_text_edit(sub, '1', 'feedback', 'Teacher fb v2', t.id, asn, False, 'AI fb 1')
    db.session.commit()
    assert r == {'version': 2, 'calibrated': False}, r
    assert FeedbackLog.query.filter_by(submission_id=sub.id, criterion_id='1', field='feedback').count() == 2
    assert FeedbackEdit.query.filter_by(submission_id=sub.id).count() == 0
    # Second call: edit feedback as calibration bank
    r2 = A._process_text_edit(sub, '1', 'feedback', 'Teacher fb v3 calibrated', t.id, asn, True, 'Teacher fb v2')
    db.session.commit()
    assert r2 == {'version': 3, 'calibrated': True}, r2
    edit = FeedbackEdit.query.filter_by(submission_id=sub.id, criterion_id='1', field='feedback', active=True).one()
    assert edit.original_text == 'AI fb 1', f'original_text was {edit.original_text!r}'
    assert edit.edited_text == 'Teacher fb v3 calibrated'
    assert edit.theme_key == 'reasoning_gap'
    # Third call: re-save to bank — should deactivate the old bank row
    r3 = A._process_text_edit(sub, '1', 'feedback', 'Teacher fb v4 supersedes', t.id, asn, True, 'Teacher fb v3 calibrated')
    db.session.commit()
    assert FeedbackEdit.query.filter_by(submission_id=sub.id, criterion_id='1', field='feedback', active=True).count() == 1
    assert FeedbackEdit.query.filter_by(submission_id=sub.id, criterion_id='1', field='feedback', active=False).count() == 1
    # Fourth call: unchanged text → no-op
    r4 = A._process_text_edit(sub, '1', 'feedback', 'Teacher fb v4 supersedes', t.id, asn, True, 'Teacher fb v4 supersedes')
    db.session.commit()
    assert r4 is None, r4
    # Total versions should still be 4 (v1 AI, v2 wf, v3 cal, v4 cal-supersede)
    assert FeedbackLog.query.filter_by(submission_id=sub.id, criterion_id='1', field='feedback').count() == 4
    print('PATCH helper OK across workflow/calibrate/supersede/no-op')
"
```

Expected: `PATCH helper OK across workflow/calibrate/supersede/no-op`

- [ ] **Step 11: Commit**

```bash
git add app.py
git commit -m "$(cat <<'EOF'
feat(edit-log): extend PATCH to log edits and write bank rows

The existing PATCH /teacher/.../result handler now writes a
feedback_log row for every feedback/improvement text change and,
when calibrate=true, also writes a feedback_edit (calibration bank)
row — replacing any prior active bank row for the same
(teacher, assignment, criterion, field). Returns edit_meta with
{version, calibrated} per text-edited field so the client can
render the per-field tag inline. Marks/status edits unchanged.

Best-effort logging — failures roll back the log/edit writes and
re-apply the in-place result_json change so the user-facing save
never blocks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Server — `text_edit_meta` on GET, retire route, history route

**Files:**
- Modify: `app.py` (extend the existing GET `/teacher/assignment/<aid>/submission/<sid>/result`; add two new routes)

- [ ] **Step 1: Add `_build_text_edit_meta` helper**

In `app.py`, immediately above the existing GET handler for `/teacher/assignment/<aid>/submission/<sid>/result` (search: the function whose body returns `jsonify({'success': True, 'result': result})` for that GET — should be the function paired with the PATCH from Task 4, named something like `get_submission_result`), add:

```python
def _build_text_edit_meta(submission_id):
    """Per (criterion_id, field), the latest teacher version + whether an
    active feedback_edit row exists. Used by the GET handler so the page
    can render per-field tags on initial load without a second round-trip.

    Shape: {criterion_id: {field: {'version': N, 'calibrated': bool}}}
    """
    from db import FeedbackLog, FeedbackEdit
    from sqlalchemy import func as _func

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

    active_edits = FeedbackEdit.query.filter_by(
        submission_id=submission_id,
        active=True,
    ).all()
    active_set = {(e.criterion_id, e.field) for e in active_edits}

    meta = {}
    for row in log_rows:
        meta.setdefault(row.criterion_id, {})[row.field] = {
            'version': int(row.latest_version),
            'calibrated': (row.criterion_id, row.field) in active_set,
        }
    return meta
```

- [ ] **Step 2: Include text_edit_meta in the GET response**

In the GET handler for `/teacher/assignment/<aid>/submission/<sid>/result`, find the line that returns `jsonify({'success': True, 'result': result})`. Replace with:

```python
    return jsonify({
        'success': True,
        'result': result,
        'text_edit_meta': _build_text_edit_meta(sub.id),
    })
```

- [ ] **Step 3: Add the retire route**

Append to `app.py`, in the same area as the other `/feedback/...` routes (around `student_feedback_*`):

```python
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
        except Exception as e:
            db.session.rollback()
            logger.error(f"Could not retire edit {edit_id}: {e}")
            return jsonify({'status': 'error', 'message': 'Could not save'}), 500
    return jsonify({'status': 'ok'})
```

- [ ] **Step 4: Add the history route**

Append to `app.py`, beside the retire route:

```python
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
    # Match log rows to feedback_edit rows by (field, edited_by, edited_text).
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
        # Format as "D MMM YYYY" (e.g. "3 Apr 2025"). %-d not supported on
        # Windows, but Railway/macOS/Linux are fine; use a portable fallback.
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
            'feedback_text': r.feedback_text,
            'created_at': _fmt_date(r.created_at),
            'edit_id': edit.id if edit else None,
            'active': edit.active if edit else None,
        })
    return jsonify(out)
```

- [ ] **Step 5: Compile check**

```bash
python3 -m py_compile app.py
```

Expected: no output.

- [ ] **Step 6: Smoke-test the meta + history + retire**

```bash
rm -f /tmp/edit_log_smoke.db
python3 -c "
import os
os.environ['DATABASE_URL'] = 'sqlite:////tmp/edit_log_smoke.db'
os.environ['FLASK_SECRET_KEY'] = 'smoke-test-key-do-not-use-in-prod'
os.environ['ANTHROPIC_API_KEY'] = 'smoke-fake'
os.environ['TEACHER_CODE'] = 'smoke'
import app as A
from db import db, Submission, Assignment, Class, Teacher, FeedbackLog, FeedbackEdit
import json, uuid
with A.app.app_context():
    t = Teacher(id=str(uuid.uuid4()), name='Mx Smoke', code='SMK')
    cls = Class(id=str(uuid.uuid4()), name='X')
    db.session.add_all([t, cls]); db.session.commit()
    asn = Assignment(id=str(uuid.uuid4()), title='T', subject='Sci', class_id=cls.id, teacher_id=t.id, provider='p', model='m', assign_type='short_answer', show_results=True, subject_family='science', rubrics=b'rb')
    db.session.add(asn); db.session.commit()
    sub = Submission(assignment_id=asn.id, status='done', result_json=json.dumps({'questions':[{'question_num':'1','feedback':'AI fb','improvement':'AI imp'}]}))
    db.session.add(sub); db.session.commit()
    A._log_ai_originals(sub.id)
    A._process_text_edit(sub, '1', 'feedback', 'Teacher v2', t.id, asn, True, 'AI fb')
    db.session.commit()
    meta = A._build_text_edit_meta(sub.id)
    assert meta == {'1': {'feedback': {'version': 2, 'calibrated': True}}}, meta
    edit = FeedbackEdit.query.filter_by(submission_id=sub.id, active=True).one()
    edit_id = edit.id
    # Retire flow (simulate as the same teacher)
    with A.app.test_client() as cli:
        with cli.session_transaction() as s:
            s['teacher_id'] = t.id
        # Wrong-teacher 403
        with cli.session_transaction() as s:
            s['teacher_id'] = str(uuid.uuid4())  # random teacher
        r403 = cli.post('/feedback/deprecate-edit', json={'edit_id': edit_id})
        assert r403.status_code == 403, (r403.status_code, r403.get_json())
        # Right teacher succeeds
        with cli.session_transaction() as s:
            s['teacher_id'] = t.id
        r200 = cli.post('/feedback/deprecate-edit', json={'edit_id': edit_id})
        assert r200.status_code == 200 and r200.get_json() == {'status':'ok'}, (r200.status_code, r200.get_json())
    # active flipped
    edit2 = FeedbackEdit.query.get(edit_id)
    assert edit2.active is False
    # And meta now shows calibrated=False
    meta2 = A._build_text_edit_meta(sub.id)
    assert meta2 == {'1': {'feedback': {'version': 2, 'calibrated': False}}}, meta2
    # History
    with A.app.test_client() as cli:
        with cli.session_transaction() as s:
            s['teacher_id'] = t.id
        rh = cli.get(f'/feedback/edit-history/{asn.id}/{sub.id}/1')
        h = rh.get_json()
        assert sorted(h.keys()) == ['feedback','improvement']
        feedback_versions = [(e['version'], e['author_type']) for e in h['feedback']]
        assert feedback_versions == [(1,'ai'),(2,'teacher')], feedback_versions
        # Retired teacher v2 should expose edit_id and active=False
        v2 = h['feedback'][1]
        assert v2['edit_id'] == edit_id and v2['active'] is False, v2
    print('GET meta + retire + history OK')
"
```

Expected: `GET meta + retire + history OK`

- [ ] **Step 7: Commit**

```bash
git add app.py
git commit -m "$(cat <<'EOF'
feat(edit-log): GET text_edit_meta + retire + history routes

GET /teacher/.../result now returns text_edit_meta keyed by
(criterion_id, field) so the page can render per-field tags on
initial load. POST /feedback/deprecate-edit soft-deletes a
feedback_edit row when the requester owns it (403 otherwise),
making it disappear from future calibration prompts via the
existing active=true filter. GET /feedback/edit-history/...
returns combined oldest-first version histories for both fields
of one criterion, with edit_id + active populated for teacher
rows that have an associated feedback_edit row.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: JS — checkbox in edit, post-save tag, initial-load tags

**Files:**
- Modify: `static/js/feedback_render.js` (extend `beginTextEdit`, `saveTextField`, the render entry point that sets up question cards)

- [ ] **Step 1: Read the existing edit pipeline**

Open `static/js/feedback_render.js` and read these functions before touching anything:
- `render(state, ...)` (top-level)
- `attachQuestionEditHandlers(state, card, q, idx)` (around line 328)
- `beginTextEdit(state, el, field)` (around line 362)
- `saveTextField(state, idx, field, newValue, el)` (around line 519)
- `patchResult(state, body)` (around line 495)

Note that the existing flow is "click element → replace with textarea → blur saves and re-renders the original element with the new value". We are *adding* — not changing — that behaviour.

- [ ] **Step 2: Render the calibration checkbox during edit**

In `beginTextEdit(state, el, field)`, *after* the existing line that creates and appends the textarea (search for `el.appendChild(ta)` or similar; the textarea variable is named `ta`), add:

```javascript
    // Calibration checkbox — only for feedback/improvement text fields.
    var cb = null;
    if (field === 'feedback' || field === 'improvement') {
        var wrap = document.createElement('label');
        wrap.className = 'fb-cal-wrap';
        wrap.style.cssText = 'display:flex;align-items:center;gap:6px;margin-top:6px;font-size:12px;color:#666;cursor:pointer;user-select:none;';
        cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'fb-cal-cb';
        cb.style.cssText = 'margin:0;cursor:pointer;';
        var labelTxt = document.createTextNode('Save to calibration bank');
        wrap.appendChild(cb);
        wrap.appendChild(labelTxt);
        el.appendChild(wrap);
        // Stop blur-save from firing when the user clicks the checkbox.
        wrap.addEventListener('mousedown', function (ev) { ev.stopPropagation(); });
        cb.addEventListener('mousedown', function (ev) { ev.stopPropagation(); });
        cb.addEventListener('click', function (ev) { ev.stopPropagation(); });
    }
```

Then find the existing blur-handler attached to `ta`. It calls `saveTextField(state, idx, field, newValue, el)` (or similar). Modify that call to also pass the checkbox state. Two cases:

  - If `saveTextField` is called via a closure that holds `idx, field, el`, replace the closure body to read the checkbox before calling save:

```javascript
        ta.addEventListener('blur', function () {
            var newValue = ta.value;
            var calibrate = !!(cb && cb.checked);
            saveTextField(state, idx, field, newValue, el, calibrate);
        });
```

  - The existing arity of `saveTextField` is `(state, idx, field, newValue, el)`; we are adding a 6th positional arg `calibrate`. Default it to `false` in the signature for safety.

- [ ] **Step 3: Update `saveTextField` to send `calibrate` and render the post-save tag**

Find `saveTextField`. Update the signature:

```javascript
function saveTextField(state, idx, field, newValue, el, calibrate) {
    if (calibrate === undefined) calibrate = false;
```

Find the body that builds the PATCH payload. It currently looks something like:

```javascript
    var body = { questions: [{ question_num: q.question_num, [field]: newValue }] };
```

Add `calibrate` into that question entry only when the field is feedback/improvement:

```javascript
    var qEntry = { question_num: q.question_num };
    qEntry[field] = newValue;
    if (field === 'feedback' || field === 'improvement') {
        qEntry.calibrate = !!calibrate;
    }
    var body = { questions: [qEntry] };
```

Then find the success branch (after `patchResult` returns OK). After the existing `el.textContent = newValue` (or however it re-renders), add:

```javascript
    // Render per-field tag from edit_meta if the server logged this edit.
    if (data && data.edit_meta) {
        var qKey = String(q.question_num);
        var fieldMeta = (data.edit_meta[qKey] || {})[field];
        if (fieldMeta) {
            renderEditTag(state, q, idx, field, fieldMeta);
        }
    }
```

- [ ] **Step 4: Add the tag renderer**

Append at the bottom of `feedback_render.js`, before any `module.exports`:

```javascript
function renderEditTag(state, q, idx, field, meta) {
    // meta = {version, calibrated}.  Replace any existing tag for this
    // (idx, field). Insert as a sibling immediately after the field's
    // visible element so the tag sits beneath it.
    var prefix = state.prefix || 'fb';
    var qCard = document.getElementById(prefix + 'QCard' + idx) ||
                document.querySelector('.fb-q-card[data-idx="' + idx + '"]');
    if (!qCard) return;
    var fieldEl = qCard.querySelector('[data-field="' + field + '"]');
    if (!fieldEl) return;
    var tagId = prefix + 'Tag-' + idx + '-' + field;
    var existing = document.getElementById(tagId);
    if (existing) existing.remove();
    var tag = document.createElement('div');
    tag.id = tagId;
    tag.className = 'fb-edit-tag' + (meta.calibrated ? ' fb-tag-cal' : ' fb-tag-wf');
    tag.style.cssText = 'font-size:11px;color:#7a7f8c;margin-top:2px;letter-spacing:0.2px;';
    tag.textContent = meta.calibrated ? '· in calibration bank' : '· workflow note';
    // Insert after the field element; if there's a "View edit history" link
    // (Task 7), we'll re-attach it after this tag.
    if (fieldEl.parentNode) {
        fieldEl.parentNode.insertBefore(tag, fieldEl.nextSibling);
    }
}
```

- [ ] **Step 5: Render initial tags on page load from `text_edit_meta`**

In `feedback_render.js`, find the top-level `render` (or `init`) function that fetches the result via the GET endpoint. After the data is in hand, capture `data.text_edit_meta` into `state.textEditMeta`:

Search for the line that calls `state.questions = data.result.questions || []` (or similar) — it's typically inside a `.then(function(data){...})` block. Add right after it:

```javascript
        state.textEditMeta = data.text_edit_meta || {};
```

Then find the place where each question card is created (probably inside a forEach or for-loop over `state.questions`). After the card is appended to the DOM and its edit handlers are attached, insert:

```javascript
        // Initial-load: render tags for criteria that already have edits.
        var qKey = String(q.question_num);
        var qMeta = state.textEditMeta[qKey] || {};
        if (qMeta.feedback)    renderEditTag(state, q, idx, 'feedback',    qMeta.feedback);
        if (qMeta.improvement) renderEditTag(state, q, idx, 'improvement', qMeta.improvement);
```

(`q` is the per-iteration question object; `idx` is the loop index — use whatever names the existing loop already uses.)

- [ ] **Step 6: Verify the JS is well-formed**

```bash
node --check static/js/feedback_render.js
```

Expected: no output. If `node` is not installed, run `python3 -c "open('static/js/feedback_render.js').read()"` as a no-op equivalent (no syntax check, but at least confirms the file is readable).

- [ ] **Step 7: Manual browser smoke test**

1. Start the app locally: `python3 app.py`
2. Open the teacher detail page for a class with a marked submission.
3. Open the feedback modal for one student.
4. Click into a feedback or improvement field.
   - **Expected:** A textarea appears with the original text. Below it, a `☐ Save to calibration bank` checkbox is visible.
5. Edit the text. Leave the checkbox **unchecked**. Click outside the textarea.
   - **Expected:** Textarea closes. The new text shows. Below it, a small grey line: `· workflow note`.
6. Click into the same field again.
   - **Expected:** Textarea opens with the latest text. Checkbox starts **unchecked** (T1: re-edit resets to default).
7. Edit again. **Check** the box. Click out.
   - **Expected:** Textarea closes. Tag updates to: `· in calibration bank`.
8. Reload the page and re-open the modal.
   - **Expected:** The tag persists — same `· in calibration bank` line is rendered from `text_edit_meta` on initial load.

If anything fails: don't commit. Read the browser console + server log together to diagnose.

- [ ] **Step 8: Commit**

```bash
git add static/js/feedback_render.js
git commit -m "$(cat <<'EOF'
feat(edit-log): inline calibration checkbox + per-field tag

beginTextEdit appends a "Save to calibration bank" checkbox below
the textarea for feedback/improvement fields. saveTextField sends
calibrate=true|false to the PATCH and renders a small grey tag
("· in calibration bank" or "· workflow note") beneath the field
based on the server's edit_meta response.

Initial page load reads text_edit_meta from the GET response and
renders existing tags so the state survives a refresh. Marks/status
edits are unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: JS — view edit history popover + retire link

**Files:**
- Modify: `static/js/feedback_render.js` (add `attachHistoryLink`, `fetchAndRenderHistory`, `retireEdit`; call `attachHistoryLink` from the same loop that calls `renderEditTag`)

- [ ] **Step 1: Render the "View edit history" link beside each per-field tag**

In `feedback_render.js`, modify `renderEditTag` (added in Task 6 Step 4) to also append a sibling history link. Replace the function with:

```javascript
function renderEditTag(state, q, idx, field, meta) {
    var prefix = state.prefix || 'fb';
    var qCard = document.getElementById(prefix + 'QCard' + idx) ||
                document.querySelector('.fb-q-card[data-idx="' + idx + '"]');
    if (!qCard) return;
    var fieldEl = qCard.querySelector('[data-field="' + field + '"]');
    if (!fieldEl) return;
    var rowId = prefix + 'TagRow-' + idx + '-' + field;
    var existing = document.getElementById(rowId);
    if (existing) existing.remove();
    var row = document.createElement('div');
    row.id = rowId;
    row.className = 'fb-edit-tag-row';
    row.style.cssText = 'display:flex;align-items:center;gap:8px;margin-top:2px;font-size:11px;color:#7a7f8c;letter-spacing:0.2px;';
    var tag = document.createElement('span');
    tag.className = 'fb-edit-tag' + (meta.calibrated ? ' fb-tag-cal' : ' fb-tag-wf');
    tag.textContent = meta.calibrated ? '· in calibration bank' : '· workflow note';
    row.appendChild(tag);
    var link = document.createElement('a');
    link.href = '#';
    link.className = 'fb-history-link';
    link.style.cssText = 'color:#5a6fd6;text-decoration:none;';
    link.textContent = 'View edit history';
    link.addEventListener('click', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        toggleHistoryPanel(state, q, idx, field, row);
    });
    row.appendChild(link);
    if (fieldEl.parentNode) {
        fieldEl.parentNode.insertBefore(row, fieldEl.nextSibling);
    }
}
```

- [ ] **Step 2: Add `toggleHistoryPanel`, `fetchAndRenderHistory`, `retireEdit`**

Append to `feedback_render.js`:

```javascript
function toggleHistoryPanel(state, q, idx, field, anchorRow) {
    var prefix = state.prefix || 'fb';
    var panelId = prefix + 'HistPanel-' + idx;
    var existing = document.getElementById(panelId);
    if (existing) {
        existing.remove();
        return;
    }
    var panel = document.createElement('div');
    panel.id = panelId;
    panel.className = 'fb-history-panel';
    panel.style.cssText = 'margin-top:8px;padding:10px 12px;background:#f7f8fb;border:1px solid #e3e6f0;border-radius:6px;font-size:12.5px;color:#333;line-height:1.5;';
    panel.textContent = 'Loading…';
    if (anchorRow.parentNode) {
        anchorRow.parentNode.insertBefore(panel, anchorRow.nextSibling);
    }
    fetchAndRenderHistory(state, q, panel);
}

function fetchAndRenderHistory(state, q, panel) {
    var url = '/feedback/edit-history/' + encodeURIComponent(state.assignmentId) +
              '/' + encodeURIComponent(state.submissionId) +
              '/' + encodeURIComponent(String(q.question_num));
    fetch(url, { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            renderHistoryPanel(state, panel, data);
        })
        .catch(function () {
            panel.textContent = 'Could not load history.';
        });
}

function renderHistoryPanel(state, panel, data) {
    panel.innerHTML = '';
    var fields = ['feedback', 'improvement'];
    var anyShown = false;
    fields.forEach(function (field) {
        var versions = (data && data[field]) || [];
        if (!versions.length) return;
        anyShown = true;
        var heading = document.createElement('div');
        heading.style.cssText = 'font-weight:600;color:#444;margin-top:6px;margin-bottom:4px;text-transform:capitalize;';
        heading.textContent = field === 'feedback' ? 'Feedback' : 'Suggested improvement';
        panel.appendChild(heading);
        versions.forEach(function (v) {
            var block = document.createElement('div');
            block.style.cssText = 'margin-bottom:8px;';
            var meta = document.createElement('div');
            meta.style.cssText = 'font-size:11.5px;color:#7a7f8c;';
            var retiredMark = (v.edit_id && v.active === false) ? ' · retired' : '';
            meta.textContent = 'Version ' + v.version + ' — ' + v.author_name + ' · ' + (v.created_at || '') + retiredMark;
            block.appendChild(meta);
            var body = document.createElement('div');
            body.style.cssText = 'white-space:pre-wrap;color:#333;margin-top:2px;';
            body.textContent = v.feedback_text || '';
            block.appendChild(body);
            // Retire link only when this version has an active edit_id and
            // the current teacher is the original editor.
            if (v.edit_id && v.active === true && state.currentTeacherId &&
                v.author_name && v.author_type === 'teacher') {
                // (Server-side already enforces ownership; the link is shown
                // for any of the current teacher's own active edits.)
                var ret = document.createElement('a');
                ret.href = '#';
                ret.className = 'fb-retire-link';
                ret.style.cssText = 'font-size:11.5px;color:#b94a48;text-decoration:none;margin-top:2px;display:inline-block;';
                ret.textContent = 'Retire this edit';
                ret.addEventListener('click', function (ev) {
                    ev.preventDefault();
                    ev.stopPropagation();
                    retireEdit(state, v.edit_id, panel);
                });
                block.appendChild(ret);
            }
            panel.appendChild(block);
        });
    });
    if (!anyShown) {
        panel.textContent = 'No edit history.';
    }
}

function retireEdit(state, editId, panel) {
    fetch('/feedback/deprecate-edit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ edit_id: editId }),
    })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data && data.status === 'ok') {
                // Re-fetch the history so the version flips to retired.
                panel.textContent = 'Loading…';
                // Pull q back via the panel's previous siblings is fragile;
                // simpler: read state and re-derive from panel id.
                var pid = panel.id || '';
                var idxMatch = pid.match(/HistPanel-(\d+)/);
                if (!idxMatch) { panel.textContent = 'Retired.'; return; }
                var idx = parseInt(idxMatch[1], 10);
                var q = state.questions[idx];
                if (q) fetchAndRenderHistory(state, q, panel);
            } else {
                panel.textContent = 'Could not retire: ' + ((data && data.message) || 'unknown error');
            }
        })
        .catch(function () { panel.textContent = 'Could not retire (network).'; });
}
```

- [ ] **Step 3: Set `state.assignmentId`, `state.submissionId`, `state.currentTeacherId` once on render**

In the top-level `render` (or `init`) function, near where `state.questions` and `state.textEditMeta` are set, add:

```javascript
        state.assignmentId = data.assignment_id || state.assignmentId || null;
        state.submissionId = data.submission_id || state.submissionId || null;
        state.currentTeacherId = data.current_teacher_id || state.currentTeacherId || null;
```

If these aren't already in the GET response, the existing template that opens the modal already passes them in via `openFeedbackModal(submissionId, ...)` — confirm by reading the call site in `templates/teacher_detail.html` (search for `openFeedbackModal(`). If they are passed in via parameters but not stored on `state`, add lines like `state.submissionId = submissionId;` at the top of the modal-open / render entry function.

If the GET response does not currently include `current_teacher_id`, **also** modify the GET handler in `app.py` (the same one extended in Task 5 Step 2) to include it:

```python
    return jsonify({
        'success': True,
        'result': result,
        'text_edit_meta': _build_text_edit_meta(sub.id),
        'assignment_id': str(asn.id),
        'submission_id': sub.id,
        'current_teacher_id': (_current_teacher().id if _current_teacher() else None),
    })
```

(If you make this server change, run `python3 -m py_compile app.py` afterwards to confirm.)

- [ ] **Step 4: Verify the JS is well-formed**

```bash
node --check static/js/feedback_render.js
```

Expected: no output.

- [ ] **Step 5: Manual browser smoke test for history + retire**

1. Reload the dev server: `python3 app.py`.
2. On the teacher detail page, open a feedback modal for a submission you've edited at least once with calibration on.
3. Below the per-field tag, you should see a `View edit history` link.
4. Click the link.
   - **Expected:** A panel expands beneath the row showing both fields' version histories oldest-first. AI v1 has author `AI`. Teacher edits show your name (or fallback `Teacher #...`).
5. Find a teacher version with `· in calibration bank` (an active edit you made). It should have a `Retire this edit` link beside it.
6. Click `Retire this edit`.
   - **Expected:** The panel reloads. The retired version now shows `· retired` next to the metadata line and no longer has the retire link.
7. Close the panel by clicking `View edit history` again.
   - **Expected:** Panel collapses.
8. Reload the page and reopen the modal.
   - **Expected:** That criterion's per-field tag has switched from `· in calibration bank` to `· workflow note` (because the bank row is now `active=false`, but the latest log version is still a teacher row, so the tag still renders — just without the calibrated marker).

If anything fails: don't commit. Read the browser console + server log to diagnose.

- [ ] **Step 6: Commit**

```bash
git add static/js/feedback_render.js app.py
git commit -m "$(cat <<'EOF'
feat(edit-log): edit history popover + retire link

A "View edit history" link sits beside each per-field tag when at
least one teacher edit exists. Click expands an inline panel under
the row showing both feedback and improvement version histories
(oldest first), with author name and date. Active calibration
bank rows owned by the current teacher carry a "Retire this edit"
link that POSTs to /feedback/deprecate-edit; on success the panel
re-renders showing the version as · retired. Server already
enforces ownership; the client-side gating is purely cosmetic.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final verification checklist

Run these after Task 7 commits to confirm the feature behaves end-to-end on a clean dev DB.

- [ ] **Step 1: Reset and walk through the full happy path**

```bash
rm -f /tmp/edit_log_full.db
DATABASE_URL=sqlite:////tmp/edit_log_full.db FLASK_SECRET_KEY=dev FLASK_DEBUG=true ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY TEACHER_CODE=test python3 app.py
```

Then in a browser:
1. Sign in as the teacher (code `test`).
2. Create a class, upload a class list (any 1-student CSV).
3. Create an assignment with a small rubric or answer key PDF.
4. Mark one student script.
5. Open the feedback modal. Confirm at least one feedback line shows.
6. Edit a feedback line. Save as workflow note. Tag appears: `· workflow note`.
7. Edit a different feedback line. Save **to calibration bank**. Tag: `· in calibration bank`.
8. Mark a *second* student script (or re-mark the first to a draft).
9. Inspect the dev server logs for the line: `Marking sub <id>: prepending 1 calibration examples` (or higher count).
10. Open the new submission's feedback. Confirm the AI-generated text reflects the calibration nudge (subjectively, or at least confirm the prepend log line fired).
11. View edit history on the calibrated criterion. Retire it. Tag flips to `· workflow note`.

- [ ] **Step 2: Database inspection**

```bash
sqlite3 /tmp/edit_log_full.db "
.headers on
.mode column
SELECT submission_id, criterion_id, field, version, author_type, length(feedback_text) AS chars FROM feedback_log ORDER BY submission_id, criterion_id, field, version;
SELECT id, criterion_id, field, edited_text, theme_key, rubric_version, scope, active FROM feedback_edit ORDER BY id;
"
```

Expected:
- `feedback_log` has v1 ai rows for every (submission × criterion × {feedback, improvement}) plus teacher v2/v3 rows where you edited.
- `feedback_edit` has at most one `active=1` row per `(edited_by, assignment_id, criterion_id, field)`. Retired rows are `active=0`.
- `scope` is always `individual`. `promoted_by` and `promoted_at` columns exist (run `.schema feedback_edit` to confirm) but are always NULL.
- No row has a `created_at < X` filter applied anywhere — you'll only see this by re-reading `app.py` and `ai_marking.py` (Step 3).

- [ ] **Step 3: Confirm no age-based filter**

```bash
grep -nE "created_at\s*[<>]" app.py ai_marking.py db.py
```

Expected: **no matches.** (Spec invariant — staleness is rubric_version + active flag only.)

- [ ] **Step 4: Push the branch when satisfied**

```bash
git status
git log --oneline -10
git push -u origin feedback_edit_log
```

(Don't push if the smoke tests didn't pass cleanly. Keep iterating.)

---

## Self-review notes

The plan was reviewed against the spec; here is what was checked:

- **Spec coverage:** Every section maps to a task.
  - Spec §"Schema" → Task 1.
  - Spec §"Marking-time integration" / "Logging AI originals" → Task 2.
  - Spec §"Marking-time integration" / "Calibration lookup helper" + "Prompt injection" → Task 3.
  - Spec §"Teacher edit flow" / "Server" → Task 4.
  - Spec §"Teacher edit flow" / "GET endpoint extension" → Task 5 Step 2.
  - Spec §"History view" → Task 5 Step 4 + Task 7.
  - Spec §"Retire route" → Task 5 Step 3.
  - Spec §"Teacher edit flow" / "Client" → Task 6.
  - Spec §"Railway / Postgres specifics" → no separate task; satisfied by SQLAlchemy types and `text()` bound params used throughout, validated in Task 1 Step 3 (smoke test runs a real `db.create_all()` on a fresh database).
  - Spec §"No age-based filter" invariant → final-checklist Step 3.

- **Placeholders:** None. All steps have concrete code or commands.

- **Type consistency:** `criterion_id` is `VARCHAR(64)` in the schema, always populated as `str(question_num)` everywhere it's written. `field` is `VARCHAR(20)` and only ever `'feedback'` or `'improvement'`. `author_type` is `VARCHAR(10)` and only ever `'ai'` or `'teacher'`. `scope` is `VARCHAR(20)` and only ever `'individual'` in this implementation. `rubric_version` is `VARCHAR(64)` carrying a 32-hex MD5 digest. Foreign-key types match (`submissions.id` is INTEGER, `assignments.id` and `teachers.id` are VARCHAR(36)).

- **Re-edit during active edit:** when a teacher saves the *same* `(criterion, field)` to the bank a second time, the helper deactivates the prior active row before inserting — never two active bank rows for the same target.

- **Legacy submissions** (marked before this branch): the back-fill in `_process_text_edit` (Task 4 Step 2) writes a v1 AI-original log row from `current_text` if absent, so calibration writes still produce a coherent `original_text`. The first edit after the branch deploys is the back-fill trigger.
