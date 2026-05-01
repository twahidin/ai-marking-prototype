// Shared document viewer: renders a scrollable column of PDF pages
// (via PDF.js) and/or images, with zoom & rotate controls.
//
// Usage:
//   var viewer = DocumentViewer.create(scrollContainerEl);
//   viewer.loadFromManifest(manifestUrl, pageUrlBuilder);  // for script with per-page endpoints
//   viewer.loadFromUrl(url);                               // for a single blob (answer key)
//   viewer.zoomIn() / zoomOut() / reset() / rotate();
//
// Requires PDF.js to be loaded (pdfjsLib global).

(function (global) {
    var ZOOM_MIN = 0.5, ZOOM_MAX = 3.0, ZOOM_STEP = 0.25;

    function create(scrollContainerEl) {
        var wrap = document.createElement('div');
        wrap.className = 'dv-scale-wrap';
        scrollContainerEl.innerHTML = '';
        scrollContainerEl.appendChild(wrap);
        scrollContainerEl.style.cursor = 'grab';

        var state = { scale: 1.0, rotation: 0, loadToken: 0, lastContainerWidth: 0 };

        function ensureBaseWidth() {
            // Capture the scale-1 width of each canvas/img the first time we see it
            // laid out. Before capture, an element stores no dataset value and the
            // applyTransform call is a no-op for it (it keeps its default styling).
            var kids = wrap.querySelectorAll('canvas, img');
            kids.forEach(function (el) {
                if (!el.dataset.dvBase && el.offsetWidth > 0) {
                    el.dataset.dvBase = String(el.offsetWidth);
                }
            });
        }

        function applyTransform() {
            ensureBaseWidth();
            var kids = wrap.querySelectorAll('canvas, img');
            kids.forEach(function (el) {
                var base = parseFloat(el.dataset.dvBase || '0');
                if (base > 0) {
                    el.style.maxWidth = 'none';
                    el.style.width = Math.round(base * state.scale) + 'px';
                    el.style.height = 'auto';
                }
            });
            // Rotation via transform on the wrap (layout-neutral; visual only).
            wrap.style.transform = state.rotation ? ('rotate(' + state.rotation + 'deg)') : '';
            wrap.style.transformOrigin = 'center top';
        }

        // Re-apply sizing whenever a page is appended or the container is resized.
        if (typeof MutationObserver !== 'undefined') {
            var mo = new MutationObserver(function () { applyTransform(); });
            mo.observe(wrap, { childList: true });
        }
        if (typeof ResizeObserver !== 'undefined') {
            var ro = new ResizeObserver(function (entries) {
                // Only react to actual container width changes (e.g. user drags the
                // split resizer). Ignore height-only changes, which happen when the
                // horizontal scrollbar appears/disappears from our own zoom — acting
                // on those would cause a feedback loop.
                for (var i = 0; i < entries.length; i++) {
                    var w = entries[i].contentRect.width;
                    if (Math.abs(w - state.lastContainerWidth) < 1) continue;
                    state.lastContainerWidth = w;
                    var kids = wrap.querySelectorAll('canvas, img');
                    kids.forEach(function (el) {
                        delete el.dataset.dvBase;
                        el.style.width = '';
                        el.style.maxWidth = '100%';
                    });
                    applyTransform();
                }
            });
            ro.observe(scrollContainerEl);
        }

        // Click-drag panning on the scroll container.
        var panState = null;
        scrollContainerEl.addEventListener('mousedown', function (e) {
            if (e.button !== 0) return;
            panState = {
                startX: e.clientX,
                startY: e.clientY,
                scrollLeft: scrollContainerEl.scrollLeft,
                scrollTop: scrollContainerEl.scrollTop,
            };
            scrollContainerEl.style.cursor = 'grabbing';
            e.preventDefault();
        });
        window.addEventListener('mousemove', function (e) {
            if (!panState) return;
            scrollContainerEl.scrollLeft = panState.scrollLeft - (e.clientX - panState.startX);
            scrollContainerEl.scrollTop = panState.scrollTop - (e.clientY - panState.startY);
        });
        window.addEventListener('mouseup', function () {
            if (panState) {
                panState = null;
                scrollContainerEl.style.cursor = 'grab';
            }
        });

        function clearPages() {
            wrap.innerHTML = '';
        }

        function appendLoadingBox(label) {
            var d = document.createElement('div');
            d.className = 'dv-loading';
            d.textContent = label;
            d.style.cssText = 'padding:20px; color:#888; text-align:center;';
            wrap.appendChild(d);
            return d;
        }

        function appendError(label) {
            var d = document.createElement('div');
            d.className = 'dv-error';
            d.textContent = label;
            d.style.cssText = 'padding:14px; color:#b00020; background:#fdecea; border-radius:6px; margin:10px 0;';
            wrap.appendChild(d);
        }

        // pdf.js character-map and standard-font URLs. These point at the
        // bundled assets shipped alongside the loaded pdf.js version on
        // jsdelivr. Required for PDFs that use CJK / non-Latin scripts
        // (Chinese, Japanese, Korean, Tamil, etc.) — without cMapUrl,
        // pdf.js can't map character codes back to glyphs and the page
        // renders blank with the warning "Ensure that the cMapUrl and
        // cMapPacked API parameters are provided".
        var PDFJS_VERSION = '4.8.69';
        var CMAP_URL = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@' + PDFJS_VERSION + '/cmaps/';
        var STANDARD_FONTS_URL = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@' + PDFJS_VERSION + '/standard_fonts/';

        async function renderPdfBlob(blob, token) {
            try {
                var ab = await blob.arrayBuffer();
                if (token !== state.loadToken) return;
                var loadingTask = pdfjsLib.getDocument({
                    data: ab,
                    cMapUrl: CMAP_URL,
                    cMapPacked: true,
                    standardFontDataUrl: STANDARD_FONTS_URL,
                });
                var pdf = await loadingTask.promise;
                if (token !== state.loadToken) return;
                for (var i = 1; i <= pdf.numPages; i++) {
                    if (token !== state.loadToken) return;
                    var page = await pdf.getPage(i);
                    if (token !== state.loadToken) return;
                    var viewport = page.getViewport({ scale: 2 });
                    var canvas = document.createElement('canvas');
                    canvas.width = viewport.width;
                    canvas.height = viewport.height;
                    canvas.style.display = 'block';
                    canvas.style.margin = '0 auto 12px';
                    canvas.style.maxWidth = '100%';
                    canvas.style.height = 'auto';
                    var ctx = canvas.getContext('2d');
                    await page.render({ canvasContext: ctx, viewport: viewport }).promise;
                    if (token !== state.loadToken) return;
                    wrap.appendChild(canvas);
                }
            } catch (err) {
                if (token !== state.loadToken) return;
                appendError('Could not render PDF page');
            }
        }

        function appendImage(url) {
            var img = document.createElement('img');
            img.style.cssText = 'display:block; margin:0 auto 12px; max-width:100%; height:auto;';
            // When the image finishes loading, its offsetWidth becomes valid; re-run
            // applyTransform so its base size gets captured and current zoom applied.
            img.onload = function () { applyTransform(); };
            img.onerror = function () { appendError('Could not load image'); };
            img.src = url;
            wrap.appendChild(img);
        }

        async function loadFromManifest(manifestUrl, pageUrlBuilder) {
            state.loadToken++;
            var token = state.loadToken;
            clearPages();
            var loading = appendLoadingBox('Loading…');
            try {
                var res = await fetch(manifestUrl);
                var data = await res.json();
                if (token !== state.loadToken) return;
                if (!data.success) {
                    loading.remove();
                    appendError('Could not load document manifest');
                    return;
                }
                loading.remove();
                for (var i = 0; i < data.pages.length; i++) {
                    if (token !== state.loadToken) return;
                    var p = data.pages[i];
                    var url = pageUrlBuilder(p.index);
                    if (p.mime === 'application/pdf') {
                        try {
                            var pageRes = await fetch(url);
                            if (token !== state.loadToken) return;
                            var blob = await pageRes.blob();
                            await renderPdfBlob(blob, token);
                        } catch (e) {
                            if (token !== state.loadToken) return;
                            appendError('Could not load page ' + (i + 1));
                        }
                    } else if (p.mime && p.mime.indexOf('image/') === 0) {
                        appendImage(url);
                    } else {
                        appendError('Page ' + (i + 1) + ' (unsupported format)');
                    }
                }
            } catch (err) {
                if (token !== state.loadToken) return;
                if (loading) loading.remove();
                appendError('Could not load document');
            }
        }

        async function loadFromUrl(url) {
            state.loadToken++;
            var token = state.loadToken;
            clearPages();
            var loading = appendLoadingBox('Loading…');
            try {
                var res = await fetch(url);
                if (token !== state.loadToken) return;
                if (!res.ok) {
                    loading.remove();
                    appendError('Document not available');
                    return;
                }
                var ctype = (res.headers.get('Content-Type') || '').toLowerCase();
                var blob = await res.blob();
                if (token !== state.loadToken) return;
                loading.remove();
                if (ctype.indexOf('application/pdf') === 0) {
                    await renderPdfBlob(blob, token);
                } else if (ctype.indexOf('image/') === 0) {
                    var objUrl = URL.createObjectURL(blob);
                    var img = new Image();
                    img.onload = function () { URL.revokeObjectURL(objUrl); applyTransform(); };
                    img.onerror = function () { URL.revokeObjectURL(objUrl); appendError('Could not load image'); };
                    img.style.cssText = 'display:block; margin:0 auto 12px; max-width:100%; height:auto;';
                    img.src = objUrl;
                    wrap.appendChild(img);
                } else {
                    appendError('Unsupported document format');
                }
            } catch (err) {
                if (token !== state.loadToken) return;
                if (loading) loading.remove();
                appendError('Could not load document');
            }
        }

        return {
            loadFromManifest: loadFromManifest,
            loadFromUrl: loadFromUrl,
            zoomIn: function () { state.scale = Math.min(ZOOM_MAX, state.scale + ZOOM_STEP); applyTransform(); },
            zoomOut: function () { state.scale = Math.max(ZOOM_MIN, state.scale - ZOOM_STEP); applyTransform(); },
            reset: function () { state.scale = 1.0; state.rotation = 0; applyTransform(); },
            rotate: function () { state.rotation = (state.rotation + 90) % 360; applyTransform(); },
            getScale: function () { return state.scale; },
        };
    }

    global.DocumentViewer = { create: create };
})(window);
