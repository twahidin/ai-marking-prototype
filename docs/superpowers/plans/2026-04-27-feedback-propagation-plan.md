# Feedback Propagation + Marking Principles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the propagation + shared subject-level marking principles system: when a teacher saves a calibration edit, surface other students who made the same kind of mistake on the same criterion and offer one-click "apply this standard to all". As the bank grows past 8 active edits in a subject, swap raw-example calibration injection at mark time for a shared markdown principles file regenerated from the pool.

**Architecture:** New table `marking_principles_cache` keyed by `subject_family` only — single shared row across the dept. New columns on `feedback_edit` for propagation state + insight extraction. Per-criterion fields (`feedback_source`, `propagated_from_edit`) added to `result_json.questions[]` inline (matches the existing pattern). All AI calls except `mark_script` use the cheap-tier model via the existing `HELPER_MODELS` map. Background threads open their own app context.

**Tech Stack:** Flask + SQLAlchemy on PostgreSQL (Railway prod) / SQLite (local). Vanilla JS in `static/js/feedback_render.js`. Jinja2 templates. No new Python deps.

**Spec:** `docs/superpowers/specs/2026-04-27-feedback-propagation-design.md`

**Verification approach:** This repo has no pytest infrastructure (verified previously — no `tests/`, no `test_*.py`). Existing plans verify with `python3 -m py_compile`, inline `python3 -c` smoke tests, `sqlite3` inspection, and `curl`/test_client against a running app. This plan follows that pattern. **Do not add pytest** — out of scope.

**Branch:** `feedback_propagation`, branched off `feed_forward_beta`. Confirm with `git branch --show-current` before starting.

**Type cheat sheet** (verified in `db.py`):

| Existing table | id type |
|---|---|
| `teachers` | `db.String(36)` (UUID) |
| `assignments` | `db.String(36)` (UUID) |
| `submissions` | `db.Integer` autoincrement |
| `feedback_edit` (existing) | `db.Integer` autoincrement |

---

## Task 1: Schema — `MarkingPrinciplesCache` + new `feedback_edit` columns

**Files:**
- Modify: `db.py` — append model class; extend `_migrate_add_columns` for the new columns on the existing `feedback_edit` table.

- [ ] **Step 1: Add `MarkingPrinciplesCache` model**

Append to `db.py` after the last existing model (`FeedbackEdit`), before any module-level functions. Use exactly:

```python
class MarkingPrinciplesCache(db.Model):
    __tablename__ = 'marking_principles_cache'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    subject_family = db.Column(db.String(40), nullable=False, unique=True)
    markdown_text = db.Column(db.Text, nullable=False, default='')
    generated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_stale = db.Column(db.Boolean, nullable=False, default=False)
    edit_count_at_gen = db.Column(db.Integer, nullable=False, default=0)
    has_conflicts = db.Column(db.Boolean, nullable=False, default=False)
```

- [ ] **Step 2: Extend `feedback_edit` columns via auto-migration**

In `db.py`, find the `_migrate_add_columns` function. Locate the block for `feedback_edit` (search: `'feedback_edit' in inspector.get_table_names()`). At the end of that block (before the next-table block), append:

```python
            if 'propagation_status' not in columns:
                db.session.execute(text("ALTER TABLE feedback_edit ADD COLUMN propagation_status VARCHAR(20) DEFAULT 'none'"))
                db.session.commit()
                logger.info('Added propagation_status column to feedback_edit table')
            if 'propagated_to' not in columns:
                db.session.execute(text("ALTER TABLE feedback_edit ADD COLUMN propagated_to TEXT DEFAULT '[]'"))
                db.session.commit()
                logger.info('Added propagated_to column to feedback_edit table')
            if 'propagated_at' not in columns:
                db.session.execute(text('ALTER TABLE feedback_edit ADD COLUMN propagated_at TIMESTAMP'))
                db.session.commit()
                logger.info('Added propagated_at column to feedback_edit table')
            if 'mistake_pattern' not in columns:
                db.session.execute(text('ALTER TABLE feedback_edit ADD COLUMN mistake_pattern VARCHAR(80)'))
                db.session.commit()
                logger.info('Added mistake_pattern column to feedback_edit table')
            if 'correction_principle' not in columns:
                db.session.execute(text('ALTER TABLE feedback_edit ADD COLUMN correction_principle VARCHAR(300)'))
                db.session.commit()
                logger.info('Added correction_principle column to feedback_edit table')
            if 'transferability' not in columns:
                db.session.execute(text('ALTER TABLE feedback_edit ADD COLUMN transferability VARCHAR(10)'))
                db.session.commit()
                logger.info('Added transferability column to feedback_edit table')
```

If the existing `feedback_edit` block doesn't exist yet (some refactor has moved it), search for `if 'feedback_edit' in inspector.get_table_names()` first; if not present, mirror the pattern from neighbouring blocks (e.g. the `submissions` block at the top of the function).

- [ ] **Step 3: Add `propagation_status`, `propagated_to`, `propagated_at`, `mistake_pattern`, `correction_principle`, `transferability` to the `FeedbackEdit` model class**

In `db.py`, find `class FeedbackEdit(db.Model)`. Append these column definitions inside the class, before `__table_args__`:

```python
    propagation_status = db.Column(db.String(20), nullable=False, default='none')
    propagated_to = db.Column(db.Text, nullable=False, default='[]')
    propagated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    mistake_pattern = db.Column(db.String(80), nullable=True)
    correction_principle = db.Column(db.String(300), nullable=True)
    transferability = db.Column(db.String(10), nullable=True)
```

- [ ] **Step 4: Compile check**

```bash
python3 -m py_compile db.py
```

Expected: no output.

- [ ] **Step 5: Smoke-test schema on a fresh DB**

```bash
rm -f /tmp/prop_smoke.db
python3 -c "
import os
os.environ['DATABASE_URL'] = 'sqlite:////tmp/prop_smoke.db'
os.environ['FLASK_SECRET_KEY'] = 'smoke'
os.environ['ANTHROPIC_API_KEY'] = 'fake'
os.environ['TEACHER_CODE'] = 'smoke'
import app
import sqlite3
conn = sqlite3.connect('/tmp/prop_smoke.db')
c = conn.cursor()
c.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")
tables = [r[0] for r in c.fetchall()]
assert 'marking_principles_cache' in tables, f'cache table missing: {tables}'
c.execute('PRAGMA table_info(marking_principles_cache)')
cols = sorted(r[1] for r in c.fetchall())
expected = sorted(['id','subject_family','markdown_text','generated_at','is_stale','edit_count_at_gen','has_conflicts'])
assert cols == expected, f'cache cols: {cols}'
c.execute('PRAGMA table_info(feedback_edit)')
fe_cols = [r[1] for r in c.fetchall()]
for col in ('propagation_status','propagated_to','propagated_at','mistake_pattern','correction_principle','transferability'):
    assert col in fe_cols, f'feedback_edit missing column {col}: {fe_cols}'
print('schema OK')
"
```

Expected: `schema OK`

- [ ] **Step 6: Commit**

```bash
git add db.py
git commit -m "$(cat <<'EOF'
feat(db): marking_principles_cache + propagation columns on feedback_edit

New table marking_principles_cache keyed by subject_family alone
(single shared row per subject across the dept) — stores the
regenerated markdown principles file plus a has_conflicts flag.

New columns on feedback_edit: propagation_status (none / pending /
partial / complete / skipped), propagated_to (JSON list of
{submission_id, status, error?}), propagated_at, plus three
insight-extraction columns (mistake_pattern, correction_principle,
transferability) populated by a background thread after a
calibration save.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Insight extraction helper + worker + candidate detection

**Files:**
- Modify: `ai_marking.py` — add `extract_correction_insight()`.
- Modify: `app.py` — add `_run_insight_extraction_worker`, `_find_propagation_candidates`. Extend `_process_text_edit` to fire both. Extend the PATCH response to include `propagation_prompt`.

- [ ] **Step 1: Add `extract_correction_insight` to `ai_marking.py`**

Append after `_run_feedback_helper` (search: `def _run_feedback_helper`). Use cheap-tier model via `_helper_model_for`.

```python
def extract_correction_insight(provider, model, session_keys,
                                subject_family, theme_key,
                                criterion_name, original_text, edited_text):
    """Extract a reusable marking principle from a teacher's correction.

    Returns {mistake_pattern, correction_principle, transferability} or None
    on failure. Caller writes the three fields back to the originating
    feedback_edit row. Cheap-tier model via HELPER_MODELS.
    """
    system_prompt = (
        "You extract a reusable marking principle from a teacher's correction.\n\n"
        "Return JSON only:\n"
        "{\n"
        '  "mistake_pattern": "2-4 word phrase naming the type of error in '
        'the original feedback — diagnostic, not advice",\n'
        '  "correction_principle": "one sentence describing what this '
        "teacher's edit reveals about their marking standard — what they "
        'always do, never do, or consistently prefer",\n'
        '  "transferability": "high | medium | low"\n'
        "}\n\n"
        "transferability:\n"
        "  high   = applies to any similar question in any assignment\n"
        "  medium = applies within this subject family\n"
        "  low    = specific to this question or assignment type only\n\n"
        "The correction_principle must be written as a generalised rule, not "
        "a description of this specific edit. It should read like something a "
        "new teacher could follow without seeing the original scripts.\n\n"
        'WRONG: "The teacher added a reference to genetic identity."\n'
        'RIGHT: "Always name the specific missing consequence rather than '
        'asking students to explain further."\n\n'
        "Maximum 30 words for correction_principle."
    )
    user_prompt = (
        f"Subject family: {subject_family or 'unknown'}\n"
        f"Theme: {theme_key or 'unknown'}\n"
        f"Criterion: {criterion_name}\n"
        f"Original AI feedback: {(original_text or '')[:600]}\n"
        f"Teacher's edited feedback: {(edited_text or '')[:600]}\n\n"
        "Return the JSON now."
    )
    helper_model = _helper_model_for(provider, model)
    try:
        parsed = _run_feedback_helper(provider, helper_model, session_keys,
                                       system_prompt, user_prompt, max_tokens=200)
    except Exception as e:
        logger.warning(f"extract_correction_insight failed: {e}")
        return None
    transferability = (parsed.get('transferability') or '').strip().lower()
    if transferability not in ('high', 'medium', 'low'):
        transferability = None
    pattern = (parsed.get('mistake_pattern') or '').strip()[:80] or None
    principle = (parsed.get('correction_principle') or '').strip()[:300] or None
    return {
        'mistake_pattern': pattern,
        'correction_principle': principle,
        'transferability': transferability,
    }
```

- [ ] **Step 2: Add `_run_insight_extraction_worker` to `app.py`**

Insert near `_run_categorisation_worker` (search: `def _run_categorisation_worker`). Use the same app-context-opening pattern.

```python
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
                subject_family=edit.subject_family,
                theme_key=edit.theme_key,
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
```

- [ ] **Step 3: Add `_find_propagation_candidates` to `app.py`**

Insert immediately above `_run_insight_extraction_worker`:

```python
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
        source = target_q.get('feedback_source') or 'original_ai'
        if source == 'teacher_edit':
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
```

- [ ] **Step 4: Extend `_process_text_edit` for the calibrate path**

In `app.py`, find `_process_text_edit` (search: `def _process_text_edit`). Inside the `if calibrate:` branch, AFTER `db.session.add(FeedbackEdit(...))` and the `calibrated = True` line, before the `return` of the helper, add the staleness flag and the worker spawn. The propagation candidates and the response wiring happen at the PATCH-handler level (Step 5).

Find the line where the new `FeedbackEdit` is added inside the `if calibrate:` branch. Immediately after that `db.session.add(FeedbackEdit(...))` call (and before `calibrated = True`), no change needed yet — just confirm the structure. The staleness + worker spawn happens in the caller (PATCH handler), since the helper doesn't have access to the new edit's `id` until after `db.session.commit()`.

So leave `_process_text_edit` itself unchanged. The changes go into the PATCH handler's caller logic — see Step 5.

- [ ] **Step 5: Wire staleness + insight worker + candidates into the PATCH handler**

In `app.py`, find the PATCH handler `teacher_submission_result_patch` (search: `def teacher_submission_result_patch`). Find the `db.session.commit()` near the end of the handler that persists the result + log + edit rows.

Immediately after that successful `db.session.commit()`, BEFORE the existing return, add:

```python
    # Propagation hooks: only fire when at least one calibrate=True text edit
    # was just written for this submission. We can detect this by looking at
    # the edit_meta we just built — if any field has calibrated=True, there
    # is at least one new feedback_edit row to wire up.
    propagation_prompt = None
    fresh_edits = []
    for crit_id, fields in (edit_meta or {}).items():
        for field_name, meta in (fields or {}).items():
            if meta and meta.get('calibrated'):
                # Look up the matching feedback_edit row we just inserted.
                from db import FeedbackEdit, MarkingPrinciplesCache
                fe = (FeedbackEdit.query
                      .filter_by(submission_id=sub.id, criterion_id=crit_id,
                                 field=field_name, edited_by=editor_id, active=True)
                      .order_by(FeedbackEdit.id.desc())
                      .first())
                if fe:
                    fresh_edits.append(fe)

    if fresh_edits:
        # Mark the shared subject cache stale (one row across the dept).
        try:
            from db import MarkingPrinciplesCache
            sf = asn.subject_family
            if sf:
                MarkingPrinciplesCache.query.filter_by(subject_family=sf).update(
                    {'is_stale': True}, synchronize_session=False
                )
                db.session.commit()
        except Exception as stale_err:
            logger.warning(f"Could not mark principles cache stale: {stale_err}")
            db.session.rollback()

        # Spawn one insight worker per fresh edit.
        for fe in fresh_edits:
            try:
                threading.Thread(
                    target=_run_insight_extraction_worker,
                    args=(app, fe.id),
                    daemon=True,
                ).start()
            except Exception as worker_err:
                logger.warning(f"Could not spawn insight worker for edit {fe.id}: {worker_err}")

        # Synchronous candidate detection on the FIRST fresh edit (the most
        # interesting one for the propagation banner — typically there's
        # just one). If multiple fresh edits exist, the banner shows the
        # first; the others are still logged + workered.
        try:
            anchor = fresh_edits[0]
            propagation_prompt = _find_propagation_candidates(anchor, asn)
        except Exception as cand_err:
            logger.warning(f"Propagation candidate lookup failed: {cand_err}")
            propagation_prompt = None
```

Also mark the edited submission's question's `feedback_source = 'teacher_edit'` in the in-memory `result` dict during the existing field-update loop. Find the existing `target[field] = ...` lines for `feedback`/`improvement` inside the for-loop. Right before those assignments, add:

```python
        # Mark this question as teacher-edited so propagation never overwrites it.
        if 'feedback' in q_data or 'improvement' in q_data:
            target['feedback_source'] = 'teacher_edit'
```

Then update the existing return statement to include `propagation_prompt`:

```python
    response = {'success': True, 'result': result}
    if edit_meta:
        response['edit_meta'] = edit_meta
    if propagation_prompt and propagation_prompt.get('candidate_count', 0) > 0:
        response['propagation_prompt'] = propagation_prompt
    return jsonify(response)
```

- [ ] **Step 6: Compile check**

```bash
python3 -m py_compile ai_marking.py app.py
```

Expected: no output.

- [ ] **Step 7: Smoke-test candidate detection**

```bash
rm -f /tmp/prop_smoke.db
python3 -c "
import os, secrets
os.environ['DATABASE_URL'] = 'sqlite:////tmp/prop_smoke.db'
os.environ['FLASK_SECRET_KEY'] = 'smoke'
os.environ['ANTHROPIC_API_KEY'] = 'fake'
os.environ['TEACHER_CODE'] = 'smoke'
import app as A
from db import db, Submission, Assignment, Class, Teacher, Student, FeedbackEdit
import json, uuid
with A.app.app_context():
    t = Teacher(id=str(uuid.uuid4()), name='Mx', code='SMK')
    cls = Class(id=str(uuid.uuid4()), name='X')
    db.session.add_all([t, cls]); db.session.commit()
    asn = Assignment(id=str(uuid.uuid4()), title='T', subject='Sci', class_id=cls.id, teacher_id=t.id, provider='anthropic', model='m', assign_type='short_answer', show_results=True, subject_family='science', rubrics=b'rb', classroom_code=secrets.token_hex(4))
    db.session.add(asn); db.session.commit()
    stu1 = Student(class_id=cls.id, name='Alice', index_number='001')
    stu2 = Student(class_id=cls.id, name='Bob',   index_number='002')
    stu3 = Student(class_id=cls.id, name='Cara',  index_number='003')
    db.session.add_all([stu1, stu2, stu3]); db.session.commit()
    # Three submissions, all on Q1, all with marks lost.
    for stu in (stu1, stu2, stu3):
        sub = Submission(student_id=stu.id, assignment_id=asn.id, status='done',
                         result_json=json.dumps({'questions': [{'question_num':'1','feedback':'x','improvement':'y','marks_awarded':2,'marks_total':5}]}))
        db.session.add(sub)
    db.session.commit()
    sub_a = Submission.query.filter_by(student_id=stu1.id).one()
    # Mark Bob's question as teacher_edit so it should be excluded.
    sub_b = Submission.query.filter_by(student_id=stu2.id).one()
    rb = sub_b.get_result(); rb['questions'][0]['feedback_source'] = 'teacher_edit'; sub_b.set_result(rb); db.session.commit()
    # Build a fake edit on Alice's submission.
    edit = FeedbackEdit(submission_id=sub_a.id, criterion_id='1', field='feedback',
                        original_text='AI', edited_text='Teacher v', edited_by=t.id,
                        subject_family='science', theme_key='reasoning_gap',
                        assignment_id=asn.id, rubric_version='hash', scope='individual', active=True)
    db.session.add(edit); db.session.commit()
    out = A._find_propagation_candidates(edit, asn)
    # Cara should be the only candidate (Alice is the source, Bob is teacher_edit).
    assert out['candidate_count'] == 1, out
    assert out['candidates'][0]['student_name'] == 'Cara', out['candidates']
    assert out['candidates'][0]['marks_awarded'] == 2 and out['candidates'][0]['marks_total'] == 5
    print('candidate detection OK')
"
```

Expected: `candidate detection OK`

- [ ] **Step 8: Commit**

```bash
git add ai_marking.py app.py
git commit -m "$(cat <<'EOF'
feat(propagation): insight extraction + candidate detection

extract_correction_insight (cheap-tier AI) pulls a structured
mistake_pattern + correction_principle + transferability from each
calibration edit. _run_insight_extraction_worker writes them back
in a background thread — never blocks the teacher's save.

_find_propagation_candidates synchronously enumerates other
submissions in the same assignment whose matching question lost
marks AND whose feedback_source is not yet 'teacher_edit'. Returns
student_name + marks + current feedback/improvement so the
banner's "Review individually" panel renders without a second
fetch.

PATCH /teacher/.../result now: marks the edited criterion's
feedback_source='teacher_edit' in result_json, marks the shared
marking_principles_cache row stale for the assignment's
subject_family, spawns one insight worker per fresh feedback_edit
row, and includes propagation_prompt (when candidates exist) in
the response.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Refresh function + propagation worker + propagation routes

**Files:**
- Modify: `ai_marking.py` — add `refresh_criterion_feedback`.
- Modify: `app.py` — add `_run_propagation_worker`, four new routes (`/feedback/propagation-candidates/<edit_id>`, `/feedback/propagate`, `/feedback/propagate-skip`, `/feedback/propagation-progress/<edit_id>`).

- [ ] **Step 1: Add `refresh_criterion_feedback` to `ai_marking.py`**

Append after `extract_correction_insight`:

```python
def refresh_criterion_feedback(provider, model, session_keys, subject,
                                criterion_name, student_answer, correct_answer,
                                marks_awarded, marks_total, calibration_edit):
    """Regenerate feedback + improvement for one criterion on one student,
    calibrated against a teacher's edit on another student. Text-only call —
    no images, no full marking pipeline. Cheap-tier model via HELPER_MODELS.
    Returns {feedback, improvement}.
    """
    helper_model = _helper_model_for(provider, model)
    system_prompt = (
        "You are regenerating feedback for one criterion on a student's "
        "script. A teacher has shown you their marking standard by editing "
        "another student's feedback on the same type of mistake.\n\n"
        "Apply the same standard to this student's answer. Do not change "
        "the marks. Do not re-evaluate correctness. Only rewrite the "
        "Feedback and Suggested Improvement fields.\n\n"
        f"{FEEDBACK_GENERATION_RULES}\n\n"
        "Return JSON only:\n"
        "{\n"
        '  "feedback": "...",\n'
        '  "improvement": "..."\n'
        "}"
    )
    orig = (calibration_edit.original_text or '')[:200]
    edited = (calibration_edit.edited_text or '')[:200]
    principle_line = ''
    cp = getattr(calibration_edit, 'correction_principle', None)
    if cp:
        principle_line = f"\nTeacher's principle: \"{cp}\""
    user_prompt = (
        "TEACHER'S CALIBRATION EDIT (apply this standard):\n"
        f"Original AI feedback: \"{orig}\"\n"
        f"Teacher changed it to: \"{edited}\"{principle_line}\n\n"
        "NOW APPLY THE SAME STANDARD TO:\n"
        f"Subject: {subject or 'General'}\n"
        f"Criterion: {criterion_name}\n"
        f"Student's answer: {(student_answer or '')[:600]}\n"
        f"Expected answer: {(correct_answer or '')[:400]}\n"
        f"Marks: {marks_awarded if marks_awarded is not None else '-'} / {marks_total if marks_total is not None else '-'}\n\n"
        "Return the JSON now."
    )
    parsed = _run_feedback_helper(provider, helper_model, session_keys,
                                   system_prompt, user_prompt, max_tokens=300)
    feedback = (parsed.get('feedback') or '').strip()
    improvement = (parsed.get('improvement') or '').strip()
    return {'feedback': feedback, 'improvement': improvement}
```

- [ ] **Step 2: Add `_run_propagation_worker` to `app.py`**

Insert near `_run_insight_extraction_worker`:

```python
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
            edit.propagated_to = _json.dumps(seeded)
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
                    )
                    target_q['feedback'] = refreshed['feedback'] or target_q.get('feedback') or ''
                    target_q['improvement'] = refreshed['improvement'] or target_q.get('improvement') or ''
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
                    # Replace the matching seeded entry with the real outcome.
                    current = _json.loads(edit_fresh.propagated_to or '[]')
                    for i, c in enumerate(current):
                        if int(c.get('submission_id')) == int(sid):
                            current[i] = entry
                            break
                    edit_fresh.propagated_to = _json.dumps(current)
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
            except Exception as final_err:
                db.session.rollback()
                logger.error(f"propagation final-status persist failed: {final_err}")
        except Exception as outer:
            logger.error(f"propagation worker crashed for edit {edit_id}: {outer}")
            try:
                edit_err = FeedbackEdit.query.get(edit_id)
                if edit_err and edit_err.propagation_status == 'pending':
                    edit_err.propagation_status = 'partial'
                    db.session.commit()
            except Exception:
                db.session.rollback()
```

- [ ] **Step 3: Add the four propagation routes**

Append to `app.py` near the existing `/feedback/...` routes (search: `@app.route('/feedback/edit-history`):

```python
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
    edit, err = _check_edit_owner(0)  # placeholder; real lookup uses body
    # ^ Above placeholder is unused — replace with body-driven id below.
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
        # Reject any that aren't legitimate candidates.
        legit = set(candidate_ids)
        invalid = [x for x in provided if x not in legit]
        if invalid:
            return jsonify({'status': 'error', 'message': f'invalid candidates: {invalid}'}), 400
        target_ids = provided

    if not target_ids:
        return jsonify({'status': 'started', 'edit_id': edit_id, 'candidate_count': 0})

    # Mark pending and spawn worker.
    edit.propagation_status = 'pending'
    db.session.commit()
    threading.Thread(
        target=_run_propagation_worker,
        args=(app, edit_id, target_ids),
        daemon=True,
    ).start()
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
```

Then remove the dead-code placeholder line in `feedback_propagate` (`edit, err = _check_edit_owner(0)  # placeholder...`). The function should look like:

```python
@app.route('/feedback/propagate', methods=['POST'])
def feedback_propagate():
    data = request.get_json(silent=True) or {}
    edit_id = data.get('edit_id')
    if not isinstance(edit_id, int):
        return jsonify({'status': 'error', 'message': 'edit_id (int) required'}), 400
    edit, err = _check_edit_owner(edit_id)
    if err:
        return err
    ...  # rest unchanged
```

- [ ] **Step 4: Compile check**

```bash
python3 -m py_compile ai_marking.py app.py
```

Expected: no output.

- [ ] **Step 5: Smoke-test the routes via test_client**

```bash
rm -f /tmp/prop_smoke.db
python3 -c "
import os, secrets, json, uuid
os.environ['DATABASE_URL'] = 'sqlite:////tmp/prop_smoke.db'
os.environ['FLASK_SECRET_KEY'] = 'smoke'
os.environ['ANTHROPIC_API_KEY'] = 'fake'
os.environ['TEACHER_CODE'] = 'smoke'
import app as A
from db import db, Submission, Assignment, Class, Teacher, Student, FeedbackEdit
with A.app.app_context():
    t = Teacher(id=str(uuid.uuid4()), name='Mx', code='SMK')
    cls = Class(id=str(uuid.uuid4()), name='X')
    db.session.add_all([t, cls]); db.session.commit()
    asn = Assignment(id=str(uuid.uuid4()), title='T', subject='Sci', class_id=cls.id, teacher_id=t.id, provider='anthropic', model='m', assign_type='short_answer', show_results=True, subject_family='science', rubrics=b'rb', classroom_code=secrets.token_hex(4))
    db.session.add(asn); db.session.commit()
    stu1 = Student(class_id=cls.id, name='Alice', index_number='001')
    stu2 = Student(class_id=cls.id, name='Bob',   index_number='002')
    db.session.add_all([stu1, stu2]); db.session.commit()
    for stu in (stu1, stu2):
        sub = Submission(student_id=stu.id, assignment_id=asn.id, status='done',
                         result_json=json.dumps({'questions': [{'question_num':'1','feedback':'AI fb','improvement':'AI imp','marks_awarded':2,'marks_total':5}]}))
        db.session.add(sub)
    db.session.commit()
    sub_a = Submission.query.filter_by(student_id=stu1.id).one()
    edit = FeedbackEdit(submission_id=sub_a.id, criterion_id='1', field='feedback',
                        original_text='AI', edited_text='Teacher v', edited_by=t.id,
                        subject_family='science', theme_key='reasoning_gap',
                        assignment_id=asn.id, rubric_version='hash', scope='individual', active=True)
    db.session.add(edit); db.session.commit()
    edit_id = edit.id
    with A.app.test_client() as cli:
        with cli.session_transaction() as s:
            s['teacher_id'] = t.id
        # Candidates route
        r = cli.get(f'/feedback/propagation-candidates/{edit_id}')
        body = r.get_json()
        assert r.status_code == 200 and body['candidate_count'] == 1, body
        # Skip route
        r2 = cli.post('/feedback/propagate-skip', json={'edit_id': edit_id})
        assert r2.status_code == 200 and r2.get_json() == {'status':'ok'}
        edit_fresh = FeedbackEdit.query.get(edit_id)
        assert edit_fresh.propagation_status == 'skipped'
        # Progress route after skip
        r3 = cli.get(f'/feedback/propagation-progress/{edit_id}')
        body3 = r3.get_json()
        assert body3['propagation_status'] == 'skipped' and body3['total'] == 0
        # Wrong-teacher 403
        with cli.session_transaction() as s:
            s['teacher_id'] = str(uuid.uuid4())
        r4 = cli.get(f'/feedback/propagation-candidates/{edit_id}')
        assert r4.status_code == 403, r4.status_code
    print('routes OK')
"
```

Expected: `routes OK`

- [ ] **Step 6: Commit**

```bash
git add ai_marking.py app.py
git commit -m "$(cat <<'EOF'
feat(propagation): refresh helper + worker + four routes

refresh_criterion_feedback rewrites only the feedback + improvement
text fields for one criterion on one student, calibrated against
the teacher's edit on another student. Text-only AI call, cheap-
tier model, no images, no marks change.

_run_propagation_worker processes candidates sequentially in a
background thread (own app context). Seeds propagated_to with
pending entries up front so the progress poll has the full target
list visible from the first poll. Per-iteration commit means
partial progress is visible. Final propagation_status is
'complete' if all done, 'partial' if any failed.

Four new routes — all require ownership of the originating
feedback_edit row:
  GET  /feedback/propagation-candidates/<edit_id>
  POST /feedback/propagate            {edit_id, mode, submission_ids?}
  POST /feedback/propagate-skip       {edit_id}
  GET  /feedback/propagation-progress/<edit_id>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Calibration injection swap (subject-level principles)

**Files:**
- Modify: `ai_marking.py` — add `count_active_calibration_edits`, `get_marking_principles`, `build_calibration_block`. Replace existing call site in `_run_submission_marking` (in `app.py`) with `build_calibration_block`.
- Modify: `app.py` — update `_run_submission_marking` to use `build_calibration_block`.

- [ ] **Step 1: Add `count_active_calibration_edits` to `ai_marking.py`**

Append after `fetch_calibration_examples`:

```python
def count_active_calibration_edits(subject_family):
    """Count active calibration edits across ALL teachers for the given
    subject_family. Used by the calibration-injection threshold gate."""
    from db import db, FeedbackEdit
    if not subject_family:
        return 0
    return db.session.query(FeedbackEdit).filter(
        FeedbackEdit.subject_family == subject_family,
        FeedbackEdit.active == True,  # noqa: E712 — SQLAlchemy comparison
    ).count()
```

- [ ] **Step 2: Add `get_marking_principles` to `ai_marking.py`**

Append after `count_active_calibration_edits`:

```python
def get_marking_principles(provider, model, session_keys, subject_family):
    """Return the shared cached markdown principles file for the subject.

    Regenerates when:
      1. Cache row missing, OR
      2. is_stale=True AND count_active_calibration_edits >= 8, OR
      3. generated_at is older than 30 days.

    Below 8 active edits across the whole subject, returns '' so the caller
    falls back to teacher-scoped raw examples.
    """
    from db import db, MarkingPrinciplesCache, FeedbackEdit
    import json as _json

    THRESHOLD = 8
    if not subject_family:
        return ''
    edit_count = count_active_calibration_edits(subject_family)
    if edit_count < THRESHOLD:
        return ''

    cache = MarkingPrinciplesCache.query.filter_by(subject_family=subject_family).first()

    needs_regen = False
    if cache is None:
        needs_regen = True
    else:
        if cache.is_stale:
            needs_regen = True
        elif cache.generated_at:
            ga = cache.generated_at
            if ga.tzinfo is None:
                ga = ga.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - ga).total_seconds() > 30 * 86400:
                needs_regen = True
        else:
            needs_regen = True

    if not needs_regen:
        return cache.markdown_text or ''

    # ---- Regenerate ----
    edits = (FeedbackEdit.query
             .filter(FeedbackEdit.subject_family == subject_family,
                     FeedbackEdit.active == True)  # noqa: E712
             .all())
    by_theme = {}
    for e in edits:
        tk = e.theme_key or 'unknown'
        by_theme.setdefault(tk, []).append(e)

    # User-prompt summary block.
    summary_lines = []
    for tk, lst in by_theme.items():
        summary_lines.append(f"\n[{tk}] ({len(lst)} edits)")
        for e in lst:
            principle = (e.correction_principle or '').strip()
            if not principle:
                continue
            summary_lines.append(f"- {principle[:150]}")
    summary_block = '\n'.join(summary_lines).strip() or '(no correction_principle text available)'

    system_prompt = (
        "You are summarising a teacher's marking corrections into a concise "
        "principles file. This file will be read by an AI model before marking "
        "new student scripts — write it for that audience, not for the teacher.\n\n"
        "Structure the output as markdown with one section per theme that has "
        "corrections. Each section: a short heading, then one to three bullet "
        "points of principles — generalised rules, not descriptions of specific "
        "edits.\n\n"
        "Rules for writing principles:\n"
        '- Write as imperatives: "Always...", "Never...", "When X, do Y"\n'
        "- Must be specific enough to change marking behaviour\n"
        "- Must not reference specific students, assignments, or dates\n"
        "- Must not exceed 20 words per bullet point\n\n"
        "Where corrections in the same theme conflict — different teachers' "
        "principles pull in opposite directions — take the dominant pattern "
        "(supported by the most edits) and write the principle reflecting it. "
        "If you had to suppress a contradicting principle, set "
        '"has_conflicts": true in your output. Otherwise "has_conflicts": false.\n\n'
        "Maximum total markdown length: 400 words.\n\n"
        "Return JSON only:\n"
        "{\n"
        '  "markdown": "...principles file content (markdown, no preamble)...",\n'
        '  "has_conflicts": true | false\n'
        "}"
    )
    user_prompt = (
        f"Subject family: {subject_family}\n"
        f"Total active calibration edits: {edit_count}\n\n"
        "Edits grouped by theme (each line is one teacher's correction principle):\n"
        f"{summary_block}\n\n"
        "Return the JSON now."
    )

    helper_model = _helper_model_for(provider, model)
    try:
        parsed = _run_feedback_helper(provider, helper_model, session_keys,
                                       system_prompt, user_prompt, max_tokens=700)
    except Exception as e:
        logger.warning(f"principles regen failed for {subject_family}: {e}")
        # Return existing markdown if any, else empty.
        return (cache.markdown_text if cache else '') or ''

    new_md = (parsed.get('markdown') or '').strip()
    has_conflicts = bool(parsed.get('has_conflicts'))
    if not new_md:
        return (cache.markdown_text if cache else '') or ''

    try:
        if cache is None:
            cache = MarkingPrinciplesCache(
                subject_family=subject_family,
                markdown_text=new_md,
                generated_at=datetime.now(timezone.utc),
                is_stale=False,
                edit_count_at_gen=edit_count,
                has_conflicts=has_conflicts,
            )
            db.session.add(cache)
        else:
            cache.markdown_text = new_md
            cache.generated_at = datetime.now(timezone.utc)
            cache.is_stale = False
            cache.edit_count_at_gen = edit_count
            cache.has_conflicts = has_conflicts
        db.session.commit()
        logger.info(f"principles regenerated for {subject_family}: "
                    f"{edit_count} edits, has_conflicts={has_conflicts}")
    except Exception as commit_err:
        db.session.rollback()
        logger.warning(f"principles regen commit failed: {commit_err}")

    return new_md
```

- [ ] **Step 3: Add `build_calibration_block` to `ai_marking.py`**

Append after `get_marking_principles`:

```python
def build_calibration_block(teacher_id, asn, subject_family, theme_keys,
                             provider, model, session_keys):
    """Tiered calibration injection.

    < 8 active edits in the subject (across ALL teachers) → existing
    teacher-scoped raw examples (format_calibration_block over
    fetch_calibration_examples).

    >= 8 → shared markdown principles file. On regeneration failure,
    falls back to a smaller raw-example pull (limit=5).
    """
    THRESHOLD = 8
    edit_count = count_active_calibration_edits(subject_family)
    if edit_count < THRESHOLD:
        return format_calibration_block(
            fetch_calibration_examples(teacher_id, asn, theme_keys, limit=10)
        )

    principles = get_marking_principles(provider, model, session_keys, subject_family)
    if not principles:
        # Regen failed and no prior cache — fall back to raw examples.
        return format_calibration_block(
            fetch_calibration_examples(teacher_id, asn, theme_keys, limit=5)
        )
    return (
        "---\n"
        "MARKING PRINCIPLES (this subject's established standard)\n\n"
        f"{principles}\n"
        "---\n\n"
    )
```

- [ ] **Step 4: Replace the call site in `_run_submission_marking`**

In `app.py`, find the existing calibration block in `_run_submission_marking` (search: `from ai_marking import fetch_calibration_examples, format_calibration_block`). It looks like:

```python
            from ai_marking import fetch_calibration_examples, format_calibration_block
            ...
            calibration_examples = fetch_calibration_examples(...)
            calibration_block = format_calibration_block(calibration_examples)
            if calibration_examples:
                logger.info(f"Marking sub {submission_id}: prepending {len(calibration_examples)} calibration examples")
```

Replace the entire `calibration_block = ''` block with:

```python
        calibration_block = ''
        try:
            from ai_marking import build_calibration_block
            prior = sub.get_result() or {}
            theme_keys = list({
                q.get('theme_key')
                for q in (prior.get('questions') or [])
                if q.get('theme_key')
            })
            calibration_block = build_calibration_block(
                teacher_id=asn.teacher_id,
                asn=asn,
                subject_family=getattr(asn, 'subject_family', None) or '',
                theme_keys=theme_keys,
                provider=asn.provider,
                model=asn.model,
                session_keys=_resolve_api_keys(asn),
            )
            if calibration_block:
                logger.info(f"Marking sub {submission_id}: prepended calibration block ({len(calibration_block)} chars)")
        except Exception as cal_err:
            logger.warning(f"Calibration lookup failed for sub {submission_id}, marking with no calibration: {cal_err}")
            calibration_block = ''
```

- [ ] **Step 5: Compile check**

```bash
python3 -m py_compile ai_marking.py app.py
```

Expected: no output.

- [ ] **Step 6: Smoke-test the threshold + raw fallback**

```bash
rm -f /tmp/prop_smoke.db
python3 -c "
import os, secrets, json, uuid
os.environ['DATABASE_URL'] = 'sqlite:////tmp/prop_smoke.db'
os.environ['FLASK_SECRET_KEY'] = 'smoke'
os.environ['ANTHROPIC_API_KEY'] = 'fake'
os.environ['TEACHER_CODE'] = 'smoke'
import app as A
from db import db, Teacher, Class, Assignment, FeedbackEdit
from ai_marking import count_active_calibration_edits, get_marking_principles, build_calibration_block
with A.app.app_context():
    t = Teacher(id=str(uuid.uuid4()), name='Mx', code='SMK')
    cls = Class(id=str(uuid.uuid4()), name='X')
    db.session.add_all([t, cls]); db.session.commit()
    asn = Assignment(id=str(uuid.uuid4()), title='T', subject='Sci', class_id=cls.id, teacher_id=t.id, provider='anthropic', model='m', assign_type='short_answer', show_results=True, subject_family='science', rubrics=b'rb', classroom_code=secrets.token_hex(4))
    db.session.add(asn); db.session.commit()
    # Below threshold
    assert count_active_calibration_edits('science') == 0
    block = build_calibration_block(t.id, asn, 'science', [], 'anthropic', 'm', None)
    assert block == '', f'expected empty when no edits and no examples, got: {block!r}'
    # Add 7 edits — still below threshold (which is 8). Below-threshold path returns raw examples; with no fetched examples it's still empty.
    for i in range(7):
        db.session.add(FeedbackEdit(submission_id=1, criterion_id=str(i), field='feedback',
                                     original_text='o', edited_text='e', edited_by=t.id,
                                     subject_family='science', theme_key='reasoning_gap',
                                     assignment_id=asn.id, rubric_version='h', scope='individual', active=True,
                                     correction_principle=f'principle {i}'))
    db.session.commit()
    assert count_active_calibration_edits('science') == 7
    # Below-threshold returns whatever fetch_calibration_examples + format produces; safe to assert the principles path is NOT taken.
    # Above threshold (add an 8th).
    db.session.add(FeedbackEdit(submission_id=2, criterion_id='99', field='feedback',
                                 original_text='o', edited_text='e', edited_by=t.id,
                                 subject_family='science', theme_key='content_gap',
                                 assignment_id=asn.id, rubric_version='h', scope='individual', active=True,
                                 correction_principle='principle 8'))
    db.session.commit()
    assert count_active_calibration_edits('science') == 8
    print('threshold counting OK')
"
```

Expected: `threshold counting OK`

(Live AI call for `get_marking_principles` regeneration is not exercised here — needs a real API key. The threshold gate and the cache fallback both work as written.)

- [ ] **Step 7: Commit**

```bash
git add ai_marking.py app.py
git commit -m "$(cat <<'EOF'
feat(propagation): subject-level principles file + calibration injection

count_active_calibration_edits counts ALL teachers' active edits in
a subject_family (no edited_by filter). The bank-shared mechanism
treats the subject as one pool.

get_marking_principles returns the cached markdown for the subject,
regenerating when missing / stale (>= 8 edits) / older than 30 days.
The regen LLM emits {markdown, has_conflicts}; both stored on the
shared cache row. Failure preserves existing markdown.

build_calibration_block tiers the injection: < 8 active edits in
the subject → teacher-scoped raw examples (existing behaviour);
>= 8 → shared principles file wrapped in a 'this subject's
established standard' delimiter.

_run_submission_marking now calls build_calibration_block instead
of fetch_calibration_examples + format_calibration_block directly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `/teacher/marking-patterns` route + template + nav link

**Files:**
- Modify: `app.py` — new route.
- Create: `templates/marking_patterns.html`.
- Modify: `templates/base.html` — nav link.

- [ ] **Step 1: Add route**

In `app.py`, add near the other `/teacher/...` routes (search: `@app.route('/teacher/marking-patterns')` — should not exist yet; pick a sensible location near `dashboard` or `hub` route definitions).

```python
@app.route('/teacher/marking-patterns')
def teacher_marking_patterns():
    if not _is_authenticated():
        return redirect(url_for('hub'))
    teacher = _current_teacher()
    teacher_id = teacher.id if teacher else None
    if not teacher_id:
        return redirect(url_for('hub'))

    from db import FeedbackEdit, MarkingPrinciplesCache
    from sqlalchemy import func as _func

    # Subjects this teacher has contributed to (active only).
    contributed_rows = (
        db.session.query(FeedbackEdit.subject_family,
                         _func.count(FeedbackEdit.id).label('my_count'))
        .filter(FeedbackEdit.edited_by == teacher_id,
                FeedbackEdit.active == True,  # noqa: E712
                FeedbackEdit.subject_family.isnot(None))
        .group_by(FeedbackEdit.subject_family)
        .all()
    )
    if not contributed_rows:
        return render_template('marking_patterns.html',
                                sections=[], teacher=teacher)

    sections = []
    for sf, my_count in contributed_rows:
        total = (db.session.query(_func.count(FeedbackEdit.id))
                 .filter(FeedbackEdit.subject_family == sf,
                         FeedbackEdit.active == True)  # noqa: E712
                 .scalar()) or 0
        cache = MarkingPrinciplesCache.query.filter_by(subject_family=sf).first()
        sections.append({
            'subject_family': sf,
            'my_count': my_count,
            'total_count': total,
            'has_principles': bool(cache and cache.markdown_text and total >= 8),
            'markdown': (cache.markdown_text if cache else '') or '',
            'has_conflicts': bool(cache and cache.has_conflicts),
            'remaining_to_threshold': max(0, 8 - total),
        })
    return render_template('marking_patterns.html', sections=sections, teacher=teacher)
```

- [ ] **Step 2: Create the template**

Create `templates/marking_patterns.html`:

```html
{% extends "base.html" %}
{% block title %}My Marking Patterns{% endblock %}
{% block body %}
<div style="max-width: 900px; margin: 32px auto; padding: 0 24px;">
    <h1 style="font-size: 22px; margin-bottom: 6px;">My Marking Patterns</h1>
    <p style="color: #666; font-size: 13px; margin-bottom: 24px;">
        Each section shows the calibration standards for one subject.
        Below 8 total edits, the AI marker uses raw examples. From 8 onwards,
        a shared principles file (regenerated lazily) replaces them.
    </p>

    {% if not sections %}
    <div style="padding: 16px; background: #f7f8fb; border-radius: 8px; color: #555;">
        No calibration edits yet. Save an edit to the calibration bank from
        a feedback modal to start building your subject's marking pattern.
    </div>
    {% endif %}

    {% for s in sections %}
    <div style="margin-bottom: 28px; padding: 18px 20px; background: white; border: 1px solid #e3e6f0; border-radius: 10px;">
        <div style="display: flex; align-items: baseline; gap: 12px; margin-bottom: 8px;">
            <h2 style="font-size: 17px; margin: 0;">{{ s.subject_family|replace('_', ' ')|title }}</h2>
            <span style="font-size: 12px; color: #7a7f8c;">
                You've contributed {{ s.my_count }} of {{ s.total_count }} active edits in this subject.
            </span>
        </div>

        {% if s.has_principles %}
            {% if s.has_conflicts %}
            <div style="margin-bottom: 12px; padding: 8px 12px; background: #fff8ec; border-left: 3px solid #d59f00; font-size: 12.5px; color: #5a4400;">
                Some standards in this subject look mixed across teachers. The summary below takes the dominant pattern; review your own bank if you'd like to refine your contribution.
            </div>
            {% endif %}
            <pre style="white-space: pre-wrap; font-family: inherit; font-size: 13.5px; color: #2d2d2d; line-height: 1.55; margin: 0;">{{ s.markdown }}</pre>
        {% else %}
            <div style="font-size: 13px; color: #666;">
                Add {{ s.remaining_to_threshold }} more calibration edits across this subject to unlock the shared marking principles.
            </div>
        {% endif %}
    </div>
    {% endfor %}
</div>
{% endblock %}
```

- [ ] **Step 3: Add nav link in `base.html`**

Find the nav block in `templates/base.html` (around line 34, after the Bank link). Insert a new link:

```html
                {% if current_teacher %}
                <a href="/teacher/marking-patterns">My marking patterns</a>
                {% endif %}
```

The exact placement: read the surrounding context to match style. Use whatever conditional pattern the existing teacher-only links use (likely `{% if current_teacher %}` or `{% if session.teacher_id %}`).

- [ ] **Step 4: Compile check + Jinja parse check**

```bash
python3 -m py_compile app.py
python3 -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('templates'))
env.get_template('marking_patterns.html')  # parses; raises if broken
env.get_template('base.html')
print('templates parse OK')
"
```

Expected: `templates parse OK`

- [ ] **Step 5: Smoke-test the route**

```bash
rm -f /tmp/prop_smoke.db
python3 -c "
import os, secrets, uuid
os.environ['DATABASE_URL'] = 'sqlite:////tmp/prop_smoke.db'
os.environ['FLASK_SECRET_KEY'] = 'smoke'
os.environ['ANTHROPIC_API_KEY'] = 'fake'
os.environ['TEACHER_CODE'] = 'smoke'
import app as A
from db import db, Teacher
with A.app.app_context():
    t = Teacher(id=str(uuid.uuid4()), name='Mx', code='SMK')
    db.session.add(t); db.session.commit()
    with A.app.test_client() as cli:
        with cli.session_transaction() as s:
            s['teacher_id'] = t.id
        r = cli.get('/teacher/marking-patterns')
        assert r.status_code == 200, r.status_code
        # Empty state body
        body = r.data.decode()
        assert 'No calibration edits yet' in body, body[:500]
    print('marking-patterns route OK')
"
```

Expected: `marking-patterns route OK`

- [ ] **Step 6: Commit**

```bash
git add app.py templates/marking_patterns.html templates/base.html
git commit -m "$(cat <<'EOF'
feat(propagation): /teacher/marking-patterns page + nav link

Per-subject section: section header shows the human-readable
subject name plus the teacher's contribution ratio
(N of M total active edits). Below 8 total → shows the count of
edits remaining to unlock the shared principles. From 8+ → renders
the cached markdown_text; if has_conflicts is set, a small inline
notice nudges the teacher to review.

No AI calls on this page — pure read from marking_principles_cache
+ feedback_edit. Nav link added to base.html for any logged-in
teacher.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Hub conflict nudge

**Files:**
- Modify: the route that renders the post-login hub (likely `def hub` in `app.py`) — add a `has_conflicts_in_my_subjects` flag passed to the template.
- Modify: `templates/hub.html` — render the soft nudge.

- [ ] **Step 1: Locate the hub route**

```bash
grep -n "@app.route.*['\"]/hub['\"]\\|def hub(" app.py | head -5
```

If `/hub` doesn't exist, the post-login landing might be `/dashboard`. Search:

```bash
grep -n "@app.route.*['\"]/dashboard['\"]" app.py | head -5
```

The right target is whichever route renders `templates/hub.html` (or `templates/dashboard.html`). Confirm by inspecting which template is rendered.

- [ ] **Step 2: Compute the nudge flag in the hub route**

Inside that route, near where other context vars are computed for `render_template`, add:

```python
    has_conflicts_in_my_subjects = False
    try:
        teacher = _current_teacher()
        if teacher:
            from db import FeedbackEdit, MarkingPrinciplesCache
            # Distinct subject_families this teacher contributes active edits to.
            my_subjects_q = (db.session.query(FeedbackEdit.subject_family)
                             .filter(FeedbackEdit.edited_by == teacher.id,
                                     FeedbackEdit.active == True,  # noqa: E712
                                     FeedbackEdit.subject_family.isnot(None))
                             .distinct())
            my_subjects = [row[0] for row in my_subjects_q.all() if row[0]]
            if my_subjects:
                hit = (MarkingPrinciplesCache.query
                       .filter(MarkingPrinciplesCache.has_conflicts == True,  # noqa: E712
                               MarkingPrinciplesCache.subject_family.in_(my_subjects))
                       .first())
                has_conflicts_in_my_subjects = bool(hit)
    except Exception as e:
        logger.warning(f"hub conflict nudge query failed: {e}")
        has_conflicts_in_my_subjects = False
```

Then pass it to the template:

```python
    return render_template('hub.html', ..., has_conflicts_in_my_subjects=has_conflicts_in_my_subjects)
```

(Add the kwarg to the existing `render_template('hub.html', ...)` call without disturbing other kwargs.)

- [ ] **Step 3: Render the nudge in the template**

In `templates/hub.html`, find a sensible top-of-page slot (above the existing card grid). Add:

```html
{% if has_conflicts_in_my_subjects %}
<div style="max-width: 1100px; margin: 16px auto 0; padding: 10px 14px; background: #fff8ec; border-left: 3px solid #d59f00; border-radius: 0 6px 6px 0; font-size: 13px; color: #5a4400;">
    Some standards in your subjects look mixed across teachers.
    <a href="/teacher/marking-patterns" style="color: #5a4400; font-weight: 600;">Review your marking patterns →</a>
</div>
{% endif %}
```

- [ ] **Step 4: Compile check + Jinja parse check**

```bash
python3 -m py_compile app.py
python3 -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('templates'))
env.get_template('hub.html')
print('hub template parse OK')
"
```

Expected: `hub template parse OK`

- [ ] **Step 5: Smoke-test that the nudge fires when has_conflicts is set**

```bash
rm -f /tmp/prop_smoke.db
python3 -c "
import os, secrets, uuid
os.environ['DATABASE_URL'] = 'sqlite:////tmp/prop_smoke.db'
os.environ['FLASK_SECRET_KEY'] = 'smoke'
os.environ['ANTHROPIC_API_KEY'] = 'fake'
os.environ['TEACHER_CODE'] = 'smoke'
import app as A
from db import db, Teacher, FeedbackEdit, MarkingPrinciplesCache
import datetime
with A.app.app_context():
    t = Teacher(id=str(uuid.uuid4()), name='Mx', code='SMK')
    db.session.add(t); db.session.commit()
    # Add an edit attributing this teacher to subject_family='science'
    db.session.add(FeedbackEdit(submission_id=1, criterion_id='1', field='feedback',
                                 original_text='o', edited_text='e', edited_by=t.id,
                                 subject_family='science', assignment_id='dummy',
                                 rubric_version='h', scope='individual', active=True))
    # Add a cache row with has_conflicts=true for science
    db.session.add(MarkingPrinciplesCache(subject_family='science', markdown_text='...',
                                           generated_at=datetime.datetime.utcnow(),
                                           is_stale=False, edit_count_at_gen=8, has_conflicts=True))
    db.session.commit()
    with A.app.test_client() as cli:
        with cli.session_transaction() as s:
            s['teacher_id'] = t.id
        # Visit hub (or whatever the post-login landing route is)
        r = cli.get('/dashboard') if cli.get('/dashboard').status_code == 200 else cli.get('/hub')
        body = r.data.decode()
        assert 'Some standards in your subjects look mixed' in body, 'nudge not rendered: ' + body[:600]
    print('hub nudge OK')
"
```

(If neither `/dashboard` nor `/hub` returns 200, find the actual landing path — `grep -n \"render_template('hub.html'\" app.py` — and use that.)

Expected: `hub nudge OK`

- [ ] **Step 6: Commit**

```bash
git add app.py templates/hub.html
git commit -m "$(cat <<'EOF'
feat(propagation): hub nudge when subjects have conflicting standards

The post-login hub now runs a tiny query at render time: any
subject this teacher contributes to where the shared
marking_principles_cache row has has_conflicts=true? If yes,
render a small one-line notice linking to /teacher/marking-patterns.
No badge, no count, no per-subject breakdown — the patterns page
is where the teacher reads the dominant-pattern summary.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: JS — propagation banner + per-student source indicator

**Files:**
- Modify: `static/js/feedback_render.js` — extend `saveTextField` to read `data.propagation_prompt`; add banner functions.
- Modify: `templates/teacher_detail.html` — add the banner DOM container and the per-student source indicator column.

- [ ] **Step 1: Add the banner DOM placeholder in `teacher_detail.html`**

In `templates/teacher_detail.html`, find `<table class="results-table">` (around line 410 per the explore report). Insert directly above the table:

```html
<div id="fbPropagationBanner" hidden style="margin: 0 0 14px; padding: 12px 16px; background: #f7f8fb; border: 1px solid #c5cbe8; border-radius: 8px; font-size: 13.5px; color: #2d2d2d;">
    <div id="fbPropagationBannerSummary" style="display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;">
        <div id="fbPropagationBannerText" style="flex: 1;"></div>
        <div id="fbPropagationBannerActions" style="display: flex; gap: 8px;">
            <button type="button" id="fbPropagateAllBtn" class="btn-primary" style="padding: 6px 12px; font-size: 12.5px;">Apply same standard to all</button>
            <button type="button" id="fbPropagateReviewBtn" class="btn-secondary" style="padding: 6px 12px; font-size: 12.5px;">Review individually</button>
            <button type="button" id="fbPropagateSkipBtn" class="btn-secondary" style="padding: 6px 12px; font-size: 12.5px;">Skip</button>
        </div>
    </div>
    <div id="fbPropagationBannerReview" hidden style="margin-top: 12px;"></div>
    <div id="fbPropagationBannerProgress" hidden style="margin-top: 8px; font-size: 12.5px; color: #5a6fd6;"></div>
</div>
```

- [ ] **Step 2: Add the per-student source indicator column**

In the same template, find the `<thead>` row (around line 410-415 area) and add a small `<th>` for the indicator:

```html
                    <th style="width: 28px;" title="Feedback source"></th>
```

Place it just before the existing first `<th>` (or in whatever leading position fits the table layout — match the existing pattern of empty-header narrow columns).

In the `<tbody>` row template (the `{% for s in students %}` loop, around lines 416-458), add a `<td>` rendering the indicator:

```html
                    <td title="{{ feedback_source_label.get(s.id, 'No feedback yet') }}" style="text-align: center; color: #7a7f8c;">
                        {{ feedback_source_icon.get(s.id, '') }}
                    </td>
```

In the route that renders `teacher_detail.html` (search `render_template('teacher_detail.html'` in `app.py`), build two dicts before rendering:

```python
    # Per-student feedback source rollup.
    feedback_source_icon = {}
    feedback_source_label = {}
    for s in students:
        sub = next((x for x in submissions if x.student_id == s.id and x.status == 'done'), None)
        if not sub:
            continue
        try:
            result = sub.get_result() or {}
        except Exception:
            continue
        sources = [(q.get('feedback_source') or 'original_ai') for q in (result.get('questions') or [])]
        if not sources:
            continue
        if any(src == 'teacher_edit' for src in sources):
            feedback_source_icon[s.id] = '✎'
            feedback_source_label[s.id] = 'Teacher edited directly'
        elif any(src == 'propagated' for src in sources):
            feedback_source_icon[s.id] = '↻'
            feedback_source_label[s.id] = 'Propagated from another student'
        else:
            feedback_source_icon[s.id] = '○'
            feedback_source_label[s.id] = 'Original AI feedback'
```

Then add `feedback_source_icon=feedback_source_icon, feedback_source_label=feedback_source_label` to the `render_template` kwargs.

(If the `students` / `submissions` variable names differ in this template's render route, use the actual local names from the existing `render_template` call.)

- [ ] **Step 3: Wire the banner into `feedback_render.js`**

Open `static/js/feedback_render.js`. Find `saveTextField` (recently extended in the feedback_edit_log feature). Find the success branch where `data.edit_meta` is read to render the per-field tag. Right after that block, add:

```javascript
    // Propagation banner: server returns propagation_prompt only when at
    // least one fresh calibration edit has > 0 candidates.
    if (data && data.propagation_prompt) {
        try { fbShowPropagationBanner(state, data.propagation_prompt); } catch (e) { /* silent */ }
    }
```

Then append at the bottom of the IIFE (before the `})(window);` close, alongside the existing exposed functions):

```javascript
function fbShowPropagationBanner(state, prompt) {
    var banner = document.getElementById('fbPropagationBanner');
    if (!banner) return;
    if (!prompt || !prompt.candidate_count || prompt.candidate_count <= 0) return;
    var summary = document.getElementById('fbPropagationBannerSummary');
    var review = document.getElementById('fbPropagationBannerReview');
    var progress = document.getElementById('fbPropagationBannerProgress');
    var text = document.getElementById('fbPropagationBannerText');
    if (summary) summary.hidden = false;
    if (review) { review.hidden = true; review.innerHTML = ''; }
    if (progress) { progress.hidden = true; progress.textContent = ''; }
    if (text) {
        text.textContent =
            '⟳ ' + prompt.candidate_count + ' other student' +
            (prompt.candidate_count === 1 ? '' : 's') +
            ' have similar mistakes on ' + (prompt.criterion_name || 'this criterion') + '.';
    }
    banner.dataset.editId = String(prompt.edit_id);
    banner.hidden = false;
    banner.scrollIntoView({behavior: 'smooth', block: 'center'});
    fbAttachPropagationButtons(state);
}

function fbAttachPropagationButtons(state) {
    var allBtn = document.getElementById('fbPropagateAllBtn');
    var revBtn = document.getElementById('fbPropagateReviewBtn');
    var skipBtn = document.getElementById('fbPropagateSkipBtn');
    if (allBtn && !allBtn.dataset.bound) {
        allBtn.dataset.bound = '1';
        allBtn.addEventListener('click', fbPropagateAll);
    }
    if (revBtn && !revBtn.dataset.bound) {
        revBtn.dataset.bound = '1';
        revBtn.addEventListener('click', fbPropagateReview);
    }
    if (skipBtn && !skipBtn.dataset.bound) {
        skipBtn.dataset.bound = '1';
        skipBtn.addEventListener('click', fbPropagateSkip);
    }
}

function fbBannerEditId() {
    var b = document.getElementById('fbPropagationBanner');
    return b ? parseInt(b.dataset.editId || '0', 10) : 0;
}

function fbPropagateAll() {
    var editId = fbBannerEditId();
    if (!editId) return;
    fetch('/feedback/propagate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        credentials: 'same-origin',
        body: JSON.stringify({edit_id: editId, mode: 'all'})
    }).then(function(r){return r.json();}).then(function(data){
        if (data && data.status === 'started') {
            fbStartPropagationPolling(editId);
        }
    });
}

function fbPropagateReview() {
    var editId = fbBannerEditId();
    if (!editId) return;
    var review = document.getElementById('fbPropagationBannerReview');
    if (!review) return;
    review.innerHTML = 'Loading…';
    review.hidden = false;
    fetch('/feedback/propagation-candidates/' + editId, {credentials: 'same-origin'})
        .then(function(r){return r.json();})
        .then(function(data){
            if (!data || !data.candidates) {
                review.textContent = 'Could not load candidates.';
                return;
            }
            review.innerHTML = '';
            (data.candidates || []).forEach(function(c){
                var row = document.createElement('div');
                row.style.cssText = 'padding: 8px 10px; margin-bottom: 6px; border: 1px solid #e3e6f0; border-radius: 6px;';
                var head = document.createElement('label');
                head.style.cssText = 'display: flex; align-items: center; gap: 8px; font-weight: 600; font-size: 12.5px; cursor: pointer;';
                var cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.checked = true;
                cb.dataset.sid = String(c.submission_id);
                head.appendChild(cb);
                var hd = document.createElement('span');
                hd.textContent = c.student_name + ' (' +
                    (c.marks_awarded != null ? c.marks_awarded : '-') + ' / ' +
                    (c.marks_total != null ? c.marks_total : '-') + ')';
                head.appendChild(hd);
                row.appendChild(head);
                var fb = document.createElement('div');
                fb.style.cssText = 'margin-top: 4px; padding-left: 24px; font-size: 12px; color: #555;';
                fb.textContent = c.current_feedback || '(no feedback)';
                row.appendChild(fb);
                review.appendChild(row);
            });
            var actions = document.createElement('div');
            actions.style.cssText = 'margin-top: 10px; display: flex; gap: 8px;';
            var confirm = document.createElement('button');
            confirm.type = 'button';
            confirm.className = 'btn-primary';
            confirm.style.cssText = 'padding: 6px 12px; font-size: 12.5px;';
            confirm.textContent = 'Apply to selected';
            confirm.addEventListener('click', fbPropagateSelectedConfirm);
            actions.appendChild(confirm);
            var cancel = document.createElement('button');
            cancel.type = 'button';
            cancel.className = 'btn-secondary';
            cancel.style.cssText = 'padding: 6px 12px; font-size: 12.5px;';
            cancel.textContent = 'Cancel';
            cancel.addEventListener('click', function(){
                var rev = document.getElementById('fbPropagationBannerReview');
                if (rev) { rev.hidden = true; rev.innerHTML = ''; }
            });
            actions.appendChild(cancel);
            review.appendChild(actions);
        })
        .catch(function(){ review.textContent = 'Could not load candidates.'; });
}

function fbPropagateSelectedConfirm() {
    var editId = fbBannerEditId();
    var review = document.getElementById('fbPropagationBannerReview');
    if (!editId || !review) return;
    var ids = [];
    review.querySelectorAll('input[type="checkbox"]').forEach(function(cb){
        if (cb.checked && cb.dataset.sid) ids.push(parseInt(cb.dataset.sid, 10));
    });
    if (!ids.length) return;
    fetch('/feedback/propagate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        credentials: 'same-origin',
        body: JSON.stringify({edit_id: editId, mode: 'selected', submission_ids: ids})
    }).then(function(r){return r.json();}).then(function(data){
        if (data && data.status === 'started') {
            review.hidden = true;
            review.innerHTML = '';
            fbStartPropagationPolling(editId);
        }
    });
}

function fbPropagateSkip() {
    var editId = fbBannerEditId();
    if (!editId) return;
    fetch('/feedback/propagate-skip', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        credentials: 'same-origin',
        body: JSON.stringify({edit_id: editId})
    }).then(function(){
        var b = document.getElementById('fbPropagationBanner');
        if (b) b.hidden = true;
    });
}

function fbStartPropagationPolling(editId) {
    var progress = document.getElementById('fbPropagationBannerProgress');
    var summary = document.getElementById('fbPropagationBannerSummary');
    if (!progress || !summary) return;
    summary.hidden = true;
    progress.hidden = false;
    progress.textContent = 'Starting…';
    var attempts = 0;
    var timer = setInterval(function(){
        attempts++;
        if (attempts > 60) { clearInterval(timer); progress.textContent = 'Still running…'; return; }
        fetch('/feedback/propagation-progress/' + editId, {credentials: 'same-origin'})
            .then(function(r){return r.json();})
            .then(function(data){
                if (!data) return;
                progress.textContent = 'Updating ' + (data.done || 0) + ' of ' + (data.total || 0) + ' students…';
                if (data.propagation_status === 'complete' || data.propagation_status === 'partial') {
                    clearInterval(timer);
                    var doneN = data.done || 0;
                    var failN = data.failed || 0;
                    progress.textContent = '✓ Feedback updated for ' + doneN + ' student' +
                        (doneN === 1 ? '' : 's') +
                        (failN ? ' · ' + failN + ' failed' : '') + '.';
                    setTimeout(function(){
                        var b = document.getElementById('fbPropagationBanner');
                        if (b) b.hidden = true;
                    }, 4000);
                }
            })
            .catch(function(){ /* silent */ });
    }, 2000);
}
```

- [ ] **Step 4: JS readability check**

```bash
node --check static/js/feedback_render.js 2>/dev/null || python3 -c "open('static/js/feedback_render.js').read(); print('readable')"
```

Expected: no node output (clean) or `readable`.

- [ ] **Step 5: Compile + Jinja parse check**

```bash
python3 -m py_compile app.py
python3 -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('templates'))
env.get_template('teacher_detail.html')
print('teacher_detail template parse OK')
"
```

Expected: `teacher_detail template parse OK`

- [ ] **Step 6: Commit**

```bash
git add app.py static/js/feedback_render.js templates/teacher_detail.html
git commit -m "$(cat <<'EOF'
feat(propagation): banner + per-student feedback source indicator

After a successful PATCH save with calibrate=true that returns
propagation_prompt with candidates, the banner above the results
table populates with criterion + count, and three buttons:
"Apply same standard to all" → POST /feedback/propagate mode=all
+ poll progress every 2s, "Review individually" → fetch candidates
inline with checkboxes, "Skip" → POST /feedback/propagate-skip.

Per-student feedback source indicator column on the results table:
✎ teacher_edit, ↻ propagated, ○ original_ai. Aggregated server-
side from result_json.questions[*].feedback_source.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final verification checklist

After all 7 tasks are committed, run these to confirm end-to-end behaviour and the spec invariants:

- [ ] **Step 1: All modules compile**

```bash
python3 -m py_compile db.py ai_marking.py app.py
```

Expected: no output.

- [ ] **Step 2: All templates parse**

```bash
python3 -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('templates'))
for tpl in ('hub.html','base.html','teacher_detail.html','marking_patterns.html'):
    env.get_template(tpl)
print('all templates parse OK')
"
```

- [ ] **Step 3: App boots clean and registers the new routes**

```bash
python3 -c "
import os
os.environ.setdefault('FLASK_SECRET_KEY','smoke')
os.environ.setdefault('ANTHROPIC_API_KEY','fake')
os.environ.setdefault('TEACHER_CODE','smoke')
import app as a
routes = [str(r) for r in a.app.url_map.iter_rules()]
for needle in ('/feedback/propagation-candidates','/feedback/propagate','/feedback/propagate-skip','/feedback/propagation-progress','/teacher/marking-patterns'):
    assert any(needle in r for r in routes), f'route missing: {needle}'
print('routes registered:', len(routes))
"
```

- [ ] **Step 4: Confirm `mark_script` is never called on already-marked submissions**

```bash
grep -n "mark_script(" app.py | head
```

Expected: every match is in the initial-marking flow (`_run_submission_marking`, bulk marking job). None in the propagation worker, the refresh function, or any new code.

- [ ] **Step 5: Confirm no age-based filter slipped in**

```bash
grep -nE "created_at\s*[<>]" app.py ai_marking.py db.py
```

Expected: no matches in propagation code paths. (The 30-day staleness check on the principles cache uses `generated_at`, which is intentional and is the only such comparison.)

- [ ] **Step 6: Push the branch when satisfied**

```bash
git status
git log --oneline -10
git push -u origin feedback_propagation
```

(Only push when smoke tests pass cleanly. This is a feature branch; the merge to feed_forward_beta and the push to origin happen as a final orchestration step after all task reviews pass.)

---

## Self-review notes

**Spec coverage** — every spec section maps to a task:

- Spec §"Schema (db.py)" → Task 1.
- Spec §"AI helpers (ai_marking.py)" / `extract_correction_insight` → Task 2.
- Spec §"AI helpers" / `refresh_criterion_feedback` → Task 3.
- Spec §"AI helpers" / `count_active_calibration_edits`, `get_marking_principles`, `build_calibration_block` → Task 4.
- Spec §"Server hooks" / `_process_text_edit` extension + `_find_propagation_candidates` + `_run_insight_extraction_worker` → Task 2.
- Spec §"Server hooks" / `_run_propagation_worker` → Task 3.
- Spec §"New routes" → Task 3 (propagation x4) + Task 5 (`/teacher/marking-patterns`).
- Spec §"Client" / banner + per-student indicator → Task 7.
- Spec §"`/teacher/marking-patterns` page" → Task 5.
- Spec §"Header link" → Task 5.
- Spec §"Soft conflict nudge on the teacher hub" → Task 6.
- Spec §"Confirmation: mark_script never re-runs" → final-checklist Step 4.

**Placeholders** — none. Every step has concrete code or commands.

**Type consistency** — `criterion_id` always `str(question_num)`. `feedback_source` enum always `'original_ai' | 'teacher_edit' | 'propagated'`. `propagation_status` enum always `none|pending|partial|complete|skipped`. `transferability` always `high|medium|low` or NULL. All FK types match (`submissions.id` INTEGER, `feedback_edit.id` INTEGER, `assignments.id` VARCHAR(36), `teachers.id` VARCHAR(36)).

**Cheap-tier model** — every new AI call (`extract_correction_insight`, `refresh_criterion_feedback`, `get_marking_principles`) routes through `_helper_model_for(provider, fallback)`. `mark_script` continues to use the assignment's main model.

**Background threads** — `_run_insight_extraction_worker` and `_run_propagation_worker` each open their own `app.app_context()` exactly like `_run_categorisation_worker` does in the existing code. Sequential propagation per worker invocation is enforced by a serial `for sid in target_ids` loop.

**Known limitations**:
- Hub nudge fires lazily — appears only after the next mark-time regen sets `has_conflicts = true`. A "regen on bank save" force-trigger would close that gap but adds latency to every save; out of scope for this branch.
- Multi-edit PATCH (a teacher saving feedback + improvement on the same criterion in one submit) anchors the propagation banner on the FIRST fresh edit. Practically the existing JS sends one field at a time so this is single-edit in real use; flagged for completeness.
