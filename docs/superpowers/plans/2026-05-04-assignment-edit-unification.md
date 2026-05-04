# Assignment Edit Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the bank-edit and class-edit modals visually + functionally identical via a shared Jinja partial; fix the kebab Edit-button reliability bugs; open universal edit/delete in the bank; add PDF previews and PDF replacement to the bank.

**Architecture:** Add 6 columns to `AssignmentBank` (provider, model, pinyin_mode, show_results, allow_drafts, max_drafts) with boot-time migration + backfill. Build one Jinja partial (`_assignment_form_fields.html`) that both modals include. Replace the inline-`onclick` kebab handler with a `data-bank-id` + per-card JSON `<script>` block + delegated handler. Add a new `/teacher/assignment/<id>/file-inline/<file_type>` route mirroring the existing bank inline route.

**Tech Stack:** Flask, SQLAlchemy, Jinja2, vanilla JS, multipart/form-data uploads. **There is no test suite in this repo** — verification is manual via `python app.py` and a browser. Commit after each task.

---

## Pre-flight

- [ ] **Step 0a: Read the spec.**

Open `docs/superpowers/specs/2026-05-04-assignment-edit-unification-design.md` and skim. The plan below implements every section of that spec.

- [ ] **Step 0b: Confirm the working tree is clean and on `sandbox_testing`.**

Run: `git status && git branch --show-current`
Expected: `nothing to commit, working tree clean` and branch `sandbox_testing`.

- [ ] **Step 0c: Make sure the app runs locally before changes.**

Run: `python app.py`
Open `http://localhost:5000/bank` in a browser, log in (use `TEACHER_CODE` env var), confirm the bank page loads. Stop the server (Ctrl-C) before continuing.

---

## Task 1: Add columns to `AssignmentBank` model + boot migration

**Files:**
- Modify: `db.py:431-462` (`AssignmentBank` model)
- Modify: `db.py:39+` (`_migrate_add_columns` function — add a new `assignment_bank` block)

**Why:** Per the schema-evolution policy in `CLAUDE.md`, new columns that downstream code reads must be populated on legacy rows before any reader runs. The model gets the columns + defaults; `_migrate_add_columns` does ALTER TABLE for existing DBs; a one-shot UPDATE backfills nulls to safe defaults.

- [ ] **Step 1.1: Add the 6 columns to the `AssignmentBank` model.**

In `db.py`, locate the `AssignmentBank` class (line 431). After the `marking_instructions` column (line 443), insert these 6 columns:

```python
    # Default settings copied into class assignments by bank_use(). Mirrors
    # the equivalent fields on Assignment so a bank item can carry per-class
    # defaults beyond just text + PDFs.
    provider = db.Column(db.String(50), default='')
    model = db.Column(db.String(100), default='')
    pinyin_mode = db.Column(db.String(20), default='off')
    show_results = db.Column(db.Boolean, default=True)
    allow_drafts = db.Column(db.Boolean, default=False)
    max_drafts = db.Column(db.Integer, default=3)
```

The final block of `AssignmentBank` should now be:

```python
    review_instructions = db.Column(db.Text, default='')
    marking_instructions = db.Column(db.Text, default='')

    provider = db.Column(db.String(50), default='')
    model = db.Column(db.String(100), default='')
    pinyin_mode = db.Column(db.String(20), default='off')
    show_results = db.Column(db.Boolean, default=True)
    allow_drafts = db.Column(db.Boolean, default=False)
    max_drafts = db.Column(db.Integer, default=3)

    question_paper = db.Column(db.LargeBinary)
    answer_key = db.Column(db.LargeBinary)
    rubrics = db.Column(db.LargeBinary)
    reference = db.Column(db.LargeBinary)

    created_by = db.Column(db.String(36), db.ForeignKey('teachers.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    creator = db.relationship('Teacher', backref='bank_items', lazy=True)
```

- [ ] **Step 1.2: Add the migration block to `_migrate_add_columns`.**

In `db.py:_migrate_add_columns` (around line 138, where the `assignments` table block lives), add a new sibling block for `assignment_bank`. Place it AFTER the existing `assignments` block ends (after line ~208 where `pinyin_mode` is added to `assignments`) and BEFORE the `feedback_edit` block (around line 214).

Insert this code:

```python
        if 'assignment_bank' in inspector.get_table_names():
            ab_cols = {c['name'] for c in inspector.get_columns('assignment_bank')}
            ensure_ab = [
                ('provider', "VARCHAR(50) DEFAULT ''"),
                ('model', "VARCHAR(100) DEFAULT ''"),
                ('pinyin_mode', "VARCHAR(20) DEFAULT 'off'"),
                ('show_results', 'BOOLEAN DEFAULT TRUE'),
                ('allow_drafts', 'BOOLEAN DEFAULT FALSE'),
                ('max_drafts', 'INTEGER DEFAULT 3'),
            ]
            for col, ddl in ensure_ab:
                if col not in ab_cols:
                    try:
                        db.session.execute(text(f'ALTER TABLE assignment_bank ADD COLUMN {col} {ddl}'))
                        db.session.commit()
                        logger.info(f'Added {col} column to assignment_bank table')
                    except Exception as _e:
                        db.session.rollback()
                        logger.error(f'assignment_bank ALTER ADD {col} failed: {_e}')
            # One-shot backfill: any null values get the model defaults so
            # bank_use() always finds populated values. Idempotent.
            try:
                db.session.execute(text(
                    "UPDATE assignment_bank SET pinyin_mode = 'off' "
                    "WHERE pinyin_mode IS NULL OR pinyin_mode = ''"
                ))
                db.session.execute(text(
                    "UPDATE assignment_bank SET show_results = TRUE WHERE show_results IS NULL"
                ))
                db.session.execute(text(
                    "UPDATE assignment_bank SET allow_drafts = FALSE WHERE allow_drafts IS NULL"
                ))
                db.session.execute(text(
                    "UPDATE assignment_bank SET max_drafts = 3 WHERE max_drafts IS NULL"
                ))
                db.session.commit()
            except Exception as _e:
                db.session.rollback()
                logger.warning(f'assignment_bank backfill skipped: {_e}')
```

- [ ] **Step 1.3: Boot the app and verify the columns were added.**

Run: `python app.py`
Watch the log output. Expected: 6 lines like `Added provider column to assignment_bank table` etc. (Only on first boot after this change. On subsequent boots, no log lines because the columns already exist.)

Open the SQLite DB to confirm:

Run: `sqlite3 marking.db ".schema assignment_bank"`
Expected: the schema includes `provider`, `model`, `pinyin_mode`, `show_results`, `allow_drafts`, `max_drafts` columns.

If using Postgres locally instead, run: `psql $DATABASE_URL -c "\d assignment_bank"` and verify the same.

Stop the server.

- [ ] **Step 1.4: Commit.**

```bash
git add db.py
git commit -m "$(cat <<'EOF'
schema: add provider/model/drafts/pinyin/show_results to AssignmentBank

Adds 6 default-settings columns to AssignmentBank so bank items can
carry per-class defaults beyond text + PDFs. Includes boot-time
ALTER TABLE migration and idempotent backfill of legacy rows.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Update `bank_use()` to lazy-fill new fields

**Files:**
- Modify: `app.py:7906-7990` (`bank_use` function, specifically the `Assignment(...)` constructor at line 7966)

**Why:** Per the schema-evolution policy, the closest write path must populate the new columns. When bank values are unset (legacy rows), fall back to the same defaults `Assignment` would use today.

- [ ] **Step 2.1: Update the `Assignment(...)` constructor in `bank_use()`.**

In `app.py`, locate `bank_use()` at line 7906. At line 7966, replace the existing `Assignment(...)` construction with the version below. Note especially the new `provider`, `model`, `show_results`, `allow_drafts`, `max_drafts`, and `pinyin_mode` lines.

```python
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
            max_drafts=item.max_drafts or 3,
            review_instructions=item.review_instructions,
            marking_instructions=item.marking_instructions,
            question_paper=item.question_paper,
            answer_key=item.answer_key,
            rubrics=item.rubrics,
            reference=item.reference,
            class_id=cid,
            teacher_id=teacher.id if teacher else None,
        )
```

The existing `provider = next(iter(api_keys))` line at app.py:7953 should be removed (it's replaced by the `chosen_provider` resolution inside the loop).

- [ ] **Step 2.2: Manually verify a Use-in-Class still works.**

Run: `python app.py`. Open the bank, find an existing bank item, click "Use in Class", select a class, click Assign. Expected: success message; navigate to the class page and confirm the new assignment shows up. The provider should be either the bank's stored provider (if set) or the first available key — same behaviour as before for existing rows. Stop the server.

- [ ] **Step 2.3: Commit.**

```bash
git add app.py
git commit -m "$(cat <<'EOF'
feat(bank): bank_use copies provider/model/drafts/pinyin into class assignment

Lazy-fills the new AssignmentBank fields into the cloned Assignment.
Falls back to the first available provider when the bank's stored
provider has no API key configured.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add `/teacher/assignment/<id>/file-inline/<file_type>` route

**Files:**
- Modify: `app.py` (new route inserted near the other class-assignment file routes around line 5569 — `teacher_download` — or near the bank inline route at line 8208)

**Why:** The class-side modal needs an inline PDF preview link analogous to `/bank/<id>/file-inline/<file_type>`. Gated by the same ownership check used by every other `/teacher/assignment/<id>/...` route.

- [ ] **Step 3.1: Add the new route.**

In `app.py`, locate the existing bank inline route at line 8208 (`bank_file_inline`). Add this new sibling route immediately after it:

```python
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
```

- [ ] **Step 3.2: Manually verify the route.**

Run: `python app.py`. Find an existing assignment ID (look at any class page URL) — call it `<asn_id>`. In a logged-in browser, navigate to `http://localhost:5000/teacher/assignment/<asn_id>/file-inline/question_paper`. Expected: the PDF opens inline (browser PDF viewer, not download prompt). Try `file_type=answer_key`, `rubrics`, `reference` similarly. Try a non-existent assignment ID — expected 404. Try an assignment owned by a different teacher (if dept mode) — expected 403. Stop the server.

- [ ] **Step 3.3: Commit.**

```bash
git add app.py
git commit -m "$(cat <<'EOF'
feat(teacher): add inline PDF preview route for class assignments

Mirrors the existing /bank/<id>/file-inline/<type> route but for class
assignments, gated by _check_assignment_ownership. Used by the edit
modal Preview links.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Drop role gate on `/bank/edit` and `/bank/delete`

**Files:**
- Modify: `app.py:8126-8132` (`bank_edit` route — drop role check)
- Modify: `app.py` near the `/bank/delete/<bank_id>` route (drop role check; verify it exists first)

**Why:** Per the user's explicit decision, all teachers can edit and delete any bank item. Authentication still required.

- [ ] **Step 4.1: Locate the `/bank/delete` route.**

Run: `grep -n "/bank/delete" /Users/changshien/Documents/Github/ai-marking-prototype/app.py`
Note the line number of the route handler.

- [ ] **Step 4.2: Drop the role gate on `bank_edit`.**

In `app.py:8126`, the current handler reads:

```python
def bank_edit(bank_id):
    """Edit a bank item. Subject head, lead, HOD can edit."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    teacher = _current_teacher()
    if not teacher or teacher.role not in ('hod', 'subject_head', 'lead', 'owner'):
        return jsonify({'success': False, 'error': 'Not authorized'}), 403

    item = AssignmentBank.query.get_or_404(bank_id)
    ...
```

Replace the docstring + role-check block (the 3 lines starting `teacher = _current_teacher()` and ending `... 'Not authorized'}), 403`) with just an authentication check. The handler should now read:

```python
def bank_edit(bank_id):
    """Edit a bank item. Any authenticated teacher can edit."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    item = AssignmentBank.query.get_or_404(bank_id)
    ...
```

(Task 5 will rebuild the body of this function. For now just remove the role gate and leave the rest unchanged.)

- [ ] **Step 4.3: Drop the role gate on `bank_delete`.**

Open the `bank_delete` route at the line number from step 4.1. Locate any `if teacher.role not in (...)` check and remove it. Keep the `_is_authenticated()` check. The handler should follow the same shape as the new `bank_edit`: auth check → fetch item → delete → respond.

If the existing `bank_delete` handler currently allows the creator-or-privileged-role pattern (`can_delete = creator OR role in (...)`), simplify it to: any authenticated teacher can delete. Remove the creator check too.

- [ ] **Step 4.4: Manually verify edit and delete are reachable as a regular teacher.**

If you don't currently have a non-HOD teacher account, skip the dept-mode test and verify locally as the normal-mode owner. Open `/bank` and confirm:
- Kebab menu still shows Edit + Delete (no errors).
- Clicking Delete prompts confirmation and deletes successfully.
- Clicking Edit (still using the OLD modal until Task 8) still opens the old modal — this is OK.

Stop the server.

- [ ] **Step 4.5: Commit.**

```bash
git add app.py
git commit -m "$(cat <<'EOF'
feat(bank): allow any authenticated teacher to edit/delete bank items

Drops the HOD/subject_head/lead role gate on /bank/edit and /bank/delete.
Trust-based collaborative library: any teacher can refine or remove any
bank item. Delete still requires confirmation client-side.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Extend `/bank/edit` to accept multipart + new fields + PDFs

**Files:**
- Modify: `app.py:8126+` (`bank_edit` function body — full rewrite)

**Why:** The bank edit endpoint must now handle the same field set as `teacher_edit`: text fields, the 6 new default-settings fields, and replace-only uploads of all 4 PDFs. Locked fields (`assign_type`, `scoring_mode`) are not writable.

- [ ] **Step 5.1: Replace the body of `bank_edit`.**

In `app.py`, the current handler (after Task 4's changes) looks like:

```python
@app.route('/bank/edit/<bank_id>', methods=['POST'])
def bank_edit(bank_id):
    """Edit a bank item. Any authenticated teacher can edit."""
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    item = AssignmentBank.query.get_or_404(bank_id)
    data = request.get_json()

    if 'title' in data:
        item.title = data['title'].strip()
    if 'subject' in data:
        item.subject = data['subject'].strip()
    if 'level' in data:
        item.level = data['level'].strip()
    if 'tags' in data:
        item.tags = data['tags'].strip()
    if 'review_instructions' in data:
        item.review_instructions = data['review_instructions'].strip()
    if 'marking_instructions' in data:
        item.marking_instructions = data['marking_instructions'].strip()

    db.session.commit()
    return jsonify({'success': True})
```

Replace the entire body (everything after the auth check) with:

```python
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
        item.subject = _f('subject')
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
```

- [ ] **Step 5.2: Manually verify the existing JSON-bodied save still works.**

Run: `python app.py`. The OLD bank.html (from before Task 8) sends JSON. Open `/bank`, click ⋮ → Edit on a card, change the title, click Save. Expected: success → page reload → new title appears. Stop the server.

- [ ] **Step 5.3: Commit.**

```bash
git add app.py
git commit -m "$(cat <<'EOF'
feat(bank): /bank/edit accepts multipart + PDFs + default-settings fields

Extends the bank edit endpoint to handle the full assignment-create
field set (provider, model, drafts, pinyin, show_results) and to
replace any of the 4 PDFs via multipart upload. Existing JSON-body
callers still work. assign_type and scoring_mode remain locked.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Lock `scoring_mode` in `/teacher/assignment/<id>/edit`

**Files:**
- Modify: `app.py:5249+` (`teacher_edit` function — pin `new_scoring_mode` to current value)

**Why:** Per the spec, `scoring_mode` is locked because changing it invalidates marked submissions. Currently editable at `app.py:5284` and `:5333`. UI lock alone is insufficient defense; reject server-side.

- [ ] **Step 6.1: Change the scoring_mode handling in `teacher_edit`.**

In `app.py`, locate line 5284:

```python
    new_scoring_mode = request.form.get('scoring_mode', asn.scoring_mode or 'status')
```

Replace it with:

```python
    # scoring_mode is locked after creation — changing it would invalidate
    # already-marked submissions. Always pin to the current value regardless
    # of what the form posts.
    new_scoring_mode = asn.scoring_mode or 'status'
```

The downstream `asn.scoring_mode = new_scoring_mode` line at app.py:5333 stays — it's a no-op write of the same value, harmless.

- [ ] **Step 6.2: Manually verify scoring_mode cannot change.**

Run: `python app.py`. Open an existing assignment edit modal (the OLD one — Task 9 rebuilds it). Note the current scoring_mode (e.g., "marks"). Use browser devtools to change the `<select id="editScoringMode">` value to the other option (e.g., "status"), then click Save Changes. Reload the page. Expected: scoring_mode is unchanged (still "marks"). Stop the server.

- [ ] **Step 6.3: Commit.**

```bash
git add app.py
git commit -m "$(cat <<'EOF'
fix(teacher): lock scoring_mode server-side in /teacher/assignment/<id>/edit

Previously the form value was honoured. Changing scoring_mode on a
live assignment invalidates already-marked submissions, so reject
mid-life changes regardless of what the UI posts.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Build the shared partial `_assignment_form_fields.html`

**Files:**
- Create: `templates/_assignment_form_fields.html`

**Why:** Single source of truth for the edit-modal field block. Both `bank.html` and `teacher_detail.html` will include it and pass `mode='bank'` or `mode='class'` plus the entity being edited (`bank_item` or `assignment`).

- [ ] **Step 7.1: Create the partial.**

Create `templates/_assignment_form_fields.html` with the exact contents below.

The partial expects these variables in context (passed via `{% with %}` from the including template):
- `mode` — `'bank'` or `'class'`
- `entity` — the row being edited (`bank_item` or `assignment`); accessed uniformly via `entity.title`, `entity.subject`, etc.
- `canonical_subjects` — list of subject options for the dropdown
- `inline_url_prefix` — string prefix for PDF inline preview links, e.g. `/bank/<id>/file-inline` or `/teacher/assignment/<id>/file-inline`
- `entity_assign_type` — the row's `assign_type` value (used to show Rubrics vs Answer Key)
- `entity_id` — the row's id, used in DOM ids to keep selectors stable when both modals theoretically coexist (they don't, but keeps things clean)

```html
{# Shared assignment edit form fields. Used by both bank.html (mode='bank')
   and teacher_detail.html (mode='class'). Bank version adds Level + Tags rows.
   Locked fields (assign_type, scoring_mode) render as read-only badges. #}

<div class="edit-form-group">
    <label>Type (locked after creation)</label>
    <div class="edit-disabled-field">
        {% if entity_assign_type == 'rubrics' %}Rubrics / Essay{% else %}Short Answer{% endif %}
    </div>
    <div class="edit-disabled-hint">Type cannot be changed after creation.</div>
</div>

<div class="edit-form-group">
    <label>Scoring Mode (locked after creation)</label>
    <div class="edit-disabled-field">
        {% if entity.scoring_mode == 'status' %}Status (correct / partial / incorrect){% else %}Marks (numerical){% endif %}
    </div>
    <div class="edit-disabled-hint">Scoring mode cannot be changed after creation — would invalidate marked submissions.</div>
</div>

<div class="edit-form-row">
    <div class="edit-form-group">
        <label for="editTitle">Title</label>
        <input type="text" id="editTitle" value="{{ entity.title or '' }}">
    </div>
    <div class="edit-form-group">
        <label for="editSubject">Subject</label>
        <input type="text" id="editSubject"
               class="subject-autocomplete"
               autocomplete="off"
               value="{{ entity.subject or '' }}"
               data-subject-options='{{ canonical_subjects|tojson }}'
               oninput="updateEditPinyinVis()"
               onchange="updateEditPinyinVis()">
    </div>
</div>

{% if mode == 'bank' %}
<div class="edit-form-row">
    <div class="edit-form-group">
        <label for="editLevel">Level</label>
        <select id="editLevel">
            <option value="" {% if not entity.level %}selected{% endif %}>No level</option>
            <option value="Sec 1" {% if entity.level == 'Sec 1' %}selected{% endif %}>Sec 1</option>
            <option value="Sec 2" {% if entity.level == 'Sec 2' %}selected{% endif %}>Sec 2</option>
            <option value="Sec 3" {% if entity.level == 'Sec 3' %}selected{% endif %}>Sec 3</option>
            <option value="Sec 4" {% if entity.level == 'Sec 4' %}selected{% endif %}>Sec 4</option>
            <option value="Sec 5" {% if entity.level == 'Sec 5' %}selected{% endif %}>Sec 5</option>
        </select>
    </div>
    <div class="edit-form-group">
        <label for="editTags">Tags (comma-separated)</label>
        <input type="text" id="editTags"
               value="{{ entity.tags or '' }}"
               placeholder="#algebra, #geometry">
    </div>
</div>
{% endif %}

<!-- Hanyu Pinyin (Chinese subject only). Hidden unless subject resolves to chinese. -->
<div class="edit-form-group" id="editPinyinGroup" style="display:none;">
    <label for="editPinyinMode">Show Pinyin (Chinese only)</label>
    <select id="editPinyinMode">
        <option value="off" {% if (entity.pinyin_mode or 'off') == 'off' %}selected{% endif %}>Off (no pinyin)</option>
        <option value="vocab" {% if entity.pinyin_mode == 'vocab' %}selected{% endif %}>Vocab — HSK 4 and above</option>
        <option value="advanced" {% if entity.pinyin_mode == 'advanced' %}selected{% endif %}>Advanced — chengyu, 4-char idioms, HSK 6 only</option>
        <option value="full" {% if entity.pinyin_mode == 'full' %}selected{% endif %}>Full — every character</option>
    </select>
</div>

<div class="edit-form-row">
    <div class="edit-form-group">
        <label for="editProvider">AI Provider</label>
        <select id="editProvider" onchange="updateEditModelOptions()"></select>
    </div>
    <div class="edit-form-group">
        <label for="editModel">Model</label>
        <select id="editModel"></select>
    </div>
</div>

<div class="edit-form-row">
    <div class="edit-form-group">
        <label for="editTotalMarks">Total Marks</label>
        <input type="text" id="editTotalMarks" value="{{ entity.total_marks or '' }}" placeholder="e.g. 100">
    </div>
    <div class="edit-form-group">
        <label for="editShowResults">Show Results to Students</label>
        <select id="editShowResults">
            <option value="on" {% if entity.show_results %}selected{% endif %}>Yes</option>
            <option value="" {% if not entity.show_results %}selected{% endif %}>No</option>
        </select>
    </div>
</div>

<!-- Allow Drafts (currently feature-flagged off in class UI; rendered hidden so the form-post still includes its current value). -->
<div class="edit-form-group" style="display:none;">
    <label for="editAllowDrafts">Allow Drafts</label>
    <select id="editAllowDrafts" onchange="document.getElementById('editMaxDraftsRow').style.display = this.value === 'on' ? '' : 'none';">
        <option value="on" {% if entity.allow_drafts %}selected{% endif %}>Yes</option>
        <option value="" {% if not entity.allow_drafts %}selected{% endif %}>No</option>
    </select>
</div>
<div class="edit-form-group" id="editMaxDraftsRow" style="display:none;">
    <label for="editMaxDrafts">Max Drafts</label>
    <input type="number" id="editMaxDrafts" min="2" max="10" value="{{ entity.max_drafts or 3 }}">
</div>

<div class="edit-form-group">
    <label for="editReviewInstructions">Feedback / Review Instructions</label>
    <textarea id="editReviewInstructions">{{ entity.review_instructions or '' }}</textarea>
</div>

<div class="edit-form-group">
    <label for="editMarkingInstructions">Marking Instructions</label>
    <textarea id="editMarkingInstructions">{{ entity.marking_instructions or '' }}</textarea>
</div>

<div class="edit-form-group">
    <label>Question Paper</label>
    {% if entity.question_paper %}
    <div class="edit-current-file">
        ✓ Currently uploaded ·
        <a href="{{ inline_url_prefix }}/question_paper" target="_blank" rel="noopener">Preview ↗</a>
    </div>
    {% else %}
    <div class="edit-current-file" style="color:#888;">No file uploaded.</div>
    {% endif %}
    <input type="file" id="editQuestionPaper" accept=".pdf,.jpg,.jpeg,.png,.heic">
    <div class="edit-disabled-hint">Leave empty to keep current; pick a file to replace.</div>
</div>

{% if entity_assign_type != 'rubrics' %}
<div class="edit-form-group">
    <label>Answer Key</label>
    {% if entity.answer_key %}
    <div class="edit-current-file">
        ✓ Currently uploaded ·
        <a href="{{ inline_url_prefix }}/answer_key" target="_blank" rel="noopener">Preview ↗</a>
    </div>
    {% else %}
    <div class="edit-current-file" style="color:#888;">No file uploaded.</div>
    {% endif %}
    <input type="file" id="editAnswerKey" accept=".pdf,.jpg,.jpeg,.png,.heic">
    <div class="edit-disabled-hint">Leave empty to keep current; pick a file to replace.</div>
</div>
{% else %}
<div class="edit-form-group">
    <label>Rubrics</label>
    {% if entity.rubrics %}
    <div class="edit-current-file">
        ✓ Currently uploaded ·
        <a href="{{ inline_url_prefix }}/rubrics" target="_blank" rel="noopener">Preview ↗</a>
    </div>
    {% else %}
    <div class="edit-current-file" style="color:#888;">No file uploaded.</div>
    {% endif %}
    <input type="file" id="editRubrics" accept=".pdf,.jpg,.jpeg,.png,.heic">
    <div class="edit-disabled-hint">Leave empty to keep current; pick a file to replace.</div>
</div>
{% endif %}

<div class="edit-form-group">
    <label>Reference Materials (optional)</label>
    {% if entity.reference %}
    <div class="edit-current-file">
        ✓ Currently uploaded ·
        <a href="{{ inline_url_prefix }}/reference" target="_blank" rel="noopener">Preview ↗</a>
    </div>
    {% else %}
    <div class="edit-current-file" style="color:#888;">No file uploaded.</div>
    {% endif %}
    <input type="file" id="editReference" accept=".pdf,.jpg,.jpeg,.png,.heic">
    <div class="edit-disabled-hint">Leave empty to keep current; pick a file to replace.</div>
</div>
```

- [ ] **Step 7.2: Commit.**

```bash
git add templates/_assignment_form_fields.html
git commit -m "$(cat <<'EOF'
feat(templates): add shared _assignment_form_fields partial

Single source of truth for the edit-modal field block. Bank vs class
mode controlled by template variable; bank adds Level + Tags rows.
Locked Type and Scoring Mode rendered as read-only badges. PDFs each
get an inline Preview link + Replace picker.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Rewire `bank.html` — kebab JS + edit modal

**Files:**
- Modify: `templates/bank.html`

**Why:** Drop the role gate (line 175). Replace the inline `onclick(...)` with seven escaped args (line 183) — the root cause of "Edit click does nothing on items with apostrophes". Embed a per-card JSON `<script>` block. Replace the modal contents with the shared partial. Switch the save handler to `multipart/form-data`.

- [ ] **Step 8.1: Drop the role gate.**

In `templates/bank.html`, locate line 175:

```jinja
{% set can_edit = teacher and teacher.role in ('hod', 'subject_head', 'lead', 'owner') %}
{% set can_delete = can_edit or (teacher and item.created_by == teacher.id) %}
```

Replace both lines with:

```jinja
{% set can_edit = bool(teacher) %}
{% set can_delete = bool(teacher) %}
```

- [ ] **Step 8.2: Add the per-card JSON data block + change the Edit button to a data-attribute trigger.**

In `templates/bank.html`, locate the card-menu block (around line 178–189). The current Edit button is:

```html
<button type="button" role="menuitem" onclick="openEditModal('{{ item.id }}', '{{ item.title | e }}', '{{ item.subject | e }}', '{{ item.level | e }}', '{{ item.tags | e }}', '{{ (item.review_instructions or '') | e }}', '{{ (item.marking_instructions or '') | e }}'); closeAllCardMenus();">Edit</button>
```

Replace the entire `<div class="card-menu">…</div>` block (lines 179–189) with:

```html
<div class="card-menu" data-card-menu>
    <button type="button" class="card-menu-btn" aria-haspopup="true" aria-expanded="false" aria-label="More actions">⋮</button>
    <div class="card-menu-pop" role="menu">
        {% if can_edit %}
        <button type="button" role="menuitem" data-action="edit" data-bank-id="{{ item.id }}">Edit</button>
        {% endif %}
        {% if can_delete %}
        <button type="button" role="menuitem" class="danger" data-action="delete" data-bank-id="{{ item.id }}">Delete</button>
        {% endif %}
    </div>
</div>
```

Then, immediately after the card opening tag (`<div class="bank-card">`), add a hidden JSON data block — place it as the FIRST child of the card so the Edit handler can find it via `card.querySelector('.bank-card-data')`:

```html
<script type="application/json" class="bank-card-data">{{ {
    'id': item.id,
    'title': item.title or '',
    'subject': item.subject or '',
    'level': item.level or '',
    'tags': item.tags or '',
    'review_instructions': item.review_instructions or '',
    'marking_instructions': item.marking_instructions or '',
    'assign_type': item.assign_type,
    'scoring_mode': item.scoring_mode,
    'total_marks': item.total_marks or '',
    'provider': item.provider or '',
    'model': item.model or '',
    'pinyin_mode': item.pinyin_mode or 'off',
    'show_results': item.show_results if item.show_results is not none else true,
    'allow_drafts': item.allow_drafts if item.allow_drafts is not none else false,
    'max_drafts': item.max_drafts or 3,
    'has_question_paper': item.question_paper is not none,
    'has_answer_key': item.answer_key is not none,
    'has_rubrics': item.rubrics is not none,
    'has_reference': item.reference is not none
} | tojson | safe }}</script>
```

- [ ] **Step 8.3: Replace the edit modal contents with the shared partial.**

In `templates/bank.html`, the existing edit modal at lines 249–279 has hand-rolled fields. Replace the inner contents of `<div class="modal-box">` (i.e., everything between `<h3>Edit Bank Item</h3>` and `<div class="modal-actions">`) with a placeholder div that the JS will populate via fetch.

The reason: the partial needs the entity's row data (PDF presence, current pinyin, etc.) to render the right initial state. Either:
- (a) render the partial server-side once per page-load with a placeholder entity and update via JS (won't work cleanly because the partial is value-bound)
- (b) re-render the modal contents per Edit click via a server endpoint
- (c) **simplest**: keep the partial-include approach but render it with a per-card JSON-serialised entity. Render the modal as an empty shell and have the JS populate field values from the per-card JSON when Edit is clicked.

Use approach (c). Replace the current edit modal (lines 249-279) with:

```html
<!-- Edit Modal: shell only; fields populated from per-card JSON on click -->
<div class="modal-overlay edit-modal" id="editModal">
    <div class="modal-box edit-modal-box" style="max-width:700px;max-height:90vh;overflow-y:auto;">
        <h3>Edit Bank Item</h3>
        <input type="hidden" id="editBankId">
        <div id="editFormHost">
            {# Server-rendered partial with a SENTINEL bank_item so the structure exists at load time. #}
            {% with mode='bank',
                   entity=sentinel_bank_item,
                   entity_id='__SENTINEL__',
                   entity_assign_type=sentinel_bank_item.assign_type,
                   inline_url_prefix='/bank/__SENTINEL__/file-inline',
                   canonical_subjects=canonical_subjects %}
                {% include '_assignment_form_fields.html' %}
            {% endwith %}
        </div>
        <div class="error-msg edit-error" id="editError" style="display:none;"></div>
        <div class="modal-actions edit-modal-actions">
            <button class="modal-cancel edit-cancel" onclick="closeEditModal()">Cancel</button>
            <button class="modal-submit edit-save" id="editBtn" onclick="saveEdit()">Save</button>
        </div>
    </div>
</div>
```

- [ ] **Step 8.4: Wire up the `bank()` view to pass the new context vars.**

The partial needs `canonical_subjects` and `sentinel_bank_item`. Find the bank route in `app.py`:

Run: `grep -n "def bank()\\|@app.route('/bank')" /Users/changshien/Documents/Github/ai-marking-prototype/app.py | head -5`

Open the route handler. Locate the `render_template('bank.html', ...)` call. Add:

```python
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
```

Then pass it (and `canonical_subjects` if not already passed) into the `render_template` call:

```python
    return render_template('bank.html',
                           ...,  # existing args
                           canonical_subjects=SUBJECT_DISPLAY_NAMES,
                           sentinel_bank_item=sentinel_bank_item)
```

- [ ] **Step 8.5: Replace `openEditModal` and the kebab handler in bank.html JS.**

Locate the `<script>` block at the bottom of `bank.html` (around line 330+). Replace `openEditModal(...)` (the 7-arg version at line 436) and add a delegated handler. Also rebuild `saveEdit()` to use FormData.

Replace the entire JS block from `/* Edit Modal */` (line 435) through the end of `saveEdit()` (line 473) with:

```javascript
/* Edit Modal — populated from per-card JSON on click. */
function openEditModal(data) {
    document.getElementById('editBankId').value = data.id;
    document.getElementById('editTitle').value = data.title || '';
    document.getElementById('editSubject').value = data.subject || '';
    var lvl = document.getElementById('editLevel'); if (lvl) lvl.value = data.level || '';
    var tg = document.getElementById('editTags'); if (tg) tg.value = data.tags || '';
    document.getElementById('editTotalMarks').value = data.total_marks || '';
    document.getElementById('editReviewInstructions').value = data.review_instructions || '';
    document.getElementById('editMarkingInstructions').value = data.marking_instructions || '';
    document.getElementById('editShowResults').value = data.show_results ? 'on' : '';
    document.getElementById('editAllowDrafts').value = data.allow_drafts ? 'on' : '';
    document.getElementById('editMaxDrafts').value = data.max_drafts || 3;
    var pin = document.getElementById('editPinyinMode'); if (pin) pin.value = data.pinyin_mode || 'off';

    // Update the inline-preview link prefixes from sentinel to the actual bank id.
    document.querySelectorAll('#editFormHost a[href*="__SENTINEL__"]').forEach(function (a) {
        a.setAttribute('href', a.getAttribute('href').replace(/__SENTINEL__/g, data.id));
    });

    // Show/hide PDF current-file rows based on data.has_*
    function setPresence(label, present, ftype) {
        var hostGroups = document.querySelectorAll('#editFormHost .edit-form-group');
        hostGroups.forEach(function (g) {
            var lbl = g.querySelector('label');
            if (!lbl || lbl.textContent.trim().split(' ')[0].toLowerCase() !== label.toLowerCase()) return;
            var cur = g.querySelector('.edit-current-file');
            if (!cur) return;
            if (present) {
                cur.innerHTML = '✓ Currently uploaded · <a href="/bank/' + data.id + '/file-inline/' + ftype + '" target="_blank" rel="noopener">Preview ↗</a>';
                cur.style.color = '';
            } else {
                cur.textContent = 'No file uploaded.';
                cur.style.color = '#888';
            }
        });
    }
    setPresence('Question', data.has_question_paper, 'question_paper');
    setPresence('Answer', data.has_answer_key, 'answer_key');
    setPresence('Rubrics', data.has_rubrics, 'rubrics');
    setPresence('Reference', data.has_reference, 'reference');

    // Reset file inputs (so previous selection doesn't carry between opens)
    ['editQuestionPaper', 'editAnswerKey', 'editRubrics', 'editReference'].forEach(function (id) {
        var el = document.getElementById(id); if (el) el.value = '';
    });

    // Populate provider/model dropdowns. updateEditPinyinVis runs after subject is set.
    if (typeof populateEditProviderModel === 'function') {
        populateEditProviderModel(data.provider, data.model);
    }
    if (typeof updateEditPinyinVis === 'function') updateEditPinyinVis();

    document.getElementById('editError').style.display = 'none';
    document.getElementById('editBtn').disabled = false;
    document.getElementById('editBtn').textContent = 'Save';
    document.getElementById('editModal').classList.add('active');
}
function closeEditModal() { document.getElementById('editModal').classList.remove('active'); }

/* Delegated handler for Edit / Delete clicks inside any kebab menu. */
document.addEventListener('click', function (ev) {
    var btn = ev.target.closest('[data-action]');
    if (!btn) return;
    var action = btn.getAttribute('data-action');
    var bankId = btn.getAttribute('data-bank-id');
    if (!action || !bankId) return;
    if (action === 'edit') {
        var card = btn.closest('.bank-card');
        if (!card) return;
        var dataNode = card.querySelector('.bank-card-data');
        if (!dataNode) return;
        var data;
        try { data = JSON.parse(dataNode.textContent); }
        catch (e) { console.error('bank-card-data JSON parse failed', e); return; }
        openEditModal(data);
        closeAllCardMenus();
    } else if (action === 'delete') {
        deleteItem(bankId);
        closeAllCardMenus();
    }
});

async function saveEdit() {
    var btn = document.getElementById('editBtn');
    btn.disabled = true; btn.textContent = 'Saving...';
    var errEl = document.getElementById('editError');
    errEl.style.display = 'none';
    var id = document.getElementById('editBankId').value;

    var fd = new FormData();
    fd.append('title', document.getElementById('editTitle').value);
    fd.append('subject', document.getElementById('editSubject').value);
    fd.append('level', (document.getElementById('editLevel') || {}).value || '');
    fd.append('tags', (document.getElementById('editTags') || {}).value || '');
    fd.append('total_marks', document.getElementById('editTotalMarks').value);
    fd.append('review_instructions', document.getElementById('editReviewInstructions').value);
    fd.append('marking_instructions', document.getElementById('editMarkingInstructions').value);
    fd.append('provider', document.getElementById('editProvider').value || '');
    fd.append('model', document.getElementById('editModel').value || '');
    fd.append('pinyin_mode', (document.getElementById('editPinyinMode') || {}).value || 'off');
    if (document.getElementById('editShowResults').value === 'on') fd.append('show_results', 'on');
    if (document.getElementById('editAllowDrafts').value === 'on') fd.append('allow_drafts', 'on');
    fd.append('max_drafts', document.getElementById('editMaxDrafts').value);

    var qp = document.getElementById('editQuestionPaper');
    if (qp && qp.files.length) fd.append('question_paper', qp.files[0]);
    var ak = document.getElementById('editAnswerKey');
    if (ak && ak.files.length) fd.append('answer_key', ak.files[0]);
    var rub = document.getElementById('editRubrics');
    if (rub && rub.files.length) fd.append('rubrics', rub.files[0]);
    var ref = document.getElementById('editReference');
    if (ref && ref.files.length) fd.append('reference', ref.files[0]);

    try {
        var res = await fetch('/bank/edit/' + id, { method: 'POST', body: fd });
        var data = await res.json();
        if (data.success) { window.location.reload(); }
        else { errEl.textContent = data.error || 'Save failed.'; errEl.style.display = 'block'; btn.disabled = false; btn.textContent = 'Save'; }
    } catch (err) { errEl.textContent = 'Network error.'; errEl.style.display = 'block'; btn.disabled = false; btn.textContent = 'Save'; }
}
```

- [ ] **Step 8.6: Add the helper functions referenced by the partial (`updateEditPinyinVis`, `updateEditModelOptions`, `populateEditProviderModel`).**

These are referenced from `_assignment_form_fields.html` and from `openEditModal`. Add them at the top of the existing `<script>` block in `bank.html` (just inside `{% block scripts %}`'s opening `<script>` tag):

```javascript
/* AI provider/model dropdown population for the edit modal. */
const ALL_PROVIDERS = {{ all_providers | tojson }};
const AVAILABLE_PROVIDERS = {{ providers | tojson }};

function populateEditProviderModel(currentProvider, currentModel) {
    var pSel = document.getElementById('editProvider');
    if (!pSel) return;
    pSel.innerHTML = '';
    Object.keys(AVAILABLE_PROVIDERS).forEach(function (key) {
        var opt = document.createElement('option');
        opt.value = key;
        opt.textContent = ALL_PROVIDERS[key] && ALL_PROVIDERS[key].label ? ALL_PROVIDERS[key].label : key;
        if (key === currentProvider) opt.selected = true;
        pSel.appendChild(opt);
    });
    if (!currentProvider && pSel.options.length) pSel.selectedIndex = 0;
    updateEditModelOptions();
    var mSel = document.getElementById('editModel');
    if (mSel && currentModel) {
        Array.from(mSel.options).forEach(function (o) { if (o.value === currentModel) o.selected = true; });
    }
}

function updateEditModelOptions() {
    var pSel = document.getElementById('editProvider');
    var mSel = document.getElementById('editModel');
    if (!pSel || !mSel) return;
    var prov = pSel.value;
    mSel.innerHTML = '';
    var conf = ALL_PROVIDERS[prov];
    if (!conf) return;
    var models = conf.models || [];
    var defaultModel = conf.default || (models[0] || '');
    models.forEach(function (m) {
        var opt = document.createElement('option');
        opt.value = m;
        opt.textContent = m;
        if (m === defaultModel) opt.selected = true;
        mSel.appendChild(opt);
    });
}

/* Show pinyin selector only when subject is Chinese. */
function updateEditPinyinVis() {
    var subj = (document.getElementById('editSubject') || {}).value || '';
    var grp = document.getElementById('editPinyinGroup');
    if (!grp) return;
    var isChinese = /chinese|中文|华文|cn/i.test(subj.trim());
    grp.style.display = isChinese ? '' : 'none';
}
```

The bank route also needs to pass `all_providers` and `providers` to the template. Find the bank route in `app.py` (from Step 8.4). Add to the render_template call:

```python
    from ai_marking import get_available_providers, ALL_PROVIDERS as _ALL_PROV
    providers = get_available_providers(api_keys=_resolve_api_keys_for_teacher(teacher) if teacher else {})
```

NOTE: the exact name of the helper is `get_available_providers` and is defined in `ai_marking.py`. If `_resolve_api_keys_for_teacher` doesn't exist, mirror what `index()` or `class()` views do — search for `get_available_providers(` in `app.py` and copy the surrounding 3-4 lines that resolve API keys for the current context. Pass both `providers` and `all_providers=_ALL_PROV` (or whatever the global provider config dict is named) into `render_template('bank.html', ...)`.

If the existing bank view already passes `providers` and `all_providers`, skip this addition.

- [ ] **Step 8.7: Manually verify the kebab + edit + save end-to-end.**

Run: `python app.py`. Open `/bank`.

Verify each:

1. **Kebab visible for regular teacher:** if you can switch to a regular-teacher account, do; otherwise verify as the owner. Kebab ⋮ should appear on every card.
2. **Edit click opens modal:** click ⋮ then Edit on a card whose title contains an apostrophe (if none exist, edit a card to add `Bob's test` as title first via the OLD modal — wait, you've already replaced it. Easier: create a fresh bank item via "Use in Class → publish to bank" or via Bulk Upload. Or just click Edit on any existing card.). Modal should open with all fields populated.
3. **Tags + Level visible only in bank modal:** confirm Level dropdown and Tags input appear in this modal.
4. **PDF preview links work:** click Preview ↗ next to Question Paper. PDF opens in new tab.
5. **Replace a PDF:** pick a different PDF in Question Paper input, click Save. Reload, click Preview again — it's the new PDF.
6. **Save without changing files:** open modal, change title, click Save. New title persists; PDFs unchanged.
7. **Locked fields:** Type and Scoring Mode show as read-only badges, no inputs.

Stop the server.

- [ ] **Step 8.8: Commit.**

```bash
git add templates/bank.html app.py
git commit -m "$(cat <<'EOF'
feat(bank): unify bank edit modal with shared partial; fix kebab reliability

- Drop role gate on Edit/Delete so all teachers see the kebab options.
- Replace inline 7-arg onclick with delegated handler reading per-card
  JSON, eliminating the apostrophe/quote-breaks-the-JS class of bugs.
- Replace bespoke modal markup with the shared _assignment_form_fields
  partial; now matches the field set of the create form.
- Bank edit modal now supports replacing all 4 PDFs via multipart
  upload, plus inline Preview ↗ links to the new file-inline route.
- Lock Type and Scoring Mode as read-only badges (server-side enforced).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Rewire `teacher_detail.html` edit modal with the shared partial

**Files:**
- Modify: `templates/teacher_detail.html:601-734` (replace the hand-rolled modal markup)
- Modify: `app.py` — pass `canonical_subjects` to the teacher_detail render if not already

**Why:** Both modals must look + behave identically. `teacher_detail.html` already has 95% of the right fields but with a free-text Subject input and an editable Scoring Mode select. Swapping in the partial fixes both.

- [ ] **Step 9.1: Confirm `canonical_subjects` is passed to the teacher_detail view.**

Run: `grep -n "teacher_detail.html" /Users/changshien/Documents/Github/ai-marking-prototype/app.py | head -5`

Open the route(s) that render `teacher_detail.html`. Confirm `canonical_subjects=` is in the kwargs. If not, add `from subjects import SUBJECT_DISPLAY_NAMES` at the top of the route and pass `canonical_subjects=SUBJECT_DISPLAY_NAMES`.

- [ ] **Step 9.2: Replace the hand-rolled modal contents with the shared partial.**

In `templates/teacher_detail.html`, lines 601–734 contain the existing edit modal. Replace lines 602–733 (the inner `<div class="edit-modal-box">`'s contents — the `<h3>` plus all field markup plus the existing actions) with:

```html
    <div class="edit-modal-box">
        <h3>Edit Assignment</h3>
        <div id="editFormHost">
            {% with mode='class',
                   entity=assignment,
                   entity_id=assignment.id,
                   entity_assign_type=assignment.assign_type,
                   inline_url_prefix='/teacher/assignment/' ~ assignment.id ~ '/file-inline',
                   canonical_subjects=canonical_subjects %}
                {% include '_assignment_form_fields.html' %}
            {% endwith %}
        </div>

        <div class="edit-error" id="editErrorMsg"></div>

        <div class="edit-modal-actions">
            <button type="button" class="edit-cancel" onclick="closeEditModal()">Cancel</button>
            <button type="button" class="edit-save" id="editSaveBtn" onclick="submitEdit()">Save Changes</button>
        </div>
    </div>
```

(I.e., the outer `<div class="edit-modal" id="editModal">` opening tag at line 601 stays; the closing `</div>` at line 734 stays; only the inner contents change.)

- [ ] **Step 9.3: Update `submitEdit()` to drop the no-longer-existent scoring_mode field.**

In `templates/teacher_detail.html:1483+`, the existing `submitEdit()` reads `editScoringMode` (line 1495). Since the partial doesn't render that input anymore (it's a read-only badge now), the `getElementById('editScoringMode')` call will return null and crash.

Locate line 1495:

```javascript
    fd.append('scoring_mode', document.getElementById('editScoringMode').value);
```

Delete that line entirely. The server route ignores `scoring_mode` anyway (Task 6).

- [ ] **Step 9.4: Manually verify the class-side edit modal.**

Run: `python app.py`. Open any class assignment page. Click Edit. Expected:

1. Modal opens with all fields populated.
2. Type and Scoring Mode show as read-only badges (no editable controls).
3. Subject is a free-text autocomplete input (existing behaviour preserved by `class="subject-autocomplete"`).
4. Level + Tags rows do NOT appear (mode='class').
5. Each PDF row shows current state (uploaded vs not) + Preview link if uploaded + Replace picker.
6. Click Preview on Question Paper — opens in new tab.
7. Replace the question paper, change the title, change provider/model, click Save. Reload — changes persist; new PDF previews correctly.
8. Past submissions on this assignment still render (their reports load) — confirms PDF replacement is non-destructive.

Stop the server.

- [ ] **Step 9.5: Commit.**

```bash
git add templates/teacher_detail.html app.py
git commit -m "$(cat <<'EOF'
feat(teacher): unify class edit modal with shared partial

Replaces the bespoke modal markup in teacher_detail.html with the
shared _assignment_form_fields partial (mode='class'). Locks Scoring
Mode visually (server-side already locked in prior commit). Adds
inline PDF preview links via the new file-inline route. Field set
now identical to the bank edit modal except for Level + Tags rows.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: End-to-end verification + cleanup

**Files:** none — verification only.

**Why:** Re-run the spec's full test plan to catch interaction bugs between the changes.

- [ ] **Step 10.1: Run the full spec test plan.**

Open `docs/superpowers/specs/2026-05-04-assignment-edit-unification-design.md` to the "Testing" section. Run all 9 scenarios:

1. **Visibility** — kebab Edit + Delete visible to all authenticated teachers.
2. **Click reliability** — title `Bob's "test" item, #algebra` opens the modal cleanly. (If you don't have such a row, edit any row and rename it to that, save, then re-open Edit.)
3. **Field parity** — open both modals side by side; same fields except bank has Level + Tags.
4. **PDF replace** — replace question paper in bank; preview confirms replacement.
5. **PDF preview without replace** — Preview link works without picking a file.
6. **Lock enforcement** — Type + Scoring Mode are non-editable in both modals.
7. **Backwards compat (bank-side)** — Use a bank item in a class. Then edit the bank item (change title, replace PDF). Confirm the class assignment still has the original title and original PDF.
8. **Backwards compat (class-side)** — Mark a submission. Then edit the assignment's PDF. Confirm the existing marked submission's report still renders.
9. **Migration** — drop the new columns from the dev DB, restart, confirm columns recreated and page renders.

For (9), if using SQLite: `sqlite3 marking.db "ALTER TABLE assignment_bank DROP COLUMN provider; ..."` then restart `python app.py`. (SQLite needs PRAGMA + recreation in older versions; alternative: delete `marking.db` and let `db.create_all()` recreate everything.)

Document any failures inline. If a step fails, debug, fix, recommit.

- [ ] **Step 10.2: Final commit (only if there were fix-ups in step 10.1).**

If any fixes were needed, commit them with a clear message describing what was wrong and why. If everything passed, skip this step.

- [ ] **Step 10.3: Push.**

```bash
git push origin sandbox_testing
```

Then announce completion to the user.

---

## Self-review

**1. Spec coverage:**
- ✅ Bug — role gate dropped: Task 4 + Task 8.1.
- ✅ Bug — kebab click reliability: Task 8.2 + 8.5 (data-attribute + JSON block).
- ✅ Universal edit + delete: Task 4.
- ✅ AssignmentBank schema additions: Task 1.
- ✅ `bank_use()` lazy-fill: Task 2.
- ✅ New file-inline route: Task 3.
- ✅ Shared partial: Task 7.
- ✅ Bank-side rebuild: Task 8.
- ✅ Class-side rebuild: Task 9.
- ✅ scoring_mode server-side lock: Task 6.
- ✅ Locked fields rendered as badges: Task 7's partial (Type + Scoring Mode badge blocks).
- ✅ Backwards compat for bank → class (copy-on-use): verified during exploration; no code change needed.
- ✅ Migration policy alignment: Task 1 + Task 2 satisfy lazy-fill-at-write + one-shot backfill.
- ✅ End-to-end verification: Task 10.

**2. Placeholder scan:** no TBD/TODO. All code blocks are complete.

**3. Type consistency:**
- `_assignment_form_fields.html` references `entity.title`, `entity.subject`, etc. Both `Assignment` and `AssignmentBank` models have these attribute names — verified in `db.py`.
- `entity_assign_type` passed in both Task 8.3 (bank) and Task 9.2 (class).
- `inline_url_prefix` formed consistently: `/bank/<id>/file-inline` and `/teacher/assignment/<id>/file-inline`.
- Form field names in `saveEdit()` (Task 8.5) and `submitEdit()` (existing in teacher_detail.html, modified in Task 9.3) match the names parsed in `bank_edit` (Task 5.1) and `teacher_edit` (existing).

**4. One known caveat:** Task 8.6 references `get_available_providers` and the `all_providers` template var. The exact integration depends on what helpers `bank.html` already uses. The step explicitly says "search for `get_available_providers(` in `app.py` and copy the surrounding 3-4 lines" — a fallback if the helper is named differently. Implementer should grep before writing the line.
