# Submission Review Split View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a split-screen review page opened in a new tab from a student's name on the assignment detail page — left pane shows the student's work (PDF.js + `<img>` with zoom/rotate), right pane has a dropdown to switch between AI feedback, answer key, and another student for comparison. Draggable resizer between the panes.

**Architecture:** One new Flask route + three new static-bytes endpoints. Two shared frontend JS modules (`feedback_render.js` and `document_viewer.js`) extracted so both the existing feedback modal and the new review page can reuse them. One new template `review.html` that wires everything together. PDF.js loaded from jsdelivr CDN.

**Tech Stack:** Flask, SQLAlchemy, Jinja2, vanilla JS, PDF.js (`pdfjs-dist@4` via CDN), existing MathJax. No new Python deps, no DB migration.

**Spec:** `docs/superpowers/specs/2026-04-23-submission-review-split-view-design.md`

---

## Task 1: Backend — 4 new endpoints + route

**Files:**
- Modify: `app.py` (add near other teacher-assignment routes around line 3820)

- [ ] **Step 1: Add a magic-byte MIME helper**

In `app.py`, immediately above the `teacher_submission_result` route (around line 3786), add:

```python
def _detect_mime(data):
    """Infer MIME type from the first bytes of a blob. Falls back to octet-stream."""
    if not data:
        return 'application/octet-stream'
    b = bytes(data[:8])
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
    return 'application/octet-stream'
```

- [ ] **Step 2: Add the script manifest endpoint**

In `app.py`, immediately AFTER the existing `teacher_submission_remark` route (around line 3848 — this is the last of the submission-scoped teacher routes), add:

```python
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
```

- [ ] **Step 3: Add the per-page script endpoint**

Immediately after the manifest endpoint, add:

```python
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
    return send_file(
        io.BytesIO(data),
        mimetype=_detect_mime(data),
        as_attachment=False,
    )
```

- [ ] **Step 4: Add the answer-key endpoint**

Immediately after the script-page endpoint, add:

```python
@app.route('/teacher/assignment/<assignment_id>/answer-key')
def teacher_assignment_answer_key(assignment_id):
    asn = Assignment.query.get_or_404(assignment_id)
    err = _check_assignment_ownership(asn)
    if err:
        return err
    if not asn.answer_key:
        return jsonify({'success': False, 'error': 'No answer key available'}), 404
    data = asn.answer_key
    return send_file(
        io.BytesIO(data),
        mimetype=_detect_mime(data),
        as_attachment=False,
    )
```

- [ ] **Step 5: Add the review-page route**

Immediately after the answer-key endpoint, add:

```python
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

    return render_template(
        'review.html',
        assignment=asn,
        submission=sub,
        student=student,
        manifest=manifest,
        other_students=other_students,
        has_answer_key=bool(asn.answer_key),
    )
```

- [ ] **Step 6: Syntax check**

```bash
python3 -m py_compile app.py
```

Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add app.py
git commit -m "feat(api): submission review route + script/answer-key serve endpoints"
```

Include `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

---

## Task 2: Extract `feedback_render.js` shared module

**Files:**
- Create: `static/js/feedback_render.js`
- Modify: `templates/teacher_detail.html` (replace inline fb* JS with module call; add `<script>` tag)

This refactor extracts the feedback-rendering logic that currently lives inline in `teacher_detail.html` (functions `fbEsc`, `renderFeedbackResult`, `fbRenderQuestion`, `fbNavQ`, `fbGoQ`, the `FB_STATUS_LABELS` constant, and the module state vars) into a reusable module. `teacher_detail.html` continues to work with `idPrefix = 'fb'`. The new review page will use a different prefix to avoid ID collisions.

- [ ] **Step 1: Create `static/js/feedback_render.js`**

Create the file with this content:

```js
// Shared feedback rendering used by:
//   - Teacher assignment detail page (feedback modal)
//   - Submission review split-view page
//
// Call FeedbackRender.render(containerEl, result, options)
//   options: { idPrefix, onNavigate? }
// The module namespaces all generated element IDs with options.idPrefix so
// multiple renderers can coexist on the same page.

(function (global) {
    var STATUS_LABELS = { correct: 'Correct', partially_correct: 'Partially Correct', incorrect: 'Incorrect' };

    function esc(s) {
        var d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }

    function render(containerEl, result, options) {
        options = options || {};
        var prefix = options.idPrefix || 'fb';
        var state = {
            questions: result.questions || [],
            errors: result.errors || [],
            assignType: result.assign_type || 'short_answer',
            recommended: result.recommended_actions || [],
            overall: result.overall_feedback || '',
            currentQ: 0,
            containerEl: containerEl,
            prefix: prefix,
        };

        var hasMarks = state.questions.some(function (q) { return q.marks_awarded != null; });
        var summary = '';
        if (hasMarks) {
            var ta = 0, tp = 0;
            state.questions.forEach(function (q) { ta += (q.marks_awarded || 0); tp += (q.marks_total || 0); });
            var pct = tp > 0 ? Math.round(ta / tp * 100) : 0;
            summary = '<div class="fb-summary-item fb-summary-marks">' + ta + ' / ' + tp + ' marks</div>' +
                      '<div class="fb-summary-item fb-summary-marks">' + pct + '%</div>';
        } else {
            var c = { correct: 0, partially_correct: 0, incorrect: 0 };
            state.questions.forEach(function (q) { if (c.hasOwnProperty(q.status)) c[q.status]++; });
            summary = '<div class="fb-summary-item fb-summary-correct">' + c.correct + ' Correct</div>' +
                      '<div class="fb-summary-item fb-summary-partial">' + c.partially_correct + ' Partial</div>' +
                      '<div class="fb-summary-item fb-summary-incorrect">' + c.incorrect + ' Incorrect</div>';
        }

        var dots = '';
        state.questions.forEach(function (q, i) {
            var s = q.status || 'incorrect';
            var label = (state.assignType === 'rubrics' && q.criterion_name)
                ? q.criterion_name.substring(0, 2).toUpperCase() : (q.question_num || i + 1);
            dots += '<div class="fb-q-dot ' + esc(s) + (i === 0 ? ' active' : '') +
                    '" data-q="' + i + '">' + esc(String(label)) + '</div>';
        });

        var overall = '';
        if (state.overall) {
            overall += '<div class="fb-overall-box"><h4>Overall Feedback</h4><p>' + esc(state.overall) + '</p></div>';
        }
        if (state.recommended.length) {
            overall += '<div class="fb-overall-box"><h4>Recommended Actions</h4><ul>';
            state.recommended.forEach(function (a) { overall += '<li>' + esc(a) + '</li>'; });
            overall += '</ul></div>';
        }
        if (state.errors.length) {
            overall += '<div class="fb-overall-box"><h4>Line-by-Line Errors (' + state.errors.length + ')</h4><div class="fb-errors-list">';
            state.errors.forEach(function (e) {
                overall += '<div class="fb-error-item"><strong>' + esc((e.type || 'error').toUpperCase()) + '</strong>';
                if (e.location) overall += ' <span style="color:#999;">' + esc(e.location) + '</span>';
                overall += '<div style="margin-top:4px;"><span style="text-decoration:line-through;color:#dc3545;">' + esc(e.original || '') + '</span> &rarr; <span style="color:#28a745;">' + esc(e.correction || '') + '</span></div></div>';
            });
            overall += '</div></div>';
        }

        var html =
            '<div class="fb-summary-bar">' + summary + '</div>' +
            (state.questions.length ? (
                '<div class="fb-q-dots" id="' + prefix + 'QDots">' + dots + '</div>' +
                '<div class="fb-q-nav">' +
                    '<button id="' + prefix + 'PrevBtn" type="button">&larr; Prev</button>' +
                    '<span id="' + prefix + 'QNavInfo"></span>' +
                    '<button id="' + prefix + 'NextBtn" type="button">Next &rarr;</button>' +
                '</div>' +
                '<div id="' + prefix + 'QCardContainer"></div>'
            ) : '<p style="color:#888;font-style:italic;">No per-question feedback.</p>') +
            overall;

        containerEl.innerHTML = html;

        if (state.questions.length) {
            bindNav(state);
            renderQuestion(state);
        }

        if (window.MathJax && MathJax.typesetPromise) {
            MathJax.typesetPromise([containerEl]).catch(function () {});
        }
    }

    function bindNav(state) {
        var prev = document.getElementById(state.prefix + 'PrevBtn');
        var next = document.getElementById(state.prefix + 'NextBtn');
        if (prev) prev.addEventListener('click', function () { goQ(state, state.currentQ - 1); });
        if (next) next.addEventListener('click', function () { goQ(state, state.currentQ + 1); });
        var dots = state.containerEl.querySelectorAll('.fb-q-dot');
        dots.forEach(function (d) {
            d.addEventListener('click', function () {
                var idx = parseInt(d.getAttribute('data-q'), 10);
                if (!isNaN(idx)) goQ(state, idx);
            });
        });
    }

    function goQ(state, idx) {
        if (idx < 0 || idx >= state.questions.length) return;
        state.currentQ = idx;
        renderQuestion(state);
    }

    function renderQuestion(state) {
        var q = state.questions[state.currentQ];
        if (!q) return;
        var s = q.status || 'incorrect';
        var label = STATUS_LABELS[s] || s;
        var hasMarks = q.marks_awarded != null;
        var badge = hasMarks
            ? '<span class="fb-status-badge ' + esc(s) + '">' + esc(String(q.marks_awarded)) + '/' + esc(String(q.marks_total || '?')) + '</span>'
            : '<span class="fb-status-badge ' + esc(s) + '">' + esc(label) + '</span>';

        var isRubrics = state.assignType === 'rubrics';
        var headerLabel = isRubrics ? (q.criterion_name || 'Criterion ' + (q.question_num || state.currentQ + 1)) : 'Question ' + (q.question_num || state.currentQ + 1);
        var ansLabel = isRubrics ? 'Assessment' : "Student's Answer";
        var refLabel = isRubrics ? 'Band Descriptor' : 'Correct Answer';
        var bandInfo = (isRubrics && q.band) ? ' <span style="font-size:12px;color:#667eea;font-weight:600;">(' + esc(q.band) + ')</span>' : '';

        var html = '<div class="fb-q-card"><div class="fb-q-card-header"><span class="fb-q-num">' + esc(headerLabel) + bandInfo + '</span>' + badge + '</div><div class="fb-q-card-body">' +
            '<div class="fb-q-field"><div class="fb-q-field-label">' + ansLabel + '</div><div class="fb-q-field-value">' + esc(q.student_answer || 'N/A') + '</div></div>' +
            '<div class="fb-q-field"><div class="fb-q-field-label">' + refLabel + '</div><div class="fb-q-field-value">' + esc(q.correct_answer || 'N/A') + '</div></div>';
        if (q.feedback) html += '<div class="fb-q-field"><div class="fb-q-field-label">Feedback</div><div class="fb-q-field-value feedback">' + esc(q.feedback) + '</div></div>';
        if (q.improvement) html += '<div class="fb-q-field"><div class="fb-q-field-label">Suggested Improvement</div><div class="fb-q-field-value improvement">' + esc(q.improvement) + '</div></div>';
        html += '</div></div>';

        var container = document.getElementById(state.prefix + 'QCardContainer');
        if (container) container.innerHTML = html;

        var info = document.getElementById(state.prefix + 'QNavInfo');
        if (info) info.textContent = 'Q' + (state.currentQ + 1) + ' of ' + state.questions.length;
        var prev = document.getElementById(state.prefix + 'PrevBtn');
        var next = document.getElementById(state.prefix + 'NextBtn');
        if (prev) prev.disabled = state.currentQ === 0;
        if (next) next.disabled = state.currentQ === state.questions.length - 1;
        state.containerEl.querySelectorAll('.fb-q-dot').forEach(function (d, i) {
            d.classList.toggle('active', i === state.currentQ);
        });

        if (window.MathJax && MathJax.typesetPromise && container) {
            MathJax.typesetPromise([container]).catch(function () {});
        }
    }

    global.FeedbackRender = { render: render };
})(window);
```

- [ ] **Step 2: Add the script tag to `teacher_detail.html`**

In `templates/teacher_detail.html`, find the opening `<script>` tag of the main script block (search for `var FB_ASSIGNMENT_ID`; walk upward to the nearest `<script>` opening tag — it should be a single large block). Immediately BEFORE that `<script>` tag, add:

```jinja
<script src="{{ url_for('static', filename='js/feedback_render.js') }}"></script>
```

- [ ] **Step 3: Replace inline fb* JS in `teacher_detail.html`**

In `templates/teacher_detail.html`, replace the block from `// --- Feedback Viewer Modal ---` (around line 990) through the closing `}` of `fbGoQ` (around line 1139) with:

```js
// --- Feedback Viewer Modal ---
var FB_ASSIGNMENT_ID = '{{ assignment.id }}';

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
            dl.style.display = 'none';
            return;
        }
        FeedbackRender.render(document.getElementById('fbModalBody'), data.result, { idPrefix: 'fb' });
    } catch (err) {
        document.getElementById('fbModalBody').innerHTML = '<p style="color:red;">Failed to load feedback.</p>';
        dl.style.display = 'none';
    }
}

function closeFeedbackModal() {
    document.getElementById('feedbackModal').classList.remove('active');
}
```

Leave the backdrop/Escape listeners that come AFTER `fbGoQ` in place — they reference `feedbackModal`, `closeFeedbackModal`, and do not depend on the removed functions.

- [ ] **Step 4: Manual verification — existing feedback modal still works**

Run `python3 -m py_compile templates/teacher_detail.html 2>/dev/null || true` (Jinja, not Python; this is a no-op safety check). The real verification is manual: open the feedback modal from the assignment detail page and confirm it still renders correctly. Defer actual browser verification to the plan's final verification step.

- [ ] **Step 5: Commit**

```bash
git add static/js/feedback_render.js templates/teacher_detail.html
git commit -m "refactor(teacher): extract shared feedback renderer to static/js/feedback_render.js"
```

Include `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

---

## Task 3: Create `document_viewer.js` shared module

**Files:**
- Create: `static/js/document_viewer.js`

This module encapsulates the PDF.js + `<img>` + zoom/rotate pipeline. It's used by three consumers in Task 4: the left pane (student script), the right pane when showing answer key, and the right pane when showing a compared student's work.

- [ ] **Step 1: Create `static/js/document_viewer.js`**

Create the file with this content:

```js
// Shared document viewer: renders a scrollable column of PDF pages
// (via PDF.js) and/or images, with zoom & rotate controls.
//
// Usage:
//   var viewer = DocumentViewer.create(scrollContainerEl);
//   viewer.loadFromManifest(manifestUrl, pageUrlBuilder);  // for script with per-page endpoints
//   viewer.loadFromUrl(url);                               // for a single blob (answer key)
//   viewer.zoomIn() / zoomOut() / reset() / rotate();
//
// Requires PDF.js to be loaded (pdfjsLib global).

(function (global) {
    var ZOOM_MIN = 0.5, ZOOM_MAX = 3.0, ZOOM_STEP = 0.25;

    function create(scrollContainerEl) {
        var wrap = document.createElement('div');
        wrap.className = 'dv-scale-wrap';
        wrap.style.transformOrigin = 'top center';
        wrap.style.transform = 'scale(1) rotate(0deg)';
        scrollContainerEl.innerHTML = '';
        scrollContainerEl.appendChild(wrap);

        var state = { scale: 1.0, rotation: 0, loadToken: 0 };

        function applyTransform() {
            wrap.style.transform = 'scale(' + state.scale + ') rotate(' + state.rotation + 'deg)';
        }

        function clearPages() {
            wrap.innerHTML = '';
        }

        function appendLoadingBox(label) {
            var d = document.createElement('div');
            d.className = 'dv-loading';
            d.textContent = label;
            d.style.cssText = 'padding:20px; color:#888; text-align:center;';
            wrap.appendChild(d);
            return d;
        }

        function appendError(label) {
            var d = document.createElement('div');
            d.className = 'dv-error';
            d.textContent = label;
            d.style.cssText = 'padding:14px; color:#b00020; background:#fdecea; border-radius:6px; margin:10px 0;';
            wrap.appendChild(d);
        }

        async function renderPdfBlob(blob, token) {
            try {
                var ab = await blob.arrayBuffer();
                if (token !== state.loadToken) return;
                var loadingTask = pdfjsLib.getDocument({ data: ab });
                var pdf = await loadingTask.promise;
                if (token !== state.loadToken) return;
                for (var i = 1; i <= pdf.numPages; i++) {
                    if (token !== state.loadToken) return;
                    var page = await pdf.getPage(i);
                    if (token !== state.loadToken) return;
                    var viewport = page.getViewport({ scale: 2 });
                    var canvas = document.createElement('canvas');
                    canvas.width = viewport.width;
                    canvas.height = viewport.height;
                    canvas.style.display = 'block';
                    canvas.style.margin = '0 auto 12px';
                    canvas.style.maxWidth = '100%';
                    canvas.style.height = 'auto';
                    var ctx = canvas.getContext('2d');
                    await page.render({ canvasContext: ctx, viewport: viewport }).promise;
                    if (token !== state.loadToken) return;
                    wrap.appendChild(canvas);
                }
            } catch (err) {
                if (token !== state.loadToken) return;
                appendError('Could not render PDF page');
            }
        }

        function appendImage(url) {
            var img = document.createElement('img');
            img.src = url;
            img.style.cssText = 'display:block; margin:0 auto 12px; max-width:100%; height:auto;';
            img.onerror = function () { appendError('Could not load image'); };
            wrap.appendChild(img);
        }

        async function loadFromManifest(manifestUrl, pageUrlBuilder) {
            state.loadToken++;
            var token = state.loadToken;
            clearPages();
            var loading = appendLoadingBox('Loading…');
            try {
                var res = await fetch(manifestUrl);
                var data = await res.json();
                if (token !== state.loadToken) return;
                if (!data.success) {
                    loading.remove();
                    appendError('Could not load document manifest');
                    return;
                }
                loading.remove();
                for (var i = 0; i < data.pages.length; i++) {
                    if (token !== state.loadToken) return;
                    var p = data.pages[i];
                    var url = pageUrlBuilder(p.index);
                    if (p.mime === 'application/pdf') {
                        try {
                            var pageRes = await fetch(url);
                            if (token !== state.loadToken) return;
                            var blob = await pageRes.blob();
                            await renderPdfBlob(blob, token);
                        } catch (e) {
                            if (token !== state.loadToken) return;
                            appendError('Could not load page ' + (i + 1));
                        }
                    } else if (p.mime && p.mime.indexOf('image/') === 0) {
                        appendImage(url);
                    } else {
                        appendError('Page ' + (i + 1) + ' (unsupported format)');
                    }
                }
            } catch (err) {
                if (token !== state.loadToken) return;
                if (loading) loading.remove();
                appendError('Could not load document');
            }
        }

        async function loadFromUrl(url) {
            state.loadToken++;
            var token = state.loadToken;
            clearPages();
            var loading = appendLoadingBox('Loading…');
            try {
                var res = await fetch(url);
                if (token !== state.loadToken) return;
                if (!res.ok) {
                    loading.remove();
                    appendError('Document not available');
                    return;
                }
                var ctype = (res.headers.get('Content-Type') || '').toLowerCase();
                var blob = await res.blob();
                if (token !== state.loadToken) return;
                loading.remove();
                if (ctype.indexOf('application/pdf') === 0) {
                    await renderPdfBlob(blob, token);
                } else if (ctype.indexOf('image/') === 0) {
                    var objUrl = URL.createObjectURL(blob);
                    var img = new Image();
                    img.src = objUrl;
                    img.style.cssText = 'display:block; margin:0 auto 12px; max-width:100%; height:auto;';
                    wrap.appendChild(img);
                } else {
                    appendError('Unsupported document format');
                }
            } catch (err) {
                if (token !== state.loadToken) return;
                if (loading) loading.remove();
                appendError('Could not load document');
            }
        }

        return {
            loadFromManifest: loadFromManifest,
            loadFromUrl: loadFromUrl,
            zoomIn: function () { state.scale = Math.min(ZOOM_MAX, state.scale + ZOOM_STEP); applyTransform(); },
            zoomOut: function () { state.scale = Math.max(ZOOM_MIN, state.scale - ZOOM_STEP); applyTransform(); },
            reset: function () { state.scale = 1.0; state.rotation = 0; applyTransform(); },
            rotate: function () { state.rotation = (state.rotation + 90) % 360; applyTransform(); },
            getScale: function () { return state.scale; },
        };
    }

    global.DocumentViewer = { create: create };
})(window);
```

- [ ] **Step 2: Commit**

```bash
git add static/js/document_viewer.js
git commit -m "feat(static): add shared DocumentViewer module (PDF.js + image + zoom/rotate)"
```

Include `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

---

## Task 4: Create `review.html` template — shell + left pane + resizer

**Files:**
- Create: `templates/review.html`

This task builds the skeleton of the review page: topbar, two-column body with draggable resizer, left pane wired to the shared `DocumentViewer` loading the student's script, right pane as an empty placeholder (Task 5 fills it in).

- [ ] **Step 1: Create `templates/review.html`**

Create the file with:

```jinja
{% extends "base.html" %}
{% block title %}Review — {{ student.name }} — {{ app_title }}{% endblock %}

{% block head %}
<style>
    body { background: #f7f7fa; margin: 0; }
    .review-root { height: 100vh; display: flex; flex-direction: column; }
    .review-topbar {
        height: 52px; padding: 0 16px; background: white; border-bottom: 1px solid #e0e0e0;
        display: flex; align-items: center; justify-content: space-between; flex-shrink: 0;
    }
    .review-topbar .title { font-size: 15px; font-weight: 600; color: #2D2D2D; }
    .review-topbar .title .muted { color: #888; font-weight: 400; margin-left: 8px; }
    .review-topbar .close-link {
        font-size: 13px; color: #667eea; text-decoration: none; padding: 6px 12px; border-radius: 6px;
    }
    .review-topbar .close-link:hover { background: #f0f0f0; }

    .review-body { flex: 1; display: flex; min-height: 0; }
    .review-pane { display: flex; flex-direction: column; min-height: 0; background: white; }
    .review-pane-left { flex: 0 0 var(--left-width, 50%); border-right: 0; }
    .review-pane-right { flex: 1 1 auto; }

    .review-resizer {
        flex: 0 0 8px; cursor: col-resize; background: #e8e8e8; position: relative;
        transition: background 0.15s;
    }
    .review-resizer:hover, .review-resizer.dragging { background: #c8c8c8; }

    .review-subtoolbar {
        height: 44px; padding: 0 12px; background: #fafafa; border-bottom: 1px solid #eee;
        display: flex; align-items: center; gap: 8px; flex-shrink: 0;
    }
    .review-subtoolbar button, .review-subtoolbar select {
        padding: 6px 12px; font-size: 13px; border: 1px solid #d0d0d0; border-radius: 6px;
        background: white; cursor: pointer;
    }
    .review-subtoolbar button:hover { background: #f0f0f0; }
    .review-subtoolbar .toolbar-info { font-size: 12px; color: #888; margin-left: auto; }

    .review-scroll { flex: 1; overflow: auto; padding: 16px; background: #f0f0f3; }
    .dv-scale-wrap { transform-origin: top center; transition: none; }

    .review-right-body { flex: 1; overflow: auto; padding: 16px; background: white; }
    .compare-controls { display: flex; align-items: center; gap: 10px; margin-left: 12px; }
    .compare-controls label { font-size: 13px; color: #555; }

    /* Feedback rendering reuses .fb-* classes. Duplicate a minimal subset needed on this page. */
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
</style>
{% endblock %}

{% block body %}
<div class="review-root" id="reviewRoot">
    <div class="review-topbar">
        <div class="title">
            {{ student.name }}<span class="muted">{{ assignment.title or assignment.subject or 'Assignment' }}</span>
        </div>
        <a href="#" class="close-link" onclick="reviewClose(); return false;">Close ×</a>
    </div>
    <div class="review-body" id="reviewBody">
        <div class="review-pane review-pane-left">
            <div class="review-subtoolbar">
                <button type="button" id="leftZoomOut">−</button>
                <button type="button" id="leftZoomIn">+</button>
                <button type="button" id="leftReset">Reset</button>
                <button type="button" id="leftRotate">⟳ Rotate</button>
                <span class="toolbar-info">{{ manifest|length }} page{{ 's' if manifest|length != 1 else '' }}</span>
            </div>
            <div class="review-scroll" id="leftScroll"></div>
        </div>
        <div class="review-resizer" id="reviewResizer"></div>
        <div class="review-pane review-pane-right">
            <div class="review-subtoolbar">
                <select id="rightMode">
                    <option value="feedback">AI Feedback</option>
                    <option value="answer_key"{% if not has_answer_key %} disabled{% endif %}>Answer Key{% if not has_answer_key %} (none){% endif %}</option>
                    <option value="compare"{% if not other_students %} disabled{% endif %}>Compare with another student{% if not other_students %} (none available){% endif %}</option>
                </select>
                <div class="compare-controls" id="compareControls" style="display:none;">
                    <select id="compareStudent">
                        <option value="">— pick a student —</option>
                        {% for o in other_students %}
                        <option value="{{ o.submission_id }}">{{ o.name }}{% if o.index %} ({{ o.index }}){% endif %}</option>
                        {% endfor %}
                    </select>
                    <label><input type="radio" name="compareView" value="work" checked> Work</label>
                    <label><input type="radio" name="compareView" value="feedback"> Feedback</label>
                </div>
                <span class="toolbar-info" id="rightInfo"></span>
            </div>
            <div class="review-right-body" id="rightBody">
                <p style="color:#888;">Loading feedback…</p>
            </div>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script src="https://cdn.jsdelivr.net/npm/pdfjs-dist@4.8.69/build/pdf.min.mjs" type="module"></script>
<script type="module">
    import * as pdfjsLib from 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.8.69/build/pdf.min.mjs';
    pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.8.69/build/pdf.worker.min.mjs';
    window.pdfjsLib = pdfjsLib;
    window.dispatchEvent(new Event('pdfjs-ready'));
</script>
<script src="{{ url_for('static', filename='js/feedback_render.js') }}"></script>
<script src="{{ url_for('static', filename='js/document_viewer.js') }}"></script>
<script>
var REVIEW_ASSIGNMENT_ID = '{{ assignment.id }}';
var REVIEW_SUBMISSION_ID = {{ submission.id }};

// --- Resizer ---
(function initResizer() {
    var root = document.getElementById('reviewRoot');
    var body = document.getElementById('reviewBody');
    var resizer = document.getElementById('reviewResizer');
    var stored = parseFloat(localStorage.getItem('review-split-left-width'));
    if (!isNaN(stored) && stored >= 20 && stored <= 80) {
        body.style.setProperty('--left-width', stored + '%');
    }
    var dragging = false;
    resizer.addEventListener('mousedown', function (e) {
        dragging = true;
        resizer.classList.add('dragging');
        document.body.style.userSelect = 'none';
        e.preventDefault();
    });
    window.addEventListener('mousemove', function (e) {
        if (!dragging) return;
        var rect = body.getBoundingClientRect();
        var pct = ((e.clientX - rect.left) / rect.width) * 100;
        pct = Math.max(20, Math.min(80, pct));
        body.style.setProperty('--left-width', pct + '%');
    });
    window.addEventListener('mouseup', function () {
        if (!dragging) return;
        dragging = false;
        resizer.classList.remove('dragging');
        document.body.style.userSelect = '';
        var current = body.style.getPropertyValue('--left-width');
        if (current) {
            var num = parseFloat(current);
            if (!isNaN(num)) localStorage.setItem('review-split-left-width', num);
        }
    });
})();

// --- Close ---
function reviewClose() {
    try { window.close(); } catch (e) {}
    if (!window.closed) window.location.href = '/teacher/assignment/' + REVIEW_ASSIGNMENT_ID;
}

// --- Left pane viewer ---
var leftViewer = null;
function initLeftViewer() {
    leftViewer = DocumentViewer.create(document.getElementById('leftScroll'));
    leftViewer.loadFromManifest(
        '/teacher/assignment/' + REVIEW_ASSIGNMENT_ID + '/submission/' + REVIEW_SUBMISSION_ID + '/script/manifest',
        function (idx) {
            return '/teacher/assignment/' + REVIEW_ASSIGNMENT_ID + '/submission/' + REVIEW_SUBMISSION_ID + '/script/page/' + idx;
        }
    );
    document.getElementById('leftZoomIn').addEventListener('click', function () { leftViewer.zoomIn(); });
    document.getElementById('leftZoomOut').addEventListener('click', function () { leftViewer.zoomOut(); });
    document.getElementById('leftReset').addEventListener('click', function () { leftViewer.reset(); });
    document.getElementById('leftRotate').addEventListener('click', function () { leftViewer.rotate(); });
}

if (window.pdfjsLib) initLeftViewer();
else window.addEventListener('pdfjs-ready', initLeftViewer);

// Task 5 wires up the right pane.
</script>
{% endblock %}
```

- [ ] **Step 2: Commit**

```bash
git add templates/review.html
git commit -m "feat(review): split-view shell, resizer, and left-pane document viewer"
```

Include `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

---

## Task 5: `review.html` right pane — all three modes

**Files:**
- Modify: `templates/review.html` (append to the existing `<script>` block)

- [ ] **Step 1: Append right-pane JS**

In `templates/review.html`, find the comment `// Task 5 wires up the right pane.` at the end of the existing `<script>` block. Replace that comment with:

```js
// --- Right pane ---
var rightViewer = null;        // Only for answer_key / compare-work modes
var rightMode = 'feedback';

function clearRightBody() {
    var body = document.getElementById('rightBody');
    body.innerHTML = '';
    rightViewer = null;
    document.getElementById('rightInfo').textContent = '';
}

function ensureRightDocViewer() {
    var body = document.getElementById('rightBody');
    body.innerHTML = '';
    // Wrap with a mini toolbar for zoom/rotate
    body.innerHTML =
        '<div class="review-subtoolbar" style="border-bottom:1px solid #eee; margin:-16px -16px 12px; padding:0 12px; background:#fff;">' +
            '<button type="button" id="rightZoomOut">−</button>' +
            '<button type="button" id="rightZoomIn">+</button>' +
            '<button type="button" id="rightReset">Reset</button>' +
            '<button type="button" id="rightRotate">⟳ Rotate</button>' +
        '</div>' +
        '<div id="rightScroll" style="overflow:auto;"></div>';
    rightViewer = DocumentViewer.create(document.getElementById('rightScroll'));
    document.getElementById('rightZoomIn').addEventListener('click', function () { rightViewer.zoomIn(); });
    document.getElementById('rightZoomOut').addEventListener('click', function () { rightViewer.zoomOut(); });
    document.getElementById('rightReset').addEventListener('click', function () { rightViewer.reset(); });
    document.getElementById('rightRotate').addEventListener('click', function () { rightViewer.rotate(); });
    return rightViewer;
}

async function renderRightFeedback(submissionId, label) {
    clearRightBody();
    var body = document.getElementById('rightBody');
    body.innerHTML = '<p style="color:#888;">Loading feedback…</p>';
    try {
        var res = await fetch('/teacher/assignment/' + REVIEW_ASSIGNMENT_ID + '/submission/' + submissionId + '/result');
        var data = await res.json();
        if (!data.success || !data.result) {
            body.innerHTML = '<p style="color:red;">Could not load feedback.</p>';
            return;
        }
        body.innerHTML = '';
        FeedbackRender.render(body, data.result, { idPrefix: 'rf' });
        if (label) document.getElementById('rightInfo').textContent = label;
    } catch (err) {
        body.innerHTML = '<p style="color:red;">Failed to load feedback.</p>';
    }
}

function renderRightAnswerKey() {
    var viewer = ensureRightDocViewer();
    viewer.loadFromUrl('/teacher/assignment/' + REVIEW_ASSIGNMENT_ID + '/answer-key');
    document.getElementById('rightInfo').textContent = 'Answer key';
}

function renderRightCompareWork(submissionId, label) {
    var viewer = ensureRightDocViewer();
    viewer.loadFromManifest(
        '/teacher/assignment/' + REVIEW_ASSIGNMENT_ID + '/submission/' + submissionId + '/script/manifest',
        function (idx) {
            return '/teacher/assignment/' + REVIEW_ASSIGNMENT_ID + '/submission/' + submissionId + '/script/page/' + idx;
        }
    );
    document.getElementById('rightInfo').textContent = label || '';
}

function handleModeChange() {
    var mode = document.getElementById('rightMode').value;
    rightMode = mode;
    var cmp = document.getElementById('compareControls');
    cmp.style.display = (mode === 'compare') ? 'flex' : 'none';
    if (mode === 'feedback') {
        renderRightFeedback(REVIEW_SUBMISSION_ID, null);
    } else if (mode === 'answer_key') {
        renderRightAnswerKey();
    } else if (mode === 'compare') {
        handleCompareChange();
    }
}

function handleCompareChange() {
    var studentSel = document.getElementById('compareStudent');
    var submissionId = studentSel.value;
    if (!submissionId) {
        clearRightBody();
        document.getElementById('rightBody').innerHTML = '<p style="color:#888;">Pick a student to compare.</p>';
        return;
    }
    var label = studentSel.options[studentSel.selectedIndex].text;
    var view = document.querySelector('input[name="compareView"]:checked').value;
    if (view === 'work') {
        renderRightCompareWork(submissionId, label + ' — Work');
    } else {
        renderRightFeedback(submissionId, label + ' — Feedback');
    }
}

document.getElementById('rightMode').addEventListener('change', handleModeChange);
document.getElementById('compareStudent').addEventListener('change', handleCompareChange);
document.querySelectorAll('input[name="compareView"]').forEach(function (r) {
    r.addEventListener('change', handleCompareChange);
});

// Initial render: AI Feedback for this student
renderRightFeedback(REVIEW_SUBMISSION_ID, null);
```

- [ ] **Step 2: Commit**

```bash
git add templates/review.html
git commit -m "feat(review): right-pane modes — AI feedback, answer key, compare student"
```

Include `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

---

## Task 6: Link student name in `teacher_detail.html` to the review page

**Files:**
- Modify: `templates/teacher_detail.html` (Name `<td>` in the student rows loop ~line 351)

- [ ] **Step 1: Replace the name cell**

In `templates/teacher_detail.html`, find the Name `<td>` around line 351. It currently reads (one line):

```jinja
<td style="font-weight:600;">{% if s.status == 'done' and s.submission_id %}<a href="/submit/{{ assignment.id }}/download/{{ s.submission_id }}" style="color:#667eea;text-decoration:none;" title="Download feedback report">{{ s.name }}</a>{% else %}{{ s.name }}{% endif %}</td>
```

Replace with:

```jinja
<td style="font-weight:600;">{% if s.status == 'done' and s.submission_id %}<a href="/teacher/assignment/{{ assignment.id }}/submission/{{ s.submission_id }}/review" target="_blank" rel="noopener" style="color:#667eea;text-decoration:none;" title="Open split-view review">{{ s.name }}</a>{% else %}{{ s.name }}{% endif %}</td>
```

- [ ] **Step 2: Commit**

```bash
git add templates/teacher_detail.html
git commit -m "feat(teacher): link student name to split-view review page"
```

Include `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.

---

## Final Verification

- [ ] **Step 1: Start the app**

```bash
python app.py
```

- [ ] **Step 2: End-to-end walkthrough**

1. Log in as teacher. Open an assignment detail page with at least two `done` submissions (where scripts include LaTeX or equations for bonus verification of MathJax).
2. Confirm `done` rows show the student name as a clickable link; non-`done` rows show plain text.
3. Click a `done` student name — a new tab opens with the review page.
4. Left pane renders the student's pages stacked vertically. If the script was a PDF, every internal PDF page is visible. If the script was image pages, each image renders.
5. Click `+` / `−` / Reset / Rotate. Confirm pages scale/rotate inside the left pane without the split shifting.
6. Drag the resizer. Confirm the split ratio changes, clamps at 20%/80%, and persists on reload (localStorage).
7. Right pane default is AI Feedback — confirm feedback renders with MathJax typeset.
8. Switch to Answer Key — answer key PDF/image renders with its own zoom/rotate toolbar.
9. Switch to Compare — pick a student; "Work" shows their script, "Feedback" shows their feedback. Switching students or toggles swaps content cleanly.
10. Close button closes the tab if it was opened from the assignment page; otherwise navigates back to the assignment.
11. Regression: the existing Feedback modal on `teacher_detail.html` still works correctly (shared renderer).
12. Regression: fresh student uploads and bulk marking still work unchanged.

---

## Self-Review (author)

**Spec coverage:**
- Linkification → Task 6.
- Route → Task 1 Step 5.
- Manifest endpoint → Task 1 Step 2.
- Per-page script endpoint → Task 1 Step 3.
- Answer-key endpoint → Task 1 Step 4.
- Split-screen layout → Task 4.
- Draggable resizer with localStorage → Task 4 Step 1 (Resizer JS).
- Left pane PDF.js + image → Task 3 + Task 4.
- Zoom / rotate controls → Task 3 + Task 4.
- Right pane AI Feedback → Task 5 (`renderRightFeedback`).
- Right pane Answer Key → Task 5 (`renderRightAnswerKey`).
- Right pane Compare (student picker + work/feedback toggle) → Task 5 (`handleCompareChange`).
- Shared feedback renderer module → Task 2.
- Shared document viewer module → Task 3.

**Placeholder scan:** no TBDs, no "implement later", no "similar to Task N". All code blocks complete.

**Type consistency:** `DocumentViewer.create()`, `FeedbackRender.render(el, result, options)`, `window.pdfjsLib`, `REVIEW_ASSIGNMENT_ID`, `REVIEW_SUBMISSION_ID`, element IDs `leftScroll` / `rightMode` / `rightBody` / `compareStudent` / `compareControls` — used identically across all tasks.
