// Shared feedback rendering used by:
//   - Teacher assignment detail page (feedback modal)
//   - Submission review split-view page
//
// Call FeedbackRender.render(containerEl, result, options)
//   options:
//     idPrefix       — namespace for generated element IDs (default 'fb')
//     editable       — when true, editable fields become interactive on click
//                      (click a field → textarea/input; blur → auto-save via
//                      PATCH; display reverts with the new value). Marks badge
//                      updates status + card color live.
//     assignmentId   — required when editable
//     submissionId   — required when editable
//     onSave(result) — optional callback after a successful save

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
            '.fb-editable { cursor: text; position: relative; transition: box-shadow 0.15s; }' +
            '.fb-editable:hover { box-shadow: 0 0 0 2px rgba(102,126,234,0.25); }' +
            '.fb-editable .edit-hint { display: none; position: absolute; top: 4px; right: 6px; font-size: 10px; color: #8a8db2; font-weight: 500; pointer-events: none; }' +
            '.fb-editable:hover .edit-hint { display: inline; }' +
            '.fb-edit-textarea { width: 100%; min-height: 130px; padding: 10px 12px; font-size: 14px; line-height: 1.5; font-family: inherit; border: 1px solid #667eea; border-radius: 8px; resize: vertical; box-sizing: border-box; background: white; outline: none; box-shadow: 0 0 0 2px rgba(102,126,234,0.2); }' +
            '.fb-edit-textarea.overall { min-height: 110px; }' +
            '.fb-edit-textarea.small { min-height: 80px; }' +
            '.fb-marks-edit { display: inline-flex; align-items: center; gap: 4px; }' +
            '.fb-marks-edit input { width: 60px; padding: 4px 6px; font-size: 12px; border: 1px solid #667eea; border-radius: 5px; text-align: center; outline: none; box-shadow: 0 0 0 2px rgba(102,126,234,0.2); }' +
            '.fb-marks-edit .sep { margin: 0 2px; color: #888; }' +
            '.fb-save-toast { position: fixed; bottom: 24px; right: 24px; padding: 10px 16px; border-radius: 8px; font-size: 13px; font-weight: 600; color: white; z-index: 3000; box-shadow: 0 4px 14px rgba(0,0,0,0.25); opacity: 0; transition: opacity 0.2s; pointer-events: none; }' +
            '.fb-save-toast.show { opacity: 1; }' +
            '.fb-save-toast.success { background: #28a745; }' +
            '.fb-save-toast.error { background: #c0392b; }' +
            '.fb-placeholder { color: #bbb; font-style: italic; }' +
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

        var questions = (result.questions || []).map(function (q) { return Object.assign({}, q); });
        // Mode: explicit scoringMode option wins; otherwise infer from data —
        // any question with marks set implies marks mode.
        var isMarksMode;
        if (options.scoringMode === 'marks') isMarksMode = true;
        else if (options.scoringMode === 'status') isMarksMode = false;
        else isMarksMode = questions.some(function (q) { return q.marks_awarded != null || q.marks_total != null; });

        var state = {
            questions: questions,
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
            currentTeacherId: options.currentTeacherId || null,
            onSave: options.onSave || null,
            isMarksMode: isMarksMode,
            textEditMeta: options.textEditMeta || {},
        };
        if (state.editable && (!state.assignmentId || !state.submissionId)) {
            state.editable = false;
        }

        // Ensure every question has a status for UI color-coding.
        state.questions.forEach(function (q) {
            var d = deriveStatus(q.marks_awarded, q.marks_total);
            if (d) q.status = d;
            if (!q.status) q.status = 'incorrect';
        });

        renderShell(state);
    }

    function renderShell(state) {
        var prefix = state.prefix;
        var summary = summaryBarHtml(state);

        var dots = '';
        state.questions.forEach(function (q, i) {
            var s = q.status || 'incorrect';
            var label = (state.assignType === 'rubrics' && q.criterion_name)
                ? q.criterion_name.substring(0, 2).toUpperCase() : (q.question_num || i + 1);
            dots += '<div class="fb-q-dot ' + esc(s) + (i === state.currentQ ? ' active' : '') +
                    '" data-q="' + i + '">' + esc(String(label)) + '</div>';
        });

        var overallHtml = overallSectionHtml(state);
        var extras = extrasHtml(state);

        var html =
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

        if (!state.containerEl || !state.containerEl.isConnected) return;
        try { state.containerEl.innerHTML = html; }
        catch (e) { return; }

        if (state.questions.length) {
            bindNav(state);
            renderQuestion(state);
        }

        if (state.editable) {
            attachOverallEditHandler(state);
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

    function overallSectionHtml(state) {
        var prefix = state.prefix;
        if (state.editable) {
            var content = state.overall
                ? esc(state.overall)
                : '<span class="fb-placeholder">Click to add overall feedback…</span>';
            return '<div class="fb-overall-box">' +
                '<h4>Overall Feedback <small style="color:#bbb;font-weight:400;">(click to edit)</small></h4>' +
                '<p class="fb-editable" id="' + prefix + 'OverallView" data-field="overall">' +
                    content +
                    '<span class="edit-hint">✎ edit</span>' +
                '</p>' +
            '</div>';
        }
        if (state.overall) {
            return '<div class="fb-overall-box"><h4>Overall Feedback</h4><p>' + esc(state.overall) + '</p></div>';
        }
        return '';
    }

    function extrasHtml(state) {
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
        return extras;
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
        var badge = questionBadgeHtml(q, statusCls, state.editable, state.isMarksMode);

        var fbBlock, impBlock;
        if (state.editable) {
            var fbContent = q.feedback ? esc(q.feedback) : '<span class="fb-placeholder">Click to add feedback…</span>';
            var impContent = q.improvement ? esc(q.improvement) : '<span class="fb-placeholder">Click to add suggested improvement…</span>';
            fbBlock = '<div class="fb-q-field"><div class="fb-q-field-label">Feedback <small style="color:#bbb;font-weight:400;">(click to edit)</small></div>' +
                '<div class="fb-q-field-value feedback fb-editable" data-field="feedback">' + fbContent +
                '<span class="edit-hint">✎ edit</span></div></div>';
            impBlock = '<div class="fb-q-field"><div class="fb-q-field-label">Suggested Improvement <small style="color:#bbb;font-weight:400;">(click to edit)</small></div>' +
                '<div class="fb-q-field-value improvement fb-editable" data-field="improvement">' + impContent +
                '<span class="edit-hint">✎ edit</span></div></div>';
        } else {
            fbBlock = q.feedback ? '<div class="fb-q-field"><div class="fb-q-field-label">Feedback</div><div class="fb-q-field-value feedback">' + esc(q.feedback) + '</div></div>' : '';
            impBlock = q.improvement ? '<div class="fb-q-field"><div class="fb-q-field-label">Suggested Improvement</div><div class="fb-q-field-value improvement">' + esc(q.improvement) + '</div></div>' : '';
        }

        // Category line: muted, contenteditable annotation in the form
        // [theme_key] specific_label. Hidden completely when categorisation
        // hasn't run for this criterion (no theme_key set).
        var catBlock = '';
        if (q.theme_key) {
            var corrMark = q.theme_key_corrected ?
                '<span class="fb-cat-corrected" style="color:#8a8db2;margin-left:6px;font-style:normal;" title="Teacher-corrected">✎</span>' : '';
            var labelTxt = q.specific_label ? esc(q.specific_label) : '';
            if (state.editable) {
                catBlock = '<div class="fb-q-cat-row" ' +
                    'title="Valid theme keys: reasoning_gap | evidence_handling | language_expression | procedural_error | content_gap" ' +
                    'style="margin-bottom:4px; font-size:12px; color:#7a7f8c; line-height:1.5;">' +
                    '<span class="fb-cat-line" data-field="category" contenteditable="true" spellcheck="false" ' +
                        'style="outline:none; padding:1px 4px; border-radius:3px; cursor:text; font-family:ui-monospace, SFMono-Regular, Menlo, monospace;">' +
                        '[' + esc(q.theme_key) + '] ' + labelTxt +
                    '</span>' +
                    corrMark +
                    '<span class="fb-cat-help" tabindex="0" ' +
                        'title="Valid theme keys: reasoning_gap | evidence_handling | language_expression | procedural_error | content_gap" ' +
                        'style="margin-left:8px; padding:0 5px; border:1px solid #c5cbe8; border-radius:50%; font-size:10px; color:#7a7f8c; cursor:help; user-select:none;">' +
                        'i' +
                    '</span>' +
                '</div>';
            } else {
                catBlock = '<div class="fb-q-cat-row" style="margin-bottom:4px; font-size:12px; color:#7a7f8c; line-height:1.5;">' +
                    '<span style="font-family:ui-monospace, SFMono-Regular, Menlo, monospace;">' +
                        '[' + esc(q.theme_key) + '] ' + labelTxt +
                    '</span>' +
                    corrMark +
                '</div>';
            }
        }

        var cardClass = 'fb-q-card status-' + statusCls;
        var html = '<div class="' + cardClass + '" id="' + state.prefix + 'QCard">' +
            '<div class="fb-q-card-header"><span class="fb-q-num">' + esc(headerLabel) + bandInfo + '</span>' +
                '<span id="' + state.prefix + 'StatusBadgeWrap">' + badge + '</span>' +
            '</div>' +
            '<div class="fb-q-card-body">' +
                '<div class="fb-q-field"><div class="fb-q-field-label">' + ansLabel + '</div><div class="fb-q-field-value">' + esc(q.student_answer || 'N/A') + '</div></div>' +
                '<div class="fb-q-field"><div class="fb-q-field-label">' + refLabel + '</div><div class="fb-q-field-value">' + esc(q.correct_answer || 'N/A') + '</div></div>' +
                catBlock +
                fbBlock +
                impBlock +
            '</div>' +
        '</div>';

        var container = document.getElementById(state.prefix + 'QCardContainer');
        if (!container || !container.isConnected) return;
        try { container.innerHTML = html; }
        catch (e) { return; }

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
            attachQuestionEditHandlers(state);
        }

        // Initial-load: render tags for criteria that already have edits.
        if (state.textEditMeta) {
            var q = state.questions[state.currentQ];
            if (q) {
                var qKey = String(q.question_num != null ? q.question_num : (state.currentQ + 1));
                var qMeta = state.textEditMeta[qKey] || {};
                if (qMeta.feedback)    renderEditTag(state, state.currentQ, 'feedback',    qMeta.feedback);
                if (qMeta.improvement) renderEditTag(state, state.currentQ, 'improvement', qMeta.improvement);
            }
        }

        if (window.MathJax && MathJax.typesetPromise && container) {
            MathJax.typesetPromise([container]).catch(function () {});
        }
    }

    function questionBadgeHtml(q, statusCls, editable, isMarksMode) {
        var label = STATUS_LABELS[statusCls] || statusCls;

        if (isMarksMode) {
            var maStr = q.marks_awarded != null ? String(q.marks_awarded) : '—';
            var mtStr = q.marks_total != null ? String(q.marks_total) : '—';
            var cls = 'fb-status-badge ' + esc(statusCls);
            if (editable) cls += ' fb-editable';
            var attr = editable ? ' data-field="marks" title="Click to edit marks"' : '';
            var hint = editable ? '<span class="edit-hint" style="color:inherit;opacity:0.7;"> ✎</span>' : '';
            return '<span class="' + cls + '"' + attr + '>' + esc(maStr) + '/' + esc(mtStr) + hint + '</span>';
        }

        // Status mode: label + cycle affordance.
        var cls2 = 'fb-status-badge ' + esc(statusCls);
        if (editable) cls2 += ' fb-editable';
        var attr2 = editable ? ' data-field="status" title="Click to cycle status"' : '';
        var hint2 = editable ? '<span class="edit-hint" style="color:inherit;opacity:0.7;"> ↻</span>' : '';
        return '<span class="' + cls2 + '"' + attr2 + '>' + esc(label) + hint2 + '</span>';
    }

    // ---- Click-to-edit handlers ----

    function attachOverallEditHandler(state) {
        var el = document.getElementById(state.prefix + 'OverallView');
        if (!el) return;
        el.addEventListener('click', function () { beginTextEdit(state, el, 'overall'); });
    }

    function attachQuestionEditHandlers(state) {
        var card = document.getElementById(state.prefix + 'QCard');
        if (!card) return;
        var marksBadge = card.querySelector('[data-field="marks"]');
        if (marksBadge) {
            var marksClick = function (e) {
                // If already editing, a bubbled click from inside the widget must
                // not re-enter edit mode — that would detach the live inputs
                // mid-edit and break the blur/save flow.
                if (marksBadge.dataset.editing === '1') return;
                beginMarksEdit(state, marksBadge);
            };
            marksBadge.addEventListener('click', marksClick);
        }
        var statusBadge = card.querySelector('[data-field="status"]');
        if (statusBadge) {
            statusBadge.addEventListener('click', function () { cycleStatus(state, statusBadge); });
        }
        card.querySelectorAll('[data-field="feedback"], [data-field="improvement"]').forEach(function (el) {
            el.addEventListener('click', function () {
                if (el.dataset.editing === '1') return;
                beginTextEdit(state, el, el.getAttribute('data-field'));
            });
        });
        attachCategoryLineHandler(state, card);
    }

    function attachCategoryLineHandler(state, card) {
        var span = card.querySelector('.fb-cat-line');
        if (!span || span.dataset.bound === '1') return;
        span.dataset.bound = '1';

        var originalText = (span.textContent || '').trim();

        // Single-line: Enter blurs instead of inserting a newline.
        span.addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter') {
                ev.preventDefault();
                span.blur();
            } else if (ev.key === 'Escape') {
                span.textContent = originalText;
                span.blur();
            }
        });
        // Strip rich text on paste.
        span.addEventListener('paste', function (ev) {
            ev.preventDefault();
            var text = ((ev.clipboardData || window.clipboardData) || { getData: function () { return ''; } }).getData('text') || '';
            text = text.replace(/[\r\n]+/g, ' ');
            try { document.execCommand('insertText', false, text); } catch (e) { /* ignore */ }
        });
        span.addEventListener('blur', function () {
            var current = (span.textContent || '').trim();
            if (current === originalText) return;
            // Parse [theme_key] specific_label
            var m = current.match(/^\s*\[([^\]]*)\]\s*(.*)$/);
            if (!m) {
                span.textContent = originalText;  // malformed → silent revert
                return;
            }
            var newTheme = (m[1] || '').trim();
            var newLabel = (m[2] || '').trim();
            var q = state.questions[state.currentQ];
            if (!q) return;
            var savedQNum = q.question_num != null ? q.question_num : (state.currentQ + 1);

            patchResult(state, {
                questions: [{
                    question_num: savedQNum,
                    theme_key: newTheme,
                    specific_label: newLabel,
                }]
            }).then(function (data) {
                if (!data || !data.success) {
                    span.textContent = originalText;
                    return;
                }
                var newQ = ((data.result && data.result.questions) || []).find(function (qq) {
                    return String(qq.question_num) === String(savedQNum);
                });
                if (!newQ) {
                    span.textContent = originalText;
                    return;
                }
                var serverTk = newQ.theme_key || '';
                var serverLabel = newQ.specific_label || '';
                // Server may have silently reverted (invalid theme_key); reflect
                // whatever the server returned, not what the user typed.
                span.textContent = '[' + serverTk + '] ' + serverLabel;
                originalText = span.textContent.trim();
                state.questions[state.currentQ].theme_key = serverTk;
                state.questions[state.currentQ].specific_label = serverLabel;
                state.questions[state.currentQ].theme_key_corrected = !!newQ.theme_key_corrected;
                if (newQ.theme_key_corrected) {
                    var row = span.parentElement;
                    if (row && !row.querySelector('.fb-cat-corrected')) {
                        var mark = document.createElement('span');
                        mark.className = 'fb-cat-corrected';
                        mark.style.cssText = 'color:#8a8db2;margin-left:6px;';
                        mark.title = 'Teacher-corrected';
                        mark.textContent = '✎';
                        row.appendChild(mark);
                    }
                }
            }).catch(function () {
                span.textContent = originalText;
            });
        });
    }

    function beginTextEdit(state, el, field) {
        var q = state.questions[state.currentQ];
        var currentValue = field === 'overall' ? (state.overall || '') : (q[field] || '');

        el.dataset.editing = '1';

        var textarea = document.createElement('textarea');
        textarea.className = 'fb-edit-textarea' + (field === 'overall' ? ' overall' : '');
        textarea.value = currentValue;
        // Stop click inside the live textarea from bubbling up to the wrapping
        // element's click-to-edit handler (which would otherwise re-enter edit
        // and detach this textarea mid-edit).
        textarea.addEventListener('click', function (e) { e.stopPropagation(); });
        textarea.addEventListener('mousedown', function (e) { e.stopPropagation(); });

        el.innerHTML = '';
        el.appendChild(textarea);
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);

        // LaTeX autocomplete: typing a backslash pops a keyboard-driven menu
        // of common math commands (ArrowUp/Down to navigate, Tab/Enter to
        // insert, Escape to dismiss). Attaches only when the module is loaded.
        if (global.LatexAutocomplete && global.LatexAutocomplete.attach) {
            global.LatexAutocomplete.attach(textarea);
        }

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
            // preventDefault on mousedown stops focus transfer to the checkbox, so
            // the textarea keeps focus and doesn't blur (which would save before
            // the checkbox state could be captured). The click still fires and
            // toggles the checkbox normally — only the focus shift is blocked.
            wrap.addEventListener('mousedown', function (ev) { ev.preventDefault(); });
            cb.addEventListener('mousedown', function (ev) { ev.preventDefault(); });
            // Manually toggle on every click (direct-on-box AND label-text both
            // bubble to wrap). preventDefault suppresses the label's implicit
            // re-toggle that would otherwise double-flip when clicking the box.
            wrap.addEventListener('click', function (ev) {
                ev.preventDefault();
                cb.checked = !cb.checked;
            });
        }

        var submitted = false;
        function commit() {
            if (submitted) return;
            submitted = true;
            var newVal = textarea.value;
            var calibrate = !!(cb && cb.checked);
            if (newVal === currentValue) {
                // No change → just revert display
                if (field === 'overall') renderShell(state);
                else renderQuestion(state);
                return;
            }
            saveTextField(state, field, newVal, calibrate);
        }
        function cancel() {
            if (submitted) return;
            submitted = true;
            if (field === 'overall') renderShell(state);
            else renderQuestion(state);
        }

        textarea.addEventListener('blur', commit);
        textarea.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') { e.preventDefault(); textarea.removeEventListener('blur', commit); cancel(); }
        });
    }

    function beginMarksEdit(state, el) {
        var q = state.questions[state.currentQ];
        var initMa = q.marks_awarded != null ? q.marks_awarded : '';
        var initMt = q.marks_total != null ? q.marks_total : '';

        el.dataset.editing = '1';
        el.classList.remove('fb-editable');
        el.innerHTML = '<span class="fb-marks-edit">' +
            '<input type="number" id="' + state.prefix + 'EditMA" step="0.5" min="0" value="' + esc(initMa) + '" placeholder="awarded">' +
            '<span class="sep">/</span>' +
            '<input type="number" id="' + state.prefix + 'EditMT" step="0.5" min="0" value="' + esc(initMt) + '" placeholder="total">' +
        '</span>';

        // Stop clicks inside the live edit widget from bubbling to the badge's
        // click handler, which would otherwise re-run beginMarksEdit and detach
        // the live inputs we're editing.
        var editWidget = el.querySelector('.fb-marks-edit');
        if (editWidget) {
            editWidget.addEventListener('click', function (e) { e.stopPropagation(); });
            editWidget.addEventListener('mousedown', function (e) { e.stopPropagation(); });
        }

        var ma = document.getElementById(state.prefix + 'EditMA');
        var mt = document.getElementById(state.prefix + 'EditMT');
        ma.focus();
        ma.select();

        var submitted = false;
        function commit() {
            if (submitted) return;
            submitted = true;
            var newMa = ma.value === '' ? null : parseFloat(ma.value);
            var newMt = mt.value === '' ? null : parseFloat(mt.value);
            if (newMa != null && isNaN(newMa)) newMa = null;
            if (newMt != null && isNaN(newMt)) newMt = null;

            var changed = (q.marks_awarded !== newMa) || (q.marks_total !== newMt);
            if (!changed) {
                renderQuestion(state);
                return;
            }
            saveMarks(state, newMa, newMt);
        }
        function maybeCommit(e) {
            var other = (e.target === ma) ? mt : ma;
            if (e.relatedTarget === other) return; // focus moving between the two inputs
            commit();
        }
        function cancel() {
            if (submitted) return;
            submitted = true;
            renderQuestion(state);
        }

        ma.addEventListener('blur', maybeCommit);
        mt.addEventListener('blur', maybeCommit);
        ma.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); mt.focus(); mt.select(); }
            if (e.key === 'Escape') { e.preventDefault(); ma.removeEventListener('blur', maybeCommit); mt.removeEventListener('blur', maybeCommit); cancel(); }
        });
        mt.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); commit(); }
            if (e.key === 'Escape') { e.preventDefault(); ma.removeEventListener('blur', maybeCommit); mt.removeEventListener('blur', maybeCommit); cancel(); }
        });
    }

    // ---- Persistence ----

    function showToast(kind, text) {
        var toast = document.createElement('div');
        toast.className = 'fb-save-toast ' + kind;
        toast.textContent = text;
        document.body.appendChild(toast);
        requestAnimationFrame(function () { toast.classList.add('show'); });
        setTimeout(function () {
            toast.classList.remove('show');
            setTimeout(function () { toast.remove(); }, 300);
        }, kind === 'error' ? 3500 : 1500);
    }

    async function patchResult(state, payload) {
        var url = '/teacher/assignment/' + state.assignmentId + '/submission/' + state.submissionId + '/result';
        var res = await fetch(url, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        var data = await res.json();
        if (!res.ok || !data.success) throw new Error((data && data.error) || 'Save failed');
        // Return full response so callers can access edit_meta alongside result.
        return data;
    }

    function mergeResult(state, newResult) {
        state.questions = (newResult.questions || []).map(function (q) { return Object.assign({}, q); });
        state.questions.forEach(function (q) {
            var d = deriveStatus(q.marks_awarded, q.marks_total);
            if (d) q.status = d;
            if (!q.status) q.status = 'incorrect';
        });
        state.overall = newResult.overall_feedback || '';
        state.errors = newResult.errors || [];
        state.recommended = newResult.recommended_actions || [];
    }

    async function saveTextField(state, field, newValue, calibrate) {
        if (calibrate === undefined) calibrate = false;
        var payload;
        var savedQNum = null;
        if (field === 'overall') {
            payload = { overall_feedback: newValue };
        } else {
            var q = state.questions[state.currentQ];
            savedQNum = q.question_num != null ? q.question_num : (state.currentQ + 1);
            var qEdit = { question_num: savedQNum };
            qEdit[field] = newValue;
            if (field === 'feedback' || field === 'improvement') {
                qEdit.calibrate = !!calibrate;
            }
            payload = { questions: [qEdit] };
        }
        try {
            var data = await patchResult(state, payload);
            mergeResult(state, data.result);
            if (field === 'overall') renderShell(state); else renderQuestion(state);
            showToast('success', 'Saved');
            if (state.onSave) { try { state.onSave(data.result); } catch (e) {} }
            // Render per-field tag from edit_meta if the server logged this edit.
            if (data && data.edit_meta && savedQNum != null) {
                var qKey = String(savedQNum);
                var fieldMeta = (data.edit_meta[qKey] || {})[field];
                if (fieldMeta) {
                    // Store into textEditMeta so the tag survives re-renders.
                    if (!state.textEditMeta) state.textEditMeta = {};
                    if (!state.textEditMeta[qKey]) state.textEditMeta[qKey] = {};
                    state.textEditMeta[qKey][field] = fieldMeta;
                    renderEditTag(state, state.currentQ, field, fieldMeta);
                }
            }
            if (data && data.propagation_prompt) {
                try { fbShowPropagationBanner(state, data.propagation_prompt); } catch (e) { /* silent */ }
            }
        } catch (err) {
            if (field === 'overall') renderShell(state); else renderQuestion(state);
            showToast('error', err.message || 'Save failed');
        }
    }

    async function saveMarks(state, newMa, newMt) {
        var q = state.questions[state.currentQ];
        var payload = {
            questions: [{
                question_num: q.question_num != null ? q.question_num : (state.currentQ + 1),
                marks_awarded: newMa,
                marks_total: newMt,
            }],
        };
        try {
            var data = await patchResult(state, payload);
            mergeResult(state, data.result);
            renderQuestion(state);
            refreshSummary(state);
            refreshDots(state);
            showToast('success', 'Saved');
            if (state.onSave) { try { state.onSave(data.result); } catch (e) {} }
        } catch (err) {
            renderQuestion(state);
            showToast('error', err.message || 'Save failed');
        }
    }

    // Click-to-cycle for status-mode assignments:
    // correct → incorrect → partially_correct → correct → …
    var STATUS_NEXT = {
        correct: 'incorrect',
        incorrect: 'partially_correct',
        partially_correct: 'correct',
    };

    async function cycleStatus(state, el) {
        var q = state.questions[state.currentQ];
        var current = q.status || 'incorrect';
        var next = STATUS_NEXT[current] || 'correct';

        // Optimistic update — feels instant.
        q.status = next;
        var card = document.getElementById(state.prefix + 'QCard');
        if (card) {
            card.classList.remove('status-correct', 'status-partially_correct', 'status-incorrect');
            card.classList.add('status-' + next);
        }
        var badgeWrap = document.getElementById(state.prefix + 'StatusBadgeWrap');
        if (badgeWrap) {
            badgeWrap.innerHTML = questionBadgeHtml(q, next, state.editable, state.isMarksMode);
            var newBadge = badgeWrap.querySelector('[data-field="status"]');
            if (newBadge) newBadge.addEventListener('click', function () { cycleStatus(state, newBadge); });
        }
        refreshDots(state);
        refreshSummary(state);

        var payload = {
            questions: [{
                question_num: q.question_num != null ? q.question_num : (state.currentQ + 1),
                status: next,
            }],
        };
        try {
            var data = await patchResult(state, payload);
            mergeResult(state, data.result);
            renderQuestion(state);
            refreshSummary(state);
            refreshDots(state);
            if (state.onSave) { try { state.onSave(data.result); } catch (e) {} }
        } catch (err) {
            // Revert on failure.
            q.status = current;
            renderQuestion(state);
            refreshSummary(state);
            refreshDots(state);
            showToast('error', err.message || 'Save failed');
        }
    }

    // ---- Edit tag renderer ----

    function renderEditTag(state, idx, field, meta) {
        // meta = {version, calibrated}.  Replace any existing tag row for this
        // (idx, field). Insert as a sibling immediately after the field's
        // visible element so the tag + history link sit beneath it.
        var prefix = state.prefix || 'fb';
        // renderQuestion only shows one card at a time, identified by prefix + 'QCard'.
        var qCard = document.getElementById(prefix + 'QCard');
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
            toggleHistoryPanel(state, idx, field, row);
        });
        row.appendChild(link);
        if (fieldEl.parentNode) {
            fieldEl.parentNode.insertBefore(row, fieldEl.nextSibling);
        }
    }

    function toggleHistoryPanel(state, idx, field, anchorRow) {
        var prefix = state.prefix || 'fb';
        var panelId = prefix + 'HistPanel-' + idx + '-' + field;
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
        fetchAndRenderHistory(state, idx, panel);
    }

    function fetchAndRenderHistory(state, idx, panel) {
        // Use the current question's question_num for the criterion_id segment.
        var q = (state.questions || [])[idx];
        if (!q) {
            panel.textContent = 'Could not resolve criterion.';
            return;
        }
        var critId = String(q.question_num != null ? q.question_num : (idx + 1));
        var url = '/feedback/edit-history/' + encodeURIComponent(state.assignmentId) +
                  '/' + encodeURIComponent(state.submissionId) +
                  '/' + encodeURIComponent(critId);
        fetch(url, { credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                renderHistoryPanel(state, idx, panel, data);
            })
            .catch(function () {
                panel.textContent = 'Could not load history.';
            });
    }

    function renderHistoryPanel(state, idx, panel, data) {
        panel.innerHTML = '';
        var fields = ['feedback', 'improvement'];
        var anyShown = false;
        fields.forEach(function (field) {
            var versions = (data && data[field]) || [];
            if (!versions.length) return;
            anyShown = true;
            var heading = document.createElement('div');
            heading.style.cssText = 'font-weight:600;color:#444;margin-top:6px;margin-bottom:4px;';
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
                // Retire link only for active teacher edits.
                if (v.edit_id && v.active === true && v.author_type === 'teacher' &&
                    v.author_id && state.currentTeacherId &&
                    v.author_id === state.currentTeacherId) {
                    var ret = document.createElement('a');
                    ret.href = '#';
                    ret.className = 'fb-retire-link';
                    ret.style.cssText = 'font-size:11.5px;color:#b94a48;text-decoration:none;margin-top:2px;display:inline-block;';
                    ret.textContent = 'Retire this edit';
                    (function (editId) {
                        ret.addEventListener('click', function (ev) {
                            ev.preventDefault();
                            ev.stopPropagation();
                            retireEdit(state, idx, editId, panel);
                        });
                    }(v.edit_id));
                    block.appendChild(ret);
                }
                panel.appendChild(block);
            });
        });
        if (!anyShown) {
            panel.textContent = 'No edit history.';
        }
    }

    function retireEdit(state, idx, editId, panel) {
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
                    fetchAndRenderHistory(state, idx, panel);
                } else {
                    panel.textContent = 'Could not retire: ' + ((data && data.message) || 'unknown error');
                }
            })
            .catch(function () { panel.textContent = 'Could not retire (network).'; });
    }

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
                confirm.className = 'upload-btn';
                confirm.style.cssText = 'padding: 6px 12px; font-size: 12.5px;';
                confirm.textContent = 'Apply to selected';
                confirm.addEventListener('click', fbPropagateSelectedConfirm);
                actions.appendChild(confirm);
                var cancel = document.createElement('button');
                cancel.type = 'button';
                cancel.className = 'upload-btn';
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

    global.FeedbackRender = { render: render };
})(window);
