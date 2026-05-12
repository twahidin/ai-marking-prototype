/* ===================================================================
   AI Marking Demo — Shared JavaScript
   =================================================================== */

/* In-page replacement for window.confirm().
 *
 * Why: iOS Safari (and some Android browsers) silently suppress native
 * JS dialogs once the page has shown any prior dialog. `confirm()` then
 * returns false without ever displaying a dialog, so call sites of the
 * shape `if (!confirm(...)) return;` silently abort and the click looks
 * dead. This utility renders an in-page modal that always shows and
 * resolves with the user's choice — no native-dialog dependency.
 *
 * Usage:
 *   if (!await uiConfirm('Delete this assignment?')) return;
 *   if (!await uiConfirm('Delete?', {okLabel: 'Delete', danger: true})) return;
 */
function uiConfirm(message, opts) {
    opts = opts || {};
    var okLabel = opts.okLabel || 'OK';
    var cancelLabel = opts.cancelLabel || 'Cancel';
    var danger = !!opts.danger;

    if (!document.getElementById('ui-confirm-styles')) {
        var s = document.createElement('style');
        s.id = 'ui-confirm-styles';
        s.textContent =
            '.ui-confirm-overlay{position:fixed;inset:0;background:rgba(0,0,0,0.45);' +
            'display:flex;align-items:center;justify-content:center;z-index:10001;' +
            'animation:ui-confirm-fade 0.12s ease-out;}' +
            '@keyframes ui-confirm-fade{from{opacity:0}to{opacity:1}}' +
            '.ui-confirm-box{background:#fff;border-radius:12px;padding:24px;' +
            'max-width:420px;width:calc(100% - 32px);box-shadow:0 12px 40px rgba(0,0,0,0.25);' +
            'animation:ui-confirm-pop 0.16s cubic-bezier(.5,1.4,.5,1);}' +
            '@keyframes ui-confirm-pop{from{transform:scale(0.92);opacity:0}to{transform:scale(1);opacity:1}}' +
            '.ui-confirm-msg{font-size:15px;color:#222;line-height:1.5;margin-bottom:20px;white-space:pre-wrap;}' +
            '.ui-confirm-btns{display:flex;gap:10px;justify-content:flex-end;flex-wrap:wrap;}' +
            '.ui-confirm-btn{padding:10px 18px;border:none;border-radius:8px;font-size:14px;' +
            'font-weight:600;cursor:pointer;-webkit-tap-highlight-color:transparent;min-width:84px;}' +
            '.ui-confirm-cancel{background:#eef0f4;color:#444;}' +
            '.ui-confirm-cancel:hover{background:#e0e3ea;}' +
            '.ui-confirm-ok{background:#667eea;color:#fff;}' +
            '.ui-confirm-ok:hover{background:#5a6fd6;}' +
            '.ui-confirm-ok.danger{background:#dc3545;}' +
            '.ui-confirm-ok.danger:hover{background:#c22a39;}';
        document.head.appendChild(s);
    }

    return new Promise(function (resolve) {
        var overlay = document.createElement('div');
        overlay.className = 'ui-confirm-overlay';
        overlay.setAttribute('data-ui-confirm', '1');
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        var box = document.createElement('div');
        box.className = 'ui-confirm-box';
        var msg = document.createElement('div');
        msg.className = 'ui-confirm-msg';
        msg.textContent = String(message);
        var btns = document.createElement('div');
        btns.className = 'ui-confirm-btns';
        var cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.className = 'ui-confirm-btn ui-confirm-cancel';
        cancel.textContent = cancelLabel;
        var ok = document.createElement('button');
        ok.type = 'button';
        ok.className = 'ui-confirm-btn ui-confirm-ok' + (danger ? ' danger' : '');
        ok.textContent = okLabel;
        btns.appendChild(cancel);
        btns.appendChild(ok);
        box.appendChild(msg);
        box.appendChild(btns);
        overlay.appendChild(box);
        document.body.appendChild(overlay);

        var prevFocus = document.activeElement;
        function close(result) {
            try { document.removeEventListener('keydown', onKey, true); } catch (e) {}
            if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
            try { if (prevFocus && prevFocus.focus) prevFocus.focus(); } catch (e) {}
            resolve(result);
        }
        function onKey(ev) {
            if (ev.key === 'Escape') { ev.stopPropagation(); close(false); }
            else if (ev.key === 'Enter') { ev.stopPropagation(); close(true); }
        }
        cancel.addEventListener('click', function () { close(false); });
        ok.addEventListener('click', function () { close(true); });
        overlay.addEventListener('mousedown', function (ev) {
            if (ev.target === overlay) close(false);
        });
        document.addEventListener('keydown', onKey, true);
        try { ok.focus(); } catch (e) {}
    });
}

/** HTML-escape a string for safe innerHTML insertion. */
function esc(text) {
    if (!text) return '';
    var d = document.createElement('div');
    d.textContent = String(text);
    return d.innerHTML;
}

/** Handle file selection in upload zones. Pass maxFiles to enforce a limit. */
function fileSelected(input, maxFiles) {
    var zone = input.closest('.upload-zone');
    var nameEl = zone.querySelector('.filename');
    var count = input.files.length;
    if (maxFiles && count > maxFiles) {
        alert('Maximum ' + maxFiles + ' files.');
        input.value = '';
        zone.classList.remove('has-file');
        nameEl.textContent = '';
        return;
    }
    if (count > 0) {
        zone.classList.add('has-file');
        nameEl.textContent = count === 1 ? input.files[0].name : count + ' files';
    } else {
        zone.classList.remove('has-file');
        nameEl.textContent = '';
    }
}

/** Toggle a collapsible section by header and body IDs. */
function toggleSection(toggleId, bodyId) {
    document.getElementById(toggleId).classList.toggle('open');
    document.getElementById(bodyId).classList.toggle('open');
}

/** Verify the main access code (used by hub, index, class pages). */
async function verifyAccessCode() {
    var code = document.getElementById('codeInput').value.trim();
    if (!code) return;
    var btn = document.getElementById('gateBtn');
    btn.disabled = true;
    btn.textContent = 'Verifying...';
    try {
        var res = await fetch('/verify-code', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: code })
        });
        if (res.ok) {
            var data = await res.json();
            if (data.redirect) {
                window.location.href = data.redirect;
            } else {
                window.location.reload();
            }
        } else {
            document.getElementById('gateError').style.display = 'block';
            document.getElementById('codeInput').classList.add('error');
            btn.disabled = false;
            btn.textContent = 'Enter';
        }
    } catch (err) {
        document.getElementById('gateError').textContent = 'Connection error.';
        document.getElementById('gateError').style.display = 'block';
        btn.disabled = false;
        btn.textContent = 'Enter';
    }
}

/* Auto-attach Enter key listener for any code input gate. */
document.addEventListener('DOMContentLoaded', function () {
    var codeInput = document.getElementById('codeInput');
    var gateBtn = document.getElementById('gateBtn');
    if (codeInput && gateBtn) {
        codeInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') gateBtn.click();
        });
    }
});

/* UP-26: click + change event delegation for the data-handler pattern.
 *
 *   <button data-handler="toggleStudents" data-class-id="42">…</button>
 *
 * On click, looks up `window.toggleStudents` and invokes it with the
 * element as the first argument (the click event is passed second).
 * Handlers read any data-* attribute they need off the element — same
 * pattern as the existing editStudentFromBtn(btn) / deleteStudentFromBtn(btn).
 *
 * For <a> elements with href="#" or data-prevent-default="true" we
 * preventDefault() automatically (so the page doesn't jump to top).
 *
 * `data-change-handler` is the same wiring for <input>/<select> change
 * events (e.g. file inputs that fire on selection).
 *
 * Existing inline onclick= handlers continue to work; this is purely
 * additive — migrate one page at a time. See dashboard.html for the
 * reference migration. */
(function () {
    function preventIfNeeded(el, ev) {
        if (el.tagName === 'A' && (el.getAttribute('href') === '#' || el.dataset.preventDefault === 'true')) {
            ev.preventDefault();
        }
    }

    function dispatch(attr, ev) {
        var el = ev.target.closest('[' + attr + ']');
        if (!el) return;
        var fname = el.getAttribute(attr);
        var fn = window[fname];
        if (typeof fn !== 'function') {
            console.warn('[data-handler] no such function on window:', fname);
            return;
        }
        if (attr === 'data-handler') preventIfNeeded(el, ev);
        try { fn(el, ev); } catch (err) { console.error('[data-handler]', fname, err); }
    }

    document.addEventListener('click', function (ev) { dispatch('data-handler', ev); });
    document.addEventListener('change', function (ev) { dispatch('data-change-handler', ev); });
})();
