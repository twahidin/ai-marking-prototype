/* Subject autocomplete combobox.
 *
 * A searchable-select that looks like a normal text input: clicking it
 * (or focusing it) opens a light-styled dropdown with all canonical
 * subjects; typing filters the list by case-insensitive substring;
 * Arrow Up/Down navigate; Tab or Enter commits the highlighted match;
 * Escape or outside-click dismisses without changing the value.
 *
 * Replaces <datalist> because the native dropdown looks browser-styled
 * (dark on macOS) and inconsistent across systems. This component
 * matches the visual language of the surrounding form inputs.
 *
 * Strict by default: a value that doesn't exactly match a canonical
 * option (case-insensitive) cannot survive blur — the field reverts to
 * the last committed valid value (or empty). Typing is only a filter
 * affordance; the saved value must come from the dropdown. Opt out
 * with data-allow-freeform="1" if a callsite genuinely needs free text.
 *
 * Usage:
 *   <input type="text" class="subject-autocomplete"
 *          data-subject-options='["Art","Biology",...]'>
 *
 * Auto-attaches at DOMContentLoaded.
 */
(function (global) {
    'use strict';

    function attach(input) {
        if (input.dataset.saBound === '1') return;
        input.dataset.saBound = '1';

        var options;
        try {
            options = JSON.parse(input.dataset.subjectOptions || '[]');
        } catch (e) { options = []; }
        if (!Array.isArray(options) || !options.length) return;

        // Optional aliases map: { "Mathematics": ["maths","a math",...], ... }
        // Used only to widen filter matches — committed value is always the display name.
        var aliasesByDisplay = {};
        try {
            var parsedAliases = JSON.parse(input.dataset.subjectAliases || '{}');
            if (parsedAliases && typeof parsedAliases === 'object') aliasesByDisplay = parsedAliases;
        } catch (e) { aliasesByDisplay = {}; }

        var strict = input.dataset.allowFreeform !== '1';

        // Wrap the input in a relative-positioned shell so the dropdown
        // can absolute-position relative to it without polluting layout.
        var wrap = document.createElement('div');
        wrap.style.cssText = 'position:relative;';
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);

        var dropdown = null;
        var highlightIdx = -1;
        var filtered = options.slice();

        function findCanonical(v) {
            var s = (v || '').trim().toLowerCase();
            if (!s) return '';
            for (var i = 0; i < options.length; i++) {
                if (options[i].toLowerCase() === s) return options[i];
            }
            // Allow exact-alias-match to resolve to the canonical display name
            // (e.g. "poa" → "Principles of Accounts"), so blur after typing
            // a nickname snaps to the canonical form rather than reverting.
            for (var j = 0; j < options.length; j++) {
                var aliases = aliasesByDisplay[options[j]];
                if (!aliases) continue;
                for (var k = 0; k < aliases.length; k++) {
                    if (String(aliases[k]).toLowerCase() === s) return options[j];
                }
            }
            return '';
        }

        // Last committed valid value (always one of `options`, or '').
        // Seed from the current input value if it matches; for legacy
        // freeform values we leave the visible text alone so editing
        // old records isn't confusing — but lastValid stays '' so the
        // first focus/blur normalises it (or the next pick replaces it).
        var lastValid = findCanonical(input.value);
        if (lastValid && input.value !== lastValid) {
            input.value = lastValid;
        }

        function buildDropdown() {
            dropdown = document.createElement('div');
            dropdown.className = 'subject-autocomplete-dropdown';
            dropdown.style.cssText = [
                'position:absolute',
                'top:100%',
                'left:0',
                'right:0',
                'margin-top:4px',
                'background:white',
                'border:1px solid #d0d4e0',
                'border-radius:8px',
                'box-shadow:0 6px 18px rgba(0,0,0,0.10)',
                'max-height:280px',
                'overflow-y:auto',
                'z-index:1000',
                'font-size:14px',
                'color:#2d2d2d',
            ].join(';') + ';';
            wrap.appendChild(dropdown);
            renderItems();
        }

        function destroyDropdown() {
            if (dropdown) {
                dropdown.remove();
                dropdown = null;
            }
            highlightIdx = -1;
        }

        function matchesQuery(opt, q) {
            if (opt.toLowerCase().indexOf(q) !== -1) return true;
            var aliases = aliasesByDisplay[opt];
            if (!aliases || !aliases.length) return false;
            for (var i = 0; i < aliases.length; i++) {
                if (String(aliases[i]).toLowerCase().indexOf(q) !== -1) return true;
            }
            return false;
        }

        function refilter() {
            var q = (input.value || '').trim().toLowerCase();
            if (!q) {
                filtered = options.slice();
            } else {
                filtered = options.filter(function (o) { return matchesQuery(o, q); });
            }
            // Auto-highlight first match when user is typing — Tab/Enter
            // should commit that without forcing arrow-key navigation.
            highlightIdx = filtered.length ? 0 : -1;
        }

        function renderItems() {
            if (!dropdown) return;
            dropdown.innerHTML = '';
            if (!filtered.length) {
                var empty = document.createElement('div');
                empty.style.cssText = 'padding:10px 14px;color:#888;font-style:italic;';
                empty.textContent = strict
                    ? 'No matching subject. Pick one from the list.'
                    : 'No matching subject — your typed value will be used as-is.';
                dropdown.appendChild(empty);
                return;
            }
            filtered.forEach(function (opt, i) {
                var item = document.createElement('div');
                item.className = 'subject-autocomplete-item';
                item.style.cssText = [
                    'padding:9px 14px',
                    'cursor:pointer',
                    'background:' + (i === highlightIdx ? '#eef1ff' : 'white'),
                ].join(';') + ';';
                item.textContent = opt;
                item.addEventListener('mouseenter', function () { highlight(i); });
                // mousedown (not click) so the input doesn't lose focus
                // before we read the selected value.
                item.addEventListener('mousedown', function (ev) {
                    ev.preventDefault();
                    commit(opt);
                });
                dropdown.appendChild(item);
            });
        }

        function highlight(i) {
            if (!dropdown) return;
            highlightIdx = i;
            var children = dropdown.children;
            for (var idx = 0; idx < children.length; idx++) {
                children[idx].style.background = (idx === i) ? '#eef1ff' : 'white';
            }
            var hi = dropdown.children[i];
            if (hi && hi.scrollIntoView) hi.scrollIntoView({ block: 'nearest' });
        }

        function commit(value) {
            input.value = value;
            lastValid = value;
            destroyDropdown();
            input.dispatchEvent(new Event('change', { bubbles: true }));
        }

        // On blur in strict mode, snap back to a valid value.
        // - Exact match → normalise capitalisation.
        // - Empty → leave empty (subject is optional on some forms).
        // - Anything else → revert to lastValid (empty if user never
        //   picked a valid value). The dropdown's empty-state message
        //   already explained that typed text wasn't in the list.
        function enforceStrictOnBlur() {
            if (!strict) return;
            var raw = (input.value || '').trim();
            if (!raw) {
                lastValid = '';
                input.value = '';
                return;
            }
            var exact = findCanonical(raw);
            if (exact) {
                if (input.value !== exact) input.value = exact;
                lastValid = exact;
                return;
            }
            input.value = lastValid || '';
            input.dispatchEvent(new Event('change', { bubbles: true }));
        }

        function open() {
            if (dropdown) return;
            refilter();
            buildDropdown();
        }

        // Open on focus and on click — matches the affordance of a
        // native <select>: clicking the field shows the options.
        input.addEventListener('focus', open);
        input.addEventListener('click', open);

        input.addEventListener('input', function () {
            if (!dropdown) buildDropdown();
            refilter();
            renderItems();
        });

        input.addEventListener('keydown', function (ev) {
            if (ev.key === 'ArrowDown') {
                ev.preventDefault();
                if (!dropdown) { open(); return; }
                if (!filtered.length) return;
                var next = highlightIdx < 0 ? 0 : (highlightIdx + 1) % filtered.length;
                highlight(next);
            } else if (ev.key === 'ArrowUp') {
                ev.preventDefault();
                if (!dropdown) { open(); return; }
                if (!filtered.length) return;
                var prev = highlightIdx <= 0 ? filtered.length - 1 : highlightIdx - 1;
                highlight(prev);
            } else if (ev.key === 'Tab' || ev.key === 'Enter') {
                if (dropdown && filtered.length && highlightIdx >= 0) {
                    if (ev.key === 'Enter') ev.preventDefault();
                    commit(filtered[highlightIdx]);
                } else {
                    // No suggestion to commit — let the keystroke do its
                    // normal thing (Tab moves focus, Enter submits form).
                    // enforceStrictOnBlur will clean up if focus leaves.
                    destroyDropdown();
                }
            } else if (ev.key === 'Escape') {
                if (dropdown) {
                    ev.preventDefault();
                    destroyDropdown();
                }
            }
        });

        input.addEventListener('blur', function () {
            // Slight delay so a click on a dropdown item registers first.
            setTimeout(function () {
                destroyDropdown();
                enforceStrictOnBlur();
            }, 150);
        });
    }

    function attachAll() {
        var inputs = document.querySelectorAll('.subject-autocomplete');
        for (var i = 0; i < inputs.length; i++) attach(inputs[i]);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', attachAll);
    } else {
        attachAll();
    }

    global.SubjectAutocomplete = { attach: attach, attachAll: attachAll };
}(window));
