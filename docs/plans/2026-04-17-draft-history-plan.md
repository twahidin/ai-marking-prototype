# Draft History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow students to submit multiple drafts per assignment (teacher opt-in, capped) and get AI feedback on each, while keeping the current single-submission flow as default.

**Architecture:** Add `draft_number` and `is_final` columns to `submissions`, add `allow_drafts`/`max_drafts` to `assignments`. Route all per-student aggregations through `is_final=True`. Upload endpoints (student, teacher single, teacher bulk) share one helper that creates a new draft when drafts are on, or overwrites when off. UI adds a drafts strip to the teacher student-detail view and a drafts list to the student portal.

**Tech Stack:** Python 3.10+, Flask, Flask-SQLAlchemy, Jinja2 templates, vanilla JS (no frontend framework). Database is SQLite in dev, PostgreSQL in prod. No test framework currently — verification via a smoke-test script + manual QA checklist.

**Spec:** `docs/plans/2026-04-17-draft-history-design.md`

---

## Task 1: Schema & Migration

**Files:**
- Modify: `db.py` (Submission and Assignment models, `_migrate_add_columns`)

Goal: Add the four new columns and update the Student↔Submission relationship so a student can have multiple submissions.

- [ ] **Step 1: Update the `Assignment` model**

In `db.py`, inside `class Assignment(db.Model)`, after the `show_results` column (~line 173), add:

```python
    allow_drafts = db.Column(db.Boolean, default=False)
    max_drafts = db.Column(db.Integer, default=3)
```

- [ ] **Step 2: Update the `Submission` model**

In `db.py`, inside `class Submission(db.Model)`, after the `student_amended` column (~line 274), add:

```python
    draft_number = db.Column(db.Integer, default=1, nullable=False)
    is_final = db.Column(db.Boolean, default=True, nullable=False, index=True)
```

- [ ] **Step 3: Change the Student↔Submission relationship to many**

In `db.py`, inside `class Student(db.Model)`, change:

```python
    submission = db.relationship('Submission', backref='student', uselist=False, lazy=True, cascade='all, delete-orphan')
```

to:

```python
    submissions = db.relationship('Submission', backref='student', lazy=True, cascade='all, delete-orphan')
```

(Renamed `submission` → `submissions`, removed `uselist=False`.)

- [ ] **Step 4: Audit for `student.submission` callers**

Run via Grep tool with pattern `\.submission[^s]` in `app.py` and `templates/`. If any reference the old attribute, replace with a query that gets the final submission:

```python
final_sub = Submission.query.filter_by(student_id=student.id, assignment_id=<aid>, is_final=True).first()
```

- [ ] **Step 5: Extend `_migrate_add_columns`**

In `db.py`, inside `_migrate_add_columns(app)`, add these blocks before the closing of the `with app.app_context():` scope. Place the `submissions` block with the existing ones (~line 44), and append an `assignments` block:

```python
        # submissions: draft columns
        if 'submissions' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('submissions')]
            if 'draft_number' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN draft_number INTEGER DEFAULT 1 NOT NULL'))
                db.session.commit()
                logger.info('Added draft_number column to submissions table')
            if 'is_final' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN is_final BOOLEAN DEFAULT TRUE NOT NULL'))
                db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_submissions_is_final ON submissions (is_final)'))
                db.session.commit()
                # Backfill: any prior rows become draft 1, final
                db.session.execute(text('UPDATE submissions SET draft_number = 1 WHERE draft_number IS NULL'))
                db.session.execute(text('UPDATE submissions SET is_final = TRUE WHERE is_final IS NULL'))
                db.session.commit()
                logger.info('Added is_final column to submissions table and backfilled defaults')

        # assignments: draft controls
        if 'assignments' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('assignments')]
            if 'allow_drafts' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN allow_drafts BOOLEAN DEFAULT FALSE NOT NULL'))
                db.session.commit()
                logger.info('Added allow_drafts column to assignments table')
            if 'max_drafts' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN max_drafts INTEGER DEFAULT 3 NOT NULL'))
                db.session.commit()
                logger.info('Added max_drafts column to assignments table')
```

- [ ] **Step 6: Smoke-test the migration**

Run: `python -c "from app import app; from db import db; app.app_context().push()"`
Expected: No errors. Logs should include "Added ... column" lines if this is a fresh migration.

Then verify columns exist:

```bash
python -c "from app import app; from db import db; from sqlalchemy import inspect
app.app_context().push()
ins = inspect(db.engine)
print('submissions cols:', [c['name'] for c in ins.get_columns('submissions')])
print('assignments cols:', [c['name'] for c in ins.get_columns('assignments')])"
```

Expected: both `draft_number` and `is_final` in submissions; both `allow_drafts` and `max_drafts` in assignments.

- [ ] **Step 7: Commit**

```bash
git add db.py
git commit -m "feat(drafts): add draft_number/is_final to submissions, allow_drafts/max_drafts to assignments"
```

---

## Task 2: Draft Helper Functions

**Files:**
- Modify: `app.py` (add helper section near other private helpers at the top of the file, after `_sort_by_index` or similar)

Goal: Centralize draft logic so the three upload paths share one implementation.

- [ ] **Step 1: Add the helpers**

Find a section in `app.py` with other private helpers (look for `_sort_by_index` or `_check_assignment_ownership`). Add these helpers alongside them:

```python
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
    """
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
```

- [ ] **Step 2: Smoke-test import**

Run: `python -c "from app import app, _prepare_new_submission, _get_final_submission, _count_drafts, _next_draft_number; print('ok')"`
Expected: `ok` (no errors).

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat(drafts): add draft helpers (_prepare_new_submission, _get_final_submission, _count_drafts, _next_draft_number)"
```

---

## Task 3: Assignment Create/Edit — Backend

**Files:**
- Modify: `app.py` (assignment create handler around line 3089, and the edit handler — search for `allow_drafts` to confirm no duplicate)

Goal: Accept and persist `allow_drafts` and `max_drafts` on assignment create and edit.

- [ ] **Step 1: Add to the create handler**

In `app.py`, find the `Assignment(...)` constructor call (~line 3089, the one with `show_results=request.form.get('show_results') == 'on',`). Add these fields to the constructor:

```python
        allow_drafts=request.form.get('allow_drafts') == 'on',
        max_drafts=_parse_max_drafts(request.form.get('max_drafts')),
```

Then add this helper near the other helpers in Task 2:

```python
def _parse_max_drafts(raw):
    """Clamp max_drafts input to [2, 10], default 3."""
    try:
        n = int(raw) if raw else 3
    except (TypeError, ValueError):
        n = 3
    return max(2, min(10, n))
```

- [ ] **Step 2: Find and update the edit handler**

Run: `grep -n "def teacher_edit_assignment\|def edit_assignment" app.py`

For each POST handler that updates an existing `Assignment`, add these assignments after the other `asn.* = request.form.get(...)` lines:

```python
    asn.allow_drafts = request.form.get('allow_drafts') == 'on'
    asn.max_drafts = _parse_max_drafts(request.form.get('max_drafts'))
```

If no edit handler exists (some fields are create-only), skip this step and note "edit UI will need a separate task" — but the `class.html` form likely already has an edit flow. Search for `assignment_edit` or `update_assignment`.

- [ ] **Step 3: Smoke-test**

Start the app: `python app.py`. From the teacher UI, create an assignment without any drafts fields in the form yet (form changes are Task 4). The new columns should default correctly — verify via Flask shell:

```bash
python -c "from app import app; from db import db, Assignment; app.app_context().push()
a = Assignment.query.order_by(Assignment.created_at.desc()).first()
print('allow_drafts:', a.allow_drafts, 'max_drafts:', a.max_drafts)"
```

Expected: `allow_drafts: False max_drafts: 3`

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat(drafts): accept allow_drafts and max_drafts on assignment create/edit"
```

---

## Task 4: Assignment Create/Edit — UI

**Files:**
- Modify: `templates/class.html` (assignment form modal)

Goal: Add the checkbox + number input to the assignment modal form so teachers can enable drafts.

- [ ] **Step 1: Locate the form**

Run: `grep -n "show_results\|assign_type\|scoring_mode" templates/class.html` — these fields live near each other in the assignment modal. Find the block that renders the "Show results to students" checkbox.

- [ ] **Step 2: Add the drafts fields**

Immediately after the `show_results` checkbox in the form, add:

```html
<div class="form-row">
  <label class="checkbox">
    <input type="checkbox" id="allow_drafts" name="allow_drafts" onchange="onAllowDraftsChange()">
    Allow students to submit multiple drafts
  </label>
</div>
<div class="form-row" id="max_drafts_row" style="display:none;">
  <label for="max_drafts">Maximum drafts per student</label>
  <input type="number" id="max_drafts" name="max_drafts" value="3" min="2" max="10">
</div>
```

- [ ] **Step 3: Add the JS toggle**

In the `<script>` section of `class.html`, add:

```javascript
function onAllowDraftsChange() {
  const cb = document.getElementById('allow_drafts');
  const row = document.getElementById('max_drafts_row');
  row.style.display = cb.checked ? '' : 'none';
}
```

- [ ] **Step 4: Populate fields when editing an existing assignment**

Find the function that opens the edit modal (search for `editAssignment` or `openEditModal` in `class.html`). Where it prefills `show_results` from the assignment data, add:

```javascript
document.getElementById('allow_drafts').checked = !!asn.allow_drafts;
document.getElementById('max_drafts').value = asn.max_drafts || 3;
onAllowDraftsChange();
```

If the assignment data passed to the modal does not include `allow_drafts`/`max_drafts`, add those fields to the backend endpoint that returns assignment JSON. Run: `grep -n "asn.show_results\|show_results=asn" app.py` to find the serialization spot, and add `'allow_drafts': asn.allow_drafts, 'max_drafts': asn.max_drafts` to the dict.

- [ ] **Step 5: Smoke-test**

Start the app, open the teacher class page, click "New assignment". Verify:
- "Allow students to submit multiple drafts" checkbox appears
- Checking it reveals the "Maximum drafts per student" number input (defaults to 3)
- Unchecking hides it
- Submitting with it checked and max=5 → new assignment has `allow_drafts=True, max_drafts=5` (verify via Flask shell like Task 3 Step 3)

- [ ] **Step 6: Commit**

```bash
git add templates/class.html app.py
git commit -m "feat(drafts): assignment form UI for enabling drafts and setting cap"
```

---

## Task 5: Student Upload — Use Draft Helper

**Files:**
- Modify: `app.py` — `student_upload` function (~line 3365, lines 3417-3421 are the delete-existing block)

Goal: Replace the current delete-existing-and-create pattern with the shared helper, enforcing the cap.

- [ ] **Step 1: Replace the existing-deletion block**

In `app.py`, find in `student_upload`:

```python
    # Delete existing submission if re-submitting
    existing = Submission.query.filter_by(student_id=student.id, assignment_id=assignment_id).first()
    if existing:
        db.session.delete(existing)
        db.session.flush()

    sub = Submission(
        student_id=student.id,
        assignment_id=assignment_id,
        script_bytes=script_pages[0] if script_pages else None,
        status='extracting',
    )
    sub.set_script_pages(script_pages)
    db.session.add(sub)
    db.session.commit()
```

Replace with:

```python
    sub, err = _prepare_new_submission(student, asn)
    if err:
        return jsonify({'success': False, 'error': err}), 400
    sub.script_bytes = script_pages[0] if script_pages else None
    sub.status = 'extracting'
    sub.set_script_pages(script_pages)
    db.session.add(sub)
    db.session.commit()
```

- [ ] **Step 2: Smoke-test the legacy path (drafts off)**

Start the app. On an existing assignment with `allow_drafts=False`, upload a student script via the student portal. Verify via Flask shell:

```bash
python -c "from app import app; from db import db, Submission; app.app_context().push()
count = Submission.query.count()
finals = Submission.query.filter_by(is_final=True).count()
print('total:', count, 'finals:', finals)"
```

Expected: exactly one submission per (student, assignment), all `is_final=True`.

- [ ] **Step 3: Smoke-test the drafts path**

Edit that assignment (or create a new one) to set `allow_drafts=True, max_drafts=2`. From the student portal, upload two different scripts for the same student. Verify:

```bash
python -c "from app import app; from db import db, Submission, Student; app.app_context().push()
subs = Submission.query.order_by(Submission.draft_number).all()
for s in subs: print(s.student_id, s.draft_number, s.is_final, s.status)"
```

Expected: two rows for that student — `draft_number=1, is_final=False` and `draft_number=2, is_final=True`.

Try a third upload: should return a 400 "Draft limit reached (2/2)" error.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat(drafts): student upload uses draft helper and enforces cap"
```

---

## Task 6: Teacher Single Upload — Use Draft Helper

**Files:**
- Modify: `app.py` — `teacher_submit_for_student` function (~line 3247, lines 3272-3285)

Goal: Same transformation as Task 5, for the teacher single-upload path.

- [ ] **Step 1: Replace the existing-deletion block**

In `app.py`, find in `teacher_submit_for_student`:

```python
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
```

Replace with:

```python
    sub, err = _prepare_new_submission(student, asn)
    if err:
        return jsonify({'success': False, 'error': err}), 400
    sub.script_bytes = script_pages[0] if script_pages else None
    sub.status = 'pending'
    sub.set_script_pages(script_pages)
    db.session.add(sub)
    db.session.commit()
```

- [ ] **Step 2: Smoke-test**

Start the app. On a `allow_drafts=True, max_drafts=2` assignment where a student already has 1 draft from Task 5, use the teacher single-upload path to upload for that same student. Verify there are now 2 rows (student's draft 1 is now `is_final=False`, new teacher draft is `draft_number=2, is_final=True`).

Then try a third teacher upload for the same student: should return 400 "Draft limit reached".

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat(drafts): teacher single upload uses draft helper and enforces cap"
```

---

## Task 7: Teacher Bulk Upload — Use Draft Helper, Skip On Cap

**Files:**
- Modify: `app.py` — bulk upload handler (~line 2796 references `existing = Submission.query.filter_by(student_id=s['db_id'], ...)`)

Goal: Bulk upload also creates drafts. When a student is at cap, skip them and record in the job summary.

- [ ] **Step 1: Locate the bulk path**

Run: `grep -n "def.*bulk\|_run_bulk\|bulk_mark\|bulk_upload" app.py`. Find the function that iterates `for s in students` and calls `Submission.query.filter_by(student_id=s['db_id'], ...)`. That is the bulk-pipeline worker (~line 2796).

- [ ] **Step 2: Replace the per-student deletion with the helper**

Find the block near line 2796 that deletes the existing submission and creates a new one inside the loop. Replace the deletion-then-create with the helper call, and add a skip branch for cap-hit:

```python
            student_obj = Student.query.get(s['db_id'])
            if not student_obj:
                continue
            new_sub, err = _prepare_new_submission(student_obj, asn)
            if err:
                skipped.append({
                    'index': s.get('index'),
                    'name': s.get('name'),
                    'reason': err,
                })
                continue
            new_sub.script_bytes = pages[0] if pages else None
            new_sub.status = 'pending'
            new_sub.set_script_pages(pages)
            db.session.add(new_sub)
            db.session.commit()
```

Adjust variable names (`pages`, `asn`, `s`, `skipped`) to match whatever the surrounding function uses. If the function does not already collect a `skipped` list, add one near the start (`skipped = []`) and include it in the final job-summary return payload.

- [ ] **Step 3: Surface skipped students in the job summary**

Find where the bulk job completes and writes its result/summary. Add `'skipped': skipped` to that payload, and in the bulk-result template (search `grep -n "bulk" templates/*.html`) render a "Skipped students" section listing each entry's index, name, and reason.

If no template renders a skipped list, surface it via the JSON API used by the bulk status poller — the frontend can display it as a warning banner.

- [ ] **Step 4: Smoke-test**

Create a `allow_drafts=True, max_drafts=2` assignment. Populate 2 drafts for one student (via student portal + teacher single). Run a bulk upload PDF that includes pages for that same student. Expected: that student is skipped with "Draft limit reached", other students are processed normally, and the job summary lists the skipped student.

- [ ] **Step 5: Commit**

```bash
git add app.py templates/
git commit -m "feat(drafts): bulk upload respects draft cap and reports skipped students"
```

---

## Task 8: Analytics & Aggregation — Filter by `is_final`

**Files:**
- Modify: `app.py` — every location in the grep output below

Goal: All per-student and per-assignment aggregates count only the final draft.

- [ ] **Step 1: List all call sites**

Run: `grep -n "Submission.query.filter_by" app.py`

The sites to update (based on earlier audit) are:
- Line 746
- Line 1175 (`has_sub = Submission.query.filter_by(student_id=s.id)`) — be cautious: this may need `is_final=True` OR it may be checking "has the student submitted anything ever" — inspect the surrounding context
- Line 1357 (`Submission.query.filter_by(status='done')`) — add `, is_final=True`
- Line 1456 (`Submission.query.filter_by(assignment_id=asn.id, status='done')`) — add `, is_final=True`
- Line 1559 (`Submission.query.filter_by(status='done')`) — add `, is_final=True`
- Line 1730 (`Submission.query.filter_by(assignment_id=assignment_id).all()`) — this builds a dict keyed by `student_id`; change to `is_final=True` so the dict maps student to final
- Line 2063 (`Submission.query.filter_by(assignment_id=assignment_id, status='done').all()`) — add `, is_final=True`
- Line 2329 (`Submission.query.filter_by(status='done')`) — add `, is_final=True`
- Line 3158 (`Submission.query.filter_by(student_id=s.id, assignment_id=assignment_id).first()`) — this is the teacher assignment detail; add `, is_final=True`
- Line 3195 (assignment `.all()` for status done) — add `, is_final=True`
- Line 3221 (same pattern) — add `, is_final=True`
- Line 3331 (student portal verify — `subs = {s.student_id: s for s in Submission.query.filter_by(assignment_id=assignment_id).all()}`) — **leave as-is** because Task 9 uses it to show all drafts. See note below.
- Line 3571 (submission status check) — add `, is_final=True`

DO NOT add `is_final=True` to:
- Line 1083 (`Submission.query.filter_by(assignment_id=asn.id).delete()`) — deletion of all submissions on assignment-delete; must delete all drafts, not just finals
- Line 2796, 3272, 3418 — these are the upload paths, now handled by `_prepare_new_submission`
- Line 3331 — student portal verify needs all drafts (see Task 9)

- [ ] **Step 2: Apply the filter**

For each line in the "Sites to update" list from Step 1, edit the call to include `is_final=True`. Example:

Before:
```python
Submission.query.filter_by(assignment_id=asn.id, status='done').all()
```

After:
```python
Submission.query.filter_by(assignment_id=asn.id, status='done', is_final=True).all()
```

If a call has no existing kwargs and is a chained `.filter(...)`, add a `.filter(Submission.is_final == True)` step.

- [ ] **Step 3: Verify no analytics site missed**

Run: `grep -n "Submission.query\|Submission\.is_final" app.py`. Every read-path query that aggregates or counts per student should either include `is_final=True` or be one of the documented exceptions.

- [ ] **Step 4: Smoke-test analytics**

On an assignment with multiple drafts, open the teacher class dashboard. Verify:
- Submitted count = number of students whose *final* draft exists (not total draft count)
- Score heatmap uses final draft's score
- Download "reports ZIP" produces one PDF per student (using final draft)

- [ ] **Step 5: Commit**

```bash
git add app.py
git commit -m "feat(drafts): analytics and aggregation queries filter by is_final"
```

---

## Task 9: Student Portal — Drafts List UI & Backend

**Files:**
- Modify: `app.py` — `student_verify` (~line 3316), `student_review_submission` (~line 3345)
- Modify: `templates/submit.html`

Goal: After a student verifies, show their draft list with view-feedback buttons. Enforce cap visually.

- [ ] **Step 1: Enrich `student_verify` response**

In `app.py`, inside `student_verify` (around line 3331, where `subs = {...}` is built), change the response to include per-student draft info:

Replace:
```python
    subs = {s.student_id: s for s in Submission.query.filter_by(assignment_id=assignment_id).all()}
    student_list = []
    for s in students:
        sub = subs.get(s.id)
        entry = {'id': s.id, 'index': s.index_number, 'name': s.name}
        if sub and sub.status == 'done':
            entry['has_submission'] = True
            entry['submission_id'] = sub.id
        student_list.append(entry)

    session[f'student_auth_{assignment_id}'] = True
    return jsonify({'success': True, 'students': student_list, 'show_results': asn.show_results})
```

With:
```python
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
            'draft_count': len(student_subs),  # includes in-progress
        }
        student_list.append(entry)

    session[f'student_auth_{assignment_id}'] = True
    return jsonify({
        'success': True,
        'students': student_list,
        'show_results': asn.show_results,
        'allow_drafts': asn.allow_drafts,
        'max_drafts': asn.max_drafts,
    })
```

- [ ] **Step 2: Ensure `student_review_submission` accepts any draft**

In `app.py`, `student_review_submission` already works per-submission. Confirm it only checks that the submission belongs to the assignment (line 3354: `if sub.assignment_id != assignment_id or sub.status != 'done'`). No code change needed — this already supports arbitrary drafts.

- [ ] **Step 3: Add drafts panel to `templates/submit.html`**

Open `templates/submit.html`. Find where the student list is rendered after verification (search `has_submission` or `showResults`). Where each student card is rendered, augment it to show the draft list when `allow_drafts` is true:

In the JS that builds the student row (find `student.has_submission` or similar reference, typically around the `students.forEach(...)` block):

```javascript
// After verify response: data.allow_drafts, data.max_drafts available on the parent scope
function renderStudentRow(student, allowDrafts, maxDrafts, showResults) {
  const row = document.createElement('div');
  row.className = 'student-row';
  row.innerHTML = `<div class="student-name">${student.index}. ${student.name}</div>`;

  if (!allowDrafts) {
    // Legacy: single submission UI (preserve existing markup here)
    if (student.drafts.length > 0 && showResults) {
      const d = student.drafts[student.drafts.length - 1];
      row.innerHTML += `<button onclick="reviewSubmission(${d.id})">Review submission</button>`;
    }
    row.innerHTML += `<button onclick="selectStudent(${student.id})">Submit</button>`;
    return row;
  }

  // Drafts UI
  const atCap = student.draft_count >= maxDrafts;
  let draftsHtml = '<div class="drafts-panel"><div class="drafts-title">Drafts</div>';
  if (student.drafts.length === 0) {
    draftsHtml += '<div class="drafts-empty">No drafts yet</div>';
  } else {
    student.drafts.slice().reverse().forEach(d => {
      const finalBadge = d.is_final ? ' <span class="final-badge">Final &#9733;</span>' : '';
      const viewBtn = showResults ? `<button onclick="reviewSubmission(${d.id})">View feedback</button>` : '';
      draftsHtml += `<div class="draft-row">Draft ${d.draft_number} &middot; ${d.submitted_at}${finalBadge} ${viewBtn}</div>`;
    });
  }
  draftsHtml += '</div>';
  row.innerHTML += draftsHtml;

  const submitBtn = atCap
    ? `<button disabled>${student.draft_count}/${maxDrafts} drafts used</button>`
    : `<button onclick="selectStudent(${student.id})">Submit new draft (${student.draft_count}/${maxDrafts})</button>`;
  row.innerHTML += submitBtn;

  return row;
}
```

Then where the existing verify-success callback calls `students.forEach(...)` to populate the list, replace that with a loop that calls `renderStudentRow(s, data.allow_drafts, data.max_drafts, data.show_results)` and appends to the container.

- [ ] **Step 4: Add minimal CSS**

In the `<style>` section of `submit.html`, add:

```css
.drafts-panel { margin: 0.5em 0; padding: 0.5em; background: #f7f7fa; border-radius: 6px; }
.drafts-title { font-weight: 600; margin-bottom: 0.25em; }
.draft-row { padding: 0.15em 0; font-size: 0.9em; }
.final-badge { color: #b8860b; font-weight: 600; }
.drafts-empty { color: #888; font-style: italic; font-size: 0.9em; }
```

- [ ] **Step 5: Smoke-test**

Start the app. As a student, open a drafts-enabled assignment's submit link, enter classroom code. Verify:
- Drafts panel appears for each student
- After a draft is uploaded and marked, it appears in the list with "Final ⭐"
- Submitting a second draft → panel shows Draft 1 + Draft 2 (with ⭐ on Draft 2)
- At cap (3/3), submit button is disabled with "3/3 drafts used"
- Click "View feedback" on any draft → report opens (if `show_results=True`)

Then verify legacy behavior: on a `allow_drafts=False` assignment, the UI should match pre-change behavior (single Review button or Submit button).

- [ ] **Step 6: Commit**

```bash
git add app.py templates/submit.html
git commit -m "feat(drafts): student portal drafts list with per-draft feedback view"
```

---

## Task 10: Teacher Student-Detail — Drafts Strip

**Files:**
- Modify: `app.py` — teacher assignment detail serializer (~line 3158), add two new endpoints
- Modify: `templates/class.html` — student detail panel

Goal: Teachers see a drafts strip with switch-view, set-final, delete actions.

- [ ] **Step 1: Add endpoint: set draft as final**

In `app.py`, after `teacher_submit_for_student`, add:

```python
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
```

- [ ] **Step 2: Add endpoint: delete a draft**

After the set-final endpoint, add:

```python
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
        # Promote the highest remaining draft_number to final
        latest = Submission.query.filter_by(
            student_id=student_id,
            assignment_id=assignment_id,
        ).order_by(Submission.draft_number.desc()).first()
        if latest:
            latest.is_final = True
    db.session.commit()
    return jsonify({'success': True})
```

- [ ] **Step 3: Add a "drafts" JSON endpoint for the teacher**

After the delete endpoint, add:

```python
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
```

- [ ] **Step 4: Render the drafts strip in the teacher UI**

In `templates/class.html`, find the student detail view (search `studentDetail` or `renderStudentFeedback`). Above the feedback rendering, add a drafts strip container:

```html
<div id="drafts_strip" class="drafts-strip"></div>
```

In the JS that opens the student detail, after fetching the feedback, also fetch drafts and render the strip:

```javascript
async function loadDraftsStrip(assignmentId, studentId, currentSubmissionId) {
  const res = await fetch(`/teacher/assignment/${assignmentId}/student/${studentId}/drafts`);
  const data = await res.json();
  if (!data.success) return;
  const strip = document.getElementById('drafts_strip');
  if (data.drafts.length <= 1) { strip.innerHTML = ''; return; }
  const pills = data.drafts.map(d => {
    const active = d.id === currentSubmissionId ? ' active' : '';
    const finalMark = d.is_final ? ' &#9733;' : '';
    return `<button class="draft-pill${active}" onclick="viewDraft(${assignmentId ? `'${assignmentId}'` : 'null'}, ${studentId}, ${d.id})">Draft ${d.draft_number}${finalMark}</button>`;
  }).join('');
  const currentDraft = data.drafts.find(d => d.id === currentSubmissionId);
  const setFinalBtn = currentDraft && !currentDraft.is_final
    ? `<button onclick="setFinal('${assignmentId}', ${currentSubmissionId})">Set as final</button>`
    : '';
  const deleteBtn = `<button class="danger" onclick="deleteDraft('${assignmentId}', ${currentSubmissionId})">Delete draft</button>`;
  strip.innerHTML = `<div class="drafts-pills">${pills}</div><div class="drafts-actions">${setFinalBtn} ${deleteBtn}</div>`;
}

function viewDraft(assignmentId, studentId, submissionId) {
  // Reuse existing logic that renders feedback for a given submission id
  openStudentFeedbackBySubmissionId(assignmentId, studentId, submissionId);
}

async function setFinal(assignmentId, submissionId) {
  const res = await fetch(`/teacher/assignment/${assignmentId}/submission/${submissionId}/set-final`, { method: 'POST' });
  const data = await res.json();
  if (data.success) { window.location.reload(); }
  else { alert(data.error || 'Failed'); }
}

async function deleteDraft(assignmentId, submissionId) {
  if (!confirm('Delete this draft? This cannot be undone.')) return;
  const res = await fetch(`/teacher/assignment/${assignmentId}/submission/${submissionId}/delete`, { method: 'POST' });
  const data = await res.json();
  if (data.success) { window.location.reload(); }
  else { alert(data.error || 'Failed'); }
}
```

The existing flow that opens feedback for a student uses the student id (via `sub = Submission.query.filter_by(..., is_final=True).first()` from Task 8). Add a companion function `openStudentFeedbackBySubmissionId(assignmentId, studentId, submissionId)` that fetches the feedback for a specific submission id (there should already be an endpoint like `/teacher/assignment/<aid>/submission/<sid>/result` or similar — check with `grep -n "submission_id\|sub.id" app.py` and wire accordingly; if missing, add a GET endpoint returning `submission.get_result()`).

- [ ] **Step 5: Add CSS**

Add to `class.html` `<style>`:

```css
.drafts-strip { margin: 1em 0; padding: 0.5em; background: #f7f7fa; border-radius: 6px; display: flex; justify-content: space-between; flex-wrap: wrap; gap: 0.5em; }
.drafts-pills { display: flex; gap: 0.25em; flex-wrap: wrap; }
.draft-pill { padding: 0.25em 0.75em; border-radius: 999px; border: 1px solid #ccc; background: white; cursor: pointer; }
.draft-pill.active { background: #2e7cd6; color: white; border-color: #2e7cd6; }
.drafts-actions button { margin-left: 0.25em; }
.drafts-actions button.danger { background: #e44; color: white; border: none; }
```

- [ ] **Step 6: Smoke-test**

On an assignment with 3 drafts for one student:
- Open teacher student detail → drafts strip shows 3 pills with ⭐ on the final
- Click a different draft → feedback view switches
- Click "Set as final" on a non-final draft → reload shows ⭐ moved; dashboard count still reflects one per student
- Click "Delete draft" on the final → confirm → reload shows 2 drafts, ⭐ promoted to latest remaining
- Delete all drafts for a student → student shows as "not submitted" on the dashboard

- [ ] **Step 7: Commit**

```bash
git add app.py templates/class.html
git commit -m "feat(drafts): teacher drafts strip with switch-view, set-final, and delete"
```

---

## Task 11: Verification Script & Manual QA Checklist

**Files:**
- Create: `scripts/verify_drafts.py`

Goal: One runnable script that asserts the invariants, plus a checklist of end-to-end flows for manual testing before release.

- [ ] **Step 1: Create the verification script**

```python
"""Smoke tests for draft history. Run: python scripts/verify_drafts.py

Fails loudly on any broken invariant. Read-only — does not modify the database.
"""
import sys
from app import app
from db import db, Assignment, Submission, Student

FAIL = 0

def check(cond, msg):
    global FAIL
    if cond:
        print(f'PASS: {msg}')
    else:
        print(f'FAIL: {msg}')
        FAIL += 1

def main():
    with app.app_context():
        # Invariant 1: every (student, assignment) has at most one is_final=True
        rows = db.session.execute(
            db.text('SELECT student_id, assignment_id, COUNT(*) AS n FROM submissions WHERE is_final = TRUE GROUP BY student_id, assignment_id HAVING COUNT(*) > 1')
        ).fetchall()
        check(len(rows) == 0, f'At most one is_final per (student, assignment). Violations: {len(rows)}')

        # Invariant 2: if a student has any submissions, at least one is final
        no_final_rows = db.session.execute(
            db.text(
                'SELECT student_id, assignment_id FROM submissions '
                'GROUP BY student_id, assignment_id '
                'HAVING SUM(CASE WHEN is_final THEN 1 ELSE 0 END) = 0'
            )
        ).fetchall()
        check(len(no_final_rows) == 0, f'Every student with submissions has a final. Violations: {len(no_final_rows)}')

        # Invariant 3: draft_number is unique per (student, assignment)
        dup_rows = db.session.execute(
            db.text(
                'SELECT student_id, assignment_id, draft_number, COUNT(*) AS n FROM submissions '
                'GROUP BY student_id, assignment_id, draft_number HAVING COUNT(*) > 1'
            )
        ).fetchall()
        check(len(dup_rows) == 0, f'draft_number unique per (student, assignment). Violations: {len(dup_rows)}')

        # Invariant 4: draft_count <= max_drafts for drafts-enabled assignments
        violations = []
        for asn in Assignment.query.filter_by(allow_drafts=True).all():
            cap = asn.max_drafts or 3
            counts = db.session.execute(
                db.text('SELECT student_id, COUNT(*) AS n FROM submissions WHERE assignment_id = :aid GROUP BY student_id'),
                {'aid': asn.id}
            ).fetchall()
            for student_id, n in counts:
                if n > cap:
                    violations.append((asn.id, student_id, n, cap))
        check(len(violations) == 0, f'No student exceeds max_drafts. Violations: {violations}')

    if FAIL:
        print(f'\n{FAIL} checks failed')
        sys.exit(1)
    print('\nAll checks passed')

if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Run it**

```bash
python scripts/verify_drafts.py
```

Expected: `All checks passed`.

- [ ] **Step 3: Manual QA checklist (run each in order)**

Do NOT skip. Check off as you go:

- [ ] Existing assignment with `allow_drafts=False`: student uploads → single row created, `is_final=True`, `draft_number=1`. Dashboard count correct.
- [ ] Existing assignment, resubmit from student portal: old row deleted, new row is the only one, `is_final=True`, `draft_number` incremented.
- [ ] Create new assignment with drafts ON, cap=3. Student uploads 3 times → 3 rows, final=last, dashboard shows latest.
- [ ] 4th upload attempt returns "Draft limit reached" error on student portal.
- [ ] Teacher single-upload on same student also respects cap.
- [ ] Teacher bulk upload on drafts-enabled assignment: student at cap is skipped, listed in summary.
- [ ] Teacher opens student detail: drafts strip shows 3 pills, final has ⭐.
- [ ] Click non-final pill: feedback view switches to that draft's content.
- [ ] Click "Set as final" on Draft 2: reload shows ⭐ on Draft 2, dashboard now reflects Draft 2's score.
- [ ] Click "Delete draft" on current (final) draft: reload shows 2 drafts remaining, ⭐ promoted.
- [ ] Delete all drafts for a student: student shows as "not submitted" on dashboard.
- [ ] Toggle `allow_drafts` OFF on assignment with existing drafts: history preserved. New submission from student overwrites current final only, older drafts untouched.
- [ ] Reports ZIP download: one PDF per student, content matches the final draft's feedback.
- [ ] CSV export / HOD dashboard (if in dept mode): counts and averages reflect final drafts only.
- [ ] Demo mode (`DEMO_MODE=TRUE`): drafts feature is irrelevant (submissions disabled). Verify no crashes on assignment list/create.

- [ ] **Step 4: Commit**

```bash
git add scripts/verify_drafts.py
git commit -m "chore(drafts): add invariant smoke-test script for draft history"
```

---

## Self-Review Notes

**Spec coverage:** Every section of the design doc has at least one task:
- Data model → Task 1
- Teacher assignment setup → Tasks 3, 4
- Upload flow → Tasks 5, 6, 7
- Student portal → Task 9
- Teacher detail view → Task 10
- Analytics / downloads → Task 8
- Edge cases → Tasks 10 (delete + auto-promote), 5/6/7 (cap enforcement), QA (Task 11: toggle off behavior)

**Known gap:** The spec mentions seeding multi-draft submissions in demo+dept mode (`seed_data.py`). This was listed as optional in the spec's "Files to touch" — left out of the plan to keep scope tight. Add later if demo needs it.

**Type consistency:** Helper names (`_prepare_new_submission`, `_get_final_submission`, `_count_drafts`, `_next_draft_number`) used consistently across tasks. Endpoint paths (`/set-final`, `/delete`, `/drafts`) used consistently between backend (Task 10) and UI (Task 10).

**Noted for implementer:** There is one pre-existing line in `db.py` that changes: `Student.submission` (singular, `uselist=False`) becomes `Student.submissions` (plural, list). Task 1 Step 4 audits for callers. This is a breaking rename and must not be missed.
