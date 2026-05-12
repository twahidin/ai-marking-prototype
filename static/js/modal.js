// UP-20: Modal accessibility helper.
//
// Auto-upgrades the app's existing dialog-like elements so that keyboard
// and screen-reader users can perceive and operate them. No template
// changes are required for the upgrade to take effect — the script tags
// existing overlay elements with role=dialog + aria-modal, watches them
// for visibility changes, and (when shown) installs:
//   - focus capture on open + restore on close
//   - Tab / Shift+Tab focus trap inside the modal
//   - Escape to close
//   - click-on-overlay to close
//
// Modals are matched on either an explicit data-modal attribute or on
// any of the legacy class names used across templates:
//   .modal-overlay  .upload-modal  .extracted-modal
//   .feedback-modal .edit-modal
//
// The helper does NOT change how the app opens / closes modals (each
// page still toggles `.active` or `style.display`); it only piggybacks
// on those toggles to add the a11y behaviour. Existing onclick handlers
// (closeUploadModal, closeFeedbackModal, etc.) continue to work — the
// helper just calls the same DOM toggle indirectly via a custom event,
// so it doesn't fight the page's own close logic.
(function () {
    'use strict';

    var MODAL_SELECTOR = [
        '[data-modal]',
        '.modal-overlay',
        '.upload-modal',
        '.extracted-modal',
        '.feedback-modal',
        '.edit-modal'
    ].join(', ');

    var FOCUSABLE = [
        'a[href]',
        'area[href]',
        'button:not([disabled])',
        'input:not([disabled]):not([type="hidden"])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        '[tabindex]:not([tabindex="-1"])',
        'audio[controls]',
        'video[controls]',
        '[contenteditable]:not([contenteditable="false"])'
    ].join(', ');

    var openModals = [];
    var savedFocus = new WeakMap();

    function isVisible(el) {
        if (!el || !el.isConnected) return false;
        if (el.hasAttribute('hidden')) return false;
        var style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        return el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0;
    }

    function focusables(modal) {
        return Array.prototype.slice.call(modal.querySelectorAll(FOCUSABLE))
            .filter(function (el) { return el.offsetParent !== null || el === document.activeElement; });
    }

    function findContent(modal) {
        return modal.firstElementChild || modal;
    }

    function findTitleId(modal) {
        var existing = modal.getAttribute('aria-labelledby');
        if (existing) return existing;
        var h = modal.querySelector('h1, h2, h3, h4, [role="heading"]');
        if (!h) return null;
        if (!h.id) h.id = 'modal-title-' + Math.random().toString(36).slice(2, 9);
        return h.id;
    }

    function tagOnce(modal) {
        if (modal.dataset.modalA11y === '1') return;
        modal.dataset.modalA11y = '1';
        if (!modal.hasAttribute('role')) modal.setAttribute('role', 'dialog');
        if (!modal.hasAttribute('aria-modal')) modal.setAttribute('aria-modal', 'true');
        if (!modal.hasAttribute('tabindex')) modal.setAttribute('tabindex', '-1');
        var titleId = findTitleId(modal);
        if (titleId) modal.setAttribute('aria-labelledby', titleId);

        modal.addEventListener('mousedown', function (ev) {
            if (ev.button !== 0) return;
            var content = findContent(modal);
            if (content && (ev.target === modal || !content.contains(ev.target))) {
                modal.dataset.backdropMouseDown = '1';
            } else {
                modal.dataset.backdropMouseDown = '';
            }
        });
        modal.addEventListener('mouseup', function (ev) {
            if (ev.button !== 0) return;
            if (modal.dataset.backdropMouseDown !== '1') return;
            modal.dataset.backdropMouseDown = '';
            var content = findContent(modal);
            if (content && (ev.target === modal || !content.contains(ev.target))) {
                requestClose(modal);
            }
        });
    }

    function onOpen(modal) {
        if (openModals.indexOf(modal) !== -1) return;
        openModals.push(modal);
        savedFocus.set(modal, document.activeElement);

        var f = focusables(modal);
        var initial = modal.querySelector('[autofocus]');
        var target = initial || f[0] || modal;
        try { target.focus({ preventScroll: false }); } catch (e) { try { target.focus(); } catch (_) {} }
    }

    function onClose(modal) {
        var idx = openModals.indexOf(modal);
        if (idx === -1) return;
        openModals.splice(idx, 1);
        var prev = savedFocus.get(modal);
        savedFocus.delete(modal);
        if (prev && typeof prev.focus === 'function' && prev.isConnected !== false) {
            try { prev.focus(); } catch (e) {}
        }
    }

    function requestClose(modal) {
        // Pages can opt out (e.g. the "AI is marking, do not close" overlay
        // in index.html) by setting `data-no-dismiss` on the modal element.
        if (modal.hasAttribute('data-no-dismiss')) return;
        var ev = new CustomEvent('modal-a11y:close', { bubbles: true, cancelable: true, detail: { modal: modal } });
        var allowed = modal.dispatchEvent(ev);
        if (!allowed) return;
        if (modal.classList.contains('active')) modal.classList.remove('active');
        modal.style.display = 'none';
    }

    function trapTab(ev) {
        if (ev.key !== 'Tab' || !openModals.length) return;
        var modal = openModals[openModals.length - 1];
        var f = focusables(modal);
        if (!f.length) {
            ev.preventDefault();
            try { modal.focus(); } catch (e) {}
            return;
        }
        var first = f[0];
        var last = f[f.length - 1];
        if (ev.shiftKey && document.activeElement === first) {
            ev.preventDefault();
            last.focus();
        } else if (!ev.shiftKey && document.activeElement === last) {
            ev.preventDefault();
            first.focus();
        }
    }

    function handleEscape(ev) {
        if (ev.key !== 'Escape' || !openModals.length) return;
        ev.stopPropagation();
        requestClose(openModals[openModals.length - 1]);
    }

    function observe(modal) {
        tagOnce(modal);
        var wasOpen = isVisible(modal);
        if (wasOpen) onOpen(modal);
        var mo = new MutationObserver(function () {
            var nowOpen = isVisible(modal);
            if (nowOpen && !wasOpen) onOpen(modal);
            else if (!nowOpen && wasOpen) onClose(modal);
            wasOpen = nowOpen;
        });
        mo.observe(modal, { attributes: true, attributeFilter: ['class', 'style', 'hidden'] });
    }

    function init() {
        document.querySelectorAll(MODAL_SELECTOR).forEach(observe);
        var bodyMo = new MutationObserver(function (records) {
            records.forEach(function (rec) {
                rec.addedNodes && rec.addedNodes.forEach(function (n) {
                    if (n.nodeType !== 1) return;
                    if (n.matches && n.matches(MODAL_SELECTOR)) observe(n);
                    if (n.querySelectorAll) n.querySelectorAll(MODAL_SELECTOR).forEach(observe);
                });
            });
        });
        bodyMo.observe(document.body, { childList: true, subtree: true });

        document.addEventListener('keydown', handleEscape, true);
        document.addEventListener('keydown', trapTab, true);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.ModalA11y = {
        attach: observe,
        open: onOpen,
        close: onClose
    };
})();
