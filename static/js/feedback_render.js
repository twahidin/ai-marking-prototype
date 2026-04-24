// Shared feedback rendering used by:
//   - Teacher assignment detail page (feedback modal)
//   - Submission review split-view page
//
// Call FeedbackRender.render(containerEl, result, options)
//   options:
//     idPrefix       — namespace for generated element IDs (default 'fb')
//     editable       — when true, fields render inline as textareas / number inputs
//                      with a Save button at the top. Saving PATCHes the result
//                      endpoint and re-renders with the returned merged result.
//     assignmentId   — required when editable
//     submissionId   — required when editable
//     onSave(result) — optional callback after a successful save
//
// Students and demo pages use their own render code paths and are not affected
// by the editable option.

(function (global) {
    var STATUS_LABELS = { correct: 'Correct', partially_correct: 'Partially Correct', incorrect: 'Incorrect' };

    function esc(s) {
        var d = document.createElement('div');
        d.textContent = s == null ? '' : String(s);
        return d.innerHTML;
    }

    function deriveStatus(marksAwarded, marksTotal) {
        if (marksAwarded == null || marksTotal == null || marksTotal <= 0) return null;
        var ratio = marksAwarded / marksTotal;
        if (ratio >= 0.99) return 'correct';
        if (ratio > 0) return 'partially_correct';
        return 'incorrect';
    }

    function injectStylesOnce() {
        if (document.getElementById('fb-inline-edit-styles')) return;
        var css = document.createElement('style');
        css.id = 'fb-inline-edit-styles';
        css.textContent =
            '.fb-edit-toolbar { display: flex; justify-content: flex-end; align-items: center; gap: 10px; margin-bottom: 10px; }' +
            '.fb-save-btn { padding: 8px 16px; background: #667eea; color: white; border: none; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; }' +
            '.fb-save-btn:hover { background: #5a6fd6; }' +
            '.fb-save-btn:disabled { background: #bbb; cursor: not-allowed; }' +
            '.fb-save-status { font-size: 12px; color: #888; }' +
            '.fb-save-status.error { color: #c0392b; }' +
            '.fb-save-status.success { color: #28a745; }' +
            '.fb-edit-textarea { width: 100%; min-height: 130px; padding: 10px 12px; font-size: 14px; line-height: 1.5; font-family: inherit; border: 1px solid #d0d0d0; border-radius: 8px; resize: vertical; box-sizing: border-box; background: white; }' +
            '.fb-edit-textarea.overall { min-height: 110px; }' +
            '.fb-edit-textarea:focus { outline: none; border-color: #667eea; box-shadow: 0 0 0 2px rgba(102,126,234,0.15); }' +
            '.fb-edit-marks { display: inline-flex; align-items: center; gap: 4px; margin-left: 12px; font-size: 12px; color: #555; }' +
            '.fb-edit-marks input { width: 68px; padding: 5px 8px; font-size: 13px; border: 1px solid #d0d0d0; border-radius: 6px; text-align: center; }' +
            '.fb-edit-marks input:focus { outline: none; border-color: #667eea; }' +
            '.fb-edit-marks .sep { color: #888; }' +
            /* Status-tinted question cards */
            '.fb-q-card.status-correct { background: #f2f9f4; border-color: #b5dcc3; }' +
            '.fb-q-card.status-partially_correct { background: #fffbeb; border-color: #ecd28c; }' +
            '.fb-q-card.status-incorrect { background: #fdf3f3; border-color: #e8b5b8; }' +
            '.fb-q-card.status-correct .fb-q-card-header { background: #e7f3eb; }' +
            '.fb-q-card.status-partially_correct .fb-q-card-header { background: #fff4d6; }' +
            '.fb-q-card.status-incorrect .fb-q-card-header { background: #fbe3e3; }';
        document.head.appendChild(css);
    }

    function render(containerEl, result, options) {
        injectStylesOnce();
        options = options || {};
        var prefix = options.idPrefix || 'fb';
        var state = {
            questions: (result.questions || []).map(function (q) { return Object.assign({}, q); }),
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
            dirty: false,
        };
        if (state.editable && (!state.assignmentId || !state.submissionId)) {
            state.editable = false;
        }

        // Ensure a derived status exists on every question so the UI color-codes
        // correctly even when the AI left status off and only set marks.
        state.questions.forEach(function (q) {
            var d = deriveStatus(q.marks_awarded, q.marks_total);
            if (d) q.status = d;
            if (!q.status) q.status = 'incorrect';
        });

        renderShell(state);
    }

    function renderShell(state) {
        var prefix = state.prefix;

        var toolbar = '';
        if (state.editable) {
            toolbar =
                '<div class="fb-edit-toolbar">' +
                    '<span class="fb-save-status" id="' + prefix + 'SaveStatus"></span>' +
                    '<button type="button" class="fb-save-btn" id="' + prefix + 'SaveBtn" disabled>Save</button>' +
                '</div>';
        }

        var summary = summaryBarHtml(state);

        var dots = '';
        state.questions.forEach(function (q, i) {
            var s = q.status || 'incorrect';
            var label = (state.assignType === 'rubrics' && q.criterion_name)
                ? q.criterion_name.substring(0, 2).toUpperCase() : (q.question_num || i + 1);
            dots += '<div class="fb-q-dot ' + esc(s) + (i === 0 ? ' active' : '') +
                    '" data-q="' + i + '">' + esc(String(label)) + '</div>';
        });

        var overallHtml = '';
        if (state.editable) {
            overallHtml =
                '<div class="fb-overall-box">' +
                    '<h4>Overall Feedback</h4>' +
                    '<textarea class="fb-edit-textarea overall" id="' + prefix + 'OverallInput" placeholder="Overall feedback…">' +
                        esc(state.overall) +
                    '</textarea>' +
                '</div>';
        } else if (state.overall) {
            overallHtml = '<div class="fb-overall-box"><h4>Overall Feedback</h4><p>' + esc(state.overall) + '</p></div>';
        }

        var extras = '';
        if (state.recommended.length) {
            extras += '<div class="fb-overall-box"><h4>Recommended Actions</h4><ul>';
            state.recommended.forEach(function (a) { extras += '<li>' + esc(a) + '</li>'; });
            extras += '</ul></div>';
        }
        if (state.errors.length) {
            extras += '<div class="fb-overall-box"><h4>Line-by-Line Errors (' + state.errors.length + ')</h4><div class="fb-errors-list">';
            state.errors.forEach(function (e) {
                extras += '<div class="fb-error-item"><strong>' + esc((e.type || 'error').toUpperCase()) + '</strong>';
                if (e.location) extras += ' <span style="color:#999;">' + esc(e.location) + '</span>';
                extras += '<div style="margin-top:4px;"><span style="text-decoration:line-through;color:#dc3545;">' + esc(e.original || '') + '</span> &rarr; <span style="color:#28a745;">' + esc(e.correction || '') + '</span></div></div>';
            });
            extras += '</div></div>';
        }

        var html =
            toolbar +
            '<div class="fb-summary-bar" id="' + prefix + 'SummaryBar">' + summary + '</div>' +
            (state.questions.length ? (
                '<div class="fb-q-dots" id="' + prefix + 'QDots">' + dots + '</div>' +
                '<div class="fb-q-nav">' +
                    '<button id="' + prefix + 'PrevBtn" type="button">&larr; Prev</button>' +
                    '<span id="' + prefix + 'QNavInfo"></span>' +
                    '<button id="' + prefix + 'NextBtn" type="button">Next &rarr;</button>' +
                '</div>' +
                '<div id="' + prefix + 'QCardContainer"></div>'
            ) : '<p style="color:#888;font-style:italic;">No per-question feedback.</p>') +
            overallHtml +
            extras;

        state.containerEl.innerHTML = html;

        if (state.questions.length) {
            bindNav(state);
            renderQuestion(state);
        }

        if (state.editable) {
            var overallEl = document.getElementById(prefix + 'OverallInput');
            if (overallEl) {
                overallEl.addEventListener('input', function () {
                    state.overall = overallEl.value;
                    markDirty(state);
                });
            }
            var saveBtn = document.getElementById(prefix + 'SaveBtn');
            if (saveBtn) saveBtn.addEventListener('click', function () { save(state); });
        }

        if (window.MathJax && MathJax.typesetPromise) {
            MathJax.typesetPromise([state.containerEl]).catch(function () {});
        }
    }

    function summaryBarHtml(state) {
        var hasMarks = state.questions.some(function (q) { return q.marks_awarded != null; });
        if (hasMarks) {
            var ta = 0, tp = 0;
            state.questions.forEach(function (q) { ta += (q.marks_awarded || 0); tp += (q.marks_total || 0); });
            var pct = tp > 0 ? Math.round(ta / tp * 100) : 0;
            return '<div class="fb-summary-item fb-summary-marks">' + ta + ' / ' + tp + ' marks</div>' +
                   '<div class="fb-summary-item fb-summary-marks">' + pct + '%</div>';
        }
        var c = { correct: 0, partially_correct: 0, incorrect: 0 };
        state.questions.forEach(function (q) { if (c.hasOwnProperty(q.status)) c[q.status]++; });
        return '<div class="fb-summary-item fb-summary-correct">' + c.correct + ' Correct</div>' +
               '<div class="fb-summary-item fb-summary-partial">' + c.partially_correct + ' Partial</div>' +
               '<div class="fb-summary-item fb-summary-incorrect">' + c.incorrect + ' Incorrect</div>';
    }

    function refreshSummary(state) {
        var bar = document.getElementById(state.prefix + 'SummaryBar');
        if (bar) bar.innerHTML = summaryBarHtml(state);
    }

    function refreshDots(state) {
        var dotEls = state.containerEl.querySelectorAll('.fb-q-dot');
        dotEls.forEach(function (el, i) {
            var q = state.questions[i];
            ['correct', 'partially_correct', 'incorrect'].forEach(function (c) { el.classList.remove(c); });
            if (q && q.status) el.classList.add(q.status);
        });
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

        var isRubrics = state.assignType === 'rubrics';
        var headerLabel = isRubrics ? (q.criterion_name || 'Criterion ' + (q.question_num || state.currentQ + 1)) : 'Question ' + (q.question_num || state.currentQ + 1);
        var ansLabel = isRubrics ? 'Assessment' : "Student's Answer";
        var refLabel = isRubrics ? 'Band Descriptor' : 'Correct Answer';
        var bandInfo = (isRubrics && q.band) ? ' <span style="font-size:12px;color:#667eea;font-weight:600;">(' + esc(q.band) + ')</span>' : '';

        var statusCls = q.status || 'incorrect';
        var badge = questionBadgeHtml(q, statusCls);

        var marksEdit = '';
        if (state.editable) {
            marksEdit =
                '<span class="fb-edit-marks">' +
                    'Marks: ' +
                    '<input type="number" id="' + state.prefix + 'MA" step="0.5" min="0" value="' + esc(q.marks_awarded != null ? q.marks_awarded : '') + '">' +
                    '<span class="sep">/</span>' +
                    '<input type="number" id="' + state.prefix + 'MT" step="0.5" min="0" value="' + esc(q.marks_total != null ? q.marks_total : '') + '">' +
                '</span>';
        }

        var fbBlock, impBlock;
        if (state.editable) {
            fbBlock = '<div class="fb-q-field"><div class="fb-q-field-label">Feedback</div>' +
                '<textarea class="fb-edit-textarea" id="' + state.prefix + 'Feedback" placeholder="Feedback… (LaTeX in $...$ is rendered for students)">' + esc(q.feedback || '') + '</textarea></div>';
            impBlock = '<div class="fb-q-field"><div class="fb-q-field-label">Suggested Improvement</div>' +
                '<textarea class="fb-edit-textarea" id="' + state.prefix + 'Improvement" placeholder="Suggested improvement…">' + esc(q.improvement || '') + '</textarea></div>';
        } else {
            fbBlock = q.feedback ? '<div class="fb-q-field"><div class="fb-q-field-label">Feedback</div><div class="fb-q-field-value feedback">' + esc(q.feedback) + '</div></div>' : '';
            impBlock = q.improvement ? '<div class="fb-q-field"><div class="fb-q-field-label">Suggested Improvement</div><div class="fb-q-field-value improvement">' + esc(q.improvement) + '</div></div>' : '';
        }

        var cardClass = 'fb-q-card status-' + statusCls;
        var html = '<div class="' + cardClass + '" id="' + state.prefix + 'QCard">' +
            '<div class="fb-q-card-header"><span class="fb-q-num">' + esc(headerLabel) + bandInfo + '</span>' +
                '<span id="' + state.prefix + 'StatusBadgeWrap">' + badge + '</span>' +
                marksEdit +
            '</div>' +
            '<div class="fb-q-card-body">' +
                '<div class="fb-q-field"><div class="fb-q-field-label">' + ansLabel + '</div><div class="fb-q-field-value">' + esc(q.student_answer || 'N/A') + '</div></div>' +
                '<div class="fb-q-field"><div class="fb-q-field-label">' + refLabel + '</div><div class="fb-q-field-value">' + esc(q.correct_answer || 'N/A') + '</div></div>' +
                fbBlock +
                impBlock +
            '</div>' +
        '</div>';

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

        if (state.editable) {
            wireQuestionEditors(state);
        }

        if (window.MathJax && MathJax.typesetPromise && container) {
            MathJax.typesetPromise([container]).catch(function () {});
        }
    }

    function questionBadgeHtml(q, statusCls) {
        var label = STATUS_LABELS[statusCls] || statusCls;
        if (q.marks_awarded != null && q.marks_total != null) {
            return '<span class="fb-status-badge ' + esc(statusCls) + '">' + esc(String(q.marks_awarded)) + '/' + esc(String(q.marks_total)) + '</span>';
        }
        return '<span class="fb-status-badge ' + esc(statusCls) + '">' + esc(label) + '</span>';
    }

    function wireQuestionEditors(state) {
        var q = state.questions[state.currentQ];
        var ma = document.getElementById(state.prefix + 'MA');
        var mt = document.getElementById(state.prefix + 'MT');
        var fbEl = document.getElementById(state.prefix + 'Feedback');
        var impEl = document.getElementById(state.prefix + 'Improvement');

        function onMarksChange() {
            var vMa = ma.value === '' ? null : parseFloat(ma.value);
            var vMt = mt.value === '' ? null : parseFloat(mt.value);
            q.marks_awarded = (vMa != null && !isNaN(vMa)) ? vMa : null;
            q.marks_total = (vMt != null && !isNaN(vMt)) ? vMt : null;
            var derived = deriveStatus(q.marks_awarded, q.marks_total);
            if (derived) q.status = derived;
            // Refresh card class, badge, dots, summary
            var card = document.getElementById(state.prefix + 'QCard');
            if (card) {
                card.classList.remove('status-correct', 'status-partially_correct', 'status-incorrect');
                card.classList.add('status-' + (q.status || 'incorrect'));
            }
            var badgeWrap = document.getElementById(state.prefix + 'StatusBadgeWrap');
            if (badgeWrap) badgeWrap.innerHTML = questionBadgeHtml(q, q.status || 'incorrect');
            refreshDots(state);
            refreshSummary(state);
            markDirty(state);
        }

        if (ma) ma.addEventListener('input', onMarksChange);
        if (mt) mt.addEventListener('input', onMarksChange);
        if (fbEl) fbEl.addEventListener('input', function () { q.feedback = fbEl.value; markDirty(state); });
        if (impEl) impEl.addEventListener('input', function () { q.improvement = impEl.value; markDirty(state); });
    }

    function markDirty(state) {
        state.dirty = true;
        var btn = document.getElementById(state.prefix + 'SaveBtn');
        if (btn) btn.disabled = false;
        var st = document.getElementById(state.prefix + 'SaveStatus');
        if (st) { st.textContent = 'Unsaved changes'; st.className = 'fb-save-status'; }
    }

    async function save(state) {
        var btn = document.getElementById(state.prefix + 'SaveBtn');
        var st = document.getElementById(state.prefix + 'SaveStatus');
        if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
        if (st) { st.textContent = ''; st.className = 'fb-save-status'; }

        var payload = {
            overall_feedback: state.overall || '',
            questions: state.questions.map(function (q, i) {
                return {
                    question_num: q.question_num != null ? q.question_num : (i + 1),
                    marks_awarded: q.marks_awarded,
                    marks_total: q.marks_total,
                    feedback: q.feedback || '',
                    improvement: q.improvement || '',
                };
            }),
        };

        try {
            var url = '/teacher/assignment/' + state.assignmentId + '/submission/' + state.submissionId + '/result';
            var res = await fetch(url, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            var data = await res.json();
            if (!res.ok || !data.success) {
                if (st) { st.textContent = (data && data.error) || 'Save failed.'; st.className = 'fb-save-status error'; }
                if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
                return;
            }
            // Re-render with the merged result so the view reflects the authoritative state.
            render(state.containerEl, data.result, {
                idPrefix: state.prefix,
                editable: true,
                assignmentId: state.assignmentId,
                submissionId: state.submissionId,
                onSave: state.onSave,
            });
            var newSt = document.getElementById(state.prefix + 'SaveStatus');
            if (newSt) { newSt.textContent = 'Saved'; newSt.className = 'fb-save-status success'; }
            if (state.onSave) { try { state.onSave(data.result); } catch (e) { /* ignore */ } }
        } catch (err) {
            if (st) { st.textContent = 'Network error. Please try again.'; st.className = 'fb-save-status error'; }
            if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
        }
    }

    global.FeedbackRender = { render: render };
})(window);
