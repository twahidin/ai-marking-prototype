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

    // Math preprocessor: AI feedback often writes math without $...$ delimiters
    // ('t^3', 'ms^-2', 't_1', '[t^3-6t^2+9t]_0^3'), which MathJax then skips,
    // leaving the carets and underscores as visual clutter. Wrap obvious bare
    // math in $...$ so MathJax typesets it as proper symbols. Mirrors the
    // server-side _preprocess_math_for_pdf logic.
    var SUPER_TO_CHAR = {
        '⁰':'0','¹':'1','²':'2','³':'3','⁴':'4','⁵':'5',
        '⁶':'6','⁷':'7','⁸':'8','⁹':'9','⁺':'+','⁻':'-'
    };
    var SUB_TO_CHAR = {
        '₀':'0','₁':'1','₂':'2','₃':'3','₄':'4',
        '₅':'5','₆':'6','₇':'7','₈':'8','₉':'9'
    };
    var SUPER_CLASS = '[⁰¹²³⁴-⁹⁺⁻]';
    var SUB_CLASS = '[₀-₉]';

    function _convertRun(run, table) {
        var out = '';
        for (var i = 0; i < run.length; i++) out += (table[run[i]] || run[i]);
        return out;
    }

    function preprocessMath(text) {
        if (!text) return text;
        text = String(text);
        // word + supers: 'ms⁻²' → '$\mathrm{ms}^{-2}$'
        text = text.replace(
            new RegExp('([A-Za-z]+)(' + SUPER_CLASS + '+)', 'g'),
            function (_, base, run) {
                return '$\\mathrm{' + base + '}^{' + _convertRun(run, SUPER_TO_CHAR) + '}$';
            }
        );
        // standalone superscript runs: '²' → '${}^{2}$'
        text = text.replace(new RegExp(SUPER_CLASS + '+', 'g'), function (m) {
            return '${}^{' + _convertRun(m, SUPER_TO_CHAR) + '}$';
        });
        // standalone subscript runs
        text = text.replace(new RegExp(SUB_CLASS + '+', 'g'), function (m) {
            return '${}_{' + _convertRun(m, SUB_TO_CHAR) + '}$';
        });
        // Bare math patterns. Outer expressions first so inner ones don't
        // double-wrap. Re-split on $ between patterns to skip already-wrapped.
        var patterns = [
            /\\int\b[^$\n]*?\\,?d[a-zA-Z]+/g,                  // \int ... dx
            /\[[^\]$\n]+\]_\S+(?:\^\S+)?/g,                    // [expr]_a^b
            /\\(?:frac|sqrt|sum)\{[^}]*\}(?:\{[^}]*\})?/g,     // \frac{a}{b}, \sqrt{x}
            /(?<![A-Za-z])[A-Za-z]+\^\{[^}]+\}/g,              // x^{2}
            /(?<![A-Za-z])[A-Za-z]+\^[-+]?\d+/g,               // x^2, ms^-2, s^-1
            /(?<![A-Za-z])[A-Za-z]+_\{[^}]+\}/g,               // x_{i}
            /(?<![A-Za-z])[A-Za-z]+_[a-zA-Z0-9]/g,             // t_1, x_i
        ];
        for (var p = 0; p < patterns.length; p++) {
            var pat = patterns[p];
            var parts = text.split('$');
            for (var i = 0; i < parts.length; i++) {
                if (i % 2 === 1) continue;
                parts[i] = parts[i].replace(pat, function (m) { return '$' + m + '$'; });
            }
            text = parts.join('$');
        }
        return text;
    }

    function escMath(s) {
        if (s == null) return '';
        return esc(preprocessMath(String(s)));
    }

    // ------------------------------------------------------------------
    // Rubrics-redesign helpers (active only when assignType === 'rubrics').
    // CALIBRATABLE_FIELDS is shared between short_answer (feedback,
    // improvement) and the new rubrics fields. Used by attachQuestionEdit
    // tag-rendering loops + saveTextField calibration writes.
    // ------------------------------------------------------------------
    var CALIBRATABLE_FIELDS = [
        'feedback', 'improvement',
        'current_band_oneliner', 'next_band_oneliner',
        'improvement_rewrite', 'improvement_rewrite_2',
        'maintain_advice'
    ];

    // Bullet-aware math escaping for the rubrics "Assessment" field, which
    // the AI emits as a markdown bullet list. Falls back to escMath when
    // the input has no bullet markers (legacy / short_answer rendering).
    function escMathBulletAware(s) {
        if (s == null || s === '') return '';
        var lines = String(s).split(/\r?\n/);
        var bullets = [];
        var sawBullet = false;
        for (var i = 0; i < lines.length; i++) {
            var trimmed = lines[i].replace(/^\s+/, '');
            if (trimmed.indexOf('- ') === 0 || trimmed.indexOf('* ') === 0) {
                bullets.push(trimmed.slice(2).trim());
                sawBullet = true;
            } else if (trimmed === '') {
                // blank line — skip
            } else if (!sawBullet) {
                return escMath(s);
            } else {
                bullets[bullets.length - 1] += ' ' + trimmed;
            }
        }
        if (bullets.length === 0) return escMath(s);
        return '<ul class="fb-bullet-list">' +
            bullets.map(function (b) { return '<li>' + escMath(b) + '</li>'; }).join('') +
            '</ul>';
    }

    // Word-level diff: highlight tokens in `newStr` that aren't in the LCS
    // with `oldStr`. Used to show students exactly which words/phrases the
    // AI added or changed in the "Could become" rewrite vs their original
    // line. Returns HTML — already escaped, with <mark class="fb-diff-add">
    // wrapping inserted/changed tokens.
    function renderWordDiffHtml(oldStr, newStr) {
        var safeNew = String(newStr || '');
        if (!oldStr) return esc(safeNew);
        var oldToks = String(oldStr).split(/(\s+)/).filter(function (t) { return t.length > 0; });
        var newToks = safeNew.split(/(\s+)/).filter(function (t) { return t.length > 0; });
        if (newToks.length === 0) return '';
        function norm(t) {
            return t.toLowerCase().replace(/^[^a-z0-9À-￿]+|[^a-z0-9À-￿]+$/gi, '');
        }
        var oldKeys = oldToks.map(norm);
        var newKeys = newToks.map(norm);
        var m = oldKeys.length, n = newKeys.length;
        var dp = [];
        for (var i = 0; i <= m; i++) dp.push(new Array(n + 1).fill(0));
        for (var i2 = 1; i2 <= m; i2++) {
            for (var j = 1; j <= n; j++) {
                if (oldKeys[i2 - 1] && oldKeys[i2 - 1] === newKeys[j - 1]) {
                    dp[i2][j] = dp[i2 - 1][j - 1] + 1;
                } else {
                    dp[i2][j] = Math.max(dp[i2 - 1][j], dp[i2][j - 1]);
                }
            }
        }
        var matched = new Array(n).fill(false);
        var ii = m, jj = n;
        while (ii > 0 && jj > 0) {
            if (oldKeys[ii - 1] && oldKeys[ii - 1] === newKeys[jj - 1]) {
                matched[jj - 1] = true;
                ii--; jj--;
            } else if (dp[ii - 1][jj] >= dp[ii][jj - 1]) {
                ii--;
            } else {
                jj--;
            }
        }
        var out = [];
        for (var k = 0; k < newToks.length; k++) {
            var tok = newToks[k];
            var isWhitespace = /^\s+$/.test(tok);
            if (!isWhitespace && !matched[k]) {
                if (newKeys[k]) {
                    out.push('<mark class="fb-diff-add">' + esc(tok) + '</mark>');
                    continue;
                }
            }
            out.push(esc(tok));
        }
        return out.join('');
    }

    // Prefer the server-provided `<key>_html` field if it's a non-empty
    // string. That field carries pinyin-annotated <ruby> markup for
    // Chinese assignments and is already HTML-safe (text portions are
    // pre-escaped by pinyin_annotate.py). Falls back to escMath on the
    // raw text for everything else (English, math, no-pinyin Chinese).
    function rawOrHtml(obj, key) {
        if (!obj) return '';
        var html = obj[key + '_html'];
        if (typeof html === 'string' && html.length) return html;
        var raw = obj[key];
        return escMath(raw || '');
    }

    // ------------------------------------------------------------------
    // Numbered-pinyin → toned-pinyin converter. Lets teachers type
    // "cheng2yu3" and have it auto-convert to "chéngyǔ" on the fly.
    // 'v' / 'V' is the standard substitute for ü on keyboards without
    // the diaeresis. Tone 5 is neutral (digit dropped, no mark).
    // ------------------------------------------------------------------
    var PY_TONE_MARKS = {
        'a': ['ā','á','ǎ','à'], 'e': ['ē','é','ě','è'],
        'i': ['ī','í','ǐ','ì'], 'o': ['ō','ó','ǒ','ò'],
        'u': ['ū','ú','ǔ','ù'], 'ü': ['ǖ','ǘ','ǚ','ǜ'],
        'A': ['Ā','Á','Ǎ','À'], 'E': ['Ē','É','Ě','È'],
        'I': ['Ī','Í','Ǐ','Ì'], 'O': ['Ō','Ó','Ǒ','Ò'],
        'U': ['Ū','Ú','Ǔ','Ù'], 'Ü': ['Ǖ','Ǘ','Ǚ','Ǜ'],
    };
    var PY_VOWELS = /[aeiouüAEIOUÜ]/;

    function applyPyTone(syllable, tone) {
        // Replace v→ü first (common on QWERTY keyboards).
        syllable = syllable.replace(/v/g, 'ü').replace(/V/g, 'Ü');
        if (tone < 1 || tone > 4) return syllable;
        // Pinyin tone-placement rules:
        //   1. If 'a' is present, mark on 'a'.
        //   2. Else if 'e' is present, mark on 'e'.
        //   3. Else if 'o' is present, mark on 'o'.
        //   4. Else the LAST vowel in the syllable.
        var pos = syllable.search(/[aA]/);
        if (pos < 0) pos = syllable.search(/[eE]/);
        if (pos < 0) pos = syllable.search(/[oO]/);
        if (pos < 0) {
            for (var i = syllable.length - 1; i >= 0; i--) {
                if (PY_VOWELS.test(syllable[i])) { pos = i; break; }
            }
        }
        if (pos < 0) return syllable;
        var ch = syllable[pos];
        var toned = PY_TONE_MARKS[ch];
        if (!toned) return syllable;
        return syllable.slice(0, pos) + toned[tone - 1] + syllable.slice(pos + 1);
    }

    function numPyToToneMarks(input) {
        if (!input) return '';
        return String(input).replace(/([a-zA-ZüÜvV]+)([1-5])/g, function (m, syl, n) {
            var tone = parseInt(n, 10);
            if (tone === 5) {
                return syl.replace(/v/g, 'ü').replace(/V/g, 'Ü');
            }
            return applyPyTone(syl, tone);
        });
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
            '.fb-q-card.status-incorrect .fb-q-card-header { background: #fbe3e3; }' +
            /* Mistake-category field — sits between Correct Answer and Feedback */
            '.fb-q-field-value.mistake-category { background: #fbeef0; padding: 10px 12px; border-radius: 8px; border-left: 3px solid #c0394a; font-size: 13.5px; }' +
            '.fb-cat-trigger { cursor: pointer; transition: box-shadow 0.15s, background 0.15s; outline: none; }' +
            '.fb-cat-trigger:hover { background: #f7e3e6; box-shadow: 0 0 0 2px rgba(192,57,74,0.18); }' +
            '.fb-cat-trigger:focus { box-shadow: 0 0 0 2px rgba(192,57,74,0.32); }' +
            '.fb-cat-display-label { font-weight: 600; color: #2d2d2d; }' +
            '.fb-cat-dropdown-item:hover { background: #eef1ff !important; }';
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

        // Pull the student's correction attempts out of the tiered bucket.
        // Stored chronologically as {question_num, text, verdict, message,
        // theme_key, submitted_at}; we group by question_num for the
        // teacher-side back-and-forth view (no timestamp shown — keeps the
        // thread readable as a conversation).
        var tieredBucket = (result && result._tiered) || {};
        var corrections = tieredBucket.corrections || [];

        var state = {
            questions: questions,
            errors: result.errors || [],
            assignType: result.assign_type || 'short_answer',
            recommended: result.recommended_actions || [],
            overall: result.overall_feedback || '',
            overallHtml: result.overall_feedback_html || '',
            currentQ: 0,
            containerEl: containerEl,
            prefix: prefix,
            editable: !!options.editable,
            assignmentId: options.assignmentId || null,
            submissionId: options.submissionId || null,
            currentTeacherId: options.currentTeacherId || null,
            onSave: options.onSave || null,
            isMarksMode: isMarksMode,
            textEditMeta: options.textEditMeta || {},  // {qKey: {field: {edit_id, version, calibrated}}}
            availableThemes: Array.isArray(options.availableThemes) ? options.availableThemes : [],
            corrections: corrections,
            // 'questions' | 'corrections' — which panel is showing
            mode: 'questions',
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

    // ====================================================================
    // RUBRICS-REDESIGN RENDERING (active when state.assignType === 'rubrics').
    // Band-first overall summary + tab strip per criterion + YOU'RE HERE /
    // TO REACH BAND X+1 cards + improvement-example pairs. Legacy rubrics
    // submissions (no current_band_oneliner / improvement_target fields)
    // get a "Re-mark to enable" affordance via the fallback paths in
    // renderImprovementExamples + renderRubricsCards.
    //
    // Short-answer rendering is unchanged — Mistake Category, Layer 3 idea,
    // Corrections (Now You Try), and the existing dot-strip nav stay in
    // their existing functions below.
    // ====================================================================

    function computeOverallBand(questions) {
        var ta = 0, tp = 0, weightedBand = 0;
        (questions || []).forEach(function (q) {
            var mt = q.marks_total || 0;
            var ma = q.marks_awarded || 0;
            ta += ma;
            tp += mt;
            var bm = (q.band || '').match(/Band\s+(\d+)/i);
            if (bm && mt > 0) weightedBand += parseInt(bm[1], 10) * mt;
        });
        var avgBand = tp > 0 ? Math.round(weightedBand / tp) : 0;
        return { bandNum: avgBand, marksAwarded: ta, marksTotal: tp };
    }

    function renderRubricsHeaderSummary(state) {
        var ob = computeOverallBand(state.questions || []);
        var bandLabel = ob.bandNum ? ('Band ' + ob.bandNum) : '—';
        return '<div class="fb-summary-rubrics">' +
            '<span class="fb-summary-band band-' + ob.bandNum + '">Overall ≈ ' + bandLabel + '</span>' +
            '<span class="fb-summary-marks">' + ob.marksAwarded + ' / ' + ob.marksTotal + '</span>' +
            '</div>';
    }

    function renderRubricsTabStrip(state) {
        var questions = state.questions || [];
        var hasErrors = (state.errors || []).length > 0;
        var bits = ['<div class="fb-tab-strip">'];
        questions.forEach(function (q, idx) {
            var crit = q.criterion_name || ('Q' + (idx + 1));
            var bandMatch = (q.band || '').match(/Band\s+(\d+)/i);
            var bandNum = bandMatch ? parseInt(bandMatch[1], 10) : 0;
            var bandClass = bandNum ? ('band-' + bandNum) : '';
            var marks = (q.marks_awarded != null && q.marks_total != null) ? (q.marks_awarded + '/' + q.marks_total) : '';
            var isActive = (idx === state.activeTabIdx);
            bits.push(
                '<div class="fb-tab' + (isActive ? ' active ' + bandClass : '') + '" data-tab-idx="' + idx + '">' +
                esc(crit) +
                '<span class="fb-tab-meta">' + (bandNum ? ('B' + bandNum) : '') + (marks ? (' · ' + esc(String(marks))) : '') + '</span>' +
                '</div>'
            );
        });
        bits.push('<div class="fb-tab-spacer"></div>');
        if (hasErrors) {
            var idxErrors = questions.length;
            bits.push('<div class="fb-tab' + (state.activeTabIdx === idxErrors ? ' active' : '') + '" data-tab-idx="' + idxErrors + '">Errors (' + state.errors.length + ')</div>');
        }
        var idxOverall = questions.length + (hasErrors ? 1 : 0);
        bits.push('<div class="fb-tab' + (state.activeTabIdx === idxOverall ? ' active' : '') + '" data-tab-idx="' + idxOverall + '">Overall</div>');
        bits.push('</div>');
        return bits.join('');
    }

    function renderRubricsCards(state, q, qIdx) {
        var bandLabel = q.band || '';
        var bandMatch = bandLabel.match(/Band\s+(\d+)/i);
        var bandNum = bandMatch ? parseInt(bandMatch[1], 10) : 0;
        var nextBandNum = bandNum + 1;
        var qNum = q.question_num || (qIdx + 1);
        var isTopBand = !!q.maintain_advice && !q.next_band_oneliner;

        var currentText = q.current_band_oneliner;
        if (currentText == null || currentText === '') currentText = q.feedback || '';

        var bandDisplayLabel = bandLabel || '— pick band —';
        var bandSelect =
            '<span class="fb-inline-edit fb-band-edit editable" data-q-num="' + qNum + '" tabindex="0">' +
                '<span class="fb-inline-display fb-band-display">' + esc(bandDisplayLabel) + '</span>' +
            '</span>';

        var marksAwardedStr = (q.marks_awarded != null ? String(q.marks_awarded) : '–');
        var marksTotalStr = (q.marks_total != null ? String(q.marks_total) : '');
        var marksInput =
            '<span class="fb-marks-inline">' +
                '<span class="fb-inline-edit fb-marks-edit editable" data-q-num="' + qNum + '" tabindex="0">' +
                    '<span class="fb-inline-display fb-marks-display">' + esc(marksAwardedStr) + '</span>' +
                '</span>' +
                '<span class="fb-card-marks-total">/ ' + esc(marksTotalStr) + ' marks</span>' +
            '</span>';

        var bandStale = !!(q.band_ai_original && q.band_ai_original !== bandLabel);
        var subId = state.submissionId || '';
        var staleNotice = bandStale
            ? '<div class="fb-stale-notice">Band manually changed (AI marked as ' + esc(q.band_ai_original) + '). Descriptions reflect AI grading. <a class="fb-remark-link" data-action="remark" data-stale="1" data-sub-id="' + esc(String(subId)) + '">Re-mark for tailored text</a> (AI keeps your band locked).</div>'
            : '';

        var leftCard = '<div class="fb-card fb-card-current band-' + bandNum + '">' +
            '<div class="fb-card-label">YOU\'RE HERE</div>' +
            '<div class="fb-card-row">' + bandSelect + marksInput + '</div>' +
            '<div class="fb-marks-error" data-q-num="' + qNum + '" hidden></div>' +
            staleNotice +
            '<div class="fb-card-oneliner editable" data-field="current_band_oneliner" data-q-num="' + qNum + '" contenteditable="false">' + esc(currentText) + '</div>' +
            '</div>';

        var rightCard;
        if (isTopBand) {
            rightCard = '<div class="fb-card fb-card-next">' +
                '<div class="fb-card-label">MAINTAIN BAND ' + bandNum + '</div>' +
                '<div class="fb-card-oneliner editable" data-field="maintain_advice" data-q-num="' + qNum + '" contenteditable="false">' + esc(q.maintain_advice || '') + '</div>' +
                '</div>';
        } else {
            var nextText = q.next_band_oneliner;
            var fallbackUsed = false;
            if (bandStale) {
                nextText = 'Re-mark for a tailored Band ' + nextBandNum + ' description.';
                fallbackUsed = true;
            } else if (nextText == null || nextText === '') {
                nextText = 'Reach Band ' + nextBandNum + ' — re-mark to enable a specific description.';
                fallbackUsed = true;
            }
            rightCard = '<div class="fb-card fb-card-next">' +
                '<div class="fb-card-label">TO REACH BAND ' + nextBandNum + '</div>' +
                '<div class="fb-card-band">Band ' + nextBandNum + '</div>' +
                '<div class="fb-card-oneliner editable" data-field="next_band_oneliner" data-q-num="' + qNum + '" contenteditable="false"' + (fallbackUsed ? ' data-fallback="1"' : '') + '>' + esc(nextText) + '</div>' +
                '</div>';
        }

        return '<div class="fb-cards-row">' + leftCard + rightCard + '</div>';
    }

    var SENTINEL_NO_SUGGEST = '__NO_CONFIDENT_SUGGESTION__';

    function renderImprovementExamples(state, q, qIdx) {
        var qNum = q.question_num || (qIdx + 1);
        var subId = state.submissionId || '';
        var isTopBand = !!q.maintain_advice && !q.next_band_oneliner;

        function pairColumn(slot, targetField, rewriteField, tgt, rew) {
            var hasTgt = (tgt != null && tgt !== '');
            var hasRew = (rew != null && rew !== '');
            var sentinel = (tgt === SENTINEL_NO_SUGGEST || rew === SENTINEL_NO_SUGGEST);
            if (!hasTgt && !hasRew) return null;
            if (sentinel) return '<div class="fb-fallback-text">AI could not produce a confident rewrite for this example.</div>';
            return '<div class="fb-rewrite-label">Your line:</div>' +
                '<span class="fb-quote editable" data-field="' + targetField + '" data-q-num="' + qNum + '" contenteditable="false">' + esc(tgt || '') + '</span>' +
                '<div class="fb-rewrite-label">Could become:</div>' +
                '<span class="fb-quote fb-quote-rewrite editable" data-field="' + rewriteField + '" data-q-num="' + qNum + '" contenteditable="false">' + renderWordDiffHtml(tgt || '', rew || '') + '</span>';
        }

        var leftHtml, rightHtml;
        if (isTopBand) {
            leftHtml = '<div class="fb-fallback-text">No upgrade suggestion — already at top band.</div>';
            rightHtml = '';
        } else {
            var p1 = pairColumn(1, 'improvement_target', 'improvement_rewrite', q.improvement_target, q.improvement_rewrite);
            var p2 = pairColumn(2, 'improvement_target_2', 'improvement_rewrite_2', q.improvement_target_2, q.improvement_rewrite_2);
            if (p1 === null && p2 === null) {
                leftHtml = '<div class="fb-fallback-text">AI did not produce a rewrite suggestion for this submission. <a class="fb-remark-link" data-action="remark" data-sub-id="' + esc(String(subId)) + '">Re-mark to enable</a></div>';
                rightHtml = '';
            } else {
                leftHtml = p1 !== null ? p1 : '';
                rightHtml = p2 !== null ? p2 : '';
            }
        }

        return '<div class="fb-evidence-row">' +
            '<div class="fb-evidence-col" data-slot="1"><h5>Example 1</h5>' + leftHtml + '</div>' +
            '<div class="fb-evidence-col" data-slot="2">' + (rightHtml ? '<h5>Example 2</h5>' + rightHtml : '') + '</div>' +
            '</div>';
    }

    function rubricsLegacyQCardHtml(state, q, idx) {
        var headerLabel = q.criterion_name || 'Criterion ' + (q.question_num || idx + 1);
        var ansLabel = 'Assessment';
        var refLabel = 'Band Descriptor';
        var bandInfo = q.band ? ' <span style="font-size:12px;color:#667eea;font-weight:600;">(' + esc(q.band) + ')</span>' : '';

        var statusCls = q.status || 'incorrect';
        var badge = questionBadgeHtml(q, statusCls, state.editable, state.isMarksMode);

        var cardClass = 'fb-q-card status-' + statusCls;
        return renderRubricsCards(state, q, idx) +
            renderImprovementExamples(state, q, idx) +
            '<div class="' + cardClass + '" id="' + state.prefix + 'QCard">' +
            '<div class="fb-q-card-header"><span class="fb-q-num">' + esc(headerLabel) + bandInfo + '</span>' +
                '<span id="' + state.prefix + 'StatusBadgeWrap">' + badge + '</span>' +
            '</div>' +
            '<div class="fb-q-card-body">' +
                '<div class="fb-q-field"><div class="fb-q-field-label">' + ansLabel + '</div><div class="fb-q-field-value">' + (q.student_answer_html || escMathBulletAware(q.student_answer || 'N/A')) + '</div></div>' +
                '<div class="fb-q-field"><div class="fb-q-field-label">' + refLabel + '</div><div class="fb-q-field-value">' + (q.correct_answer_html || escMath(q.correct_answer || 'N/A')) + '</div></div>' +
            '</div>' +
        '</div>';
    }

    function rubricsErrorsTabHtml(state) {
        if (!state.errors || !state.errors.length) {
            return '<div class="fb-overall-box"><h4>Line-by-Line Errors</h4><p style="color:#888;font-style:italic;">No errors recorded.</p></div>';
        }
        var html = '<div class="fb-overall-box"><h4>Line-by-Line Errors (' + state.errors.length + ')</h4><div class="fb-errors-list">';
        state.errors.forEach(function (e) {
            html += '<div class="fb-error-item"><strong>' + esc((e.type || 'error').toUpperCase()) + '</strong>';
            if (e.location) html += ' <span style="color:#999;">' + esc(e.location) + '</span>';
            html += '<div style="margin-top:4px;"><span style="text-decoration:line-through;color:#dc3545;">' + esc(e.original || '') + '</span> &rarr; <span style="color:#28a745;">' + esc(e.correction || '') + '</span></div></div>';
        });
        html += '</div></div>';
        return html;
    }

    function rubricsOverallTabHtml(state) {
        var html = overallSectionHtml(state);
        if (state.recommended && state.recommended.length) {
            html += '<div class="fb-overall-box"><h4>Recommended Actions</h4><ul>';
            state.recommended.forEach(function (a) { html += '<li>' + esc(a) + '</li>'; });
            html += '</ul></div>';
        }
        if (!html) {
            html = '<div class="fb-overall-box"><p style="color:#888;font-style:italic;">No overall feedback.</p></div>';
        }
        return html;
    }

    function renderRubricsShell(state) {
        var prefix = state.prefix;
        var summaryHtml = renderRubricsHeaderSummary(state);
        var tabStrip = renderRubricsTabStrip(state);
        var idx = state.activeTabIdx || 0;
        var questions = state.questions || [];
        var hasErrors = (state.errors || []).length > 0;
        var bodyHtml;
        if (questions.length === 0) {
            bodyHtml = rubricsOverallTabHtml(state);
        } else if (idx < questions.length) {
            bodyHtml = rubricsLegacyQCardHtml(state, questions[idx], idx);
        } else if (idx === questions.length && hasErrors) {
            bodyHtml = rubricsErrorsTabHtml(state);
        } else {
            bodyHtml = rubricsOverallTabHtml(state);
        }

        var html = summaryHtml + tabStrip +
            '<div id="' + prefix + 'QCardContainer">' + bodyHtml + '</div>';

        if (!state.containerEl || !state.containerEl.isConnected) return;
        try { state.containerEl.innerHTML = html; }
        catch (e) { return; }

        rebindRubricsHandlers(state);
        if (state.editable) {
            var idx2 = state.activeTabIdx || 0;
            if (idx2 < (state.questions || []).length) {
                attachQuestionEditHandlers(state);
                if (state.textEditMeta) {
                    var qNow = state.questions[idx2];
                    if (qNow) {
                        var qKey = String(qNow.question_num != null ? qNow.question_num : (idx2 + 1));
                        var qMeta = state.textEditMeta[qKey] || {};
                        CALIBRATABLE_FIELDS.forEach(function (f) {
                            if (qMeta[f] && typeof renderEditTag === 'function') renderEditTag(state, idx2, f, qMeta[f]);
                        });
                    }
                }
            } else {
                attachOverallEditHandler(state);
            }
        }

        if (window.MathJax && MathJax.typesetPromise) {
            MathJax.typesetPromise([state.containerEl]).catch(function () {});
        }
    }

    function attachRubricsTabStripHandler(state) {
        if (state.assignType !== 'rubrics') return;
        if (state.containerEl._fbTabHandler) {
            state.containerEl.removeEventListener('click', state.containerEl._fbTabHandler);
        }
        var handler = function (e) {
            var tab = e.target.closest && e.target.closest('.fb-tab');
            if (!tab) return;
            if (!state.containerEl.contains(tab)) return;
            var idx = parseInt(tab.getAttribute('data-tab-idx'), 10);
            if (isNaN(idx) || idx === state.activeTabIdx) return;
            state.activeTabIdx = idx;
            if (idx < (state.questions || []).length) {
                state.currentQ = idx;
            }
            renderShell(state);
        };
        state.containerEl._fbTabHandler = handler;
        state.containerEl.addEventListener('click', handler);
    }

    function attachRemarkLinkHandler(state) {
        if (state.assignType !== 'rubrics') return;
        if (state.containerEl._fbRemarkHandler) {
            state.containerEl.removeEventListener('click', state.containerEl._fbRemarkHandler);
        }
        var handler = function (e) {
            var link = e.target.closest && e.target.closest('a.fb-remark-link[data-action="remark"]');
            if (!link) return;
            if (!state.containerEl.contains(link)) return;
            e.preventDefault();
            var sid = link.getAttribute('data-sub-id');
            var overrides = null;
            if (link.getAttribute('data-stale') === '1') {
                overrides = {};
                (state.questions || []).forEach(function (q) {
                    var crit = (q.criterion_name || '').trim();
                    var band = (q.band || '').trim();
                    if (crit && band) overrides[crit] = band;
                });
            }
            if (typeof window.triggerRemark === 'function') {
                window.triggerRemark(sid, overrides);
            } else {
                console.warn('feedback_render: window.triggerRemark not available');
                alert('Re-mark trigger not available — please re-mark from the row actions on the table.');
            }
        };
        state.containerEl.addEventListener('click', handler);
        state.containerEl._fbRemarkHandler = handler;
    }

    function attachInlineEditTriggerHandler(state) {
        if (state.assignType !== 'rubrics') return;
        if (state.containerEl._fbInlineTriggerHandler) {
            state.containerEl.removeEventListener('click', state.containerEl._fbInlineTriggerHandler);
        }
        var handler = function (e) {
            var disp = e.target && e.target.closest && e.target.closest('.fb-inline-display');
            if (!disp) return;
            var wrap = disp.closest('.fb-inline-edit');
            if (!wrap || !state.containerEl.contains(wrap)) return;
            if (wrap.querySelector('select, input')) return;
            var qNum = parseInt(wrap.getAttribute('data-q-num'), 10);
            if (isNaN(qNum)) return;
            var q = state.questions.find(function (qq) { return (qq.question_num || 0) === qNum; });
            if (!q) return;
            if (wrap.classList.contains('fb-band-edit')) {
                _swapBandToEditor(wrap, q, qNum);
            } else if (wrap.classList.contains('fb-marks-edit')) {
                _swapMarksToEditor(wrap, q, qNum);
            }
        };
        state.containerEl.addEventListener('click', handler);
        state.containerEl._fbInlineTriggerHandler = handler;
    }

    function _swapBandToEditor(wrap, q, qNum) {
        var bandLabel = q.band || '';
        var bandsForCrit = (window.__rubricBandsByCriterion || {})[q.criterion_name] || [];
        var html;
        if (bandsForCrit.length > 0) {
            var opts = bandsForCrit.map(function (b) {
                var sel = (b === bandLabel) ? ' selected' : '';
                return '<option value="' + esc(b) + '"' + sel + '>' + esc(b) + '</option>';
            }).join('');
            if (bandLabel && bandsForCrit.indexOf(bandLabel) === -1) {
                opts = '<option value="' + esc(bandLabel) + '" selected>' + esc(bandLabel) + ' (legacy)</option>' + opts;
            }
            html = '<select class="fb-band-select" data-q-num="' + qNum + '">' + opts + '</select>';
        } else {
            html = '<input class="fb-band-input" type="text" data-q-num="' + qNum + '" value="' + esc(bandLabel) + '">';
        }
        wrap.innerHTML = html;
        var ed = wrap.querySelector('select, input');
        if (!ed) return;
        ed.focus();
        ed.addEventListener('blur', function () {
            if (wrap.isConnected) _swapBandBackToDisplay(wrap, q);
        });
    }

    function _swapBandBackToDisplay(wrap, q) {
        var bandLabel = q.band || '— pick band —';
        wrap.innerHTML = '<span class="fb-inline-display fb-band-display">' + esc(bandLabel) + '</span>';
    }

    function _swapMarksToEditor(wrap, q, qNum) {
        var marksStr = (q.marks_awarded != null ? String(q.marks_awarded) : '');
        wrap.innerHTML = '<input class="fb-marks-input" type="number" step="1" min="0" data-q-num="' + qNum + '" value="' + esc(marksStr) + '">';
        var ed = wrap.querySelector('input');
        if (!ed) return;
        ed.focus();
        try { ed.select(); } catch (e) { /* number input */ }
        ed.addEventListener('blur', function () {
            if (wrap.isConnected) _swapMarksBackToDisplay(wrap, q);
        });
    }

    function _swapMarksBackToDisplay(wrap, q) {
        var marksStr = (q.marks_awarded != null ? String(q.marks_awarded) : '–');
        wrap.innerHTML = '<span class="fb-inline-display fb-marks-display">' + esc(marksStr) + '</span>';
    }

    function attachBandSelectHandler(state) {
        if (state.assignType !== 'rubrics') return;
        if (state.containerEl._fbBandHandler) {
            state.containerEl.removeEventListener('change', state.containerEl._fbBandHandler);
        }
        var handler = function (e) {
            var sel = e.target.closest && e.target.closest('.fb-band-select, .fb-band-input');
            if (!sel) return;
            if (!state.containerEl.contains(sel)) return;
            var qNum = parseInt(sel.getAttribute('data-q-num'), 10);
            if (isNaN(qNum)) return;
            var newBand = sel.value;
            fetch('/teacher/assignment/' + state.assignmentId + '/submission/' + state.submissionId + '/result', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ questions: [{ question_num: qNum, band: newBand }] }),
            }).then(function (r) {
                if (!r.ok) { alert('Failed to save band change.'); return; }
                var q = state.questions.find(function (qq) { return (qq.question_num || 0) === qNum; });
                if (q) {
                    if (q.band_ai_original == null) q.band_ai_original = q.band || '';
                    q.band = newBand;
                }
                renderShell(state);
                if (typeof window.refreshSubmissionScore === 'function') {
                    window.refreshSubmissionScore(state.submissionId);
                }
            }).catch(function () { alert('Network error saving band change.'); });
        };
        state.containerEl.addEventListener('change', handler);
        state.containerEl._fbBandHandler = handler;
    }

    function parseBandRange(label) {
        if (!label) return null;
        var m = String(label).match(/\((\d+)\s*[\-–—]\s*(\d+)/);
        if (!m) return null;
        var lo = parseInt(m[1], 10);
        var hi = parseInt(m[2], 10);
        if (isNaN(lo) || isNaN(hi)) return null;
        if (lo > hi) { var tmp = lo; lo = hi; hi = tmp; }
        return { lo: lo, hi: hi };
    }

    function bandLabelForMarks(criterionName, marksAwarded) {
        var bands = (window.__rubricBandsByCriterion || {})[criterionName] || [];
        var lowest = null, highest = null;
        for (var i = 0; i < bands.length; i++) {
            var range = parseBandRange(bands[i]);
            if (!range) continue;
            if (marksAwarded >= range.lo && marksAwarded <= range.hi) return bands[i];
            if (lowest === null || range.lo < lowest.lo) lowest = { label: bands[i], lo: range.lo, hi: range.hi };
            if (highest === null || range.hi > highest.hi) highest = { label: bands[i], lo: range.lo, hi: range.hi };
        }
        if (lowest === null) return null;
        if (marksAwarded < lowest.lo) return lowest.label;
        if (marksAwarded > highest.hi) return highest.label;
        return null;
    }

    function attachMarksInputHandler(state) {
        if (state.assignType !== 'rubrics') return;
        if (state.containerEl._fbMarksHandler) {
            state.containerEl.removeEventListener('change', state.containerEl._fbMarksHandler);
        }
        var handler = function (e) {
            var input = e.target.closest && e.target.closest('.fb-marks-input');
            if (!input) return;
            if (!state.containerEl.contains(input)) return;
            var qNum = parseInt(input.getAttribute('data-q-num'), 10);
            if (isNaN(qNum)) return;
            var q = state.questions.find(function (qq) { return (qq.question_num || 0) === qNum; });
            if (!q) return;

            var raw = input.value;
            var errEl = state.containerEl.querySelector('.fb-marks-error[data-q-num="' + qNum + '"]');
            var clearError = function () { if (errEl) { errEl.hidden = true; errEl.textContent = ''; } };
            var showError = function (msg) {
                if (errEl) { errEl.textContent = msg; errEl.hidden = false; }
                input.value = (q.marks_awarded != null ? String(q.marks_awarded) : '');
            };
            clearError();

            if (raw === '' || raw == null) {
                var payload = { question_num: qNum, marks_awarded: null };
                fetch('/teacher/assignment/' + state.assignmentId + '/submission/' + state.submissionId + '/result', {
                    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ questions: [payload] }),
                }).then(function (r) { if (r.ok) { q.marks_awarded = null; renderShell(state); } });
                return;
            }

            var newMarks = Number(raw);
            if (isNaN(newMarks)) { showError('Marks must be a number.'); return; }
            var maxMarks = q.marks_total != null ? q.marks_total : null;
            if (maxMarks != null && newMarks > maxMarks) {
                showError('Marks (' + newMarks + ') exceed the maximum (' + maxMarks + ') for this criterion. Cannot save.');
                return;
            }
            if (newMarks < 0) { showError('Marks cannot be negative.'); return; }

            var derivedBand = bandLabelForMarks(q.criterion_name, newMarks);
            var body = { question_num: qNum, marks_awarded: newMarks };
            if (derivedBand && derivedBand !== q.band) body.band = derivedBand;
            fetch('/teacher/assignment/' + state.assignmentId + '/submission/' + state.submissionId + '/result', {
                method: 'PATCH', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ questions: [body] }),
            }).then(function (r) {
                if (!r.ok) { showError('Failed to save marks.'); return; }
                q.marks_awarded = newMarks;
                if (body.band) {
                    if (q.band_ai_original == null) q.band_ai_original = q.band || '';
                    q.band = body.band;
                }
                renderShell(state);
                if (typeof window.refreshSubmissionScore === 'function') {
                    window.refreshSubmissionScore(state.submissionId);
                }
            }).catch(function () { showError('Network error.'); });
        };
        state.containerEl.addEventListener('change', handler);
        state.containerEl._fbMarksHandler = handler;
    }

    function rebindRubricsHandlers(state) {
        if (state.assignType !== 'rubrics') return;
        attachRubricsTabStripHandler(state);
        attachRemarkLinkHandler(state);
        attachBandSelectHandler(state);
        attachMarksInputHandler(state);
        attachInlineEditTriggerHandler(state);
    }

    // ====================================================================
    // END rubrics-redesign block. Short-answer rendering follows.
    // ====================================================================

    function renderShell(state) {
        // Rubrics-redesign branch: dispatch to the new shell. Always taken
        // for assignType === 'rubrics' (legacy submissions show "Re-mark to
        // enable" affordances inside the new shell — see plan decision Q1).
        if (state.assignType === 'rubrics') {
            return renderRubricsShell(state);
        }

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

        // "Corrections" pill — only rendered when the student actually
        // submitted at least one correction attempt. Sits at the right
        // of the dot row so the spatial relationship matches the user's
        // mental model (question pills → corrections pill, all lined up).
        var correctionsPill = '';
        if (state.corrections && state.corrections.length) {
            correctionsPill =
                '<button type="button" class="fb-corrections-pill" id="' + prefix + 'CorrPill"' +
                ' aria-pressed="false">Corrections (' + state.corrections.length + ')</button>';
        }

        var overallHtml = overallSectionHtml(state);
        var extras = extrasHtml(state);

        var html =
            '<div class="fb-summary-bar" id="' + prefix + 'SummaryBar">' + summary + '</div>' +
            (state.questions.length ? (
                '<div class="fb-q-dots-row" id="' + prefix + 'QDotsRow">' +
                    '<div class="fb-q-dots" id="' + prefix + 'QDots">' + dots + '</div>' +
                    correctionsPill +
                '</div>' +
                '<div class="fb-q-nav" id="' + prefix + 'QNavRow">' +
                    '<button id="' + prefix + 'PrevBtn" type="button">&larr; Prev</button>' +
                    '<span id="' + prefix + 'QNavInfo"></span>' +
                    '<button id="' + prefix + 'NextBtn" type="button">Next &rarr;</button>' +
                '</div>' +
                '<div id="' + prefix + 'QCardContainer"></div>' +
                '<div id="' + prefix + 'CorrPanel" class="fb-corrections-panel" style="display:none;"></div>'
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
                ? escMath(state.overall)
                : '<span class="fb-placeholder">Click to add overall feedback…</span>';
            return '<div class="fb-overall-box">' +
                '<h4>Overall Feedback <small style="color:#bbb;font-weight:400;">(click to edit)</small></h4>' +
                '<p class="fb-editable" id="' + prefix + 'OverallView" data-field="overall">' +
                    content +
                    '<span class="edit-hint">✎ edit</span>' +
                '</p>' +
            '</div>';
        }
        if (state.overall || state.overallHtml) {
            var body = state.overallHtml || escMath(state.overall);
            return '<div class="fb-overall-box"><h4>Overall Feedback</h4><p>' + body + '</p></div>';
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
        var corrPill = document.getElementById(state.prefix + 'CorrPill');
        if (corrPill) {
            corrPill.addEventListener('click', function () { toggleCorrections(state); });
        }
    }

    function goQ(state, idx) {
        if (idx < 0 || idx >= state.questions.length) return;
        state.currentQ = idx;
        // Clicking any question dot returns us to question-card mode.
        if (state.mode !== 'questions') setMode(state, 'questions');
        renderQuestion(state);
    }

    function toggleCorrections(state) {
        setMode(state, state.mode === 'corrections' ? 'questions' : 'corrections');
    }

    function setMode(state, mode) {
        state.mode = mode;
        var qCard = document.getElementById(state.prefix + 'QCardContainer');
        var qNav = document.getElementById(state.prefix + 'QNavRow');
        var corrPanel = document.getElementById(state.prefix + 'CorrPanel');
        var corrPill = document.getElementById(state.prefix + 'CorrPill');
        if (mode === 'corrections') {
            if (qCard) qCard.style.display = 'none';
            if (qNav) qNav.style.display = 'none';
            if (corrPanel) corrPanel.style.display = 'block';
            if (corrPill) {
                corrPill.classList.add('active');
                corrPill.setAttribute('aria-pressed', 'true');
            }
            // De-activate every question dot so it's clear no question is
            // selected while we're in corrections view.
            state.containerEl.querySelectorAll('.fb-q-dot').forEach(function (d) {
                d.classList.remove('active');
            });
            renderCorrections(state);
        } else {
            if (qCard) qCard.style.display = '';
            if (qNav) qNav.style.display = '';
            if (corrPanel) corrPanel.style.display = 'none';
            if (corrPill) {
                corrPill.classList.remove('active');
                corrPill.setAttribute('aria-pressed', 'false');
            }
            renderQuestion(state);
        }
    }

    // Build the back-and-forth corrections thread, grouped by question.
    // Each question card shows: header → AI's original feedback → an
    // alternating sequence of student attempts and AI verdict responses.
    // Verdict colours reuse the same palette used in the student-side
    // "Now You Try" panel: good=green, not_quite=yellow, error=red.
    function renderCorrections(state) {
        var panel = document.getElementById(state.prefix + 'CorrPanel');
        if (!panel) return;
        // Group attempts by question_num, preserving insertion order
        // (the array is appended chronologically server-side).
        var byQ = {};
        var qOrder = [];
        state.corrections.forEach(function (a) {
            var key = String(a.question_num != null ? a.question_num : '');
            if (!(key in byQ)) {
                byQ[key] = [];
                qOrder.push(key);
            }
            byQ[key].push(a);
        });

        // Sort question groups in the same order as the question carousel
        // so the corrections view mirrors the question sequence.
        var qIndex = {};
        state.questions.forEach(function (q, i) {
            qIndex[String(q.question_num != null ? q.question_num : (i + 1))] = i;
        });
        qOrder.sort(function (a, b) {
            var ai = qIndex[a] != null ? qIndex[a] : 999;
            var bi = qIndex[b] != null ? qIndex[b] : 999;
            return ai - bi;
        });

        if (!qOrder.length) {
            panel.innerHTML = '<p style="color:#888;font-style:italic;">No corrections submitted.</p>';
            return;
        }

        var html = '';
        qOrder.forEach(function (qKey) {
            var qIdx = qIndex[qKey];
            var q = qIdx != null ? state.questions[qIdx] : null;
            var heading = q
                ? (state.assignType === 'rubrics'
                    ? (q.criterion_name || ('Criterion ' + qKey))
                    : ('Question ' + (q.question_num || qKey)))
                : ('Question ' + qKey);

            var aiFb = q ? (q.feedback_html || (q.feedback ? escMath(q.feedback) : '')) : '';

            html += '<div class="fb-corr-q-card">';
            html += '<div class="fb-corr-q-header">' + esc(heading) + '</div>';
            if (aiFb) {
                html += '<div class="fb-corr-bubble fb-corr-ai">' +
                            '<div class="fb-corr-bubble-label">AI feedback</div>' +
                            '<div class="fb-corr-bubble-body">' + aiFb + '</div>' +
                        '</div>';
            }
            byQ[qKey].forEach(function (a) {
                html += '<div class="fb-corr-bubble fb-corr-student">' +
                            '<div class="fb-corr-bubble-label">Student wrote</div>' +
                            '<div class="fb-corr-bubble-body">' + escMath(a.text || '') + '</div>' +
                        '</div>';
                var verdictCls = 'good';
                if (a.verdict === 'not_quite') verdictCls = 'not_quite';
                else if (a.verdict === 'error' || a.verdict === 'bad') verdictCls = 'error';
                html += '<div class="fb-corr-verdict ' + verdictCls + '">' +
                            esc(a.message || '') +
                        '</div>';
            });
            html += '</div>';
        });

        panel.innerHTML = html;

        if (window.MathJax && MathJax.typesetPromise) {
            MathJax.typesetPromise([panel]).catch(function () {});
        }
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
            // Editable view: render the annotated _html when present so
            // ruby tags are visible and clickable for the per-word
            // edit popover. When there's no _html (English / non-pinyin
            // assignments), fall back to plain escMath so contenteditable
            // shows clean prose.
            var fbContent = q.feedback_html
                ? q.feedback_html
                : (q.feedback ? escMath(q.feedback) : '<span class="fb-placeholder">Click to add feedback…</span>');
            var impContent = q.improvement_html
                ? q.improvement_html
                : (q.improvement ? escMath(q.improvement) : '<span class="fb-placeholder">Click to add suggested improvement…</span>');
            fbBlock = '<div class="fb-q-field"><div class="fb-q-field-label">Feedback <small style="color:#bbb;font-weight:400;">(click to edit)</small></div>' +
                '<div class="fb-q-field-value feedback fb-editable" data-field="feedback">' + fbContent +
                '<span class="edit-hint">✎ edit</span></div></div>';
            impBlock = '<div class="fb-q-field"><div class="fb-q-field-label">Suggested Improvement <small style="color:#bbb;font-weight:400;">(click to edit)</small></div>' +
                '<div class="fb-q-field-value improvement fb-editable" data-field="improvement">' + impContent +
                '<span class="edit-hint">✎ edit</span></div></div>';
        } else {
            fbBlock = (q.feedback || q.feedback_html) ? '<div class="fb-q-field"><div class="fb-q-field-label">Feedback</div><div class="fb-q-field-value feedback">' + rawOrHtml(q, 'feedback') + '</div></div>' : '';
            impBlock = (q.improvement || q.improvement_html) ? '<div class="fb-q-field"><div class="fb-q-field-label">Suggested Improvement</div><div class="fb-q-field-value improvement">' + rawOrHtml(q, 'improvement') + '</div></div>' : '';
        }

        // Mistake-category field. Sits between Correct Answer and Feedback.
        // Editable mode + lost marks: always visible (so teachers can categorise
        // even when the AI didn't run categorisation, or set a category for a
        // criterion that fell below the worker's 2-criteria threshold).
        // Read-only mode: only visible when a theme_key is already set.
        var mtMC = q.marks_total, maMC = q.marks_awarded;
        var lostByMarks = (mtMC != null && maMC != null && mtMC > 0 && maMC < mtMC);
        var lostByStatus = (!lostByMarks && q.status && q.status !== 'correct');
        var hasLostMarks = lostByMarks || lostByStatus;

        var catBlock = '';
        var themesByKey = {};
        (state.availableThemes || []).forEach(function (t) { themesByKey[t.key] = t; });
        var currentThemeMeta = q.theme_key ? themesByKey[q.theme_key] : null;
        var currentLabel = currentThemeMeta ? currentThemeMeta.label : (q.theme_key || '');
        var specificTxt = q.specific_label ? esc(q.specific_label) : '';
        var corrMark = q.theme_key_corrected
            ? '<span class="fb-cat-corrected" style="color:#8a8db2;margin-left:8px;font-style:normal;" title="Teacher-corrected">✎</span>'
            : '';

        if (state.editable && hasLostMarks && window.TEACHER_THEME_UI_ENABLED === true) {
            // Editable: full clickable field block. Visible label is the
            // human theme label (or "Click to set" placeholder); raw key
            // and specific_label are kept on data-* attrs so saves preserve
            // the AI's original specific_label across category changes.
            // §4.9: only rendered when TEACHER_THEME_UI_ENABLED is explicitly true.
            var displayInner;
            if (q.theme_key) {
                displayInner = '<span class="fb-cat-display-label">' + esc(currentLabel) + '</span>' +
                    (specificTxt ? '<span class="fb-cat-display-specific" style="color:#888;font-style:italic;margin-left:8px;">— ' + specificTxt + '</span>' : '') +
                    corrMark;
            } else {
                displayInner = '<span class="fb-cat-display-placeholder" style="color:#aaa;font-style:italic;">Click to set category</span>';
            }
            catBlock = '<div class="fb-q-field"><div class="fb-q-field-label">Mistake Category <small style="color:#bbb;font-weight:400;">(click to set)</small></div>' +
                '<div class="fb-q-field-value mistake-category fb-cat-trigger" data-field="category" tabindex="0" ' +
                    'data-theme-key="' + esc(q.theme_key || '') + '" ' +
                    'data-specific-label="' + esc(q.specific_label || '') + '">' +
                    displayInner +
                '</div></div>';
        } else if (q.theme_key && window.TEACHER_THEME_UI_ENABLED === true) {
            // Read-only with a category set: show the label inline.
            // §4.9: also gated by TEACHER_THEME_UI_ENABLED so the entire
            // theme/category surface disappears when the flag is off.
            catBlock = '<div class="fb-q-field"><div class="fb-q-field-label">Mistake Category</div>' +
                '<div class="fb-q-field-value mistake-category">' +
                    '<span class="fb-cat-display-label">' + esc(currentLabel) + '</span>' +
                    (specificTxt ? '<span class="fb-cat-display-specific" style="color:#888;font-style:italic;margin-left:8px;">— ' + specificTxt + '</span>' : '') +
                    corrMark +
                '</div></div>';
        }

        var cardClass = 'fb-q-card status-' + statusCls;
        var html = '<div class="' + cardClass + '" id="' + state.prefix + 'QCard">' +
            '<div class="fb-q-card-header"><span class="fb-q-num">' + esc(headerLabel) + bandInfo + '</span>' +
                '<span id="' + state.prefix + 'StatusBadgeWrap">' + badge + '</span>' +
            '</div>' +
            '<div class="fb-q-card-body">' +
                '<div class="fb-q-field"><div class="fb-q-field-label">' + ansLabel + '</div><div class="fb-q-field-value">' + (q.student_answer_html || escMath(q.student_answer || 'N/A')) + '</div></div>' +
                '<div class="fb-q-field"><div class="fb-q-field-label">' + refLabel + '</div><div class="fb-q-field-value">' + (q.correct_answer_html || escMath(q.correct_answer || 'N/A')) + '</div></div>' +
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

        // Initial-load: render any per-field tag for this question that
        // already has an active calibration row (server populates state via
        // text_edit_meta on the GET response).
        if (state.textEditMeta) {
            var qNow = state.questions[state.currentQ];
            if (qNow) {
                var qKey = String(qNow.question_num != null ? qNow.question_num : (state.currentQ + 1));
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
            el.addEventListener('click', function (ev) {
                if (el.dataset.editing === '1') return;
                // If the click landed on a <ruby> annotation, open the
                // per-word pinyin editor instead of switching the whole
                // field to plain-text contenteditable.
                var ruby = ev.target && ev.target.closest ? ev.target.closest('ruby') : null;
                if (ruby && el.contains(ruby)) {
                    ev.stopPropagation();
                    openRubyPopover(state, el.getAttribute('data-field'), { rubyEl: ruby });
                    return;
                }
                beginTextEdit(state, el, el.getAttribute('data-field'));
            });
        });
        attachCategoryLineHandler(state, card);
    }

    // ------------------------------------------------------------------
    // Per-ruby pinyin edit popover
    // ------------------------------------------------------------------
    var rubyPopover = null;

    function closeRubyPopover() {
        if (rubyPopover && rubyPopover.parentNode) rubyPopover.parentNode.removeChild(rubyPopover);
        rubyPopover = null;
        document.removeEventListener('mousedown', rubyOutsideClose, true);
    }

    function rubyOutsideClose(ev) {
        if (!rubyPopover) return;
        if (rubyPopover.contains(ev.target)) return;
        closeRubyPopover();
    }

    function openRubyPopover(state, field, opts) {
        // Two callers: clicking an existing <ruby> (opts.rubyEl provided)
        // or the "+ 拼音" affordance after selecting plain Chinese in the
        // textarea (opts.oldWord + opts.anchorRect provided). Same UI both
        // ways — the server endpoint treats "add" and "edit" identically.
        opts = opts || {};
        closeRubyPopover();

        var oldWord, oldPinyin, positionRect;
        if (opts.rubyEl) {
            var clone = opts.rubyEl.cloneNode(true);
            var rtClone = clone.querySelector('rt');
            oldPinyin = rtClone ? (rtClone.textContent || '').trim() : '';
            if (rtClone) rtClone.remove();
            oldWord = (clone.textContent || '').trim();
            positionRect = opts.rubyEl.getBoundingClientRect();
        } else {
            oldWord = (opts.oldWord || '').trim();
            oldPinyin = (opts.oldPinyin || '').trim();
            positionRect = opts.anchorRect || { top: 0, left: 0, bottom: 0 };
        }
        if (!oldWord) return;

        var pop = document.createElement('div');
        pop.className = 'fb-ruby-edit-pop';
        pop.style.cssText =
            'position: absolute; z-index: 9999;' +
            'background: white; border: 1px solid #d8d8dc; border-radius: 8px;' +
            'box-shadow: 0 6px 22px rgba(0,0,0,0.18);' +
            'padding: 10px 12px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap;';
        pop.innerHTML =
            '<label style="font-size:11px;color:#777;">中文</label>' +
            '<input class="fb-ruby-zh" style="font-size:16px; padding:4px 8px; border:1px solid #d8d8dc; border-radius:5px; min-width:80px;">' +
            '<label style="font-size:11px;color:#777;">拼音</label>' +
            '<input class="fb-ruby-py" style="font-size:13px; padding:4px 8px; border:1px solid #d8d8dc; border-radius:5px; color:#5b6cf0; min-width:140px;" placeholder="cheng2yu3 → chéngyǔ">' +
            '<button type="button" class="fb-ruby-save" style="font-size:12px; padding:5px 10px; border:none; border-radius:5px; background:#5b6cf0; color:white; cursor:pointer; font-weight:600;">Save</button>' +
            '<button type="button" class="fb-ruby-cancel" style="font-size:12px; padding:5px 10px; border:none; border-radius:5px; background:#e8e8ec; color:#555; cursor:pointer;">Cancel</button>' +
            '<div style="flex:1 1 100%; font-size:11px; color:#888; margin-top:2px;">Tip: type <code>cheng2yu3</code> → <code style="color:#5b6cf0">chéngyǔ</code> (1=ā 2=á 3=ǎ 4=à 5=neutral, v=ü)</div>';
        document.body.appendChild(pop);
        rubyPopover = pop;

        // Position below the anchor.
        pop.style.top = (positionRect.bottom + window.scrollY + 6) + 'px';
        pop.style.left = (positionRect.left + window.scrollX) + 'px';

        var zhI = pop.querySelector('.fb-ruby-zh');
        var pyI = pop.querySelector('.fb-ruby-py');
        zhI.value = oldWord;
        pyI.value = oldPinyin;
        // For "edit existing" focus pinyin (more common edit). For "add
        // new" the pinyin field is empty so focus it for typing.
        pyI.focus();
        if (oldPinyin) pyI.select();

        // Numbered pinyin → tone marks. Convert as the teacher types so
        // they get instant visual feedback after every digit. Cursor is
        // restored after the substitution so typing keeps flowing.
        pyI.addEventListener('input', function () {
            if (!/[1-5vV]/.test(pyI.value)) return;
            var prev = pyI.value;
            var caret = pyI.selectionStart;
            var converted = numPyToToneMarks(prev);
            if (converted === prev) return;
            pyI.value = converted;
            // Best-effort cursor restoration: shift left by however many
            // chars the conversion shrank the string by.
            var shift = prev.length - converted.length;
            var newCaret = Math.max(0, caret - shift);
            pyI.setSelectionRange(newCaret, newCaret);
        });

        pop.querySelector('.fb-ruby-save').addEventListener('click', function () {
            saveRubyEdit(state, field, oldWord, zhI.value.trim(), pyI.value.trim());
        });
        pop.querySelector('.fb-ruby-cancel').addEventListener('click', closeRubyPopover);
        pop.addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter') {
                ev.preventDefault();
                saveRubyEdit(state, field, oldWord, zhI.value.trim(), pyI.value.trim());
            } else if (ev.key === 'Escape') {
                ev.preventDefault();
                closeRubyPopover();
            }
        });
        // Defer outside-click handler so the click that opened us doesn't
        // immediately close it.
        setTimeout(function () { document.addEventListener('mousedown', rubyOutsideClose, true); }, 0);
    }

    function saveRubyEdit(state, field, oldWord, newWord, newPinyin) {
        if (!oldWord || !newWord) { closeRubyPopover(); return; }
        var qIdx = state.currentQ;
        var qNum = state.questions[qIdx] && state.questions[qIdx].question_num;
        var url = '/teacher/assignment/' + encodeURIComponent(state.assignmentId) +
                  '/submission/' + encodeURIComponent(state.submissionId) +
                  '/result/pinyin';
        var body = {
            question_num: qNum != null ? qNum : null,
            field: field,
            old_word: oldWord,
            new_word: newWord,
            new_pinyin: newPinyin,
        };
        fetch(url, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (!data.success) {
                alert(data.error || 'Failed to save edit.');
                return;
            }
            // Refresh the in-memory questions array with the server's
            // re-annotated result and re-render the current card.
            var newResult = data.result || {};
            if (Array.isArray(newResult.questions)) {
                state.questions = newResult.questions;
            }
            state.overall = newResult.overall_feedback || state.overall;
            state.overallHtml = newResult.overall_feedback_html || '';
            closeRubyPopover();
            try { renderQuestion(state); } catch (e) {}
        })
        .catch(function () {
            alert('Network error. Please try again.');
        });
    }

    function attachCategoryLineHandler(state, card) {
        // Spec 2026-05-13 §4.9: hide the teacher-facing theme/category
        // dropdown by default. Categorisation pipeline keeps running on the
        // server — only the click-to-edit surface is suppressed.
        if (window.TEACHER_THEME_UI_ENABLED !== true) {
            return;
        }
        var trigger = card.querySelector('.fb-cat-trigger');
        if (!trigger || trigger.dataset.bound === '1') return;
        trigger.dataset.bound = '1';

        var themes = (state.availableThemes || []);
        var dropdown = null;
        var highlightIdx = -1;

        function renderTriggerInner(themeKey, specificLabel, corrected) {
            var meta = themes.find(function (t) { return t.key === themeKey; });
            var lbl = meta ? meta.label : themeKey;
            var spec = specificLabel ? esc(specificLabel) : '';
            var corrMark = corrected
                ? '<span class="fb-cat-corrected" style="color:#8a8db2;margin-left:8px;font-style:normal;" title="Teacher-corrected">✎</span>'
                : '';
            if (themeKey) {
                trigger.innerHTML = '<span class="fb-cat-display-label">' + esc(lbl) + '</span>' +
                    (spec ? '<span class="fb-cat-display-specific" style="color:#888;font-style:italic;margin-left:8px;">— ' + spec + '</span>' : '') +
                    corrMark;
            } else {
                trigger.innerHTML = '<span class="fb-cat-display-placeholder" style="color:#aaa;font-style:italic;">Click to set category</span>';
            }
        }

        function onDocMouseDown(ev) {
            if (!dropdown) return;
            if (dropdown.contains(ev.target) || trigger.contains(ev.target)) return;
            hideDropdown();
        }

        // Close on any scroll — the dropdown is absolute-positioned to the
        // body at click time, so once the modal box (or window) scrolls,
        // it would otherwise float over unrelated content. Capture phase
        // catches scroll events from the nested scroll container too.
        function onAnyScroll() { hideDropdown(); }

        function showDropdown() {
            if (dropdown) return;
            if (!themes.length) return;  // nothing to choose from
            dropdown = document.createElement('div');
            dropdown.className = 'fb-cat-dropdown';
            dropdown.style.cssText = 'position:absolute; background:white; border:1px solid #c5cbe8; border-radius:8px; box-shadow:0 6px 16px rgba(0,0,0,0.14); padding:4px 0; font-size:13px; z-index:99999; min-width:240px; max-width:320px; max-height:280px; overflow-y:auto;';
            themes.forEach(function (t, i) {
                var item = document.createElement('div');
                item.className = 'fb-cat-dropdown-item';
                item.style.cssText = 'padding:8px 14px; cursor:pointer; color:#333; font-weight:600; line-height:1.3;';
                item.textContent = t.label;
                item.addEventListener('mousedown', function (ev) {
                    ev.preventDefault();  // keep focus; fires before blur
                    applyKey(t.key);
                });
                item.addEventListener('mouseenter', function () { highlight(i); });
                dropdown.appendChild(item);
            });
            document.body.appendChild(dropdown);
            var rect = trigger.getBoundingClientRect();
            dropdown.style.top = (rect.bottom + window.scrollY + 2) + 'px';
            dropdown.style.left = (rect.left + window.scrollX) + 'px';

            // Pre-highlight the current selection.
            var currentKey = trigger.getAttribute('data-theme-key') || '';
            if (currentKey) {
                var idx = themes.findIndex(function (t) { return t.key === currentKey; });
                if (idx >= 0) highlight(idx);
            }

            document.addEventListener('mousedown', onDocMouseDown);
            window.addEventListener('scroll', onAnyScroll, true);
        }

        function hideDropdown() {
            document.removeEventListener('mousedown', onDocMouseDown);
            window.removeEventListener('scroll', onAnyScroll, true);
            if (dropdown) { dropdown.remove(); dropdown = null; }
            highlightIdx = -1;
        }

        function highlight(i) {
            if (!dropdown) return;
            highlightIdx = i;
            var children = dropdown.children;
            for (var idx = 0; idx < children.length; idx++) {
                children[idx].style.background = (idx === i) ? '#eef1ff' : 'white';
            }
            // Keep highlighted item visible.
            var el = children[i];
            if (el && el.scrollIntoView) {
                try { el.scrollIntoView({ block: 'nearest' }); } catch (e) {}
            }
        }

        function applyKey(k) {
            var prevKey = trigger.getAttribute('data-theme-key') || '';
            if (k === prevKey) { hideDropdown(); return; }
            var specificLabel = trigger.getAttribute('data-specific-label') || '';
            // Optimistic update — re-rendered from server response below.
            trigger.setAttribute('data-theme-key', k);
            renderTriggerInner(k, specificLabel, false);
            hideDropdown();

            var q = state.questions[state.currentQ];
            if (!q) return;
            var savedQNum = q.question_num != null ? q.question_num : (state.currentQ + 1);

            patchResult(state, {
                questions: [{
                    question_num: savedQNum,
                    theme_key: k,
                    specific_label: specificLabel,
                }]
            }).then(function (data) {
                if (!data || !data.success) {
                    trigger.setAttribute('data-theme-key', prevKey);
                    renderTriggerInner(prevKey, specificLabel, !!q.theme_key_corrected);
                    return;
                }
                var newQ = ((data.result && data.result.questions) || []).find(function (qq) {
                    return String(qq.question_num) === String(savedQNum);
                });
                if (!newQ) {
                    trigger.setAttribute('data-theme-key', prevKey);
                    renderTriggerInner(prevKey, specificLabel, !!q.theme_key_corrected);
                    return;
                }
                var serverTk = newQ.theme_key || '';
                var serverLabel = newQ.specific_label || '';
                trigger.setAttribute('data-theme-key', serverTk);
                trigger.setAttribute('data-specific-label', serverLabel);
                renderTriggerInner(serverTk, serverLabel, !!newQ.theme_key_corrected);
                state.questions[state.currentQ].theme_key = serverTk;
                state.questions[state.currentQ].specific_label = serverLabel;
                state.questions[state.currentQ].theme_key_corrected = !!newQ.theme_key_corrected;
            }).catch(function () {
                trigger.setAttribute('data-theme-key', prevKey);
                renderTriggerInner(prevKey, specificLabel, !!q.theme_key_corrected);
            });
        }

        trigger.addEventListener('click', function () {
            if (dropdown) { hideDropdown(); } else { showDropdown(); }
        });
        trigger.addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter' || ev.key === ' ') {
                ev.preventDefault();
                if (!dropdown) showDropdown();
                else if (highlightIdx >= 0) applyKey(themes[highlightIdx].key);
            } else if (ev.key === 'ArrowDown') {
                ev.preventDefault();
                if (!dropdown) showDropdown();
                var next = highlightIdx < 0 ? 0 : (highlightIdx + 1) % themes.length;
                highlight(next);
            } else if (ev.key === 'ArrowUp') {
                ev.preventDefault();
                if (!dropdown) showDropdown();
                var prev = highlightIdx <= 0 ? themes.length - 1 : highlightIdx - 1;
                highlight(prev);
            } else if (ev.key === 'Escape') {
                ev.preventDefault();
                hideDropdown();
                trigger.blur();
            }
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
        // The element holding the textarea becomes the relative parent for
        // the floating "+ 拼音" affordance.
        el.style.position = 'relative';
        el.appendChild(textarea);

        // ------------------------------------------------------------------
        // "+ 拼音" affordance: appears when the teacher selects Chinese
        // characters inside the textarea, lets them add pinyin to a word
        // that wasn't auto-annotated. Only meaningful on assignments where
        // pinyin annotation is enabled (state.questions carry _html fields
        // when the assignment's pinyin_mode != 'off') — otherwise we still
        // show the button on selection, the server validates and rejects.
        var addPinyinBtn = document.createElement('button');
        addPinyinBtn.type = 'button';
        addPinyinBtn.className = 'fb-add-pinyin-btn';
        addPinyinBtn.textContent = '＋ 拼音';
        addPinyinBtn.style.cssText =
            'position: absolute; top: 6px; right: 6px;' +
            'padding: 6px 12px; min-height: 30px;' +
            'background: #5b6cf0; color: white;' +
            'border: none; border-radius: 6px;' +
            'font-size: 13px; font-weight: 600; cursor: pointer;' +
            'box-shadow: 0 2px 8px rgba(0,0,0,0.18);' +
            'opacity: 0; transform: scale(0.85); pointer-events: none;' +
            'transition: opacity 0.12s, transform 0.12s;' +
            'z-index: 20;';
        el.appendChild(addPinyinBtn);

        var CJK_CHECK = /[一-鿿]/;

        function updateAddPinyinBtn() {
            var sel = textarea.value
                .substring(textarea.selectionStart, textarea.selectionEnd)
                .trim();
            if (sel && CJK_CHECK.test(sel)) {
                addPinyinBtn.dataset.word = sel;
                addPinyinBtn.style.opacity = '1';
                addPinyinBtn.style.transform = 'scale(1)';
                addPinyinBtn.style.pointerEvents = 'auto';
            } else {
                addPinyinBtn.dataset.word = '';
                addPinyinBtn.style.opacity = '0';
                addPinyinBtn.style.transform = 'scale(0.85)';
                addPinyinBtn.style.pointerEvents = 'none';
            }
        }
        textarea.addEventListener('select', updateAddPinyinBtn);
        textarea.addEventListener('mouseup', updateAddPinyinBtn);
        textarea.addEventListener('keyup', updateAddPinyinBtn);

        // Don't blur the textarea when pressing the button (would commit).
        addPinyinBtn.addEventListener('mousedown', function (ev) { ev.preventDefault(); });
        addPinyinBtn.addEventListener('click', async function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var word = (addPinyinBtn.dataset.word || '').trim();
            if (!word) return;
            // Commit the prose first so any unsaved edits land before the
            // override is applied. After commit the field re-renders out of
            // edit mode; we then float the popover anchored at that field.
            submitted = true;            // block blur-driven re-commit
            textarea.removeEventListener('blur', commit);
            var newVal = textarea.value;
            var amend = !!(amendCb && amendCb.checked);
            var promote = !!(promoteCb && promoteCb.checked);
            var changed = (amend !== initialAmend) || (promote !== initialPromote);
            try {
                if (newVal !== currentValue || changed) {
                    await saveTextField(state, field, newVal, amend, promote);
                } else {
                    if (field === 'overall') renderShell(state); else renderQuestion(state);
                }
            } catch (e) {
                if (field === 'overall') renderShell(state); else renderQuestion(state);
            }
            // After re-render the field's display element has the same
            // data-field attribute. Anchor the popover to that element's
            // top-left so it floats just below the field.
            var anchorEl = document.querySelector('[data-field="' + field + '"]');
            var anchorRect = anchorEl ? anchorEl.getBoundingClientRect() : {
                top: 0, left: 0, bottom: 0,
            };
            openRubyPopover(state, field, {
                oldWord: word,
                oldPinyin: '',
                anchorRect: anchorRect,
            });
        });

        // Two-checkbox intent (spec 2026-05-13 §4.1) — only on feedback / improvement.
        // First box: "Amend answer key for this assignment" (always visible).
        // Second box: "Update subject standards" (hidden on legacy assignments or
        // freeform subjects since server-side enforcement drops it anyway).
        var amendCb = null;
        var promoteCb = null;
        var initialAmend = false;
        var initialPromote = false;
        if (field === 'feedback' || field === 'improvement') {
            var subjectStandardsEnabled = (
                global.ASSIGNMENT_HAS_CANONICAL_SUBJECT === true &&
                global.ASSIGNMENT_TOPIC_KEYS_STATUS !== 'legacy'
            );

            var qNow2 = state.questions[state.currentQ];
            var qKey2 = qNow2 ? String(qNow2.question_num != null ? qNow2.question_num : (state.currentQ + 1)) : null;
            var existingMeta2 = (qKey2 && state.textEditMeta && state.textEditMeta[qKey2] && state.textEditMeta[qKey2][field]) || null;
            if (existingMeta2) {
                initialAmend = !!existingMeta2.amend_answer_key;
                initialPromote = !!existingMeta2.update_subject_standards;
                // Legacy single-toggle back-compat: if only old shape present, treat as amend.
                if (!('amend_answer_key' in existingMeta2) && existingMeta2.calibrated) {
                    initialAmend = true;
                }
            }

            function makeIntentRow(labelText, preChecked) {
                var wrap = document.createElement('div');
                wrap.className = 'fb-cal-wrap';
                wrap.style.cssText = 'display:flex;align-items:center;gap:6px;margin-top:6px;font-size:12px;color:#666;cursor:pointer;user-select:none;';
                var cb = document.createElement('input');
                cb.type = 'checkbox';
                cb.style.cssText = 'margin:0;pointer-events:none;';
                cb.checked = !!preChecked;
                wrap.appendChild(cb);
                wrap.appendChild(document.createTextNode(labelText));
                wrap.addEventListener('mousedown', function (ev) { ev.preventDefault(); });
                wrap.addEventListener('click', function (ev) {
                    ev.preventDefault();
                    ev.stopPropagation();
                    cb.checked = !cb.checked;
                });
                el.appendChild(wrap);
                return cb;
            }

            amendCb = makeIntentRow('Amend answer key for this assignment', initialAmend);
            amendCb.className = 'fb-cal-cb fv-amend-answer-key';
            if (subjectStandardsEnabled) {
                promoteCb = makeIntentRow('Update subject standards', initialPromote);
                promoteCb.className = 'fb-cal-cb fv-update-subject-standards';
            }
        }

        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);

        // LaTeX autocomplete: typing a backslash pops a keyboard-driven menu
        // of common math commands (ArrowUp/Down to navigate, Tab/Enter to
        // insert, Escape to dismiss). Attaches only when the module is loaded.
        if (global.LatexAutocomplete && global.LatexAutocomplete.attach) {
            global.LatexAutocomplete.attach(textarea);
        }

        var submitted = false;
        function commit() {
            if (submitted) return;
            submitted = true;
            var newVal = textarea.value;
            var amend = !!(amendCb && amendCb.checked);
            var promote = !!(promoteCb && promoteCb.checked);
            var changed = (amend !== initialAmend) || (promote !== initialPromote);
            // Skip the round-trip only when nothing changed: same text AND
            // same intent state. If the teacher changed either checkbox (or
            // changed the text), we need to round-trip so the server can
            // write/deactivate the bank row.
            if (newVal === currentValue && !changed) {
                if (field === 'overall') renderShell(state);
                else renderQuestion(state);
                return;
            }
            saveTextField(state, field, newVal, amend, promote);
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
        // Return full response so callers can inspect edit_meta + auto_propagation
        // alongside the merged result_json.
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

    async function saveTextField(state, field, newValue, amendAnswerKey, updateSubjectStandards) {
        if (amendAnswerKey === undefined) amendAnswerKey = false;
        if (updateSubjectStandards === undefined) updateSubjectStandards = false;
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
                qEdit.amend_answer_key = !!amendAnswerKey;
                qEdit.update_subject_standards = !!updateSubjectStandards;
            }
            payload = { questions: [qEdit] };
        }
        try {
            var data = await patchResult(state, payload);
            mergeResult(state, data.result);
            if (field === 'overall') renderShell(state); else renderQuestion(state);
            showToast('success', 'Saved');
            if (state.onSave) { try { state.onSave(data.result); } catch (e) {} }
            // Reflect the server-confirmed calibration state. calibrated:true
            // means a row was written/affirmed → render the indicator.
            // calibrated:false means the prior row was deactivated (user
            // unchecked the box) → drop the indicator and the cached meta.
            if (data && data.edit_meta && savedQNum != null && !data.calibration_warning) {
                var qKey = String(savedQNum);
                var fieldMeta = (data.edit_meta[qKey] || {})[field];
                if (fieldMeta) {
                    var isActive = !!(fieldMeta.amend_answer_key || fieldMeta.update_subject_standards);
                    // Back-compat: if the server didn't return the new flags but the old `calibrated`,
                    // fall back to that.
                    if (!('amend_answer_key' in fieldMeta) && fieldMeta.calibrated) {
                        isActive = true;
                    }
                    if (!isActive) {
                        if (state.textEditMeta && state.textEditMeta[qKey]) {
                            delete state.textEditMeta[qKey][field];
                        }
                        removeEditTag(state, state.currentQ, field);
                    } else {
                        if (!state.textEditMeta) state.textEditMeta = {};
                        if (!state.textEditMeta[qKey]) state.textEditMeta[qKey] = {};
                        state.textEditMeta[qKey][field] = fieldMeta;
                        renderEditTag(state, state.currentQ, field, fieldMeta);
                    }
                }
            }
            // Auto-propagation: server kicked off the worker for all matching
            // candidates (or surfaced 0 candidates so we tell the teacher the
            // calibration save was accepted but nothing else needs updating).
            if (data && data.auto_propagation) {
                var n = data.auto_propagation.candidate_count || 0;
                if (n > 0) {
                    showToast('success',
                        'Auto-applying to ' + n + ' similar answer' + (n === 1 ? '' : 's') + '…');
                } else {
                    showToast('success',
                        'Calibration saved. No other answers needed updating.');
                }
            }
            if (data && data.calibration_warning) {
                showToast('error', data.calibration_warning);
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

    // -------------------------------------------------------------------
    // Calibration bank: per-field tag + retire + view-history link
    // -------------------------------------------------------------------

    function removeEditTag(state, idx, field) {
        var prefix = state.prefix || 'fb';
        var rowId = prefix + 'TagRow-' + idx + '-' + field;
        var existing = document.getElementById(rowId);
        if (existing) existing.remove();
    }

    function renderEditTag(state, idx, field, meta) {
        // Only render for active calibration rows. Anything else (uncheck,
        // retire, no meta) routes through removeEditTag.
        var isActive = !!(meta && (meta.amend_answer_key || meta.update_subject_standards || meta.calibrated));
        if (!isActive) {
            removeEditTag(state, idx, field);
            return;
        }
        var prefix = state.prefix || 'fb';
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
        tag.className = 'fb-edit-tag fb-tag-cal';
        var amend = !!(meta && meta.amend_answer_key);
        var promote = !!(meta && meta.update_subject_standards);
        if (amend && promote) {
            tag.textContent = '✓ Amended answer key and promoted to subject standards';
        } else if (promote) {
            tag.textContent = '✓ Promoted to subject standards';
        } else if (amend) {
            tag.textContent = '✓ Amended answer key for this assignment';
        } else {
            // Old-shape back-compat
            tag.textContent = '✓ Saved to calibration bank — your edit will help calibrate similar answers';
        }
        row.appendChild(tag);
        if (meta.edit_id) {
            var retire = document.createElement('a');
            retire.href = '#';
            retire.className = 'fb-retire-link';
            retire.style.cssText = 'color:#b94a48;text-decoration:none;';
            retire.textContent = 'Retire';
            retire.title = 'Remove this edit from your calibration bank — it will no longer influence future marking.';
            retire.addEventListener('click', function (ev) {
                ev.preventDefault();
                ev.stopPropagation();
                fbRetireEdit(state, idx, field, meta.edit_id);
            });
            row.appendChild(retire);
        }
        // History link — visible whenever there's a versioned log entry.
        if (meta.version && meta.version >= 1) {
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
        }
        if (fieldEl.parentNode) {
            fieldEl.parentNode.insertBefore(row, fieldEl.nextSibling);
        }
    }

    function fbRetireEdit(state, idx, field, editId) {
        // Per-field tag retire (X next to the indicator). Drops the tag and
        // the cached meta on success so the box reopens unchecked.
        fetch('/feedback/deprecate-edit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ edit_id: editId }),
        }).then(function (r) { return r.json(); }).then(function (data) {
            if (data && data.status === 'ok') {
                var q = state.questions[idx];
                var qKey = q ? String(q.question_num != null ? q.question_num : (idx + 1)) : null;
                if (qKey && state.textEditMeta && state.textEditMeta[qKey]) {
                    delete state.textEditMeta[qKey][field];
                }
                removeEditTag(state, idx, field);
            }
        }).catch(function () { /* silent */ });
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
                            retireEditFromHistory(state, idx, editId, panel);
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

    function retireEditFromHistory(state, idx, editId, panel) {
        // History-panel retire (works inside the expanded version list).
        // On success, re-fetch the history so the version flips to "retired".
        fetch('/feedback/deprecate-edit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ edit_id: editId }),
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data && data.status === 'ok') {
                    panel.textContent = 'Loading…';
                    fetchAndRenderHistory(state, idx, panel);
                } else {
                    panel.textContent = 'Could not retire: ' + ((data && data.message) || 'unknown error');
                }
            })
            .catch(function () { panel.textContent = 'Could not retire (network).'; });
    }

    // -------------------------------------------------------------------
    // Propagation banner — kept for future re-enable; auto-apply behavior
    // means no caller currently invokes fbShowPropagationBanner.
    // -------------------------------------------------------------------


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
            var critLabel = prompt.criterion_name || prompt.criterion_id || 'this criterion';
            text.textContent = '⟳ ' + prompt.candidate_count + ' other student' +
                (prompt.candidate_count === 1 ? '' : 's') +
                ' have similar mistakes on ' + critLabel + '.';
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
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ edit_id: editId, mode: 'all' }),
        }).then(function (r) { return r.json(); }).then(function (data) {
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
        fetch('/feedback/propagation-candidates/' + editId, { credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data || !data.candidates) {
                    review.textContent = 'Could not load candidates.';
                    return;
                }
                review.innerHTML = '';
                (data.candidates || []).forEach(function (c) {
                    var row = document.createElement('div');
                    row.style.cssText = 'padding:8px 10px;margin-bottom:6px;border:1px solid #e3e6f0;border-radius:6px;';
                    var head = document.createElement('label');
                    head.style.cssText = 'display:flex;align-items:center;gap:8px;font-weight:600;font-size:12.5px;cursor:pointer;';
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
                    // Student's extracted answer — so the teacher can confirm
                    // the calibration applies to what the student actually wrote.
                    if (c.student_answer) {
                        var ans = document.createElement('div');
                        ans.style.cssText = 'margin-top:6px;padding-left:24px;font-size:12px;color:#333;';
                        var ansLbl = document.createElement('span');
                        ansLbl.style.cssText = 'color:#888;font-weight:600;';
                        ansLbl.textContent = "Student's answer: ";
                        ans.appendChild(ansLbl);
                        ans.appendChild(document.createTextNode(c.student_answer));
                        row.appendChild(ans);
                    }
                    var fb = document.createElement('div');
                    fb.style.cssText = 'margin-top:4px;padding-left:24px;font-size:12px;color:#555;';
                    var fbLbl = document.createElement('span');
                    fbLbl.style.cssText = 'color:#888;font-weight:600;';
                    fbLbl.textContent = 'Current feedback: ';
                    fb.appendChild(fbLbl);
                    fb.appendChild(document.createTextNode(c.current_feedback || '(no feedback)'));
                    row.appendChild(fb);
                    review.appendChild(row);
                });
                var actions = document.createElement('div');
                actions.style.cssText = 'margin-top:10px;display:flex;gap:8px;';
                var confirm = document.createElement('button');
                confirm.type = 'button';
                confirm.className = 'upload-btn';
                confirm.style.cssText = 'padding:6px 12px;font-size:12.5px;';
                confirm.textContent = 'Apply to selected';
                confirm.addEventListener('click', fbPropagateSelectedConfirm);
                actions.appendChild(confirm);
                var cancel = document.createElement('button');
                cancel.type = 'button';
                cancel.className = 'upload-btn';
                cancel.style.cssText = 'padding:6px 12px;font-size:12.5px;';
                cancel.textContent = 'Cancel';
                cancel.addEventListener('click', function () {
                    var rev = document.getElementById('fbPropagationBannerReview');
                    if (rev) { rev.hidden = true; rev.innerHTML = ''; }
                });
                actions.appendChild(cancel);
                review.appendChild(actions);
            })
            .catch(function () { review.textContent = 'Could not load candidates.'; });
    }

    function fbPropagateSelectedConfirm() {
        var editId = fbBannerEditId();
        var review = document.getElementById('fbPropagationBannerReview');
        if (!editId || !review) return;
        var ids = [];
        review.querySelectorAll('input[type="checkbox"]').forEach(function (cb) {
            if (cb.checked && cb.dataset.sid) ids.push(parseInt(cb.dataset.sid, 10));
        });
        if (!ids.length) return;
        fetch('/feedback/propagate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ edit_id: editId, mode: 'selected', submission_ids: ids }),
        }).then(function (r) { return r.json(); }).then(function (data) {
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
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ edit_id: editId }),
        }).then(function () {
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
        var timer = setInterval(function () {
            attempts++;
            if (attempts > 60) { clearInterval(timer); progress.textContent = 'Still running…'; return; }
            fetch('/feedback/propagation-progress/' + editId, { credentials: 'same-origin' })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    if (!data) return;
                    progress.textContent = 'Updating ' + (data.done || 0) + ' of ' + (data.total || 0) + ' students…';
                    if (data.propagation_status === 'complete' || data.propagation_status === 'partial') {
                        clearInterval(timer);
                        var doneN = data.done || 0;
                        var failN = data.failed || 0;
                        progress.textContent = '✓ Feedback updated for ' + doneN + ' student' +
                            (doneN === 1 ? '' : 's') +
                            (failN ? ' · ' + failN + ' failed' : '') + '.';
                        setTimeout(function () {
                            var b = document.getElementById('fbPropagationBanner');
                            if (b) b.hidden = true;
                        }, 4000);
                    }
                })
                .catch(function () { /* silent */ });
        }, 2000);
    }

    global.FeedbackRender = { render: render };
})(window);
