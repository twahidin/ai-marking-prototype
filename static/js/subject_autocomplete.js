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
 * Usage:
 *   <input type="text" class="subject-autocomplete"
 *          data-subject-options='["Art","Biology",...]'>
 *
 * Auto-attaches at DOMContentLoaded. Free-text values that don't match
 * any option are still allowed — the backend classifier handles them.
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

        // Wrap the input in a relative-positioned shell so the dropdown
        // can absolute-position relative to it without polluting layout.
        var wrap = document.createElement('div');
        wrap.style.cssText = 'position:relative;';
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);

        var dropdown = null;
        var highlightIdx = -1;
        var filtered = options.slice();

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

        function refilter() {
            var q = (input.value || '').trim().toLowerCase();
            if (!q) {
                filtered = options.slice();
            } else {
                filtered = options.filter(function (o) {
                    return o.toLowerCase().indexOf(q) !== -1;
                });
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
                empty.textContent = 'No matching subject — your typed value will be used as-is.';
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
            // Keep the highlighted item visible inside the scroll viewport.
            var hi = dropdown.children[i];
            if (hi && hi.scrollIntoView) hi.scrollIntoView({ block: 'nearest' });
        }

        function commit(value) {
            input.value = value;
            destroyDropdown();
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
                    ev.preventDefault();
                    commit(filtered[highlightIdx]);
                } else {
                    // No suggestion to commit — let the keystroke do its
                    // normal thing (Tab moves focus, Enter submits form).
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
            setTimeout(destroyDropdown, 150);
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
