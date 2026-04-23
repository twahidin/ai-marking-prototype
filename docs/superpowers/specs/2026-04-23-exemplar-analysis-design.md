# Exemplar Analysis

**Date:** 2026-04-23
**Status:** Approved

## Problem

Teachers conducting post-marking class discussions want a quick way to surface discussion-worthy exemplars: two students who illustrate a specific issue, and two who handle it well. Currently the only way to do this is to manually page through every student's feedback, remember who did what, and open each submission separately. The existing "Class Overview & Item Analysis" PDF aggregates statistics but doesn't pull up concrete student work for side-by-side comparison.

## Goal

A new page that lists AI-generated "areas for analysis" (misconceptions, technique errors, procedural slips, argumentation issues — whatever the subject surfaces). Clicking one shows four exemplar tiles: two students whose work illustrates the issue, and two who handle it well. Tiles render the students' actual handwritten script pages via the existing `DocumentViewer`.

## Scope

### In
- New "Exemplar Analysis" button on `teacher_detail.html` next to "Class Overview & Item Analysis".
- New page at `/teacher/assignment/<aid>/exemplars`.
- AI endpoint that analyzes done submissions and returns a list of areas with exemplars.
- Caching on the Assignment so the expensive AI call runs once per regeneration.
- Gating: generate button disabled until ≥20% of the class has done submissions.
- 2×2 tile viewing area reusing the existing `DocumentViewer` (shared frontend module).

### Out
- Precise pixel-level cropping of student work. Tiles render the whole relevant page; the teacher zooms/pans within the tile.
- Student anonymisation. Teacher-only view; names are shown.
- Manual edit/override of AI-selected exemplars (could be a follow-up).
- Scheduled/automatic regeneration. User triggers each regenerate.
- Multi-language prompt tuning — English prompt only for this iteration.

## Design

### Data Model

Two new columns on `Assignment` (auto-migrated via the same pattern in `db.py`):

- `exemplar_analysis_json TEXT` — stored result JSON from the AI.
- `exemplar_analyzed_at TIMESTAMP` — when it was last run.

No new tables.

### JSON Shape

The AI returns (validated and stored):

```json
{
  "areas": [
    {
      "question_part": "Q2",
      "label": "Used weight (W=mg) instead of mass in F=ma",
      "description": "Students substituted weight where mass was required, producing a factor-of-g error. See how the strong examples resolved units before substituting.",
      "needs_work_examples": [
        { "submission_id": 57, "page_index": 0, "note": "Second line — wrote F = W × a instead of m × a." },
        { "submission_id": 63, "page_index": 1, "note": "Uses 50 N in place of 5 kg." }
      ],
      "strong_examples": [
        { "submission_id": 71, "page_index": 0, "note": "Labels units before every substitution." },
        { "submission_id": 42, "page_index": 0, "note": "Writes 'mass = 5 kg; weight ignored here'." }
      ]
    }
  ]
}
```

- `question_part`: short label — usually "Q<n>" or a section name from the assignment.
- `label`: one-line issue description. **Must be specific to the chosen exemplars** — what's actually wrong in those students' answers — not a generic textbook category. Example: "Q2 — used weight instead of mass in F=ma", not "Q2 — misconception about force".
- `description`: one paragraph teacher-facing note the teacher can use verbatim in class.
- `*_examples`: each object is `{submission_id, page_index, note}`. `submission_id` must correspond to an existing done submission. `page_index` is an index into the submission's `script_pages_json` list (0-based). `note` is a short pointer to where on the page to look.

Between 3 and 8 areas per analysis is the target range (AI is prompted with this).

### Area Categories (subject-flexible)

The AI is prompted to cover any mix of these category types as appropriate. The category itself is not stored — only the concrete label. Examples:

- **Conceptual misconceptions** (sciences): confuses A with B, applies principle only partially
- **Question-answering technique** (all subjects): misread the question, missed a keyword ("except", "compare"), didn't quote evidence, answered a different question, ignored the mark allocation
- **Procedural / careless errors** (maths, sciences): sign error, unit error, arithmetic slip, lost a factor during algebra
- **Presentation / formatting** (all): missing working, skipped steps, unclear layout, wrong format for situational writing
- **Argumentation / structure** (english, humanities): weak topic sentence, lacks evidence, no counterargument, disorganised paragraphing
- **Application / transfer** (sciences, maths): could state the concept but couldn't apply to the specific scenario

The AI selects the most discussion-worthy areas based on the actual patterns in the submitted work and feedback.

### Endpoints

All three run `_check_assignment_ownership(asn)` first.

**`GET /teacher/assignment/<aid>/exemplars`**
Renders `templates/exemplars.html` with:
- `assignment`, `done_count`, `total_students`, `gate_pct` (= `done_count / total_students` × 100)
- `can_generate` (bool; true when `gate_pct ≥ 20`)
- `analysis` (parsed `exemplar_analysis_json`, or None)
- `analyzed_at`
- A map of `submission_id → student_name` for any exemplars referenced in the current analysis, so the template can label tiles server-side without an extra fetch.

**`POST /teacher/assignment/<aid>/exemplars/generate`**
- Enforces the 20% gate (returns 400 with a clear message if short).
- Gathers all done + is_final submissions, extracts per-question student_answer + correct_answer + feedback + improvement, plus overall feedback.
- Builds a prompt and calls the assignment's configured AI provider/model (reusing `_resolve_api_keys(asn)` and existing provider dispatch code).
- Validates the returned JSON:
  - Every `submission_id` must match a done+is_final submission for this assignment.
  - Every `page_index` must be valid for its submission (`0 <= page_index < len(script_pages)`).
  - Invalid entries are dropped; if an `*_examples` list ends up empty, the whole area is dropped.
- Saves to `exemplar_analysis_json` + `exemplar_analyzed_at`, returns the sanitised payload.

**`GET /teacher/assignment/<aid>/exemplars/data`**
Returns the cached `exemplar_analysis_json` (parsed) plus the `submission_id → name` map. Used by the frontend to re-render without a full page reload after generation.

Script-page serving (and manifest) already exists from the review-split-view feature — reused unchanged.

### Prompt

System prompt (approximate wording — actual prompt in `ai_marking.py`):

```
You are an education analytics assistant. You receive every student's answers and AI feedback for a class's assignment. Produce a short JSON list of "areas for analysis" that a teacher could use to run a class discussion after returning work.

Each area should be:
- Tied to a specific, concrete issue observed in the actual submissions — not a generic textbook category. Label it so a teacher scanning a grid of buttons can tell what the area is about at a glance.
- Cross-subject: include question-answering technique issues (misread the question, missed keywords, didn't quote evidence) alongside conceptual misconceptions, procedural errors, presentation, and argumentation, as appropriate to the subject.
- Accompanied by FOUR concrete exemplars: two students whose work illustrates the issue ("needs_work_examples"), and two whose work handles it well ("strong_examples"). For each exemplar, give submission_id + page_index (0-based) + a short "note" pointing to where on the page to look.

Return 3-8 areas, ordered by teaching value (most discussion-worthy first).

Respond ONLY with valid JSON in this exact shape: {...}
```

The user prompt includes per-submission data as compact text. For very large classes we only send the top N students per mark bucket to stay inside context limits — parameterised with a safe default (e.g. 40 submissions).

### Frontend — `templates/exemplars.html`

Layout (extends `base.html`):

- Top bar: assignment title, "Back to Assignment" link.
- Header card:
  - Title: "Exemplar Analysis"
  - Submission stats (e.g., "12 of 40 done — 30%")
  - Generate / Regenerate button. Disabled when `done_count / total_students < 20%`; hover tooltip explains why.
  - "Last generated {timestamp}" when an analysis exists.
- Area buttons row: one rectangular button per area, labelled `{question_part} — {label}`. Wraps onto multiple lines. Each button has a subtle status color (no special status in this iteration).
- Viewing area (below buttons):
  - Default: centred "Select an area to analyse" placeholder.
  - When an area is clicked: a header block with the area's `description`; a 2×2 grid:
    - Row 1: "Needs work" — 2 tiles.
    - Row 2: "Strong" — 2 tiles.
  - Each tile:
    - Header: student name + AI note.
    - Body: the student's page rendered via `DocumentViewer.loadFromManifest(...)` (loading just the single specified `page_index` by passing a filtered manifest — reuse existing script-page endpoints).
    - Mini toolbar per tile: zoom in, zoom out, reset, rotate. Each tile has its own viewer instance.

**Generate flow:**
1. Teacher clicks Generate.
2. Button shows spinner/disabled; POST to `/exemplars/generate`.
3. On success, JS replaces the area-buttons row and clears the viewing area (back to placeholder).
4. On error, show inline error message.

**Area click flow:**
1. Teacher clicks an area button.
2. JS clears the viewing area, renders 4 tile skeletons, then for each tile instantiates a `DocumentViewer` and calls `loadFromManifest(...)` scoped to that submission (but internally the module loads all pages; we'll only show the one specified by filtering DOM after load). Actually simpler: call the existing `/script/page/<idx>` URL directly via `loadFromUrl(url)` — that renders a single blob. **Use `loadFromUrl` for each tile, scoped to that single page URL.**
3. `DocumentViewer.loadFromUrl` already infers MIME from the response and renders PDF or image accordingly.

### Gating Logic (≥20%)

Computed server-side on page load and enforced server-side on generate:

```
total = Student.query.filter_by(class_id=asn.class_id).count()
done = Submission.query.filter_by(
    assignment_id=asn.id, status='done', is_final=True
).count()
can_generate = total > 0 and (done / total) * 100 >= 20
```

The frontend disables the button based on the rendered value; the server re-checks and returns 400 if the threshold isn't met (prevents a race).

### Error Handling

- AI returns malformed JSON → return 502 with "AI analysis failed — try regenerating".
- AI returns validly-shaped JSON but all areas are dropped during validation → return 502 with "AI analysis could not find valid exemplars".
- AI provider not configured → return 400 with "Configure an AI provider in assignment settings".
- Network error mid-generate → frontend shows inline retry button.
- Rendering a tile fails (page out of range, already handled by the script-page endpoint returning 404) → DocumentViewer renders its built-in "Document not available" error.

### Security

All new endpoints ownership-checked.
`submission_id` and `page_index` in the stored analysis are re-validated on every read (not trusted from the cache blob) — if a submission was deleted between generations, the tile shows an error and the page doesn't crash.
No user input beyond URL path params.

### Risks / Limits

- AI occasionally returns stale or incorrect `submission_id` values. Mitigated by validation that drops invalid entries.
- AI occasionally picks the same student for multiple exemplar slots. Validation enforces unique `submission_id` within each area's combined needs+strong set.
- Very large classes (>60 done submissions) produce a long prompt. Solution: cap at top 40 by marks-awarded distribution (sampled from each bucket to preserve range). Parameter tunable.
- PDF.js rendering of large submissions on eight simultaneous tiles is memory-heavy. Mitigation: only the 4 visible tiles for the selected area are mounted; switching areas destroys the previous viewers by replacing the container innerHTML.

### Testing

Manual:
1. Create an assignment with at least 5 students, mark all 5 as done.
2. Open the Exemplar Analysis page. Confirm Generate enabled (5/5 = 100%).
3. Click Generate, confirm AI call completes and area buttons appear.
4. Click several areas, confirm 4 tiles render correctly with student handwriting.
5. Click Regenerate, confirm a different (or same) set is produced.
6. Negative: reduce the test class to 10 students with only 1 done (10%) — confirm Generate is disabled with explanatory tooltip.
7. Regression: the existing Class Overview & Item Analysis button still works.

## Architecture Notes

- Reuses `DocumentViewer` module (from the review-split-view feature) — no new viewer code.
- Reuses `/script/manifest` and `/script/page/<idx>` endpoints — no new file-serving code.
- Reuses `_resolve_api_keys(asn)` and the existing per-provider dispatch in `ai_marking.py` — no new provider integration.
- New file: `templates/exemplars.html`.
- Modified files: `app.py` (three routes + AI helper), `db.py` (two columns), `templates/teacher_detail.html` (one new button).
- No new Python dependencies.
