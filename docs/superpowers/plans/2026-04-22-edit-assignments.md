# Edit Assignments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let teachers edit an assignment after creation without changing the student access link or auto-remarking. Surface a "last edited" timestamp on the teacher landing page, and a persistent banner prompting bulk re-mark when major fields change.

**Architecture:** Two new columns on `Assignment` (`last_edited_at`, `needs_remark`). One new endpoint (`POST /teacher/assignment/<id>/edit`) that mirrors the create form, server-side detects "major" field changes, sets the flag. The bulk-mark background job clears the flag on successful completion. Edit UI is a modal on `/teacher/assignment/<id>` reachable from there or from `/class` (the class page button just navigates to the landing page with `?edit=1`).

**Tech Stack:** Flask, SQLAlchemy, vanilla JS, Jinja2 templates, SQLite/PostgreSQL.

**Spec:** `docs/superpowers/specs/2026-04-22-edit-assignments-design.md`

**Testing:** This project has no automated test suite (per CLAUDE.md). Verification is manual: start the dev server, exercise the UI, check the database state with sqlite3 / psql when needed.

---

## Task 1: Add `last_edited_at` and `needs_remark` columns to the Assignment model

**Files:**
- Modify: `db.py:181-237` (Assignment model)
- Modify: `db.py:107-128` (auto-migration block for assignments table)

- [ ] **Step 1: Add columns to the Assignment SQLAlchemy model**

Open `db.py`. Find the `Assignment` class (starts at line 181). Locate the `created_at` line (around line 212). Insert two new columns immediately after `created_at`:

```python
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_edited_at = db.Column(db.DateTime, nullable=True)
    needs_remark = db.Column(db.Boolean, default=False, nullable=False)
```

- [ ] **Step 2: Add ALTER TABLE migration block for existing databases**

In `db.py`, find the `_migrate_add_columns` function (line 39). Inside the `if 'assignments' in inspector.get_table_names():` block (line 107), after the `max_drafts` migration (line 128), add two new migration checks:

```python
            if 'last_edited_at' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN last_edited_at TIMESTAMP'))
                db.session.commit()
                logger.info('Added last_edited_at column to assignments table')
            if 'needs_remark' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN needs_remark BOOLEAN DEFAULT FALSE NOT NULL'))
                db.session.commit()
                logger.info('Added needs_remark column to assignments table')
```

Note: SQLite tolerates `TIMESTAMP` and `BOOLEAN` as type aliases. PostgreSQL handles them natively.

- [ ] **Step 3: Verify the app starts and migration runs**

Run: `python app.py`

Expected: Server starts on port 5000 without errors. The first startup logs both `Added last_edited_at column to assignments table` and `Added needs_remark column to assignments table` (if running against an existing database with assignments).

- [ ] **Step 4: Verify columns exist in the DB**

For SQLite (default):
```bash
sqlite3 marking.db ".schema assignments" | grep -E "last_edited_at|needs_remark"
```

Expected output contains both `last_edited_at` and `needs_remark`.

Stop the dev server (Ctrl+C).

- [ ] **Step 5: Commit**

```bash
git add db.py
git commit -m "feat(db): add last_edited_at and needs_remark columns to Assignment"
```

---

## Task 2: Add `POST /teacher/assignment/<assignment_id>/edit` endpoint

**Files:**
- Modify: `app.py` (insert new route after `teacher_create` at line 3438, before `teacher_assignment_detail`)

- [ ] **Step 1: Add helper to detect major changes**

In `app.py`, locate the section just above `teacher_create` (line 3299). Add a module-level constant near the top of the file with the other helpers (e.g. just above `_check_assignment_ownership` at line 324). Add this constant:

```python
# Fields whose changes require re-running bulk mark to update existing submissions.
# File fields are detected separately (any new upload counts as a change).
ASSIGNMENT_MAJOR_TEXT_FIELDS = (
    'marking_instructions',
    'review_instructions',
    'provider',
    'model',
    'total_marks',
)
```

- [ ] **Step 2: Add the edit route**

In `app.py`, find the end of `teacher_create` (around line 3436, just before the `@app.route('/teacher/assignment/<assignment_id>')` at line 3438). Insert the following new route between them:

```python
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

    new_provider = request.form.get('provider', asn.provider)
    new_model = request.form.get('model', asn.model)

    if new_provider not in api_keys:
        return jsonify({'success': False, 'error': 'Selected provider has no API key configured'}), 400

    # Parse incoming text/scalar fields (default to current value if missing)
    new_title = request.form.get('title', asn.title or '')
    new_subject = request.form.get('subject', asn.subject or '')
    new_scoring_mode = request.form.get('scoring_mode', asn.scoring_mode or 'status')
    new_total_marks = request.form.get('total_marks', asn.total_marks or '')
    new_show_results = request.form.get('show_results') == 'on'
    new_allow_drafts = request.form.get('allow_drafts') == 'on'
    new_max_drafts = _parse_max_drafts(request.form.get('max_drafts')) if request.form.get('max_drafts') is not None else asn.max_drafts
    new_review = request.form.get('review_instructions', asn.review_instructions or '')
    new_marking = request.form.get('marking_instructions', asn.marking_instructions or '')

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

    # Detect major change BEFORE applying writes
    major_change = (
        qp_changed or ak_changed or rub_changed or ref_changed
        or (new_marking != (asn.marking_instructions or ''))
        or (new_review != (asn.review_instructions or ''))
        or (new_provider != asn.provider)
        or (new_model != asn.model)
        or (new_total_marks != (asn.total_marks or ''))
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
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to save edits for assignment {assignment_id}: {e}")
        return jsonify({'success': False, 'error': f'Failed to save: {e}'}), 500

    return jsonify({
        'success': True,
        'major_change': major_change,
        'needs_remark': asn.needs_remark,
        'last_edited_at': asn.last_edited_at.isoformat(),
    })
```

- [ ] **Step 3: Sanity-check the route registers**

Run: `python app.py`

Expected: Server starts. From a separate terminal:
```bash
curl -i http://localhost:5000/teacher/assignment/nonexistent/edit -X POST
```

Expected: HTTP 401 with `{"success": false, "error": "Not authenticated"}` JSON. (401 because not logged in, which proves the route is wired.)

Stop the dev server.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat(api): add POST /teacher/assignment/<id>/edit endpoint"
```

---

## Task 3: Clear `needs_remark` when bulk-mark job completes successfully

**Files:**
- Modify: `app.py:2932-2934` (success branch of `run_bulk_marking_job`)

- [ ] **Step 1: Update bulk job completion to clear the flag**

In `app.py`, find `run_bulk_marking_job` (line 2835). Locate the block where the job is marked as done (around line 2932-2934):

```python
        jobs[job_id]['results'] = results
        jobs[job_id]['status'] = 'done'
        jobs[job_id]['progress'] = {'current': total, 'total': total, 'current_name': 'Complete'}
```

Replace it with:

```python
        jobs[job_id]['results'] = results
        jobs[job_id]['status'] = 'done'
        jobs[job_id]['progress'] = {'current': total, 'total': total, 'current_name': 'Complete'}

        # Clear the "needs re-mark" flag on the assignment now that bulk-mark finished.
        if assignment_id:
            try:
                with app.app_context():
                    asn = Assignment.query.get(assignment_id)
                    if asn and asn.needs_remark:
                        asn.needs_remark = False
                        db.session.commit()
            except Exception as flag_err:
                db.session.rollback()
                logger.error(f"Failed to clear needs_remark for assignment {assignment_id}: {flag_err}")
```

Note: only success-path clears the flag. The `except Exception as job_err:` branch below (line 2935) is left unchanged so partial failures keep the flag set.

- [ ] **Step 2: Verify the app still starts cleanly**

Run: `python app.py`

Expected: server boots without import or syntax errors.

Stop the dev server.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: clear needs_remark on successful bulk-mark completion"
```

---

## Task 4: Add "Edit Assignment" UI on `/teacher/assignment/<id>`

**Files:**
- Modify: `templates/teacher_detail.html` (add CSS, banner markup, edit button, edit modal, JS)

- [ ] **Step 1: Add CSS for the new UI elements**

In `templates/teacher_detail.html`, locate the closing `</style>` tag (around line 138-140 — the end of the `{% block head %}` style block). Just before `</style>`, add:

```css
    .last-edited-line {
        font-size: 12px; color: #888; margin-bottom: 16px;
        display: flex; align-items: center; gap: 6px;
    }
    .needs-remark-banner {
        background: #fff8e1; border: 1px solid #f0ad4e; color: #8a6d3b;
        border-radius: 10px; padding: 14px 18px; margin-bottom: 20px;
        display: flex; align-items: center; justify-content: space-between;
        gap: 16px; flex-wrap: wrap;
    }
    .needs-remark-banner .nrb-text { font-size: 13px; font-weight: 600; flex: 1 1 auto; min-width: 220px; }
    .needs-remark-banner .nrb-btn {
        padding: 8px 16px; border-radius: 8px; border: 1px solid #f0ad4e;
        background: white; color: #b8860b; font-size: 12px; font-weight: 700;
        cursor: pointer; white-space: nowrap;
    }
    .needs-remark-banner .nrb-btn:hover { background: #f0ad4e; color: white; }

    .edit-modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 1000; align-items: center; justify-content: center; backdrop-filter: blur(4px); }
    .edit-modal.active { display: flex; }
    .edit-modal-box { background: white; border-radius: 16px; padding: 28px; width: 92%; max-width: 640px; max-height: 88vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }
    .edit-modal-box h3 { font-size: 18px; font-weight: 700; color: #333; margin-bottom: 14px; }
    .edit-form-group { margin-bottom: 14px; }
    .edit-form-group label { display: block; font-size: 12px; font-weight: 700; color: #555; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.4px; }
    .edit-form-group input[type="text"], .edit-form-group input[type="number"], .edit-form-group select, .edit-form-group textarea {
        width: 100%; padding: 9px 12px; border: 1px solid #ddd; border-radius: 8px;
        font-size: 13px; box-sizing: border-box; font-family: inherit;
    }
    .edit-form-group textarea { min-height: 80px; resize: vertical; }
    .edit-form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    @media (max-width: 600px) { .edit-form-row { grid-template-columns: 1fr; } }
    .edit-disabled-field {
        padding: 9px 12px; background: #f5f5f5; border: 1px solid #e0e0e0;
        border-radius: 8px; font-size: 13px; color: #666;
    }
    .edit-disabled-hint { font-size: 11px; color: #999; margin-top: 4px; }
    .edit-current-file { font-size: 11px; color: #667eea; margin-top: 4px; font-weight: 600; }
    .edit-modal-actions { display: flex; gap: 10px; margin-top: 18px; }
    .edit-modal-actions button { flex: 1; padding: 11px; border-radius: 10px; font-size: 14px; font-weight: 700; cursor: pointer; border: none; }
    .edit-cancel { background: #f0f0f0; color: #666; }
    .edit-cancel:hover { background: #e0e0e0; }
    .edit-save { background: #667eea; color: white; }
    .edit-save:hover { background: #5a6fd6; }
    .edit-save:disabled { background: #ccc; cursor: not-allowed; }
    .edit-error { color: #c0392b; font-size: 12px; margin-top: 8px; display: none; }
```

- [ ] **Step 2: Add the "Last edited" line and "Needs re-mark" banner above Assignment Details card**

In `templates/teacher_detail.html`, find the header/title area (around line 146-152, right after the `<h1>` block ends with `</div>` on line 152). After that closing `</div>` and before the `<!-- Info Card -->` comment (line 154), insert:

```html
    {% if assignment.last_edited_at %}
    <div class="last-edited-line">
        <span>&#9999;&#65039;</span>
        <span>Last edited: {{ assignment.last_edited_at.strftime('%d %b %Y, %H:%M') }}</span>
    </div>
    {% endif %}

    {% if assignment.needs_remark %}
    <div class="needs-remark-banner" id="needsRemarkBanner">
        <div class="nrb-text">
            &#9888;&#65039; This assignment was edited with changes that may affect grading. Re-run <strong>Bulk Mark</strong> to update existing submissions.
        </div>
        <button class="nrb-btn" type="button" onclick="document.querySelector('.bulk-section').scrollIntoView({behavior:'smooth', block:'start'});">Jump to Bulk Mark</button>
    </div>
    {% endif %}
```

- [ ] **Step 3: Add the "Edit Assignment" button in the Assignment Details card**

In `templates/teacher_detail.html`, find the "Share to Assignment Bank" button block (around line 193-195):

```html
        <div style="margin-top:16px;padding-top:16px;border-top:1px solid #eee;">
            <button class="upload-btn" onclick="openShareModal()" style="padding:8px 20px;font-size:13px;">Share to Assignment Bank</button>
        </div>
```

Replace it with:

```html
        <div style="margin-top:16px;padding-top:16px;border-top:1px solid #eee;display:flex;gap:10px;flex-wrap:wrap;">
            <button class="upload-btn" onclick="openEditModal()" style="padding:8px 20px;font-size:13px;">Edit Assignment</button>
            <button class="upload-btn" onclick="openShareModal()" style="padding:8px 20px;font-size:13px;">Share to Assignment Bank</button>
        </div>
```

- [ ] **Step 4: Add the edit modal markup**

In `templates/teacher_detail.html`, find the `{% endblock %}` for the body (search for `{% endblock %}` near the bottom of the body block — look around line 600-700 for the end of `{% block body %}`). Just before that body `{% endblock %}`, add the modal:

```html
<!-- Edit Assignment Modal -->
<div class="edit-modal" id="editModal">
    <div class="edit-modal-box">
        <h3>Edit Assignment</h3>

        <div class="edit-form-group">
            <label>Type (locked after creation)</label>
            <div class="edit-disabled-field">
                {% if assignment.assign_type == 'rubrics' %}Rubrics / Essay{% else %}Short Answer{% endif %}
            </div>
            <div class="edit-disabled-hint">Type cannot be changed after creation.</div>
        </div>

        <div class="edit-form-row">
            <div class="edit-form-group">
                <label for="editTitle">Title</label>
                <input type="text" id="editTitle" value="{{ assignment.title or '' }}">
            </div>
            <div class="edit-form-group">
                <label for="editSubject">Subject</label>
                <input type="text" id="editSubject" value="{{ assignment.subject or '' }}">
            </div>
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
                <label for="editScoringMode">Scoring Mode</label>
                <select id="editScoringMode">
                    <option value="status" {% if assignment.scoring_mode == 'status' %}selected{% endif %}>Status (correct / partial / incorrect)</option>
                    <option value="marks" {% if assignment.scoring_mode == 'marks' %}selected{% endif %}>Marks (numerical)</option>
                </select>
            </div>
            <div class="edit-form-group">
                <label for="editTotalMarks">Total Marks</label>
                <input type="text" id="editTotalMarks" value="{{ assignment.total_marks or '' }}" placeholder="e.g. 100">
            </div>
        </div>

        <div class="edit-form-row">
            <div class="edit-form-group">
                <label for="editShowResults">Show Results to Students</label>
                <select id="editShowResults">
                    <option value="on" {% if assignment.show_results %}selected{% endif %}>Yes</option>
                    <option value="" {% if not assignment.show_results %}selected{% endif %}>No</option>
                </select>
            </div>
            <div class="edit-form-group">
                <label for="editAllowDrafts">Allow Drafts</label>
                <select id="editAllowDrafts" onchange="document.getElementById('editMaxDraftsRow').style.display = this.value === 'on' ? '' : 'none';">
                    <option value="on" {% if assignment.allow_drafts %}selected{% endif %}>Yes</option>
                    <option value="" {% if not assignment.allow_drafts %}selected{% endif %}>No</option>
                </select>
            </div>
        </div>

        <div class="edit-form-group" id="editMaxDraftsRow" style="{% if not assignment.allow_drafts %}display:none;{% endif %}">
            <label for="editMaxDrafts">Max Drafts</label>
            <input type="number" id="editMaxDrafts" min="1" max="20" value="{{ assignment.max_drafts or 3 }}">
        </div>

        <div class="edit-form-group">
            <label for="editReviewInstructions">Feedback / Review Instructions</label>
            <textarea id="editReviewInstructions">{{ assignment.review_instructions or '' }}</textarea>
        </div>

        <div class="edit-form-group">
            <label for="editMarkingInstructions">Marking Instructions</label>
            <textarea id="editMarkingInstructions">{{ assignment.marking_instructions or '' }}</textarea>
        </div>

        <div class="edit-form-group">
            <label>Question Paper</label>
            <input type="file" id="editQuestionPaper" accept=".pdf,.jpg,.jpeg,.png,.heic">
            {% if assignment.question_paper %}<div class="edit-current-file">Current: file already uploaded. Leave empty to keep, or upload a new file to replace.</div>{% endif %}
        </div>

        {% if assignment.assign_type != 'rubrics' %}
        <div class="edit-form-group">
            <label>Answer Key</label>
            <input type="file" id="editAnswerKey" accept=".pdf,.jpg,.jpeg,.png,.heic">
            {% if assignment.answer_key %}<div class="edit-current-file">Current: file already uploaded. Leave empty to keep.</div>{% endif %}
        </div>
        {% else %}
        <div class="edit-form-group">
            <label>Rubrics</label>
            <input type="file" id="editRubrics" accept=".pdf,.jpg,.jpeg,.png,.heic">
            {% if assignment.rubrics %}<div class="edit-current-file">Current: file already uploaded. Leave empty to keep.</div>{% endif %}
        </div>
        {% endif %}

        <div class="edit-form-group">
            <label>Reference Materials (optional)</label>
            <input type="file" id="editReference" accept=".pdf,.jpg,.jpeg,.png,.heic">
            {% if assignment.reference %}<div class="edit-current-file">Current: file already uploaded. Leave empty to keep.</div>{% endif %}
        </div>

        <div class="edit-error" id="editErrorMsg"></div>

        <div class="edit-modal-actions">
            <button type="button" class="edit-cancel" onclick="closeEditModal()">Cancel</button>
            <button type="button" class="edit-save" id="editSaveBtn" onclick="submitEdit()">Save Changes</button>
        </div>
    </div>
</div>
```

- [ ] **Step 5: Add JS to drive the edit modal**

In `templates/teacher_detail.html`, find the `{% block scripts %}` block (search for `{% block scripts %}` near the bottom). Inside that block, append the following script section (before the closing `</script>` if there is a single existing script block, otherwise inside its own `<script>` tag):

```html
<script>
const ALL_PROVIDERS = {{ all_providers | tojson }};
const CURRENT_PROVIDER = {{ assignment.provider | tojson }};
const CURRENT_MODEL = {{ assignment.model | tojson }};

function populateEditProviderModel() {
    const provSel = document.getElementById('editProvider');
    const modelSel = document.getElementById('editModel');
    provSel.innerHTML = '';
    for (const [key, config] of Object.entries(ALL_PROVIDERS)) {
        const opt = document.createElement('option');
        opt.value = key;
        opt.textContent = config.label;
        if (key === CURRENT_PROVIDER) opt.selected = true;
        provSel.appendChild(opt);
    }
    updateEditModelOptions(true);
}

function updateEditModelOptions(initial) {
    const provSel = document.getElementById('editProvider');
    const modelSel = document.getElementById('editModel');
    const prov = provSel.value;
    modelSel.innerHTML = '';
    if (!ALL_PROVIDERS[prov]) return;
    const models = ALL_PROVIDERS[prov].models;
    const def = ALL_PROVIDERS[prov].default;
    for (const [id, label] of Object.entries(models)) {
        const opt = document.createElement('option');
        opt.value = id;
        opt.textContent = label;
        if (initial && id === CURRENT_MODEL) opt.selected = true;
        else if (!initial && id === def) opt.selected = true;
        modelSel.appendChild(opt);
    }
}

function openEditModal() {
    populateEditProviderModel();
    document.getElementById('editErrorMsg').style.display = 'none';
    document.getElementById('editModal').classList.add('active');
}

function closeEditModal() {
    document.getElementById('editModal').classList.remove('active');
}

async function submitEdit() {
    const btn = document.getElementById('editSaveBtn');
    const errEl = document.getElementById('editErrorMsg');
    errEl.style.display = 'none';
    btn.disabled = true;
    btn.textContent = 'Saving...';

    const fd = new FormData();
    fd.append('title', document.getElementById('editTitle').value);
    fd.append('subject', document.getElementById('editSubject').value);
    fd.append('provider', document.getElementById('editProvider').value);
    fd.append('model', document.getElementById('editModel').value);
    fd.append('scoring_mode', document.getElementById('editScoringMode').value);
    fd.append('total_marks', document.getElementById('editTotalMarks').value);
    if (document.getElementById('editShowResults').value === 'on') fd.append('show_results', 'on');
    if (document.getElementById('editAllowDrafts').value === 'on') fd.append('allow_drafts', 'on');
    fd.append('max_drafts', document.getElementById('editMaxDrafts').value);
    fd.append('review_instructions', document.getElementById('editReviewInstructions').value);
    fd.append('marking_instructions', document.getElementById('editMarkingInstructions').value);

    const qp = document.getElementById('editQuestionPaper');
    if (qp && qp.files.length) fd.append('question_paper', qp.files[0]);
    const ak = document.getElementById('editAnswerKey');
    if (ak && ak.files.length) fd.append('answer_key', ak.files[0]);
    const rub = document.getElementById('editRubrics');
    if (rub && rub.files.length) fd.append('rubrics', rub.files[0]);
    const ref = document.getElementById('editReference');
    if (ref && ref.files.length) fd.append('reference', ref.files[0]);

    try {
        const res = await fetch('/teacher/assignment/{{ assignment.id }}/edit', { method: 'POST', body: fd });
        const data = await res.json();
        if (!data.success) {
            errEl.textContent = data.error || 'Failed to save changes.';
            errEl.style.display = 'block';
            btn.disabled = false;
            btn.textContent = 'Save Changes';
            return;
        }
        // Reload to reflect new state (banner, last-edited line, populated fields)
        window.location.reload();
    } catch (e) {
        errEl.textContent = 'Network error: ' + e.message;
        errEl.style.display = 'block';
        btn.disabled = false;
        btn.textContent = 'Save Changes';
    }
}

// Auto-open edit modal if ?edit=1 in URL (used by the Class page Edit button)
window.addEventListener('DOMContentLoaded', () => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('edit') === '1') {
        openEditModal();
    }
});
</script>
```

- [ ] **Step 6: Pass `all_providers` to the template from the route**

In `app.py`, find `teacher_assignment_detail` (line 3438). The `render_template('teacher_detail.html', ...)` call at line 3474 currently passes only `assignment` and `students`. We need to also pass the providers metadata so the edit modal's provider/model dropdowns can be built.

`PROVIDERS` is already imported at the top of `app.py` (line 16: `from ai_marking import mark_script, get_available_providers, PROVIDERS`) and is the same dict passed to `class.html` (see app.py:689 — `all_providers=PROVIDERS`).

Update the `render_template` call in `teacher_assignment_detail` (lines 3474-3476) from:

```python
    return render_template('teacher_detail.html',
                           assignment=asn,
                           students=student_data)
```

to:

```python
    return render_template('teacher_detail.html',
                           assignment=asn,
                           students=student_data,
                           all_providers=PROVIDERS)
```

This matches exactly what `class.html` receives, so the JS shape (`{provider_key: {label, models, default}}`) is identical and the modal's `populateEditProviderModel()` JS works without changes.

- [ ] **Step 7: Manual UI verification — open modal and edit a non-major field**

Run: `python app.py`

1. Log in as a teacher (use `TEACHER_CODE`).
2. Open an existing assignment landing page: `/teacher/assignment/<some-id>`.
3. Click "Edit Assignment".
4. Modal opens with all fields pre-filled. Type field shows as disabled with "Short Answer" or "Rubrics / Essay" + lock hint.
5. Change only the title (e.g. append " edited"). Click Save Changes.
6. Page reloads. The H1 reflects new title. A small "Last edited: <date>" line appears below the H1.
7. The yellow `needs-remark-banner` should NOT appear (title is a minor field).

Stop the dev server.

- [ ] **Step 8: Manual UI verification — major field triggers banner**

Run: `python app.py`

1. Log in. Open the same assignment.
2. Click Edit, change "Marking Instructions" text. Save.
3. Page reloads. "Last edited" line shows new timestamp. The yellow banner appears: "This assignment was edited with changes that may affect grading...".
4. Click "Jump to Bulk Mark" — page scrolls smoothly to the Bulk Mark card.
5. Reload the page (Cmd-R / F5). Banner persists (because `needs_remark` is stored).

Stop the dev server.

- [ ] **Step 9: Commit**

```bash
git add templates/teacher_detail.html app.py
git commit -m "feat(ui): add Edit Assignment modal and edit-state notices on teacher landing page"
```

---

## Task 5: Add "Edit" button to assignment cards on `/class`

**Files:**
- Modify: `templates/class.html` (find `.assignment-actions` block and add Edit button)

- [ ] **Step 1: Add Edit button to each assignment card**

In `templates/class.html`, find the `.assignment-actions` block (around line 281-284):

```html
                    <div class="assignment-actions">
                        <a href="/teacher/assignment/{{ asn.id }}" class="btn-sm view">View</a>
                        <button class="btn-sm delete" onclick="deleteAssignment('{{ asn.id }}', '{{ asn.subject | e }}')">Delete</button>
                    </div>
```

Replace with:

```html
                    <div class="assignment-actions">
                        <a href="/teacher/assignment/{{ asn.id }}" class="btn-sm view">View</a>
                        <a href="/teacher/assignment/{{ asn.id }}?edit=1" class="btn-sm view" style="background:#fff8e1;color:#b8860b;border-color:#f0ad4e;">Edit</a>
                        <button class="btn-sm delete" onclick="deleteAssignment('{{ asn.id }}', '{{ asn.subject | e }}')">Delete</button>
                    </div>
```

(Note: the Edit button is just a link with `?edit=1` — the landing page's auto-open JS from Task 4 Step 5 handles opening the modal.)

- [ ] **Step 2: Manual UI verification**

Run: `python app.py`

1. Log in. Go to `/class`.
2. In any class with assignments, locate an assignment card. Verify three buttons appear: View / Edit / Delete.
3. Click Edit. Browser navigates to `/teacher/assignment/<id>?edit=1` and the edit modal auto-opens.
4. Cancel the modal. URL still has `?edit=1`. Reloading auto-reopens the modal — acceptable.

Stop the dev server.

- [ ] **Step 3: Commit**

```bash
git add templates/class.html
git commit -m "feat(ui): add Edit button to assignment cards on class page"
```

---

## Task 6: End-to-end verification (no code changes)

- [ ] **Step 1: Verify student access link is unaffected**

Run: `python app.py`

1. Log in. Note the student submission link on an assignment landing page (`/submit/<assignment_id>`).
2. Edit the assignment (e.g. change marking instructions). Save.
3. Verify the URL on the landing page is still the same `/submit/<assignment_id>` and the `classroom_code` shown in the header is unchanged.
4. Open the submission link in an incognito window — it loads correctly.

- [ ] **Step 2: Verify existing submissions and results untouched after edit**

1. Pick an assignment that has at least one student with `status = 'done'`.
2. Note the current score in the Submissions Table.
3. Edit the assignment (change marking instructions — major change).
4. Reload the landing page. The student's prior result and score are unchanged. Banner appears prompting bulk mark.

- [ ] **Step 3: Verify bulk-mark clears the banner**

1. With `needs_remark = True` set from the previous step, run a bulk-mark of the assignment.
2. Wait for completion.
3. Reload the landing page. Banner is gone. (`needs_remark = False` in the DB.)

Optionally verify in DB:
```bash
sqlite3 marking.db "SELECT id, needs_remark, last_edited_at FROM assignments WHERE id = '<id>';"
```

Expected: `needs_remark` is `0` (false), `last_edited_at` is set.

- [ ] **Step 4: Verify type cannot be changed**

1. Open the edit modal. Type field is rendered as a disabled grey box, not a select. There's no input named `assign_type` posted in the form (confirm by inspecting the FormData built in `submitEdit()` — only `title, subject, provider, model, scoring_mode, total_marks, show_results, allow_drafts, max_drafts, review_instructions, marking_instructions, question_paper, answer_key/rubrics, reference` are sent).
2. Even if a malicious user POSTed `assign_type=rubrics` to the edit endpoint directly, the server-side handler in Task 2 never reads or writes `asn.assign_type`, so it cannot change. ✓

- [ ] **Step 5: Verify rubrics-type assignment shows rubrics field, not answer_key**

1. Create or open a `rubrics` (essay) type assignment.
2. Open the edit modal. The "Answer Key" file row should NOT appear; instead a "Rubrics" file row should appear.
3. Replace the rubrics file. Save. Verify the major-change banner appears.

- [ ] **Step 6: Final commit (if any tweaks made during verification)**

If the verification surfaces any small fixes, commit them:

```bash
git status
git add -A
git commit -m "fix: small adjustments after end-to-end verification"
```

If nothing to commit, skip this step.

---

## Self-Review Checklist (already applied — for reference)

- ✓ Spec coverage: every requirement in the design doc maps to a task above (data model → T1, edit endpoint → T2, bulk-mark flag clear → T3, landing page UI → T4, class page button → T5, manual tests → T6).
- ✓ No placeholders: every step has concrete code, file paths, and verification commands.
- ✓ Type consistency: column names (`last_edited_at`, `needs_remark`) match across db.py, app.py, and templates. JS function names (`openEditModal`, `closeEditModal`, `submitEdit`, `populateEditProviderModel`, `updateEditModelOptions`) are consistent. The route path `/teacher/assignment/<id>/edit` matches between the route definition (Task 2 Step 2) and the JS fetch call (Task 4 Step 5) and the link href (Task 5 Step 1).
- ✓ Atomic edit: server-side validation rejects bad input before any DB writes, then commits all changes in one `db.session.commit()` (Task 2 Step 2).
- ✓ Frequent commits: each task ends with a commit; long tasks (T4) commit once at the end since the file edits are interdependent.
