# LaTeX Rendering Across All User-Facing Surfaces

**Date:** 2026-04-23
**Status:** Approved

## Problem

The AI is prompted to emit math in LaTeX (`$...$` inline, `$$...$$` display — see `ai_marking.py:411,523,594`), and MathJax is loaded globally (`base.html:11-16`). But LaTeX only renders on some surfaces:

- **Works today:** student feedback results (`submit.html`), insights dashboards (`class_insights.html`), demo results (`index.html`), server-rendered page loads (`teacher_detail.html` on initial render).
- **Broken:** the student's extracted-answer edit step shows `$x^2$` as literal text inside textareas; the teacher's "View Edits" modal populates dynamically without calling `MathJax.typesetPromise()`; teachers can only view per-student feedback via PDF download.

The goal: every user-facing surface that displays equations renders them as typeset math. Backend storage remains raw LaTeX.

## Scope

### In

1. Student extracted-answer edit step — live preview panel.
2. Teacher "View Edits" modal — typeset after populate.
3. Teacher in-app feedback viewer — new modal on each student row.
4. Audit of other dynamic surfaces where feedback/answer snippets may appear.

### Out

- PDF report upgrade. `clean_for_pdf()` continues to approximate LaTeX with Unicode. Faithful PDF LaTeX is a separate effort.
- Backend AI-prompt changes. Prompts already request LaTeX.
- Any change to the data model.

## Design

### 1. Student edit step — live preview panel

**File:** `templates/submit.html`, function `renderPreview` (~line 579).

Structure change: under each `<textarea class="answer-textarea">`, append a preview block.

```html
<div class="answer-preview" id="preview-<i>">
  <div class="answer-preview-label">Preview</div>
  <div class="answer-preview-body" id="preview-body-<i>"></div>
</div>
```

Behavior:
- On `renderPreview`, populate `#preview-body-<i>` with the initial extracted text (copied verbatim, wrapped in a text node so MathJax sees the delimiters), then call `MathJax.typesetPromise([previewBody])`.
- On textarea `input`, debounce 200 ms, copy `textarea.value` into the preview body as a text node (replaces prior content), then re-typeset **only that preview element** (`MathJax.typesetPromise([previewBody])`) to avoid re-typesetting the whole page.
- Existing `markEdited(idx)` logic is unchanged.

Styling (add to existing `<style>` block in `submit.html`):
- `.answer-preview` — top margin 8 px, border-top 1 px dashed `#e0e0e0`, padding-top 8 px.
- `.answer-preview-label` — 11 px, `#888`, uppercase, letter-spacing 0.05 em, margin-bottom 4 px.
- `.answer-preview-body` — 14 px, `#333`, line-height 1.5, min-height 1.5 em (so empty previews don't collapse).
- On mobile, preview stacks naturally below the textarea (no layout change needed).

Edge cases:
- Empty textarea → empty preview body, no typeset call needed (but call is safe — MathJax no-ops on empty).
- Unmatched `$` (typed mid-equation) → MathJax's default error-handling renders the fragment as-is. Acceptable.
- Very long answers → preview scrolls with the card naturally.

### 2. Teacher "View Edits" modal

**File:** `templates/teacher_detail.html`, function `viewExtracted` (~line 874).

At the end of the function, after setting `extractedDiffContent.innerHTML`, add:

```js
if (window.MathJax && MathJax.typesetPromise) {
    MathJax.typesetPromise([document.getElementById('extractedDiffContent')]);
}
```

Both "AI Extracted" and "Student Version" sides render as typeset math. The `escHtml()` function uses `textContent`, which preserves `$` delimiters — nothing else changes.

### 3. Teacher in-app feedback viewer (new)

**File:** `templates/teacher_detail.html`.

**UI:**
- New button on each student row (beside the existing PDF-download link and "View Edits" button): **"View Feedback"**. Shown only when `s.status == 'done'` and `s.submission_id` is present.
- Clicking opens a new modal (`#feedbackModal`) reusing the visual layout from `submit.html`'s results view:
  - Header: student name + assignment subject.
  - Overall feedback box (if present).
  - Question nav dots.
  - Per-question card with fields: student answer, correct answer / band descriptor, feedback, suggested improvement, marks or status badge.
  - Recommended actions list (if present).
  - Line-by-line errors list (if present).
- Rubrics assignments use the same alternate labels ("Assessment", "Band Descriptor", criterion name).
- Footer: "Download PDF" link (reuses the existing download URL), "Close" button.

**Data source:** reuse existing endpoint `GET /teacher/assignment/<assignment_id>/submission/<int:submission_id>/result` (`app.py:3786`). No backend change.

**Rendering:** after populating the modal, call `MathJax.typesetPromise([feedbackModalContent])`. All text fields run through the existing `esc()`/`escHtml` helpers. LaTeX `$` delimiters pass through untouched.

**Implementation note:** extract the results-rendering logic from `submit.html` into a small shared JS helper if natural, or duplicate it in `teacher_detail.html` — whichever is less invasive. Duplication is acceptable here because `submit.html` and `teacher_detail.html` don't share a script bundle today.

**Interactions:**
- Close on backdrop click, Escape key, or close button.
- Question nav dots + prev/next arrows work within the modal.
- MathJax re-typesets the question card whenever the user navigates questions (same pattern as `submit.html:767`).

### 4. Audit other dynamic surfaces

Pages to audit for dynamically-injected answer or feedback text:

- `dashboard.html`
- `bank.html`
- `department_insights.html`
- `class.html`
- `class_insights.html` (already typesets — confirm coverage of any newly-added snippets)

For each: if an element is populated via JS (fetch → innerHTML) and contains user-visible equation text, add `MathJax.typesetPromise([el])` after the populate. Server-rendered content is already covered by existing page-load typeset calls.

This is an audit pass — expect zero or a small number of additions.

### 5. Student "View feedback" on prior submission

**File:** `templates/submit.html`, functions `reviewSubmission` (~line 393) and `reviewPrevious` (~line 421).

These funnel back into `renderResult` / `renderQuestion`, which already call `MathJax.typesetPromise()` (lines 734, 767). No code change expected. **Verify during implementation**; add a typeset call only if a gap is found.

## Architecture Notes

- No new backend dependencies.
- No new Python modules.
- No DB migrations.
- Only frontend changes plus (possibly) a small shared JS helper for the results-view rendering if duplication becomes awkward.
- MathJax 3 is already configured with `startup.typeset: false`, so targeted `typesetPromise([el])` calls are the correct pattern and are already used elsewhere in the codebase.

## Testing

Manual verification:

1. **Student edit step** — submit a script with known LaTeX in answers. Confirm preview shows typeset math, updates as textarea is edited, and no lag/jank at typical answer lengths.
2. **Teacher View Edits modal** — open on a submission with equations. Confirm both AI-extracted and student-edited sides render.
3. **Teacher in-app feedback viewer** — open on a submission with equations in overall feedback, per-question fields, and errors list. Navigate between questions. Confirm typeset on each question switch.
4. **Prior-submission review (student)** — verify LaTeX still renders (no regression).
5. **Audited surfaces** — for any surface changed in step 4 of the Design, verify before and after.
6. **Regression sweep** — existing working surfaces (`submit.html` results, `class_insights.html`) still render correctly.

No automated tests exist for the frontend today; this change does not introduce any.

## Risks

- **MathJax performance on live typing:** mitigated by 200 ms debounce and targeted `typesetPromise([el])` (not full page).
- **Malformed LaTeX in AI output:** MathJax renders fragments as-is; worst case is a partially-rendered equation. Acceptable.
- **JS duplication** between `submit.html` and `teacher_detail.html` for the results view: if it becomes more than ~50 lines, factor into a shared script file; otherwise inline.
