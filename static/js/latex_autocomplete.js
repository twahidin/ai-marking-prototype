// Keyboard-driven LaTeX autocomplete for textareas.
//
// Usage:
//   LatexAutocomplete.attach(textarea);
//
// Triggers when the user types a backslash followed by letters (the standard
// start of a LaTeX command). The typical flow after the user types "$" is to
// type "\frac" / "\int" / "\alpha" etc. — so triggering on "\" covers the
// author's intent without being intrusive when "$" is used in plain text
// (dollar signs, inline prose, etc.).
//
// Navigation:
//   ArrowDown / ArrowUp   — move selection
//   Tab or Enter          — insert the selected snippet
//   Escape                — close the popup (no insertion)
//   Any other key         — typing continues, popup re-filters live
//
// Snippets with placeholders ({} slots) place the caret inside the first
// empty slot so the user can immediately type the next token without
// touching the mouse.

(function (global) {
    'use strict';

    // Snippet table. `insert` is the raw text inserted; `caret` is the offset
    // from the start of `insert` where the caret lands after insertion.
    // `caret == null` means "place caret at the end of `insert`".
    var SNIPPETS = [
        // --- Fractions & roots ---
        { trigger: 'frac',     insert: '\\frac{}{}',             caret: 6,  desc: 'Fraction  a/b' },
        { trigger: 'dfrac',    insert: '\\dfrac{}{}',            caret: 7,  desc: 'Display fraction' },
        { trigger: 'sqrt',     insert: '\\sqrt{}',               caret: 6,  desc: 'Square root  √x' },
        { trigger: 'sqrtn',    insert: '\\sqrt[]{}',             caret: 6,  desc: 'nth root  ⁿ√x' },
        { trigger: 'binom',    insert: '\\binom{}{}',            caret: 7,  desc: 'Binomial  (n choose k)' },

        // --- Scripts ---
        { trigger: 'sup',      insert: '^{}',                    caret: 2,  desc: 'Superscript  a^b' },
        { trigger: 'sub',      insert: '_{}',                    caret: 2,  desc: 'Subscript  a_b' },
        { trigger: 'squared',  insert: '^{2}',                   caret: null, desc: 'x squared' },
        { trigger: 'cubed',    insert: '^{3}',                   caret: null, desc: 'x cubed' },

        // --- Calculus ---
        { trigger: 'int',      insert: '\\int  \\,dx',           caret: 5,  desc: 'Indefinite integral ∫' },
        { trigger: 'intdef',   insert: '\\int_{}^{} \\,dx',      caret: 6,  desc: 'Definite integral ∫ₐᵇ' },
        { trigger: 'oint',     insert: '\\oint  \\,dx',          caret: 6,  desc: 'Contour integral ∮' },
        { trigger: 'iint',     insert: '\\iint  \\,dx\\,dy',     caret: 6,  desc: 'Double integral ∬' },
        { trigger: 'ddx',      insert: '\\frac{d}{dx}',          caret: null, desc: 'Derivative  d/dx' },
        { trigger: 'dydx',     insert: '\\frac{dy}{dx}',         caret: null, desc: 'dy/dx' },
        { trigger: 'partial',  insert: '\\frac{\\partial }{\\partial x}', caret: 14, desc: 'Partial derivative ∂/∂x' },
        { trigger: 'lim',      insert: '\\lim_{ \\to }',         caret: 6,  desc: 'Limit' },
        { trigger: 'nabla',    insert: '\\nabla ',               caret: null, desc: 'Nabla ∇' },

        // --- Sums / products ---
        { trigger: 'sum',      insert: '\\sum_{}^{}',            caret: 6,  desc: 'Sum  Σ' },
        { trigger: 'prod',     insert: '\\prod_{}^{}',           caret: 7,  desc: 'Product  Π' },

        // --- Matrices ---
        { trigger: 'pmatrix2', insert: '\\begin{pmatrix}  &  \\\\  &  \\end{pmatrix}', caret: 16, desc: '2×2 matrix (parentheses)' },
        { trigger: 'pmatrix3', insert: '\\begin{pmatrix}  &  &  \\\\  &  &  \\\\  &  &  \\end{pmatrix}', caret: 16, desc: '3×3 matrix (parentheses)' },
        { trigger: 'bmatrix2', insert: '\\begin{bmatrix}  &  \\\\  &  \\end{bmatrix}', caret: 16, desc: '2×2 matrix [brackets]' },
        { trigger: 'vmatrix2', insert: '\\begin{vmatrix}  &  \\\\  &  \\end{vmatrix}', caret: 16, desc: '2×2 determinant' },
        { trigger: 'vec',      insert: '\\vec{}',                caret: 5,  desc: 'Vector arrow' },
        { trigger: 'hat',      insert: '\\hat{}',                caret: 5,  desc: 'Hat' },
        { trigger: 'bar',      insert: '\\bar{}',                caret: 5,  desc: 'Bar' },
        { trigger: 'overline', insert: '\\overline{}',           caret: 10, desc: 'Overline' },

        // --- Relations & operators ---
        { trigger: 'times',    insert: '\\times ',               caret: null, desc: 'Multiply ×' },
        { trigger: 'cdot',     insert: '\\cdot ',                caret: null, desc: 'Dot product ·' },
        { trigger: 'div',      insert: '\\div ',                 caret: null, desc: 'Divide ÷' },
        { trigger: 'pm',       insert: '\\pm ',                  caret: null, desc: 'Plus-minus ±' },
        { trigger: 'mp',       insert: '\\mp ',                  caret: null, desc: 'Minus-plus ∓' },
        { trigger: 'neq',      insert: '\\neq ',                 caret: null, desc: 'Not equal ≠' },
        { trigger: 'approx',   insert: '\\approx ',              caret: null, desc: 'Approximately ≈' },
        { trigger: 'leq',      insert: '\\leq ',                 caret: null, desc: 'Less or equal ≤' },
        { trigger: 'geq',      insert: '\\geq ',                 caret: null, desc: 'Greater or equal ≥' },
        { trigger: 'll',       insert: '\\ll ',                  caret: null, desc: 'Much less ≪' },
        { trigger: 'gg',       insert: '\\gg ',                  caret: null, desc: 'Much greater ≫' },
        { trigger: 'propto',   insert: '\\propto ',              caret: null, desc: 'Proportional to ∝' },
        { trigger: 'equiv',    insert: '\\equiv ',               caret: null, desc: 'Equivalent ≡' },
        { trigger: 'to',       insert: '\\to ',                  caret: null, desc: 'Arrow → (to)' },
        { trigger: 'rightarrow', insert: '\\rightarrow ',        caret: null, desc: 'Right arrow →' },
        { trigger: 'leftarrow',  insert: '\\leftarrow ',         caret: null, desc: 'Left arrow ←' },
        { trigger: 'Rightarrow', insert: '\\Rightarrow ',        caret: null, desc: 'Implies ⇒' },
        { trigger: 'Leftrightarrow', insert: '\\Leftrightarrow ', caret: null, desc: 'Iff ⇔' },
        { trigger: 'infty',    insert: '\\infty ',               caret: null, desc: 'Infinity ∞' },
        { trigger: 'degree',   insert: '^{\\circ}',              caret: null, desc: 'Degree °' },
        { trigger: 'circ',     insert: '\\circ ',                caret: null, desc: 'Circle ∘' },
        { trigger: 'angle',    insert: '\\angle ',               caret: null, desc: 'Angle ∠' },
        { trigger: 'perp',     insert: '\\perp ',                caret: null, desc: 'Perpendicular ⊥' },
        { trigger: 'parallel', insert: '\\parallel ',            caret: null, desc: 'Parallel ∥' },
        { trigger: 'triangle', insert: '\\triangle ',            caret: null, desc: 'Triangle △' },

        // --- Set theory & logic ---
        { trigger: 'in',       insert: '\\in ',                  caret: null, desc: 'Element of ∈' },
        { trigger: 'notin',    insert: '\\notin ',               caret: null, desc: 'Not element of ∉' },
        { trigger: 'subset',   insert: '\\subset ',              caret: null, desc: 'Subset ⊂' },
        { trigger: 'subseteq', insert: '\\subseteq ',            caret: null, desc: 'Subset or equal ⊆' },
        { trigger: 'cup',      insert: '\\cup ',                 caret: null, desc: 'Union ∪' },
        { trigger: 'cap',      insert: '\\cap ',                 caret: null, desc: 'Intersection ∩' },
        { trigger: 'emptyset', insert: '\\emptyset ',            caret: null, desc: 'Empty set ∅' },
        { trigger: 'forall',   insert: '\\forall ',              caret: null, desc: 'For all ∀' },
        { trigger: 'exists',   insert: '\\exists ',              caret: null, desc: 'Exists ∃' },
        { trigger: 'land',     insert: '\\land ',                caret: null, desc: 'And ∧' },
        { trigger: 'lor',      insert: '\\lor ',                 caret: null, desc: 'Or ∨' },
        { trigger: 'neg',      insert: '\\neg ',                 caret: null, desc: 'Not ¬' },

        // --- Common functions ---
        { trigger: 'sin',      insert: '\\sin ',                 caret: null, desc: 'sin' },
        { trigger: 'cos',      insert: '\\cos ',                 caret: null, desc: 'cos' },
        { trigger: 'tan',      insert: '\\tan ',                 caret: null, desc: 'tan' },
        { trigger: 'log',      insert: '\\log ',                 caret: null, desc: 'log' },
        { trigger: 'ln',       insert: '\\ln ',                  caret: null, desc: 'ln' },
        { trigger: 'exp',      insert: '\\exp ',                 caret: null, desc: 'exp' },

        // --- Greek (lowercase) ---
        { trigger: 'alpha',    insert: '\\alpha ',    caret: null, desc: 'α' },
        { trigger: 'beta',     insert: '\\beta ',     caret: null, desc: 'β' },
        { trigger: 'gamma',    insert: '\\gamma ',    caret: null, desc: 'γ' },
        { trigger: 'delta',    insert: '\\delta ',    caret: null, desc: 'δ' },
        { trigger: 'epsilon',  insert: '\\epsilon ',  caret: null, desc: 'ε' },
        { trigger: 'varepsilon', insert: '\\varepsilon ', caret: null, desc: 'ɛ' },
        { trigger: 'zeta',     insert: '\\zeta ',     caret: null, desc: 'ζ' },
        { trigger: 'eta',      insert: '\\eta ',      caret: null, desc: 'η' },
        { trigger: 'theta',    insert: '\\theta ',    caret: null, desc: 'θ' },
        { trigger: 'iota',     insert: '\\iota ',     caret: null, desc: 'ι' },
        { trigger: 'kappa',    insert: '\\kappa ',    caret: null, desc: 'κ' },
        { trigger: 'lambda',   insert: '\\lambda ',   caret: null, desc: 'λ' },
        { trigger: 'mu',       insert: '\\mu ',       caret: null, desc: 'μ' },
        { trigger: 'nu',       insert: '\\nu ',       caret: null, desc: 'ν' },
        { trigger: 'xi',       insert: '\\xi ',       caret: null, desc: 'ξ' },
        { trigger: 'pi',       insert: '\\pi ',       caret: null, desc: 'π' },
        { trigger: 'rho',      insert: '\\rho ',      caret: null, desc: 'ρ' },
        { trigger: 'sigma',    insert: '\\sigma ',    caret: null, desc: 'σ' },
        { trigger: 'tau',      insert: '\\tau ',      caret: null, desc: 'τ' },
        { trigger: 'phi',      insert: '\\phi ',      caret: null, desc: 'φ' },
        { trigger: 'chi',      insert: '\\chi ',      caret: null, desc: 'χ' },
        { trigger: 'psi',      insert: '\\psi ',      caret: null, desc: 'ψ' },
        { trigger: 'omega',    insert: '\\omega ',    caret: null, desc: 'ω' },

        // --- Greek (uppercase) ---
        { trigger: 'Gamma',    insert: '\\Gamma ',    caret: null, desc: 'Γ' },
        { trigger: 'Delta',    insert: '\\Delta ',    caret: null, desc: 'Δ' },
        { trigger: 'Theta',    insert: '\\Theta ',    caret: null, desc: 'Θ' },
        { trigger: 'Lambda',   insert: '\\Lambda ',   caret: null, desc: 'Λ' },
        { trigger: 'Xi',       insert: '\\Xi ',       caret: null, desc: 'Ξ' },
        { trigger: 'Pi',       insert: '\\Pi ',       caret: null, desc: 'Π' },
        { trigger: 'Sigma',    insert: '\\Sigma ',    caret: null, desc: 'Σ' },
        { trigger: 'Phi',      insert: '\\Phi ',      caret: null, desc: 'Φ' },
        { trigger: 'Psi',      insert: '\\Psi ',      caret: null, desc: 'Ψ' },
        { trigger: 'Omega',    insert: '\\Omega ',    caret: null, desc: 'Ω' },

        // --- Misc useful ---
        { trigger: 'text',     insert: '\\text{}',               caret: 6,  desc: 'Plain text inside math' },
        { trigger: 'boxed',    insert: '\\boxed{}',              caret: 7,  desc: 'Boxed answer' },
        { trigger: 'cdots',    insert: '\\cdots ',               caret: null, desc: 'Centered dots ⋯' },
        { trigger: 'ldots',    insert: '\\ldots ',               caret: null, desc: 'Low dots …' },
    ];

    // Caret-position trick: build a hidden mirror div that matches the
    // textarea's typography, pipe the value-up-to-caret into it, and measure
    // where a trailing zero-width span lands. This gives pixel-accurate
    // caret coordinates inside the textarea.
    var MIRROR_PROPS = [
        'boxSizing', 'width', 'height', 'overflowX', 'overflowY',
        'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth',
        'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
        'fontStyle', 'fontVariant', 'fontWeight', 'fontStretch', 'fontSize',
        'fontSizeAdjust', 'lineHeight', 'fontFamily', 'textAlign', 'textTransform',
        'textIndent', 'textDecoration', 'letterSpacing', 'wordSpacing', 'tabSize'
    ];

    function getCaretCoords(textarea, position) {
        var cs = window.getComputedStyle(textarea);
        var mirror = document.createElement('div');
        MIRROR_PROPS.forEach(function (p) { mirror.style[p] = cs[p]; });
        mirror.style.position = 'absolute';
        mirror.style.visibility = 'hidden';
        mirror.style.whiteSpace = 'pre-wrap';
        mirror.style.wordWrap = 'break-word';
        mirror.style.top = '0';
        mirror.style.left = '-9999px';

        mirror.textContent = textarea.value.substring(0, position);
        var span = document.createElement('span');
        // A non-empty trailing char avoids zero-sized measurements on some browsers.
        span.textContent = textarea.value.substring(position) || '.';
        mirror.appendChild(span);
        document.body.appendChild(mirror);

        var result = {
            top: span.offsetTop,
            left: span.offsetLeft,
            height: parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.4,
        };
        document.body.removeChild(mirror);
        return result;
    }

    // Find the LaTeX command prefix currently being typed: the characters
    // between the nearest preceding "\" and the caret (letters only, no
    // whitespace). Returns {start, prefix} where `start` is the index of the
    // backslash; or null when the caret isn't inside a command.
    function findCommandContext(value, caret) {
        var i = caret - 1;
        while (i >= 0) {
            var c = value.charCodeAt(i);
            // a-z or A-Z
            if ((c >= 65 && c <= 90) || (c >= 97 && c <= 122)) {
                i--;
                continue;
            }
            if (value[i] === '\\') {
                return { start: i, prefix: value.substring(i + 1, caret) };
            }
            return null;
        }
        return null;
    }

    function escapeHtml(s) {
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function attach(textarea) {
        if (!textarea || textarea.dataset.latexAcAttached === '1') return;
        textarea.dataset.latexAcAttached = '1';

        var popup = null;
        var filtered = [];
        var selected = 0;
        var active = false;
        var ctxStart = -1;        // index of the \ that opened the command
        var triggerListener;
        var blurListener;
        var keydownListener;

        function ensurePopup() {
            if (popup) return popup;
            popup = document.createElement('div');
            popup.className = 'latex-ac-popup';
            popup.style.cssText = [
                'position:fixed',
                'z-index:9999',
                'background:white',
                'border:1px solid #cbd1e0',
                'border-radius:8px',
                'box-shadow:0 6px 20px rgba(0,0,0,0.18)',
                'font:13px/1.35 "SF Mono", "Menlo", "Courier New", monospace',
                'max-height:260px',
                'overflow-y:auto',
                'min-width:240px',
                'padding:4px 0'
            ].join(';');
            // mousedown (not click) so the textarea's blur doesn't fire before insert
            popup.addEventListener('mousedown', function (e) {
                var row = e.target.closest ? e.target.closest('.latex-ac-item') : null;
                if (!row) return;
                e.preventDefault();
                var idx = parseInt(row.getAttribute('data-idx'), 10);
                if (!isNaN(idx)) { selected = idx; accept(); }
            });
            document.body.appendChild(popup);
            return popup;
        }

        function render() {
            ensurePopup();
            popup.innerHTML = filtered.map(function (s, i) {
                var cls = 'latex-ac-item' + (i === selected ? ' active' : '');
                var bg = i === selected ? 'background:#eef1ff;' : '';
                return (
                    '<div class="' + cls + '" data-idx="' + i + '" style="' + bg +
                    'padding:5px 12px; cursor:pointer; display:flex; align-items:baseline; gap:12px; white-space:nowrap;">' +
                        '<span style="color:#6c4bd3; font-weight:600;">\\' + escapeHtml(s.trigger) + '</span>' +
                        '<span style="color:#888; font-size:11.5px; font-family:-apple-system,Segoe UI,Roboto,sans-serif; flex:1; text-align:right;">' + escapeHtml(s.desc) + '</span>' +
                    '</div>'
                );
            }).join('');
            var activeRow = popup.querySelector('.latex-ac-item.active');
            if (activeRow && activeRow.scrollIntoView) {
                activeRow.scrollIntoView({ block: 'nearest' });
            }
        }

        function position() {
            if (!popup) return;
            var coords = getCaretCoords(textarea, textarea.selectionStart);
            var rect = textarea.getBoundingClientRect();
            var top = rect.top + coords.top - textarea.scrollTop + coords.height + 4;
            var left = rect.left + coords.left - textarea.scrollLeft;
            // Clamp inside viewport.
            var maxTop = window.innerHeight - 280;
            if (top > maxTop) top = Math.max(10, rect.top + coords.top - textarea.scrollTop - 250);
            var maxLeft = window.innerWidth - 260;
            if (left > maxLeft) left = maxLeft;
            popup.style.top = top + 'px';
            popup.style.left = left + 'px';
        }

        function open(prefix) {
            var lower = prefix.toLowerCase();
            filtered = SNIPPETS.filter(function (s) {
                return lower === '' ? true : (s.trigger.toLowerCase().indexOf(lower) === 0);
            });
            if (!filtered.length) { close(); return; }
            // Limit to a reasonable window to keep the popup compact.
            if (filtered.length > 20) filtered = filtered.slice(0, 20);
            if (selected >= filtered.length) selected = 0;
            if (selected < 0) selected = 0;
            active = true;
            render();
            position();
        }

        function close() {
            active = false;
            ctxStart = -1;
            if (popup) { popup.remove(); popup = null; }
        }

        function accept() {
            var snippet = filtered[selected];
            if (!snippet || ctxStart < 0) { close(); return; }
            var value = textarea.value;
            var pos = textarea.selectionStart;
            var before = value.substring(0, ctxStart);
            var after = value.substring(pos);
            var inserted = snippet.insert;
            var newValue = before + inserted + after;
            var caretOffset = snippet.caret != null ? snippet.caret : inserted.length;
            var newCaret = before.length + caretOffset;
            textarea.value = newValue;
            textarea.setSelectionRange(newCaret, newCaret);
            close();
            // Let consumers (live preview, dirty-tracking, etc.) re-sync.
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
        }

        function update() {
            var pos = textarea.selectionStart;
            var value = textarea.value;
            var ctx = findCommandContext(value, pos);
            if (!ctx) { close(); return; }
            ctxStart = ctx.start;
            open(ctx.prefix);
        }

        triggerListener = function () {
            // Defer so selectionStart is post-keystroke.
            setTimeout(update, 0);
        };

        keydownListener = function (e) {
            if (!active) return;
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                selected = (selected + 1) % filtered.length;
                render();
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                selected = (selected - 1 + filtered.length) % filtered.length;
                render();
            } else if (e.key === 'Tab' || e.key === 'Enter') {
                e.preventDefault();
                accept();
            } else if (e.key === 'Escape') {
                e.preventDefault();
                close();
            }
            // Other keys: default handling + our input listener re-filters.
        };

        blurListener = function () {
            // Small delay so the popup's mousedown handler can run first.
            setTimeout(close, 150);
        };

        textarea.addEventListener('input', triggerListener);
        textarea.addEventListener('click', triggerListener);
        textarea.addEventListener('keyup', function (e) {
            // Arrow keys move the caret; re-evaluate context so we close the
            // popup if the user arrows out of the current command.
            if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') triggerListener();
        });
        textarea.addEventListener('keydown', keydownListener);
        textarea.addEventListener('blur', blurListener);

        // Reposition popup on scroll/resize while open.
        window.addEventListener('scroll', function () { if (active) position(); }, true);
        window.addEventListener('resize', function () { if (active) position(); });
    }

    global.LatexAutocomplete = { attach: attach, SNIPPETS: SNIPPETS };
})(window);
