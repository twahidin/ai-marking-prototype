# Per-Student Re-Mark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-student "Re-mark" button on the teacher detail page that re-runs marking on a submission's stored script — no re-upload required. Auto-clear the assignment's `needs_remark` flag when all stale `done` submissions have been re-marked.

**Architecture:** New POST endpoint wraps the existing `_run_submission_marking` background thread. One line added to that function to auto-clear `needs_remark` when there are no remaining stale done submissions. Frontend adds a button per row plus a small polling routine to reflect status changes.

**Tech Stack:** Flask, SQLAlchemy, vanilla JS, Jinja2. No new deps.

**Spec:** `docs/superpowers/specs/2026-04-23-per-student-remark-design.md`

---

## Task 1: Backend endpoint + needs_remark auto-clear

**Files:**
- Modify: `app.py` (`_run_submission_marking` ~line 3258, add new route after `teacher_submission_result` ~line 3801)

- [ ] **Step 1: Add the auto-clear logic inside `_run_submission_marking`**

In `app.py`, locate `_run_submission_marking` (around line 3258). The function currently ends with `db.session.commit()` after setting `sub.status = 'done'` or `'error'`. Replace the final commit block with one that, after commit, checks whether any stale `done` submissions remain and clears the flag if none do.

Replace this block (the end of the try path and the final commit):

```python
        db.session.commit()
```

with:

```python
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
        except Exception as flag_err:
            db.session.rollback()
            logger.error(f"Failed to auto-clear needs_remark for assignment {assignment_id}: {flag_err}")
```

**Note:** this runs on EVERY completion of `_run_submission_marking`, not just re-marks. That is intentional — the invariant "no stale done submissions remain" holds regardless of whether this particular submission was a fresh upload or a re-mark. The block is a no-op when there are still stale submissions or when the flag is already clear.

- [ ] **Step 2: Add the new `/remark` route**

In `app.py`, find the `teacher_submission_result` route (around line 3786). Immediately after its closing block (just before `@app.route('/teacher/assignment/<assignment_id>/delete', methods=['POST'])`), add:

```python
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

    sub.status = 'pending'
    sub.result_json = None
    sub.marked_at = None
    db.session.commit()

    thread = threading.Thread(
        target=_run_submission_marking,
        args=(app, sub.id, assignment_id),
        daemon=True,
    )
    thread.start()

    return jsonify({'success': True})
```

- [ ] **Step 3: Quick sanity check**

Run a Python syntax check on app.py:

```bash
python -m py_compile app.py
```

Expected: no output (success).

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat(api): per-student re-mark endpoint; auto-clear needs_remark when no stale submissions"
```

Include the `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

---

## Task 2: Frontend Re-mark button + polling

**Files:**
- Modify: `templates/teacher_detail.html` (row Actions cell ~line 364, CSS in existing `<style>` block, JS in existing `<script>` block)

- [ ] **Step 1: Add CSS for the Re-mark button**

In `templates/teacher_detail.html`, inside the existing `<style>` block, immediately after the `.view-drafts-btn:hover` rule (around line 138), add:

```css
.remark-btn {
    padding: 4px 10px; border: 1px solid #b8860b; border-radius: 6px;
    background: white; color: #b8860b; font-size: 11px; font-weight: 600;
    cursor: pointer; transition: all 0.2s; margin-left: 6px;
}
.remark-btn:hover { background: #fff8e1; }
.remark-btn:disabled { opacity: 0.6; cursor: not-allowed; }
```

- [ ] **Step 2: Add the Re-mark button to the row Actions cell**

In `templates/teacher_detail.html`, find the Actions `<td>` inside the student rows loop. Locate the `{% if s.status == 'done' and s.submission_id %}` block that contains the existing "View Feedback" and "Download" buttons (after Task 3 of the LaTeX plan, this is around line 410-414). Change the enclosing condition and add the new button so that:

- "View Feedback" and "Download" still only appear for `status == 'done'`.
- A new **Re-mark** button appears for both `done` and `error` statuses when `submission_id` is present.

Replace the existing block:

```jinja
{% if s.status == 'done' and s.submission_id %}
<button class="upload-btn" type="button" onclick='openFeedbackModal({{ s.submission_id|tojson }}, {{ s.name|tojson }})'>View Feedback</button>
<a href="/submit/{{ assignment.id }}/download/{{ s.submission_id }}" class="upload-btn" style="text-decoration:none;display:inline-block;">Download</a>
{% endif %}
```

with:

```jinja
{% if s.status == 'done' and s.submission_id %}
<button class="upload-btn" type="button" onclick='openFeedbackModal({{ s.submission_id|tojson }}, {{ s.name|tojson }})'>View Feedback</button>
<a href="/submit/{{ assignment.id }}/download/{{ s.submission_id }}" class="upload-btn" style="text-decoration:none;display:inline-block;">Download</a>
{% endif %}
{% if s.status in ('done', 'error') and s.submission_id %}
<button class="remark-btn" type="button" data-submission-id="{{ s.submission_id }}" onclick='remarkStudent(this, {{ s.submission_id|tojson }})'>Re-mark</button>
{% endif %}
```

- [ ] **Step 3: Add the `remarkStudent` JS function**

In `templates/teacher_detail.html`, find the closing `}` of the `closeFeedbackModal` function (around the feedback viewer JS added in Task 3 of the LaTeX plan, ~line 1002 area). Immediately before the `// --- Feedback Viewer Modal ---` comment block OR immediately after the feedback-modal keydown listener (choose whichever keeps the code in a logical section; the keydown listener is the last line of that block), add a new section:

```js
// --- Per-student Re-mark ---
var REMARK_ASSIGNMENT_ID = '{{ assignment.id }}';
var remarkPollTimers = {};

async function remarkStudent(btn, submissionId) {
    if (!confirm('Re-mark this student using the stored script? The existing result will be replaced.')) return;
    btn.disabled = true;
    btn.textContent = 'Marking…';
    try {
        var res = await fetch('/teacher/assignment/' + REMARK_ASSIGNMENT_ID + '/submission/' + submissionId + '/remark', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        var data = await res.json();
        if (!data.success) {
            alert(data.error || 'Re-mark failed to start.');
            btn.disabled = false;
            btn.textContent = 'Re-mark';
            return;
        }
        pollRemarkStatus(submissionId);
    } catch (err) {
        alert('Network error. Please try again.');
        btn.disabled = false;
        btn.textContent = 'Re-mark';
    }
}

function pollRemarkStatus(submissionId) {
    if (remarkPollTimers[submissionId]) clearTimeout(remarkPollTimers[submissionId]);
    remarkPollTimers[submissionId] = setTimeout(async function() {
        try {
            var res = await fetch('/teacher/assignment/' + REMARK_ASSIGNMENT_ID + '/submission/' + submissionId + '/result');
            var data = await res.json();
            if (data.success && (data.status === 'done' || data.status === 'error')) {
                window.location.reload();
                return;
            }
        } catch (err) { /* ignore, retry */ }
        pollRemarkStatus(submissionId);
    }, 3000);
}
```

- [ ] **Step 4: Manual verification notes**

(No automated tests. Plan's Final Verification section covers manual checks.)

- [ ] **Step 5: Commit**

```bash
git add templates/teacher_detail.html
git commit -m "feat(teacher): per-student Re-mark button with status polling"
```

Include the `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

---

## Final Verification

- [ ] **Step 1: Start the app**

```bash
python app.py
```

- [ ] **Step 2: End-to-end walkthrough**

1. As teacher: create/open an assignment with at least two `done` student submissions.
2. Edit the assignment with a major change (e.g. update marking instructions) — confirm the `needs_remark` banner appears on the assignment detail page.
3. On one row, click **Re-mark**. Confirm the button shows "Marking…" and the row eventually refreshes (page reload) with a new status badge and possibly new score.
4. Do the same for all remaining stale `done` rows. After the last one completes, refresh the page and confirm the `needs_remark` banner is gone.
5. Try Re-mark on an `error`-status row — confirm it runs and either succeeds or ends in error again.
6. Confirm Re-mark button does NOT appear on `not_submitted`, `pending`, `processing`, `extracting`, or `preview` rows.
7. Regression: upload a fresh student script and confirm the existing single/bulk upload flows still work.

---

## Self-Review (author)

**Spec coverage:** ✓
- Spec "UI" → Task 2 (button, styling, interaction, polling).
- Spec "Endpoint" → Task 1 Step 2.
- Spec "needs_remark auto-clear" → Task 1 Step 1.
- Spec "Edge cases" (409 for already-marking, 400 for no script, error-status re-mark) → Task 1 Step 2.

**Placeholder scan:** no TBDs.

**Type consistency:** `REMARK_ASSIGNMENT_ID` / `remarkPollTimers` / `remarkStudent` / `pollRemarkStatus` names all match across tasks. URL path `/teacher/assignment/<aid>/submission/<sid>/remark` matches between backend and frontend. Status check uses `status in ('done', 'error')` consistently.
