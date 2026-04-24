// Shared feedback rendering used by:
//   - Teacher assignment detail page (feedback modal)
//   - Submission review split-view page
//
// Call FeedbackRender.render(containerEl, result, options)
//   options:
//     idPrefix       — namespace for generated element IDs (default 'fb')
//     editable       — when true, show an Edit Feedback button + modal
//     assignmentId   — required when editable
//     submissionId   — required when editable
//     onSave(result) — optional callback after a successful edit
//
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
            editable: !!options.editable,
            assignmentId: options.assignmentId || null,
            submissionId: options.submissionId || null,
            onSave: options.onSave || null,
            result: result,
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

        var editBar = '';
        if (state.editable && state.assignmentId && state.submissionId) {
            editBar = '<div class="fb-edit-bar">' +
                '<button type="button" id="' + prefix + 'EditBtn" class="fb-edit-btn">&#9998; Edit Feedback</button>' +
                '</div>';
        }

        var html =
            editBar +
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

        if (state.editable && state.assignmentId && state.submissionId) {
            var editBtn = document.getElementById(prefix + 'EditBtn');
            if (editBtn) editBtn.addEventListener('click', function () { openEditModal(state); });
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

    // ---- Edit modal (singleton attached to document.body on first use) ----

    var editState = { open: false, current: null };

    function ensureEditModal() {
        if (document.getElementById('frEditModal')) return;

        var css = document.createElement('style');
        css.textContent =
            '.fb-edit-bar { display: flex; justify-content: flex-end; margin-bottom: 8px; }' +
            '.fb-edit-btn { padding: 6px 12px; font-size: 12px; border: 1px solid #d0d0d0; background: white; border-radius: 6px; cursor: pointer; color: #2D2D2D; }' +
            '.fb-edit-btn:hover { background: #f0f0f0; }' +
            '.fr-edit-modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 2000; align-items: center; justify-content: center; backdrop-filter: blur(4px); }' +
            '.fr-edit-modal.active { display: flex; }' +
            '.fr-edit-modal-box { background: white; border-radius: 16px; padding: 24px; width: 92%; max-width: 680px; max-height: 88vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }' +
            '.fr-edit-modal-box h3 { font-size: 17px; font-weight: 700; color: #333; margin: 0 0 4px; }' +
            '.fr-edit-modal-box .sub { font-size: 12px; color: #888; margin-bottom: 14px; }' +
            '.fr-edit-section { margin-bottom: 14px; }' +
            '.fr-edit-label { font-size: 11px; font-weight: 700; color: #888; text-transform: uppercase; letter-spacing: 0.4px; margin-bottom: 4px; }' +
            '.fr-edit-section textarea { width: 100%; min-height: 70px; padding: 9px 11px; font-size: 13px; font-family: inherit; border: 1px solid #d0d0d0; border-radius: 8px; resize: vertical; box-sizing: border-box; }' +
            '.fr-edit-q { border: 1px solid #eee; border-radius: 10px; padding: 12px 14px; margin-bottom: 10px; background: #fafafa; }' +
            '.fr-edit-q-header { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }' +
            '.fr-edit-q-header strong { font-size: 13px; color: #2D2D2D; }' +
            '.fr-edit-q-marks input { width: 64px; padding: 5px 7px; font-size: 13px; border: 1px solid #d0d0d0; border-radius: 6px; text-align: center; }' +
            '.fr-edit-q-marks .sep { margin: 0 4px; color: #888; }' +
            '.fr-edit-q-field { margin-top: 8px; }' +
            '.fr-edit-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; }' +
            '.fr-edit-actions button { padding: 9px 18px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; border: none; }' +
            '.fr-edit-cancel { background: #f0f0f0; color: #555; }' +
            '.fr-edit-cancel:hover { background: #e0e0e0; }' +
            '.fr-edit-save { background: #667eea; color: white; }' +
            '.fr-edit-save:hover { background: #5a6fd6; }' +
            '.fr-edit-save:disabled { background: #bbb; cursor: not-allowed; }' +
            '.fr-edit-error { color: #c0392b; font-size: 12px; margin-top: 8px; min-height: 16px; }';
        document.head.appendChild(css);

        var modal = document.createElement('div');
        modal.className = 'fr-edit-modal';
        modal.id = 'frEditModal';
        modal.innerHTML =
            '<div class="fr-edit-modal-box" role="dialog" aria-modal="true">' +
                '<h3>Edit Feedback</h3>' +
                '<p class="sub">Overwrites the AI-generated feedback. Students and other teachers will see these edits.</p>' +
                '<div id="frEditBody"></div>' +
                '<div class="fr-edit-error" id="frEditError"></div>' +
                '<div class="fr-edit-actions">' +
                    '<button type="button" class="fr-edit-cancel" id="frEditCancel">Cancel</button>' +
                    '<button type="button" class="fr-edit-save" id="frEditSave">Save</button>' +
                '</div>' +
            '</div>';
        document.body.appendChild(modal);

        modal.addEventListener('click', function (e) {
            if (e.target === modal) closeEditModal();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && editState.open) closeEditModal();
        });
        document.getElementById('frEditCancel').addEventListener('click', closeEditModal);
        document.getElementById('frEditSave').addEventListener('click', saveEdits);
    }

    function openEditModal(state) {
        ensureEditModal();
        editState.open = true;
        editState.current = state;

        var body = document.getElementById('frEditBody');
        var overall = state.overall || '';
        var html =
            '<div class="fr-edit-section">' +
                '<div class="fr-edit-label">Overall Feedback</div>' +
                '<textarea id="frEditOverall">' + esc(overall) + '</textarea>' +
            '</div>' +
            '<div class="fr-edit-label" style="margin-top:6px;">Per-Question</div>';

        state.questions.forEach(function (q, i) {
            var isRubrics = state.assignType === 'rubrics';
            var header = isRubrics ? (q.criterion_name || ('Criterion ' + (q.question_num || i + 1))) : ('Question ' + (q.question_num || i + 1));
            var hasMarks = q.marks_awarded != null || q.marks_total != null;
            var marksInput = '';
            if (hasMarks) {
                marksInput = '<span class="fr-edit-q-marks">' +
                    'Marks: <input type="number" data-q="' + i + '" data-field="marks_awarded" value="' + esc(q.marks_awarded != null ? q.marks_awarded : '') + '" min="0" step="0.5">' +
                    '<span class="sep">/</span>' +
                    '<input type="number" data-q="' + i + '" data-field="marks_total" value="' + esc(q.marks_total != null ? q.marks_total : '') + '" min="0" step="0.5">' +
                '</span>';
            }
            html +=
                '<div class="fr-edit-q">' +
                    '<div class="fr-edit-q-header">' +
                        '<strong>' + esc(header) + '</strong>' + marksInput +
                    '</div>' +
                    '<div class="fr-edit-q-field">' +
                        '<div class="fr-edit-label">Feedback</div>' +
                        '<textarea data-q="' + i + '" data-field="feedback">' + esc(q.feedback || '') + '</textarea>' +
                    '</div>' +
                    '<div class="fr-edit-q-field">' +
                        '<div class="fr-edit-label">Suggested Improvement</div>' +
                        '<textarea data-q="' + i + '" data-field="improvement">' + esc(q.improvement || '') + '</textarea>' +
                    '</div>' +
                '</div>';
        });

        body.innerHTML = html;
        document.getElementById('frEditError').textContent = '';
        document.getElementById('frEditSave').disabled = false;
        document.getElementById('frEditSave').textContent = 'Save';
        document.getElementById('frEditModal').classList.add('active');
    }

    function closeEditModal() {
        var modal = document.getElementById('frEditModal');
        if (modal) modal.classList.remove('active');
        editState.open = false;
        editState.current = null;
    }

    async function saveEdits() {
        var state = editState.current;
        if (!state) return;
        var saveBtn = document.getElementById('frEditSave');
        var errEl = document.getElementById('frEditError');
        errEl.textContent = '';

        var overallEl = document.getElementById('frEditOverall');
        var body = document.getElementById('frEditBody');

        var payload = { overall_feedback: overallEl ? overallEl.value : '' };
        var editedQs = [];
        state.questions.forEach(function (q, i) {
            var qEdit = { question_num: q.question_num != null ? q.question_num : i + 1 };
            body.querySelectorAll('[data-q="' + i + '"]').forEach(function (el) {
                var field = el.getAttribute('data-field');
                if (field === 'marks_awarded' || field === 'marks_total') {
                    qEdit[field] = el.value === '' ? null : parseFloat(el.value);
                } else {
                    qEdit[field] = el.value;
                }
            });
            editedQs.push(qEdit);
        });
        payload.questions = editedQs;

        saveBtn.disabled = true;
        saveBtn.textContent = 'Saving…';
        try {
            var url = '/teacher/assignment/' + state.assignmentId + '/submission/' + state.submissionId + '/result';
            var res = await fetch(url, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            var data = await res.json();
            if (!res.ok || !data.success) {
                errEl.textContent = (data && data.error) || 'Save failed.';
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save';
                return;
            }
            var newResult = data.result;
            closeEditModal();
            // Re-render with the saved result so all views reflect the edit.
            render(state.containerEl, newResult, {
                idPrefix: state.prefix,
                editable: true,
                assignmentId: state.assignmentId,
                submissionId: state.submissionId,
                onSave: state.onSave,
            });
            if (state.onSave) {
                try { state.onSave(newResult); } catch (e) { /* ignore */ }
            }
        } catch (err) {
            errEl.textContent = 'Network error. Please try again.';
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save';
        }
    }

    global.FeedbackRender = { render: render };
})(window);
