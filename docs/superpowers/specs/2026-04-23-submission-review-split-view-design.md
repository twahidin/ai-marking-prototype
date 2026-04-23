# Submission Review Split View

**Date:** 2026-04-23
**Status:** Approved

## Problem

Teachers currently review a student's work by (a) downloading the feedback PDF, (b) viewing feedback in a modal on the assignment detail page, or (c) downloading the raw script from their own local files. There is no way to look at the student's submitted work and its AI feedback side-by-side, and no way to compare two students' submissions at once.

## Goal

A dedicated review page opened in a new tab, showing the student's actual submitted work on one side of a split screen and AI feedback / answer key / another student's work on the other — with zoom, rotate, vertical scrolling, and a draggable resizer.

## Scope

### In
- A new route that renders a split-screen review page for any `done` submission.
- Linkification of student names on rows with `status == 'done'` in `teacher_detail.html`.
- PDF.js rendering for PDF pages; `<img>` for image pages; unified zoom/rotate with CSS transforms.
- Three right-pane modes via a primary dropdown: AI Feedback, Answer Key, Compare with another student.
- A draggable vertical resizer between the two panes with localStorage persistence.
- Three new endpoints (script manifest, per-page script serve, answer key serve).

### Out
- Annotation / drawing / highlighting on the student's work.
- Mobile layout (desktop / tablet-landscape only).
- Left-pane content switching (left pane always shows the original student's work).
- URL parameters for deep-linking into a specific mode. Start state is always "AI Feedback".
- Vendoring PDF.js locally — use the CDN for this iteration.

## Design

### Linkification in `teacher_detail.html`

In the student rows table, when `s.status == 'done' and s.submission_id`, wrap the name in:

```jinja
<a href="/teacher/assignment/{{ assignment.id }}/submission/{{ s.submission_id }}/review" target="_blank" rel="noopener">{{ s.name }}</a>
```

This replaces the existing name-as-download-PDF link for `done` rows. The dedicated Download button in the Actions cell still provides direct PDF export, so no functionality is lost.

For non-`done` rows, the name remains plain text.

### Route

`GET /teacher/assignment/<assignment_id>/submission/<int:submission_id>/review`

- Ownership check via `_check_assignment_ownership(asn)`.
- Verify `sub.assignment_id == assignment_id` and `sub.status == 'done'`.
- Server-render the template with: assignment metadata, this submission's id and student name, the script page manifest (count + mime per page), and a list of other students on the same assignment whose `status == 'done'` and `submission_id is not None`. This pre-rendering avoids a second round-trip for the compare dropdown.
- Renders `templates/review.html`.

### New endpoints — script and answer key serving

All routes run `_check_assignment_ownership(asn)` first.

**`GET /teacher/assignment/<aid>/submission/<sid>/script/manifest`**

Returns:
```json
{
  "success": true,
  "pages": [
    { "index": 0, "mime": "application/pdf" },
    { "index": 1, "mime": "image/jpeg" }
  ]
}
```

The MIME is inferred from magic bytes (`%PDF` → `application/pdf`; `FF D8 FF` → `image/jpeg`; `89 50 4E 47` → `image/png`; anything else → `application/octet-stream`).

**`GET /teacher/assignment/<aid>/submission/<sid>/script/page/<int:page_idx>`**

- 404 if `page_idx` is out of range for `sub.get_script_pages()`.
- Returns the page bytes with the auto-detected MIME. `as_attachment=False` so the browser can inline-render.

**`GET /teacher/assignment/<aid>/answer-key`**

- 404 if `asn.answer_key` is None.
- Returns the blob with auto-detected MIME.

### Template `templates/review.html`

Layout:

```
┌──────────────────────────────────────────────────────────┐
│ Top bar: {student name} — {assignment title}  [Close ×]  │
├──────────────────────┬──┬────────────────────────────────┤
│ Left sub-toolbar     │  │ Right sub-toolbar              │
│ [− + Reset ⟳] {n pg} │░░│ [AI Feedback ▼] [...mode-ui]   │
├──────────────────────┤░░├────────────────────────────────┤
│                      │░░│                                │
│  Student work        │░░│  AI feedback / Answer key /    │
│  (scrollable pages,  │░░│  Compared student content      │
│   zoom + rotate      │░░│                                │
│   applied to wrap)   │░░│                                │
│                      │░░│                                │
└──────────────────────┴──┴────────────────────────────────┘
                       ^
                resizer (cursor: col-resize)
```

- Top bar: compact header with student name, assignment title / subject, and a Close button that `window.close()`s (falls back to navigating to the assignment detail page if the tab wasn't opened with `window.open`).
- Two-column body wrapped in a flex container. A CSS variable `--left-width` (default `50%`) drives the left pane's flex-basis; the right pane fills the remainder. Min left width 20%, max 80%.
- Resizer is an 8px-wide div between the panes with `cursor: col-resize`. On mousedown, attach window-level mousemove / mouseup listeners; on move, compute the new left-pane width as a percentage of the container and update `--left-width`. On mouseup, persist the width to `localStorage['review-split-left-width']` (global, not per-submission — teachers tend to prefer one ratio). On page load, read from localStorage if present.

### Left pane — student work rendering

Inside the scroll container, a `<div class="scale-wrap">` holds the rendered pages. The wrapper gets `transform: scale(s) rotate(r)` and `transform-origin: top center`. Zoom is `scale` in increments of 0.25× from 0.5× to 3×. Rotate cycles 0°/90°/180°/270°.

For each entry `p` in the server-provided manifest:
- If `p.mime == 'application/pdf'`: fetch `.../script/page/<idx>` as a blob, feed to `pdfjsLib.getDocument(...)`. For each internal page, render to a `<canvas>` at render-scale 2 (for crispness at high zoom) and append to the scroll container. A single stored "page" that contains a multi-page PDF produces multiple canvases — this is the normal case.
- If `p.mime.startsWith('image/')`: append `<img src=".../script/page/<idx>">`.
- If `p.mime == 'application/octet-stream'` (unknown): render a small warning box `"Page <n> (unsupported format)"`. Teachers see something went wrong rather than a silent blank.

Page count indicator in the toolbar shows `Page <n>/<N>` derived from the scroll position and per-page offsets (computed after layout). Falls back to just `N pages` if the math is brittle — nice-to-have.

### Right pane — mode dropdown

Primary select:
```html
<select id="rightMode">
  <option value="feedback">AI Feedback</option>
  <option value="answer_key">Answer Key</option>
  <option value="compare">Compare with another student</option>
</select>
```

Mode handlers:

- **`feedback`**: on select, show the feedback renderer container. Fetch `/teacher/assignment/<aid>/submission/<sid>/result` (existing endpoint), then render via the shared feedback renderer (see below). Navigation dots, overall feedback, errors list, per-question cards, MathJax typeset — all identical to the existing feedback modal on `teacher_detail.html`.

- **`answer_key`**: on select, show the document-viewer container (mirrors left pane's pipeline). Fetch `/teacher/assignment/<aid>/answer-key` as a blob, detect type by `Content-Type`, render via the same PDF-or-image pipeline used on the left. Its own mini zoom/rotate toolbar, independent of the left pane.

- **`compare`**: reveal a secondary UI:
  - Secondary `<select id="compareStudent">` listing other `done` students (server-rendered).
  - Radio toggle: `Their work` / `Their feedback` (default: `Their work`).
  - When the toggle is `Their work`, render that student's script pages via the document-viewer pipeline (fetching their manifest and pages from the same endpoints, `<sid>` swapped for the compared student's submission id).
  - When `Their feedback`, render their feedback via the shared renderer.
  - Switching students or toggle preserves the right-pane toolbar state (mode, zoom, rotate reset on document swap).

### Shared feedback renderer — `static/js/feedback_render.js`

Extract the feedback-rendering JS currently inlined in `teacher_detail.html` (functions `renderFeedbackResult`, `fbRenderQuestion`, `fbNavQ`, `fbGoQ`, `fbEsc`, and the `FB_STATUS_LABELS` constant) into a new file `static/js/feedback_render.js`.

The new file exposes a single entry point:

```js
window.FeedbackRender = {
    render: function(containerEl, result, options) { /* ... */ }
};
```

Where:
- `containerEl` is the DOM node to populate.
- `result` is the result JSON (same shape as `sub.get_result()`).
- `options = { idPrefix: 'fb' }` namespaces all generated element IDs (`<prefix>QCardContainer`, etc.). `teacher_detail.html` passes `idPrefix: 'fb'` to preserve existing IDs; `review.html` passes `idPrefix: 'rf'` (right-feedback) and, when rendering a compared student's feedback, `idPrefix: 'cf'` (compare-feedback). This keeps multiple simultaneous renders (feedback modal + review page) collision-free.

Both `teacher_detail.html` and `review.html` `<script src="{{ url_for('static', filename='js/feedback_render.js') }}">`.

The module internally uses `MathJax.typesetPromise([containerEl])` after render. Feature-check as elsewhere.

### Shared document viewer — `static/js/document_viewer.js`

To avoid duplicating the PDF.js + image + zoom/rotate pipeline between left pane, right-pane answer key, and right-pane compared student's work, extract it into a single module:

```js
window.DocumentViewer = {
    create: function(containerEl, opts) {
        // Returns { loadFromUrl(url, mime), loadFromManifest(manifestUrl, pageUrlBuilder), zoomIn(), zoomOut(), reset(), rotate(), destroy() }
    }
};
```

One instance for the left pane, one for the right-pane answer key / compare-work mode. Each instance has its own scroll container and toolbar; the toolbar buttons wire into the instance's methods.

### CSS

New styles added to a `<style>` block at the top of `review.html` (not shared — the review page is the only consumer):

```
.review-root       { height: 100vh; display: flex; flex-direction: column; ... }
.review-topbar     { height: 48px; ... }
.review-body       { flex: 1; display: flex; min-height: 0; }
.review-pane       { display: flex; flex-direction: column; min-height: 0; }
.review-pane-left  { flex: 0 0 var(--left-width, 50%); }
.review-pane-right { flex: 1 1 auto; }
.review-resizer    { flex: 0 0 8px; cursor: col-resize; background: #eee; }
.review-resizer:hover { background: #d0d0d0; }
.review-subtoolbar { height: 44px; border-bottom: 1px solid #eee; ... }
.review-scroll     { flex: 1; overflow: auto; padding: 16px; }
.scale-wrap        { transform-origin: top center; transition: none; }
```

Resizer state via CSS variable on the body container: `style="--left-width: <persisted>%"`.

### Error handling

- Script page fetch fails → replace that page's slot with a red "Could not load page <n>" box.
- Answer key fetch fails → right pane shows "Answer key could not be loaded." with a retry button.
- Feedback fetch fails → reuse the existing feedback modal's "Could not load feedback" message.
- PDF.js throws on a corrupt PDF → catch, log, render a warning in place of that page.

### Security

- All new endpoints check assignment ownership.
- Content-Type is inferred server-side from magic bytes (not user-controlled).
- No new user inputs beyond URL path parameters.
- PDF.js worker is loaded from the CDN; we set its `workerSrc` explicitly. The CDN origin is `cdn.jsdelivr.net` — already used in the repo for MathJax.
- Script/answer-key URLs return bytes only to authenticated teachers who own the assignment.

### Risks

- PDF.js from CDN adds a network dependency. Acceptable (MathJax already has the same pattern). If the CDN blip becomes an issue, vendor it later.
- Rendering a very large multi-page PDF at 2× scale can consume significant memory. Mitigation: render pages lazily as they scroll into view (IntersectionObserver) if performance suffers; otherwise keep it simple and render all pages on open.
- Unknown / unsupported file types in `script_pages_json` (legacy data) are shown as a "unsupported format" placeholder — graceful degradation, not a crash.

### Testing

Manual verification (no frontend tests in this repo):

1. On `teacher_detail.html`, student name is a link only for `done` rows. Clicking opens a new tab.
2. Left pane renders multi-page PDF scripts with all internal pages stacked. Zoom in/out works. Rotate 90° cycles correctly. Pages stay inside the left pane; split position doesn't change on zoom/rotate.
3. Left pane renders image-based scripts (jpg/png pages) correctly.
4. Right pane → AI Feedback renders correctly with MathJax typeset.
5. Right pane → Answer Key renders the assignment's stored answer key with its own zoom/rotate.
6. Right pane → Compare with another student reveals secondary UI. Selecting a student shows their work (correct script pages). Toggling to feedback shows their feedback. Switching back to the original student is covered by refreshing the page (per spec — no "reset compare" button).
7. Drag the resizer — split ratio changes. Reload page — ratio persists.
8. Try resizing down to 10% — should stop at 20%. Same on the other side (80% cap).
9. Open review for an `error` submission — spec requires 404/403 (link wasn't rendered).
10. Open review with a bad URL (wrong assignment id for submission) — 400 / 404.
11. Feedback modal on `teacher_detail.html` still works (shared renderer regression check).

## Architecture Notes

- This iteration intentionally extracts the feedback renderer and document viewer into shared `static/js/*.js` modules. The two JS files are small and have clear single responsibilities. This is an "improve code you're working in" step — the feedback renderer lived inline in `teacher_detail.html` from the previous feature because it had only one caller; now we have two, and inlining a second copy would be worse than extraction.
- No changes to `app.py` beyond the three new endpoints and the route.
- No DB migration.
- `review.html` is a new top-level template.
