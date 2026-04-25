# Feedback Edit Log — Design

**Branch:** `feedback_edit_log` (off `feed_forward_beta`)
**Date:** 2026-04-25
**Status:** Approved by user, ready for implementation plan

## Goal

Capture every AI-generated feedback string and every teacher edit to it,
so future AI marking prompts can be calibrated by the teacher's own
prior corrections. The system is teacher-individual today; the schema
admits a future department-level promotion path without retrofitting.

## Non-goals

- Department-wide promotion of edits to a shared bank. The schema
  carries `scope`, `promoted_by`, `promoted_at` columns for forward
  compatibility but never writes them with non-default values in this
  implementation.
- Any change to the student-facing feedback view. Students continue to
  see only the most up-to-date feedback text. The calibration bank vs
  workflow note distinction is invisible to students.
- Diff visualisation, autosave, age-based staleness, or text similarity
  search. Staleness is controlled exclusively by `rubric_version` and
  the `active` flag.

## Locked decisions

| # | Decision |
|---|---|
| 1 | The student always sees the latest text. The "calibration bank vs workflow note" choice only changes whether the edit feeds future AI prompts. |
| 2 | Default = workflow note (calibration off). Opting in to the bank is a deliberate action. |
| 3 | UI = single labelled checkbox below the inline-edit textarea: `☐ Save to calibration bank`. |
| 4 | The toggle applies only to `feedback` and `improvement` text fields. Marks/status edits stay as plain PATCH operations — no toggle, no log, no calibration. |
| 5 | After save, a small grey one-liner tag (`· in calibration bank` or `· workflow note`) renders directly under the field that was edited. AI-original (un-edited) fields show no tag. |
| 6 | On every fresh edit, the checkbox resets to default (workflow note). The teacher's previous choice does NOT pre-populate. |
| 7 | When a teacher saves the same `(criterion, field)` to the calibration bank a second time, the older `feedback_edit` row is deactivated (`active=false`) and a new row written. One active edit per `(teacher, assignment, criterion, field)`. |
| 8 | Empty / unchanged saves are no-ops: no log row, no edit row. The PATCH itself is still idempotent. |
| 9 | Rubric/answer_key columns are `LargeBinary` blobs. Hash the **raw bytes**: `hashlib.md5(asn.rubrics or asn.answer_key or b'').hexdigest()`. The spec's `.encode()`-over-text formulation does not match the actual columns and is replaced. |
| 10 | The retire (deprecate) link sits inside the edit-history popover, only beside active edits the current teacher made. |
| 11 | The "View edit history" link appears under a criterion's per-field tag only after at least one teacher edit exists for that field. |
| 12 | Calibration prompt block is prepended at the very top of the system prompt in both `_build_rubrics_prompt()` and `_build_short_answer_prompt()` — before `Subject:` — capped at 10 examples. |
| 13 | The relevance query runs once per distinct `theme_key` in the current submission's lost-mark criteria, plus once for the same-assignment+same-rubric tier. Results merged, deduplicated by edit id, collapsed to most-recent per `(criterion_id, field)`, then truncated to 10. |

## Scope of implementation

This spec covers Parts 1–8 of the user-supplied build prompt, with
the following small, deliberate divergences from that prompt's wording:

- **`feedback_log.field` column added.** The spec listed one `feedback_text` per criterion. We need separate version histories for the `feedback` and `improvement` lines because both are edited independently and both feed calibration. Without `field`, the two streams collide.
- **`feedback_edit.field` column added.** Same reason — calibration injection differentiates between feedback and improvement examples.
- **Single PATCH endpoint instead of new `POST /feedback/edit-criterion`.** The codebase already has a working `PATCH /teacher/assignment/<aid>/submission/<sid>/result` for inline edits. We extend that endpoint with an optional `calibrate: bool` per question entry rather than splitting the same edit flow across two URLs. The existing path also handles auth + ownership in one place.
- **Three-segment edit-history path.** `GET /feedback/edit-history/<assignment_id>/<submission_id>/<criterion_id>` rather than the spec's two-segment path, so the existing `_check_assignment_ownership(asn)` helper applies cleanly.
- **`rubric_version` hashes raw bytes**, not text (see decision 9).

## Architecture

The codebase is a six-file Flask app per `CLAUDE.md`. We keep that
contract: no new module file. Models extend `db.py`, the lookup +
injection helper extends `ai_marking.py`, routes extend `app.py`, UI
changes go into `templates/teacher_detail.html` +
`static/js/feedback_render.js`.

### Schema (`db.py`)

#### `feedback_log` — version history

```
id              INTEGER PRIMARY KEY (autoincrement)
submission_id   INTEGER FK → submissions.id   (indexed)
criterion_id    VARCHAR(64)                    -- str(question_num)
field           VARCHAR(20)                    -- 'feedback' | 'improvement'
version         INTEGER                        -- 1 = AI original, 2+ = teacher edits
feedback_text   TEXT
author_type     VARCHAR(10)                    -- 'ai' | 'teacher'
author_id       INTEGER NULL                   -- null for AI; teachers.id otherwise
created_at      TIMESTAMP WITH TIME ZONE       (default now())
UNIQUE (submission_id, criterion_id, field, version)
```

Append-only. Unique constraint makes `version=1` AI inserts idempotent
across re-marks. Stores both AI originals and every teacher save
(workflow note OR calibration bank — both create a row here).

#### `feedback_edit` — calibration bank

```
id              INTEGER PRIMARY KEY
submission_id   INTEGER FK → submissions.id
criterion_id    VARCHAR(64)
field           VARCHAR(20)                    -- 'feedback' | 'improvement'
original_text   TEXT                           -- the version-1 AI text
edited_text     TEXT                           -- the teacher's text
edited_by       INTEGER FK → teachers.id      (indexed)
subject_family  VARCHAR(40)                    -- snapshot from assignment at edit time
theme_key       VARCHAR(40) NULL               -- snapshot, may be null if categorisation hadn't run
assignment_id   VARCHAR(64) FK → assignments.id   (indexed)
rubric_version  VARCHAR(64)                    -- md5 hex over raw rubric/answer_key bytes
scope           VARCHAR(20) DEFAULT 'individual'   -- FUTURE: department-level promotion
promoted_by     INTEGER NULL                       -- FUTURE: department-level promotion
promoted_at     TIMESTAMP WITH TIME ZONE NULL      -- FUTURE: department-level promotion
active          BOOLEAN DEFAULT TRUE
created_at      TIMESTAMP WITH TIME ZONE       (default now())
INDEX (edited_by, active, subject_family, theme_key)
INDEX (assignment_id, rubric_version)
```

Only opted-in edits land here. Soft-delete via `active=false`; rows
are never physically removed.

### Marking-time integration (`ai_marking.py`, `app.py`)

#### Calibration lookup helper (in `ai_marking.py`)

```
def fetch_calibration_examples(teacher_id, assignment, theme_keys, limit=10):
    """Returns [{original_text, edited_text, theme_key, match_tier, ...}]."""
```

Run `len(theme_keys) + 1` queries via `db.session.execute(text(...), {...})`:

1. **Same-assignment, same-rubric** (no theme filter):
   `assignment_id = :aid AND rubric_version = :hash AND edited_by = :teacher AND active = true`
   Returns with `match_tier = 0`.
2. **Per-theme cross-assignment** (one query per `theme_key`):
   `subject_family = :sf AND theme_key = :tk AND assignment_id != :aid AND edited_by = :teacher AND active = true`
   Returns with `match_tier = 1`.

Merge into a dict keyed by `feedback_edit.id`, sort by `(match_tier
ASC, created_at DESC)`, then collapse to most-recent per
`(criterion_id, field)`, then truncate to `limit`. All bound
parameters — no string interpolation.

`theme_keys` is collected from `result_json.questions[*].theme_key` for
criteria where marks were lost. If the submission has no themed
criteria yet (categorisation hadn't run, or it's the first submission
for the assignment), only tier 0 returns rows — correct degraded
behaviour.

#### Prompt injection

Helper formats the (≤10) results into the exact block from spec
Part 3 (truncating each text to 200 chars at the nearest word boundary
with trailing `...`), then prepends to the system prompt in both
`_build_rubrics_prompt()` and `_build_short_answer_prompt()`, before
`Subject:`. Empty results → no block added, no mention of absence.

The lookup runs synchronously inside the existing background marking
worker, before the AI call. A few short Postgres queries plus
formatting; comfortably under 50ms.

#### Logging AI originals

After `result_json` is parsed and stored, but before the worker marks
the submission `done`, write one `feedback_log` row per criterion for
both `feedback` and `improvement`:
- `version=1`, `author_type='ai'`, `author_id=NULL`,
- `feedback_text` = exact AI text.

Wrapped in `try/except` that logs and swallows. The unique index
makes this idempotent: re-marks see `ON CONFLICT DO NOTHING` on the
v1 row and skip silently. Re-marks therefore preserve the *first*
AI output as `original_text` for the calibration bank — which is the
correct semantics (the bank reflects what the teacher actually
corrected, not whatever a later re-mark happened to produce).

### Teacher edit flow (`app.py`, `static/js/feedback_render.js`, `templates/teacher_detail.html`)

#### Server — extending the existing PATCH

The existing `PATCH /teacher/assignment/<aid>/submission/<sid>/result`
takes `{questions: [{question_num, feedback?, improvement?, marks_*?, status?}], overall_feedback?}`. We extend each question entry with an optional `calibrate: bool` and the response with `versions` per-question.

For each question entry that carries a `feedback` or `improvement`
text edit:

1. Auth and ownership check (existing `_check_assignment_ownership`).
2. Validate text non-empty and ≤ 2000 chars; 400 if invalid.
3. Compute `criterion_id = str(question_num)`.
4. Read current `feedback`/`improvement` from `result_json`. If
   `edited_text == current_text`, no-op the log/edit writes — still
   PATCH `result_json` for shape consistency.
5. In a single SQL transaction:
   - Read `MAX(version)` for `(submission_id, criterion_id, field)` from `feedback_log`.
   - Insert new `feedback_log` row at `version = max+1`, `author_type='teacher'`, `author_id=teacher.id`, `feedback_text=edited_text`.
   - If `calibrate=True`:
     - Read or back-fill the `version=1` row's `feedback_text` (the AI original). Back-fill happens for legacy submissions marked before this branch; the back-filled row uses the current `result_json` as its source.
     - `UPDATE feedback_edit SET active = false WHERE edited_by=:t AND assignment_id=:a AND criterion_id=:c AND field=:f AND active=true` (single active edit per teacher per criterion-field).
     - `INSERT INTO feedback_edit (...)` with the snapshot fields per Section 1.
   - Commit. On failure, roll back; the `result_json` PATCH is still applied (log/edit are best-effort and never block the user-facing save).

Response per text-edited question:
```json
{
  "success": true,
  "result": { ... existing payload ... },
  "edit_meta": {
    "<criterion_id>": {
      "feedback":   {"version": 3, "calibrated": true},
      "improvement": {"version": 2, "calibrated": false}
    }
  }
}
```

Marks/status edits don't appear in `edit_meta`.

#### GET endpoint extension

`GET /teacher/assignment/<aid>/submission/<sid>/result` returns the
`text_edit_meta` map (latest version + `calibrated` flag per
`(criterion_id, field)`) so the page can render the per-field tag on
load without an extra round-trip.

#### Client — `feedback_render.js`

`beginTextEdit()` is the existing function that opens a textarea. Two
additions when the field is `feedback` or `improvement`:

1. Append a checkbox below the textarea: `☐ Save to calibration bank`. Default unchecked. Re-renders fresh on each entry to edit mode — no carryover from previous saves.
2. On blur (existing save trigger), include `calibrate: checkbox.checked` in the PATCH payload. On success, read `edit_meta[criterion_id][field]` and render the per-field grey tag below the field.

Esc-to-cancel, blur-to-save, the `data-editing="1"` re-entry guard,
and all other existing behaviours are unchanged.

#### Tag rendering

Plain HTML span below each field, class `fb-edit-tag`, content
`· in calibration bank` (when `calibrated=true`) or `· workflow note`
(otherwise). On initial page load, populated from `text_edit_meta`
returned by the GET. After save, populated from the PATCH response.
Fields with no teacher edit show no tag.

### History view (`app.py`, `static/js/feedback_render.js`)

#### Server route

`GET /feedback/edit-history/<assignment_id>/<submission_id>/<criterion_id>`. Auth via `_check_assignment_ownership(asn)`.

Returns:
```json
{
  "feedback":   [HistoryEntry, ...],
  "improvement":[HistoryEntry, ...]
}
```

`HistoryEntry`:
```json
{
  "version": 1,
  "author_type": "ai",
  "author_name": "AI",
  "feedback_text": "...",
  "created_at": "3 Apr 2025",
  "edit_id": null,
  "active": null
}
```

For teacher rows, `author_name` = `Teacher.display_name` if set, else
`name`, else `f"Teacher #{author_id}"`. `edit_id` and `active` come
from a left-join on `feedback_edit` matching
`(submission_id, criterion_id, field, edited_text=feedback_text,
edited_by=author_id)`. Null when no calibration row exists for that
version (the teacher saved as workflow note).

Per-version entries are oldest-first.

#### Client

A "View edit history" link renders below each tag for fields that have
≥ 1 teacher edit. Clicking expands an inline panel under the criterion
row showing both fields' histories (with small headings for each), in
the spec format. Plain text. No diff highlighting. Dates as `D MMM YYYY`.

For each entry where `edit_id` is non-null, `active=true`, and
`author_id == current_teacher_id`, render a small `[Retire this edit]`
link. Retired entries (`active=false`) display with a muted `· retired`
marker and no link.

### Retire route (`app.py`)

`POST /feedback/deprecate-edit` with body `{edit_id: int}`:
1. Auth: logged-in teacher only.
2. Load `feedback_edit` by id; 404 if missing.
3. Authorize: `fe.edited_by == current_teacher_id`. 403 if not.
4. `UPDATE feedback_edit SET active = false WHERE id = :id`.
5. Return `{status: "ok"}` (or `{status: "error", "message": "..."}` on failure).

The Section 2 lookup query already filters `active = true` so retired
edits naturally drop out of future calibration prompts.

### Railway / Postgres specifics

- New columns: `TIMESTAMP WITH TIME ZONE`, `TEXT`, `VARCHAR(N)`, `BOOLEAN`, `INTEGER` — all psycopg2-compatible.
- Relevance lookup uses `db.session.execute(text(...), {...})` with bound parameters per spec — never f-string interpolation.
- No new dependencies. `hashlib` (stdlib) and SQLAlchemy already in.
- No Alembic. Schema changes live in the existing auto-migration block invoked from `db.init_db()`. Two new tables via `CREATE TABLE IF NOT EXISTS ... ` and indexes via `CREATE INDEX IF NOT EXISTS ...` — Railway re-runs this on every deploy without harm.
- `DATABASE_URL` is consumed by the existing app config; no change.

## Data flow

```
Submission marked
  → AI response parsed
  → result_json written
  → feedback_log v1 inserts (one per criterion × {feedback, improvement})  [best-effort]
  → submission.status = 'done'

Teacher inline-edits a feedback or improvement field
  → PATCH .../result with {questions: [{question_num, field, calibrate?}]}
  → result_json updated
  → feedback_log v(max+1) insert
  → if calibrate: deactivate old bank rows for (teacher, assignment, criterion, field), insert new feedback_edit
  → response includes edit_meta
  → client renders per-field tag

New submission marked
  → fetch_calibration_examples(teacher, assignment, themes)
    → 1 same-assignment+same-rubric query (tier 0)
    → N per-theme cross-assignment queries (tier 1)
    → merge, dedup by id, collapse per (criterion, field), top 10
  → format calibration block
  → prepend to system prompt
  → AI call
  → (loop)
```

## Error handling

- **Log/edit write failures** are caught, logged, and swallowed. The user-facing PATCH succeeds regardless. The student is never blocked by a logging error.
- **Calibration lookup errors** are caught and logged; marking proceeds with no calibration block. Never blocks marking.
- **Auth failures** return 401/403 from the existing helpers.
- **Validation failures** (empty text, > 2000 chars, missing edit_id) return 400 with a JSON `error` body.

## Confirmation: no age-based filter

Verified across the spec. Staleness is controlled exclusively by:
- `rubric_version` matching (same-assignment tier rejects rows from
  before a rubric/answer-key change),
- the `active` flag (manual retirement).

No `created_at < X` predicate, no TTL, no cron sweep. None proposed,
none accepted.

## Out-of-scope items (carried in schema for future)

- `feedback_edit.scope` always `'individual'`. # FUTURE: department-level promotion would set this to `'department'`.
- `feedback_edit.promoted_by` always `NULL`. # FUTURE: HOD id who promoted the edit.
- `feedback_edit.promoted_at` always `NULL`. # FUTURE: timestamp of promotion.

These columns exist; they are never written with non-default values
in this implementation.
