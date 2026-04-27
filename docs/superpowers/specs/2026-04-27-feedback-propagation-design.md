# Feedback Propagation + Marking Principles — Design

**Branch:** `feedback_propagation` (off `feed_forward_beta`)
**Date:** 2026-04-27
**Status:** Approved by user, ready for implementation plan

## Goal

When a teacher edits feedback on one student's submission and saves it
to the calibration bank, the system surfaces other students in the
same assignment who made the same kind of mistake on the same
criterion and offers a one-click "apply this standard to all".
Propagation refreshes only the two text fields (`feedback`,
`improvement`) on the affected criterion via a cheap text-only AI
call — never re-runs `mark_script()`.

As the calibration bank grows across all teachers in a subject,
raw-example calibration injection at mark time is replaced by a
shared markdown **subject-level** principles file that aggregates
every contributing teacher's standards into a single token-efficient
prompt prefix. When teachers' standards diverge in the same theme,
the regeneration LLM picks the dominant pattern and flags the row;
the teacher hub then shows a quiet nudge to review the patterns
page.

## Non-goals

- No re-marking. `mark_script()` is never called on already-marked
  submissions. Refresh changes only `feedback` and `improvement`;
  marks, status, theme_key, and every other field stay put.
- No re-evaluation of correctness. Refresh assumes the original
  marking decision was correct; only the wording of the feedback
  changes.
- No automatic propagation. Every refresh batch is explicitly
  opt-in by the teacher (Apply / Review individually / Skip).
- No new feedback_log version rows for propagation. Provenance
  lives on the question entry in `result_json` via
  `feedback_source` and `propagated_from_edit`. (Locked per user
  interpretation answer.)
- Student-facing view is unchanged.

## Locked decisions

| # | Decision |
|---|---|
| 1 | New per-criterion fields (`feedback_source`, `propagated_from_edit`) live inside `result_json.questions[]` — no new criterion table. Matches the existing pattern for `theme_key`, `idea`, `specific_label`, etc. |
| 2 | Cross-submission criterion match key is `str(q.get('question_num')) == edit.criterion_id`. The existing `feedback_edit.criterion_id` is already populated as `str(question_num)` by `_process_text_edit`, and every submission in the same assignment uses the same `question_num` scheme. The earlier `criterion_name` fallback proposed during brainstorming turned out to be unnecessary because we always start from the edit row. |
| 3 | Propagation does NOT write `feedback_log` rows. Provenance is per-criterion: `feedback_source = 'propagated'` and `propagated_from_edit = <feedback_edit.id>`. |
| 4 | All AI calls in this feature use the cheap-tier model via the existing `HELPER_MODELS` map. Reserved: `mark_script()` continues to use the assignment's main model. |
| 5 | All background threads open their own app context (existing `_run_categorisation_worker` pattern). Sequential propagation per edit; never parallel — avoids DB contention and keeps Railway memory predictable. |
| 6 | `marking_principles_cache` is keyed by `subject_family` ALONE — one shared row per subject across the whole department. Drops the original spec's `(teacher_id, subject_family)` keying. Raw calibration examples (`fetch_calibration_examples`) stay teacher-scoped because they carry verbatim contextual wording; the summarised principles file is the cross-teacher consistency mechanism. |
| 7 | New columns added to `feedback_edit` via the existing auto-migration block in `db.py`, not Alembic (the project doesn't use Alembic). |
| 8 | Propagation candidate detection runs synchronously inside the PATCH handler so the client gets `candidate_count` alongside `edit_meta` in the same response — no second round-trip just to know whether to show the banner. |
| 9 | Insight extraction (mistake_pattern, correction_principle, transferability) runs in a background thread, never blocks the teacher's save. Same pattern as categorisation kickoff. |
| 10 | Calibration injection at mark time is now tiered: < 8 active edits in the subject across ALL teachers → raw examples (existing teacher-scoped path); >= 8 edits → shared markdown principles file. |
| 11 | The principles file is regenerated lazily: marked stale at edit time, regenerated on next mark-time read when stale + threshold met (or every 30 days). Saves never wait on regeneration. |
| 12 | Conflict handling: regeneration LLM is instructed to take the dominant pattern when corrections in the same theme conflict, and to set `has_conflicts: true` on the cache row when it had to suppress a contradicting principle. The teacher hub shows a soft nudge (not a badge) when any subject the teacher contributes to has `has_conflicts = true`. No in-page conflict resolution UI; resolution is teachers refining their own bank. |
| 13 | Propagation candidates, retire flow, and the original feedback_edit log all stay teacher-scoped. Only the principles file is cross-teacher. |

## Scope of implementation

This spec covers Parts 1–9 of the user-supplied build prompt. Small
deliberate divergences from the prompt's wording, all confirmed
during brainstorming:

- `marking_principles_cache` is keyed by `subject_family` alone — one shared row per subject across the dept. The original spec's `teacher_id` column is dropped because we want consistency across teachers in the same subject, not per-teacher principles.
- Cross-submission matching uses `str(question_num)` against the edit's existing `criterion_id` field (the spec's literal "criterion_name" misses that the edit row already carries question_num; matching on it works for both rubrics and short-answer with no fallback needed).
- New per-criterion fields live in `result_json.questions[]`, not in a new criterion table (matches existing pattern).
- Propagation does NOT write `feedback_log` rows.

## Architecture

Six-file Flask app per `CLAUDE.md`. Models extend `db.py`. AI helpers
extend `ai_marking.py`. Routes + worker thread extend `app.py`. UI
changes go into `templates/teacher_detail.html`,
`templates/base.html`, a new `templates/marking_patterns.html`, and
`static/js/feedback_render.js`. No new module file.

### Schema (`db.py`)

#### New table: `marking_principles_cache`

```
id                  INTEGER PRIMARY KEY (autoincrement)
subject_family      VARCHAR(40) UNIQUE NOT NULL   (one shared row per subject)
markdown_text       TEXT NOT NULL DEFAULT ''
generated_at        TIMESTAMP WITH TIME ZONE
is_stale            BOOLEAN DEFAULT FALSE
edit_count_at_gen   INTEGER DEFAULT 0
has_conflicts       BOOLEAN DEFAULT FALSE
```

Created via SQLAlchemy `db.create_all()` — same pattern as the
feedback_log/feedback_edit tables added in the prior feature. The
`UNIQUE` on `subject_family` prevents accidental duplicates and acts
as the upsert key during regeneration.

#### New columns on `feedback_edit`

```
propagation_status    VARCHAR(20) DEFAULT 'none'   -- none|pending|partial|complete|skipped
propagated_to         TEXT DEFAULT '[]'            -- JSON list: [{submission_id, status, error?}]
propagated_at         TIMESTAMP WITH TIME ZONE NULL
mistake_pattern       VARCHAR(80) NULL
correction_principle  VARCHAR(300) NULL
transferability       VARCHAR(10) NULL              -- high|medium|low
```

Added via `_migrate_add_columns` in `db.py` (existing ALTER TABLE
auto-migration pattern).

#### Per-criterion fields in `result_json.questions[]`

```
feedback_source       'original_ai' | 'teacher_edit' | 'propagated'   (default 'original_ai' when absent)
propagated_from_edit  integer FK to feedback_edit.id, only when feedback_source == 'propagated'
```

These live inside the existing `result_json` TEXT column on
`Submission`. No schema change. The PATCH handler that already
mutates `result_json` is the natural place to set
`feedback_source = 'teacher_edit'` when a teacher edits feedback or
improvement (whether or not they checked "calibration bank").

### AI helpers (`ai_marking.py`)

#### `extract_correction_insight(provider, model, session_keys, subject_family, theme_key, criterion_name, original_text, edited_text) -> dict`

Cheap-tier model. System prompt verbatim from spec Part 2. Returns
`{mistake_pattern, correction_principle, transferability}`. Caller
writes the three fields back to the originating `feedback_edit` row.
On any AI failure: logger.warning + return `None`; caller leaves the
columns NULL.

#### `refresh_criterion_feedback(provider, model, session_keys, subject, criterion_name, student_answer, correct_answer, marks_awarded, marks_total, calibration_edit) -> dict`

Cheap-tier model, max_tokens=300, text-only call. System prompt:

```
You are regenerating feedback for one criterion on a student's
script. A teacher has shown you their marking standard by editing
another student's feedback on the same type of mistake.

Apply the same standard to this student's answer. Do not change
the marks. Do not re-evaluate correctness. Only rewrite the
Feedback and Suggested Improvement fields.

{FEEDBACK_GENERATION_RULES}

Return JSON only:
{
  "feedback": "...",
  "improvement": "..."
}
```

User prompt (literal from spec Part 4): truncated original_text,
edited_text, optional `correction_principle` line, criterion_name,
student_answer (≤ 600 chars), correct_answer (≤ 400 chars), marks.
Returns `{feedback, improvement}` parsed from JSON. No image
content. No images, ever — the original-marking pipeline already
processed them.

#### `count_active_calibration_edits(subject_family) -> int`

Single SELECT COUNT against `feedback_edit` filtered to
`subject_family, active=true` — counts ALL teachers' active edits
in the subject. Used by the calibration injection threshold gate.

#### `get_marking_principles(provider, model, session_keys, subject_family) -> str`

Returns the shared cached markdown file for the subject. Regenerates
when:
1. Cache row missing, OR
2. `is_stale == true` AND `count_active_calibration_edits >= 8`, OR
3. `generated_at` is older than 30 days.

Below 8 active edits across all teachers, returns `''` always —
caller falls back to raw examples.

Regeneration calls cheap-tier model, max_tokens=700. System prompt
verbatim from spec Part 6 with one addition: instructs the LLM to
take the dominant pattern when corrections in the same theme
conflict, and to emit `has_conflicts: true | false` alongside the
markdown.

Output JSON shape:
```json
{
  "markdown": "...principles file content...",
  "has_conflicts": true | false
}
```

User prompt: a structured summary of all active edits across all
teachers for this subject_family, grouped by `theme_key`, listing
`correction_principle` (truncated to 150 chars per edit) +
per-theme edit count.

On regeneration: `markdown_text`, `generated_at = now()`,
`is_stale = false`, `edit_count_at_gen = current_count`,
`has_conflicts = parsed value`.

Failure during regeneration → log + return existing `markdown_text`
if any, else `''`. `has_conflicts` left untouched on failure.

#### `build_calibration_block(teacher_id, asn, subject_family, theme_keys, provider, model, session_keys) -> str`

Replaces direct `format_calibration_block(fetch_calibration_examples(...))`
calls in `_run_submission_marking`. Tiered logic:

```
edit_count = count_active_calibration_edits(subject_family)
if edit_count < 8:
    # Below shared-threshold — fall back to teacher-scoped raw examples.
    return format_calibration_block(fetch_calibration_examples(teacher_id, asn, theme_keys, limit=10))
else:
    principles = get_marking_principles(provider, model, session_keys, subject_family)
    if not principles:
        return format_calibration_block(fetch_calibration_examples(teacher_id, asn, theme_keys, limit=5))
    return "---\nMARKING PRINCIPLES (this subject's established standard)\n\n" + principles + "\n---\n\n"
```

Note the wrapper now says "this subject's" not "this teacher's" —
the principles are shared across the subject and the marking model
should know the calibration is collective, not personal.

### Server hooks (`app.py`)

#### `_process_text_edit` extension (existing PATCH handler helper)

After the existing log + edit row writes, when the calibrate path is
taken (`feedback_edit` row was just inserted):

1. `target_q['feedback_source'] = 'teacher_edit'` on the question
   dict in `result_json` (in-memory; the existing `sub.set_result`
   persists it in the same commit).
2. `UPDATE marking_principles_cache SET is_stale=true WHERE subject_family=:sf` — same transaction. (Subject-keyed; ANY teacher's bank save invalidates the shared cache for that subject.)
3. Spawn `threading.Thread(target=_run_insight_extraction_worker, args=(app, edit_id), daemon=True).start()` — non-blocking, opens its own app context.
4. Synchronously: call `_find_propagation_candidates(edit, asn)` and
   include `{candidate_count, edit_id, criterion_name}` in the PATCH
   response under a new `propagation_prompt` key (alongside the
   existing `edit_meta`). Empty when no candidates exist.

When a teacher edit lands on the workflow-note path (no
`feedback_edit` row written, just a feedback_log version), still set
`target_q['feedback_source'] = 'teacher_edit'` so propagation never
overwrites it — but skip the staleness flag, the insight worker, and
the candidate detection (those are bank-only).

#### `_find_propagation_candidates(edit, asn) -> dict`

```sql
SELECT s.id AS submission_id, s.result_json, st.name AS student_name,
       st.id AS student_id
FROM submissions s
LEFT JOIN students st ON s.student_id = st.id
WHERE s.assignment_id = :aid
  AND s.id != :source_sid
  AND s.status = 'done'
ORDER BY s.id
```

Iterate the rows in Python:
- Decode `result_json`, find the question where
  `str(q.get('question_num')) == edit.criterion_id`.
- Skip if the question is missing on this submission.
- Keep only when:
  - `(marks_total > 0 AND marks_awarded < marks_total)` OR `(status != 'correct')`, AND
  - `feedback_source IN (None, 'original_ai', 'propagated')` — never re-propagate over a `teacher_edit`.

Return:
```
{
  'edit_id': edit.id,
  'criterion_name': edit.criterion_id,  -- the question_num key, exposed as `criterion_name` to match the spec's response shape
  'candidate_count': len(candidates),
  'candidates': [
    {submission_id, student_name, marks_awarded, marks_total,
     current_feedback, current_improvement},
    ...
  ]
}
```

#### `_run_insight_extraction_worker(app_obj, edit_id)`

Background thread, opens own app context. Loads the edit row,
calls `extract_correction_insight()` with the cheap-tier model for
the assignment's provider, writes the three columns back. Wrapped
in try/except — failure leaves the columns NULL and is logged but
not surfaced.

#### `_run_propagation_worker(app_obj, edit_id, target_ids)`

Background thread, sequential per-candidate loop (never parallel).
At the start of the worker, write
`feedback_edit.propagated_to = json.dumps([{submission_id, status: 'pending'}, ...])`
and commit, so the progress poll has the full target list visible
from the first poll.

For each `target_id`:
1. Load the submission, find the matching question by
   `str(q.question_num) == edit.criterion_id`.
2. Call `refresh_criterion_feedback()` with the cheap-tier model
   and the originating edit's metadata (original_text, edited_text,
   correction_principle).
3. On success: update `feedback`, `improvement`, set
   `feedback_source='propagated'`, `propagated_from_edit=edit_id`,
   `sub.set_result(result)`, commit. Replace the corresponding
   entry in `propagated_to` with `{submission_id, status: 'done'}`.
4. On failure: log, append `{submission_id, status: 'failed', error}`
   to `propagated_to`. Continue the loop.

After the loop:
- `propagation_status = 'complete'` if all done, else `'partial'`
- `propagated_at = now()`
- Commit.

### New routes (`app.py`)

| Method | Path | Purpose |
|---|---|---|
| GET  | `/feedback/propagation-candidates/<edit_id>` | Return candidate list for a feedback_edit row. Auth: edit owner. |
| POST | `/feedback/propagate` | Kick off propagation. Body `{edit_id, mode, submission_ids?}`. Auth: edit owner. |
| POST | `/feedback/propagate-skip` | Mark the edit `propagation_status = 'skipped'`. Body `{edit_id}`. Auth: edit owner. |
| GET  | `/feedback/propagation-progress/<edit_id>` | Return current `{propagation_status, total, done, failed, propagated_to}`. Auth: edit owner. |
| GET  | `/teacher/marking-patterns` | Render per-subject_family principles + edit counts for the current teacher. Auth: any logged-in teacher. |

Auth pattern for propagation routes: load the `feedback_edit` row,
check `edit.edited_by == _current_teacher().id` (mirrors the retire
route's ownership pattern). 403 otherwise.

### Client (`static/js/feedback_render.js`, `templates/teacher_detail.html`)

#### Banner DOM

A new `<div id="fbPropagationBanner" hidden>` inserted directly above
`<table class="results-table">` in `templates/teacher_detail.html`.
JS populates and shows it.

#### Banner trigger

Existing `saveTextField` flow already reads `data.edit_meta` after a
successful PATCH. New branch: if `data.propagation_prompt` is present
AND `propagation_prompt.candidate_count > 0`, show the banner with:

- Criterion name, edited student name, candidate count.
- Three buttons: `Apply same standard to all`, `Review individually`, `Skip`.

#### Banner actions

- **Apply all** → `POST /feedback/propagate` mode `all`. On 200,
  start polling `/feedback/propagation-progress/<edit_id>` every 2s.
  Update banner text "Updating N of M students..." → "✓ Feedback
  updated for M students." Auto-dismiss 4s after complete.
- **Review individually** → expand banner inline; `GET
  /feedback/propagation-candidates/<edit_id>` returns the list with
  current_feedback and current_improvement so the panel renders
  without a second fetch. Each candidate has a checkbox (default
  checked). Confirm → `POST /feedback/propagate` mode `selected`
  with the checked submission_ids. Cancel → collapses back.
- **Skip** → `POST /feedback/propagate-skip`, dismiss banner.

#### Per-student feedback source indicator

A new column rendered server-side in `templates/teacher_detail.html`'s
results table. For each submission row, aggregate the
`result_json.questions[*].feedback_source` values:

```
if any 'teacher_edit'  → ✎  (tooltip: "Teacher edited directly")
elif any 'propagated'  → ↻  (tooltip: "Updated DD MMM YYYY — propagated from <student name if accessible>")
else                   → ○  (tooltip: "Original AI feedback")
```

No new endpoint. No interaction. The aggregation happens in the
view function that already renders the assignment results table.

### `/teacher/marking-patterns` page

New route + new template `templates/marking_patterns.html`.

Server:
- Query `feedback_edit` grouped by `subject_family` for ALL teachers,
  filtered to `active = true`. Counts the shared subject pool.
- For each `subject_family` the current teacher has contributed at
  least one edit to: fetch the shared cache row.
- For each family with `>= 8` edits across the dept: render the
  cache row's `markdown_text`.
- For families `< 8`: render a count + "Add N more calibration
  edits across this subject to unlock the shared marking principles."
- Per family, also render the current teacher's contribution count:
  "You've contributed N of M total edits in this subject."

Template:
- One section per subject_family the teacher has contributed to.
- Section heading: human-readable subject family label + total
  active edit count + the teacher's contribution count.
- Body: principles markdown (server-side conversion via existing
  pipeline if any, else `<pre>`). One-line nudge if
  `has_conflicts = true`: "Some standards in this subject look
  mixed across teachers. The summary above takes the dominant
  pattern; review your own bank if you'd like to refine your
  contribution."
- No AI calls. No progress indicators. Pure read.

#### Header link

`templates/base.html` line 34, after the Bank link: a new `<a href="/teacher/marking-patterns">My marking patterns</a>` for any logged-in teacher.

### Soft conflict nudge on the teacher hub

When a teacher lands on the post-login hub (`templates/hub.html` —
the existing card grid), render a small one-line notice at the top
when ANY subject they contribute to has `has_conflicts = true`.

Server: a single tiny query at hub render —

```sql
SELECT 1
FROM marking_principles_cache c
WHERE c.has_conflicts = true
  AND c.subject_family IN (
      SELECT DISTINCT subject_family
      FROM feedback_edit
      WHERE edited_by = :teacher_id
        AND active = true
        AND subject_family IS NOT NULL
  )
LIMIT 1
```

If the result set is non-empty, render:

> Some standards in your subjects look mixed across teachers. [Review your marking patterns →]

The link target is `/teacher/marking-patterns`. No badge, no count,
no per-subject breakdown, no in-page conflict resolution UI.
Resolution is teachers refining their own bank and the next
regeneration settling on a single dominant pattern.

## Data flow

```
Teacher saves a calibration edit
  → PATCH /teacher/.../result with calibrate=true
  → _process_text_edit writes feedback_log v(N+1) + feedback_edit row
  → target_q['feedback_source'] = 'teacher_edit' in result_json
  → marking_principles_cache.is_stale = true (same txn)
  → spawn _run_insight_extraction_worker (background, non-blocking)
  → _find_propagation_candidates synchronously
  → response carries edit_meta + propagation_prompt
  → client shows the propagation banner

Teacher clicks "Apply same standard to all"
  → POST /feedback/propagate mode='all'
  → server seeds propagated_to with {sid, status: 'pending'} list
  → spawn _run_propagation_worker
  → 200 with status='started', candidate_count
  → client polls /feedback/propagation-progress/<edit_id> every 2s
  → worker loop: refresh_criterion_feedback per candidate, update result_json,
    set feedback_source='propagated', append {sid, status: 'done'|'failed'}
  → final: propagation_status='complete'|'partial', propagated_at=now()
  → client receives 'complete'/'partial', shows summary, auto-dismisses

Next assignment marked by ANY teacher in the same subject
  → _run_submission_marking calls build_calibration_block
  → count_active_calibration_edits(subject_family) — counts ALL teachers' active edits in the subject
  → < 8 across the subject: raw examples (teacher-scoped, existing path)
  → >= 8: get_marking_principles(subject_family) — shared file
        → cache row stale? → regenerate via cheap-tier model
            → LLM emits {markdown, has_conflicts}
            → write back markdown_text, has_conflicts, generated_at, edit_count_at_gen, is_stale=false
        → return cached markdown
        → wrap in "MARKING PRINCIPLES (this subject's established standard)" delimiter block
  → block prepended to system prompt as before

Teacher lands on hub
  → small COUNT query: any subject I contribute to with has_conflicts = true?
  → if yes: render one-line nudge linking to /teacher/marking-patterns
  → no badge, no count, no in-page resolution UI
```

## Error handling

- **Insight extraction failure** — logged, columns left NULL, save flow unaffected.
- **Candidate detection failure** — logged, response omits `propagation_prompt`, banner doesn't appear, save flow unaffected.
- **Propagation worker per-candidate failure** — logged, recorded in `propagated_to[i].status='failed'` + error message, batch continues with the next candidate. Final status becomes `'partial'`.
- **Refresh AI call failure** — same as above. Original feedback on the candidate stays untouched.
- **Principles regen failure** — logged, falls back to existing markdown if any, else falls back to raw examples in the calibration block.
- **Conflict detection failure** — the regen LLM may produce malformed JSON or omit `has_conflicts`. Default to `has_conflicts = false` if the field is missing or unparseable; the hub nudge stays quiet rather than firing on noise.
- **Auth failures** — 401/403 from the existing helpers.
- **Validation failures** — 400 with a JSON error body.

## Confirmation: mark_script() never re-runs on already-marked submissions

Verified across the spec. `refresh_criterion_feedback()` is the only
AI call that touches an already-marked submission, and it is
text-only (no images, no rubric ingestion, no full-pipeline
behaviour). The propagation worker explicitly mutates only the two
text fields plus the per-criterion provenance flags.

## Confirmation: cheapest model used everywhere except mark_script()

| AI call | Model |
|---|---|
| `mark_script` (initial marking) | Assignment's main model (Sonnet / GPT-5.4 / Qwen 3.6) |
| `extract_correction_insight` | Cheap-tier (Haiku / mini / Qwen 3.5) |
| `refresh_criterion_feedback` | Cheap-tier |
| `get_marking_principles` regen | Cheap-tier |
| `categorise_mistakes` | Assignment's main model (existing — unchanged) |
| `explain_criterion` | Cheap-tier (existing — from feedback_token_optimization) |
| `evaluate_correction` | Cheap-tier (existing — from feedback_token_optimization) |

All routed via the `_helper_model_for(provider, fallback)` helper
already added in `feedback_token_optimization`.
