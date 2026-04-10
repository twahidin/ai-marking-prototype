/* ===================================================================
   AI Marking Demo — Shared JavaScript
   =================================================================== */

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
            window.location.reload();
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
