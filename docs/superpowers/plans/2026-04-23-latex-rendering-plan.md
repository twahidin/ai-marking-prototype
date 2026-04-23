# LaTeX Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render LaTeX math (via MathJax) on every user-facing surface where equations appear — student edit step (with live preview), teacher "View Edits" modal, a new teacher in-app feedback viewer, and any other dynamic snippet surfaces.

**Architecture:** Frontend-only changes. MathJax 3 is already loaded globally with `startup.typeset: false`; we target specific elements via `MathJax.typesetPromise([el])`. For the student edit textarea (which can't render inline), we add a live preview block below. A new teacher "View Feedback" modal reuses an existing JSON endpoint (`app.py:3786`) and mirrors the results layout from `submit.html`. No backend changes, no new dependencies, no DB migrations.

**Tech Stack:** Jinja2 templates, vanilla JS, MathJax 3 (already in `base.html`). No test runner exists for the frontend; verification is manual via the Flask dev server (`python app.py` → `http://localhost:5000`).

**Spec:** `docs/superpowers/specs/2026-04-23-latex-rendering-design.md`

---

## Task 1: Student edit step — live preview under textareas

**Files:**
- Modify: `templates/submit.html` (styles ~line 66, `renderPreview` ~line 579, `markEdited` ~line 591)

- [ ] **Step 1: Add preview CSS**

In `templates/submit.html`, inside the existing `<style>` block, find the rule `.answer-textarea.edited` (around line 66). Immediately after it, add these rules:

```css
.answer-preview {
    margin-top: 8px; padding-top: 8px;
    border-top: 1px dashed #e0e0e0;
}
.answer-preview-label {
    font-size: 11px; color: #888; text-transform: uppercase;
    letter-spacing: 0.05em; margin-bottom: 4px; font-weight: 600;
}
.answer-preview-body {
    font-size: 14px; color: #333; line-height: 1.6;
    min-height: 1.5em; white-space: pre-wrap; word-break: break-word;
}
.answer-preview-body:empty::before {
    content: "(empty)"; color: #bbb; font-style: italic;
}
```

- [ ] **Step 2: Modify `renderPreview` to emit preview blocks**

In `templates/submit.html`, replace the `renderPreview` function (around line 579) with:

```js
function renderPreview(answers) {
    var html = '';
    answers.forEach(function(a, i) {
        var label = a.label || ('Question ' + a.question_num);
        html += '<div class="answer-block">' +
            '<div class="answer-label">' + esc(label) + '<span class="edited-badge" id="edited-' + i + '" style="display:none;">Edited</span></div>' +
            '<textarea class="answer-textarea" id="answer-' + i + '" data-index="' + i + '" oninput="onPreviewInput(' + i + ')">' + esc(a.extracted_text || '') + '</textarea>' +
            '<div class="answer-preview">' +
                '<div class="answer-preview-label">Preview</div>' +
                '<div class="answer-preview-body" id="preview-body-' + i + '"></div>' +
            '</div>' +
            '</div>';
    });
    document.getElementById('previewAnswers').innerHTML = html;
    // Initial render of each preview with the AI-extracted text
    answers.forEach(function(a, i) {
        updatePreview(i, a.extracted_text || '');
    });
}
```

Note the `oninput` handler is renamed from `markEdited` to `onPreviewInput` — we'll define it next to combine both concerns.

- [ ] **Step 3: Add `onPreviewInput` and `updatePreview` helpers; keep `markEdited` unchanged**

In `templates/submit.html`, immediately after the existing `markEdited` function (around line 602), add:

```js
var previewDebounceTimers = {};

function onPreviewInput(idx) {
    markEdited(idx);
    if (previewDebounceTimers[idx]) clearTimeout(previewDebounceTimers[idx]);
    previewDebounceTimers[idx] = setTimeout(function() {
        var textarea = document.getElementById('answer-' + idx);
        updatePreview(idx, textarea ? textarea.value : '');
    }, 200);
}

function updatePreview(idx, text) {
    var body = document.getElementById('preview-body-' + idx);
    if (!body) return;
    body.textContent = text;  // textContent preserves $ delimiters for MathJax
    if (window.MathJax && MathJax.typesetPromise) {
        MathJax.typesetPromise([body]).catch(function() {});
    }
}
```

- [ ] **Step 4: Manually verify the preview in a browser**

1. Start the app: `python app.py`
2. Open `http://localhost:5000` and log in (or use demo mode).
3. Submit a student script to an assignment so you reach the "Review Your Answers" screen.
4. Confirm each textarea has a "Preview" block below it, showing the AI‑extracted text typeset. Equations like `$x^2 + 3x = 0$` should render as math, not raw `$` symbols.
5. Type `$\frac{1}{2}$` into a textarea. After ~200 ms, the preview should update and show ½ typeset.
6. Backspace to break a `$` pair — preview shouldn't crash; renders fragment as-is.

Expected: live preview visible under each answer, updates on typing, renders LaTeX correctly.

- [ ] **Step 5: Commit**

```bash
git add templates/submit.html
git commit -m "feat(submit): add LaTeX live preview under extracted-answer textareas"
```

---

## Task 2: Teacher "View Edits" modal — typeset after populate

**Files:**
- Modify: `templates/teacher_detail.html` (`viewExtracted` ~line 874)

- [ ] **Step 1: Add MathJax typeset call at the end of `viewExtracted`**

In `templates/teacher_detail.html`, find the `viewExtracted` function (around line 874). Replace the line near line 905 that reads:

```js
        document.getElementById('extractedDiffContent').innerHTML = html;
```

with:

```js
        var diffEl = document.getElementById('extractedDiffContent');
        diffEl.innerHTML = html;
        if (window.MathJax && MathJax.typesetPromise) {
            MathJax.typesetPromise([diffEl]).catch(function() {});
        }
```

- [ ] **Step 2: Manually verify**

1. Start the app: `python app.py`
2. Log in as teacher, open a class with a submission where the student edited the AI-extracted text and included LaTeX.
3. If no such submission exists, create one: upload a script that will produce a LaTeX-style extraction (or temporarily add `$x^2$` to an AI extraction via the edit step before submitting).
4. On the teacher's assignment detail page, click "View Edits" on that student's row.
5. Confirm both "AI Extracted" and "Student Version" render LaTeX as typeset math (not raw `$...$`).

Expected: rendered math on both sides of the diff.

- [ ] **Step 3: Commit**

```bash
git add templates/teacher_detail.html
git commit -m "fix(teacher): render LaTeX in View Edits modal"
```

---

## Task 3: Teacher in-app feedback viewer — new modal

**Files:**
- Modify: `templates/teacher_detail.html` (add button per row, add modal markup, add styles, add JS)

This task reuses the existing endpoint `GET /teacher/assignment/<assignment_id>/submission/<submission_id>/result` (`app.py:3786`) which returns `{success, result, status, draft_number, is_final}`. The `result` is the same JSON structure rendered by `submit.html`'s `renderResults` (overall_feedback, questions array, errors array, recommended_actions, assign_type).

- [ ] **Step 1: Add "View Feedback" button to student rows**

In `templates/teacher_detail.html`, find the Actions `<td>` inside the student rows loop (around line 364-377). Locate the block:

```jinja
{% if s.status == 'done' and s.submission_id %}
<a href="/submit/{{ assignment.id }}/download/{{ s.submission_id }}" class="upload-btn" style="text-decoration:none;display:inline-block;">Download</a>
{% endif %}
```

Replace with:

```jinja
{% if s.status == 'done' and s.submission_id %}
<button class="upload-btn" type="button" onclick='openFeedbackModal({{ s.submission_id|tojson }}, {{ s.name|tojson }})'>View Feedback</button>
<a href="/submit/{{ assignment.id }}/download/{{ s.submission_id }}" class="upload-btn" style="text-decoration:none;display:inline-block;">Download</a>
{% endif %}
```

- [ ] **Step 2: Add feedback modal CSS**

In `templates/teacher_detail.html`, inside the existing `<style>` block, immediately after the `.edit-error` rule (around line 183), append:

```css
.feedback-modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 1000; align-items: center; justify-content: center; backdrop-filter: blur(4px); }
.feedback-modal.active { display: flex; }
.feedback-modal-box { background: white; border-radius: 16px; padding: 28px; width: 92%; max-width: 720px; max-height: 88vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }
.feedback-modal-box h3 { font-size: 18px; font-weight: 700; color: #333; margin-bottom: 4px; }
.feedback-modal-subtitle { font-size: 13px; color: #888; margin-bottom: 16px; }
.fb-summary-bar { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
.fb-summary-item { font-size: 13px; font-weight: 700; padding: 6px 12px; border-radius: 6px; background: #f0f0f0; color: #333; }
.fb-summary-marks { background: #e8eaff; color: #4a54c4; }
.fb-summary-correct { background: #e8f5e9; color: #28a745; }
.fb-summary-partial { background: #fff8e1; color: #e68a00; }
.fb-summary-incorrect { background: #fdecea; color: #dc3545; }
.fb-overall-box { background: #f8f9fa; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.fb-overall-box h4 { font-size: 13px; font-weight: 700; color: #555; text-transform: uppercase; letter-spacing: 0.3px; margin-bottom: 6px; }
.fb-overall-box p { font-size: 14px; color: #333; line-height: 1.6; margin: 0; white-space: pre-wrap; }
.fb-overall-box ul { margin: 0; padding-left: 20px; }
.fb-overall-box li { font-size: 14px; color: #333; line-height: 1.6; }
.fb-q-dots { display: flex; gap: 6px; flex-wrap: wrap; margin: 12px 0; }
.fb-q-dot { width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; background: #eee; color: #555; cursor: pointer; border: 2px solid transparent; }
.fb-q-dot.active { border-color: #667eea; }
.fb-q-dot.correct { background: #e8f5e9; color: #28a745; }
.fb-q-dot.partially_correct { background: #fff8e1; color: #e68a00; }
.fb-q-dot.incorrect { background: #fdecea; color: #dc3545; }
.fb-q-nav { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.fb-q-nav button { padding: 8px 14px; border: 1px solid #e0e0e0; background: white; border-radius: 8px; font-size: 13px; cursor: pointer; }
.fb-q-nav button:disabled { opacity: 0.4; cursor: not-allowed; }
.fb-q-card { border: 1px solid #eee; border-radius: 10px; margin-bottom: 10px; }
.fb-q-card-header { padding: 12px 14px; background: #fafafa; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center; }
.fb-q-num { font-size: 14px; font-weight: 700; color: #333; }
.fb-status-badge { font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 999px; text-transform: uppercase; letter-spacing: 0.3px; }
.fb-status-badge.correct { background: #e8f5e9; color: #28a745; }
.fb-status-badge.partially_correct { background: #fff8e1; color: #e68a00; }
.fb-status-badge.incorrect { background: #fdecea; color: #dc3545; }
.fb-q-card-body { padding: 14px; }
.fb-q-field { margin-bottom: 12px; }
.fb-q-field-label { font-size: 11px; font-weight: 700; color: #888; text-transform: uppercase; letter-spacing: 0.3px; margin-bottom: 4px; }
.fb-q-field-value { font-size: 14px; color: #333; line-height: 1.6; white-space: pre-wrap; }
.fb-q-field-value.feedback { background: #f0f4ff; padding: 10px 12px; border-radius: 8px; border-left: 3px solid #667eea; }
.fb-q-field-value.improvement { background: #fff8e1; padding: 10px 12px; border-radius: 8px; border-left: 3px solid #e68a00; }
.fb-errors-list { margin-top: 10px; }
.fb-error-item { padding: 10px 12px; border-radius: 6px; margin-bottom: 4px; background: #fafafa; border-left: 3px solid #888; font-size: 13px; }
.fb-modal-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 16px; }
.fb-modal-actions button, .fb-modal-actions a { padding: 10px 20px; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; border: none; text-decoration: none; display: inline-block; }
.fb-close-btn { background: #f0f0f0; color: #555; }
.fb-close-btn:hover { background: #e0e0e0; }
.fb-download-btn { background: #667eea; color: white; }
.fb-download-btn:hover { background: #5a6fd6; }
```

- [ ] **Step 3: Add feedback modal markup**

In `templates/teacher_detail.html`, immediately after the existing Extracted Text Modal (end of the `<div class="extracted-modal" id="extractedModal">` block, just before `<!-- Share to Bank Modal -->` around line 452), add:

```html
<!-- Feedback Viewer Modal -->
<div class="feedback-modal" id="feedbackModal">
    <div class="feedback-modal-box">
        <h3 id="fbModalTitle">Student Feedback</h3>
        <p class="feedback-modal-subtitle" id="fbModalSubtitle"></p>
        <div id="fbModalBody">
            <p style="color:#888;">Loading...</p>
        </div>
        <div class="fb-modal-actions">
            <a id="fbDownloadLink" class="fb-download-btn" href="#" target="_blank" style="display:none;">Download PDF</a>
            <button class="fb-close-btn" type="button" onclick="closeFeedbackModal()">Close</button>
        </div>
    </div>
</div>
```

- [ ] **Step 4: Add feedback modal JS**

In `templates/teacher_detail.html`, find the `closeExtractedModal` function (around line 911). Immediately after its closing `}`, add:

```js
// --- Feedback Viewer Modal ---
var FB_ASSIGNMENT_ID = '{{ assignment.id }}';
var fbQuestions = [];
var fbErrors = [];
var fbAssignType = 'short_answer';
var fbRecommendedActions = [];
var fbOverallFeedback = '';
var fbCurrentQ = 0;
var FB_STATUS_LABELS = { correct: 'Correct', partially_correct: 'Partially Correct', incorrect: 'Incorrect' };

async function openFeedbackModal(submissionId, studentName) {
    document.getElementById('fbModalTitle').textContent = studentName + ' — Feedback';
    document.getElementById('fbModalSubtitle').textContent = '';
    document.getElementById('fbModalBody').innerHTML = '<p style="color:#888;">Loading...</p>';
    var dl = document.getElementById('fbDownloadLink');
    dl.href = '/submit/' + FB_ASSIGNMENT_ID + '/download/' + submissionId;
    dl.style.display = 'inline-block';
    document.getElementById('feedbackModal').classList.add('active');

    try {
        var res = await fetch('/teacher/assignment/' + FB_ASSIGNMENT_ID + '/submission/' + submissionId + '/result');
        var data = await res.json();
        if (!data.success || !data.result) {
            document.getElementById('fbModalBody').innerHTML = '<p style="color:red;">Could not load feedback.</p>';
            return;
        }
        renderFeedbackResult(data.result);
    } catch (err) {
        document.getElementById('fbModalBody').innerHTML = '<p style="color:red;">Failed to load feedback.</p>';
    }
}

function closeFeedbackModal() {
    document.getElementById('feedbackModal').classList.remove('active');
}

function fbEsc(s) { var d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }

function renderFeedbackResult(result) {
    fbQuestions = result.questions || [];
    fbErrors = result.errors || [];
    fbAssignType = result.assign_type || 'short_answer';
    fbRecommendedActions = result.recommended_actions || [];
    fbOverallFeedback = result.overall_feedback || '';
    fbCurrentQ = 0;

    var hasMarks = fbQuestions.some(function(q) { return q.marks_awarded != null; });
    var summary = '';
    if (hasMarks) {
        var ta = 0, tp = 0;
        fbQuestions.forEach(function(q) { ta += (q.marks_awarded || 0); tp += (q.marks_total || 0); });
        var pct = tp > 0 ? Math.round(ta / tp * 100) : 0;
        summary = '<div class="fb-summary-item fb-summary-marks">' + ta + ' / ' + tp + ' marks</div>' +
                  '<div class="fb-summary-item fb-summary-marks">' + pct + '%</div>';
    } else {
        var c = { correct: 0, partially_correct: 0, incorrect: 0 };
        fbQuestions.forEach(function(q) { if (c.hasOwnProperty(q.status)) c[q.status]++; });
        summary = '<div class="fb-summary-item fb-summary-correct">' + c.correct + ' Correct</div>' +
                  '<div class="fb-summary-item fb-summary-partial">' + c.partially_correct + ' Partial</div>' +
                  '<div class="fb-summary-item fb-summary-incorrect">' + c.incorrect + ' Incorrect</div>';
    }

    var dots = '';
    fbQuestions.forEach(function(q, i) {
        var s = q.status || 'incorrect';
        var label = (fbAssignType === 'rubrics' && q.criterion_name)
            ? q.criterion_name.substring(0, 2).toUpperCase() : (q.question_num || i + 1);
        dots += '<div class="fb-q-dot ' + fbEsc(s) + (i === 0 ? ' active' : '') + '" onclick="fbGoQ(' + i + ')">' + fbEsc(String(label)) + '</div>';
    });

    var overall = '';
    if (fbOverallFeedback) {
        overall += '<div class="fb-overall-box"><h4>Overall Feedback</h4><p>' + fbEsc(fbOverallFeedback) + '</p></div>';
    }
    if (fbRecommendedActions.length) {
        overall += '<div class="fb-overall-box"><h4>Recommended Actions</h4><ul>';
        fbRecommendedActions.forEach(function(a) { overall += '<li>' + fbEsc(a) + '</li>'; });
        overall += '</ul></div>';
    }
    if (fbErrors.length) {
        overall += '<div class="fb-overall-box"><h4>Line-by-Line Errors (' + fbErrors.length + ')</h4><div class="fb-errors-list">';
        fbErrors.forEach(function(e) {
            overall += '<div class="fb-error-item"><strong>' + fbEsc((e.type || 'error').toUpperCase()) + '</strong>';
            if (e.location) overall += ' <span style="color:#999;">' + fbEsc(e.location) + '</span>';
            overall += '<div style="margin-top:4px;"><span style="text-decoration:line-through;color:#dc3545;">' + fbEsc(e.original || '') + '</span> &rarr; <span style="color:#28a745;">' + fbEsc(e.correction || '') + '</span></div></div>';
        });
        overall += '</div></div>';
    }

    var html =
        '<div class="fb-summary-bar">' + summary + '</div>' +
        (fbQuestions.length ? (
            '<div class="fb-q-dots">' + dots + '</div>' +
            '<div class="fb-q-nav">' +
                '<button id="fbPrevBtn" type="button" onclick="fbNavQ(-1)">&larr; Prev</button>' +
                '<span id="fbQNavInfo"></span>' +
                '<button id="fbNextBtn" type="button" onclick="fbNavQ(1)">Next &rarr;</button>' +
            '</div>' +
            '<div id="fbQCardContainer"></div>'
        ) : '<p style="color:#888;font-style:italic;">No per-question feedback.</p>') +
        overall;

    document.getElementById('fbModalBody').innerHTML = html;
    if (fbQuestions.length) fbRenderQuestion();

    if (window.MathJax && MathJax.typesetPromise) {
        MathJax.typesetPromise([document.getElementById('fbModalBody')]).catch(function() {});
    }
}

function fbRenderQuestion() {
    if (!fbQuestions.length) return;
    var q = fbQuestions[fbCurrentQ];
    var s = q.status || 'incorrect';
    var label = FB_STATUS_LABELS[s] || s;
    var hasMarks = q.marks_awarded != null;
    var badge = hasMarks
        ? '<span class="fb-status-badge ' + fbEsc(s) + '">' + fbEsc(String(q.marks_awarded)) + '/' + fbEsc(String(q.marks_total || '?')) + '</span>'
        : '<span class="fb-status-badge ' + fbEsc(s) + '">' + fbEsc(label) + '</span>';

    var isRubrics = fbAssignType === 'rubrics';
    var headerLabel = isRubrics ? (q.criterion_name || 'Criterion ' + (q.question_num || fbCurrentQ + 1)) : 'Question ' + (q.question_num || fbCurrentQ + 1);
    var ansLabel = isRubrics ? 'Assessment' : "Student's Answer";
    var refLabel = isRubrics ? 'Band Descriptor' : 'Correct Answer';
    var bandInfo = (isRubrics && q.band) ? ' <span style="font-size:12px;color:#667eea;font-weight:600;">(' + fbEsc(q.band) + ')</span>' : '';

    var html = '<div class="fb-q-card"><div class="fb-q-card-header"><span class="fb-q-num">' + fbEsc(headerLabel) + bandInfo + '</span>' + badge + '</div><div class="fb-q-card-body">' +
        '<div class="fb-q-field"><div class="fb-q-field-label">' + ansLabel + '</div><div class="fb-q-field-value">' + fbEsc(q.student_answer || 'N/A') + '</div></div>' +
        '<div class="fb-q-field"><div class="fb-q-field-label">' + refLabel + '</div><div class="fb-q-field-value">' + fbEsc(q.correct_answer || 'N/A') + '</div></div>';
    if (q.feedback) html += '<div class="fb-q-field"><div class="fb-q-field-label">Feedback</div><div class="fb-q-field-value feedback">' + fbEsc(q.feedback) + '</div></div>';
    if (q.improvement) html += '<div class="fb-q-field"><div class="fb-q-field-label">Suggested Improvement</div><div class="fb-q-field-value improvement">' + fbEsc(q.improvement) + '</div></div>';
    html += '</div></div>';

    var container = document.getElementById('fbQCardContainer');
    container.innerHTML = html;

    document.getElementById('fbQNavInfo').textContent = 'Q' + (fbCurrentQ + 1) + ' of ' + fbQuestions.length;
    document.getElementById('fbPrevBtn').disabled = fbCurrentQ === 0;
    document.getElementById('fbNextBtn').disabled = fbCurrentQ === fbQuestions.length - 1;
    document.querySelectorAll('#fbModalBody .fb-q-dot').forEach(function(d, i) { d.classList.toggle('active', i === fbCurrentQ); });

    if (window.MathJax && MathJax.typesetPromise) {
        MathJax.typesetPromise([container]).catch(function() {});
    }
}

function fbNavQ(dir) { fbGoQ(fbCurrentQ + dir); }
function fbGoQ(idx) { if (idx < 0 || idx >= fbQuestions.length) return; fbCurrentQ = idx; fbRenderQuestion(); }

// Close on backdrop click and Escape
document.getElementById('feedbackModal').addEventListener('click', function(e) {
    if (e.target === this) closeFeedbackModal();
});
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && document.getElementById('feedbackModal').classList.contains('active')) closeFeedbackModal();
});
```

- [ ] **Step 5: Manually verify**

1. Start the app: `python app.py`
2. Log in as teacher and open a class with done submissions; make sure at least one submission contains LaTeX in the feedback (overall_feedback or a question's feedback).
3. On a student row with status "Done", click the new **View Feedback** button.
4. Verify the modal opens and shows:
   - Student name in the title
   - Summary bar (marks or correct/partial/incorrect)
   - Question dots and navigation
   - Per-question card with student answer, correct answer, feedback, improvement (all LaTeX rendered)
   - Overall feedback, recommended actions, errors list (if present)
   - "Download PDF" and "Close" buttons
5. Click through prev/next question — LaTeX should render on each question.
6. Click the backdrop and press Escape — modal closes.
7. Click the "Download PDF" link — existing PDF download works.

Expected: modal with typeset math throughout; navigation and close interactions work.

- [ ] **Step 6: Commit**

```bash
git add templates/teacher_detail.html
git commit -m "feat(teacher): add in-app feedback viewer modal with LaTeX rendering"
```

---

## Task 4: Verify student prior-submission review still renders LaTeX

**Files:**
- Read-only: `templates/submit.html` (`reviewSubmission` ~line 393, `reviewPrevious` ~line 421, `renderResults` ~line 676)

This is a verification step, no code change expected. The spec calls out that `reviewSubmission` and `reviewPrevious` both funnel into `renderResults` / `renderQuestion`, which already call `MathJax.typesetPromise()` at lines 734 and 767.

- [ ] **Step 1: Read the relevant functions to confirm the typeset calls exist**

Read `templates/submit.html` lines 393–480 (`reviewSubmission`, `reviewPrevious`) and 674–770 (`renderResults`, `renderQuestion`). Confirm typeset is called inside `renderResults` (after all DOM updates) and inside `renderQuestion` (after each question card is written).

- [ ] **Step 2: Manually verify**

1. Start the app: `python app.py`
2. Submit a student script with LaTeX-containing answers on an assignment where `teacher_show_results` is true (so the student can view feedback).
3. After submission, re-open the submit page for that assignment as the same student.
4. Click **View feedback** on the prior submission.
5. Confirm LaTeX renders in the overall feedback, per-question feedback, and any student-answer fields.

Expected: LaTeX renders; no code change required. If it doesn't render, wrap the relevant section in a `MathJax.typesetPromise([el])` call matching the pattern from `renderResults`/`renderQuestion`.

- [ ] **Step 3: Commit (only if a change was needed)**

If no code change was required, skip this step. Otherwise:

```bash
git add templates/submit.html
git commit -m "fix(submit): ensure LaTeX renders on prior-submission review"
```

---

## Task 5: Audit other dynamic surfaces for feedback/answer snippets

**Files:**
- Read-only audit: `templates/dashboard.html`, `templates/bank.html`, `templates/department_insights.html`, `templates/class.html`, `templates/class_insights.html`
- Modify only if a gap is found.

- [ ] **Step 1: Audit each template**

For each of the five files listed above, run:

```bash
grep -n "innerHTML\|feedback\|student_answer\|extracted\|criterion\|improvement\|overall_feedback\|recommended_actions" templates/<file>
```

For any match where:
- content is injected via JS (not server-rendered Jinja), AND
- the content could plausibly contain equation text (feedback text, student answers, extracted text, overall feedback, recommended actions, improvement text)

check whether the surrounding function already calls `MathJax.typesetPromise()` after the injection. If not, note the element ID and function.

- [ ] **Step 2: Add typeset calls where missing**

For each gap found in Step 1, add a typeset call immediately after the `innerHTML =` (or equivalent) assignment, scoped to the element that was updated:

```js
if (window.MathJax && MathJax.typesetPromise) {
    MathJax.typesetPromise([<element>]).catch(function() {});
}
```

If no gaps are found, proceed to Step 3 with no code changes.

- [ ] **Step 3: Manually verify each touched page**

For each template modified (if any), open the relevant page in a browser, trigger the dynamic injection, and confirm LaTeX renders.

If no templates were modified, open each of the five pages with LaTeX-containing data and visually confirm existing rendering is correct (no regression).

- [ ] **Step 4: Commit (only if changes were made)**

If no code changes were needed, skip. Otherwise:

```bash
git add templates/<modified-files>
git commit -m "fix(templates): trigger MathJax typeset on dynamic feedback/answer snippets"
```

---

## Final verification

- [ ] **Step 1: Regression sweep**

Open each previously-working surface to confirm nothing broke:

- Student results view after a fresh submission (`submit.html` results)
- Class insights page (`class_insights.html`)
- Demo "Try AI Marking" (`index.html`)

All should still render LaTeX correctly.

- [ ] **Step 2: Full end-to-end walkthrough**

As a teacher, upload a class list, create an assignment, have a student (or yourself via classroom code) submit a script with math answers, and walk through:

1. Student edit step — live preview under textareas.
2. Student results view — LaTeX renders.
3. Teacher assignment page — "View Edits" modal renders LaTeX.
4. Teacher assignment page — "View Feedback" modal renders LaTeX.
5. PDF download — still works (LaTeX approximated via Unicode, as before; out of scope for this plan).

All five surfaces should render math cleanly.

---

## Self-Review (already performed by plan author)

**Spec coverage:** ✓
- Spec §1 (student live preview) → Task 1
- Spec §2 (View Edits modal) → Task 2
- Spec §3 (teacher in-app feedback viewer) → Task 3
- Spec §4 (audit other surfaces) → Task 5
- Spec §5 (prior-submission review verify) → Task 4

**Placeholder scan:** No TBDs, no "handle edge cases" phrases, all code blocks complete.

**Type consistency:** `fbEsc`, `escHtml`, `esc` — three similar helpers, each scoped to its own file/module (teacher_detail.html uses `escHtml` for the existing extracted modal and `fbEsc` for the new feedback modal to avoid collision; submit.html uses `esc`). IDs `preview-body-<i>`, `answer-<i>`, `edited-<i>` follow existing patterns. Endpoint URL `/teacher/assignment/<aid>/submission/<sid>/result` matches `app.py:3786`.
