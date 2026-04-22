# Edit Assignments After Creation — Design

**Date:** 2026-04-22
**Status:** Approved (pending implementation)

## Goal

Allow teachers to edit an assignment after it has been created and assigned, without disrupting the student submission link or auto-remarking existing submissions. Surface a "last edited" timestamp on the teacher landing page, and a persistent prompt to re-run bulk mark when changes affect grading.

## Non-Goals

- Auto-remarking when an assignment is edited
- Changing the student submission link or `classroom_code`
- Changing the assignment type (`short_answer` ↔ `rubrics`) after creation
- Edit history per field — only a single "last edited at" timestamp is tracked

## Data Model Changes

Two new columns on `Assignment` (in `db.py`):

| Column | Type | Default | Purpose |
|---|---|---|---|
| `last_edited_at` | DateTime, nullable | NULL | Set on every edit save. Null until first edit. |
| `needs_remark` | Boolean | False | True when a major field has changed; cleared on successful bulk-mark completion. |

The existing auto-migration logic in `db.py` handles new columns (per CLAUDE.md). `classroom_code` is never modified — student access link stays identical.

## Editable Fields

All fields are editable **except `assign_type`** (locked at creation; UI shows it disabled).

### Major fields (set `needs_remark = True` when changed)

| Field | Compared how |
|---|---|
| `question_paper` | New file uploaded (any new upload = change) |
| `answer_key` | New file uploaded |
| `rubrics` | New file uploaded |
| `reference` | New file uploaded |
| `marking_instructions` | String inequality |
| `review_instructions` | String inequality |
| `provider` | String inequality |
| `model` | String inequality |
| `total_marks` | String inequality |

### Minor fields (update `last_edited_at` only, never set `needs_remark`)

- `title`
- `subject`
- `scoring_mode`
- `show_results`
- `allow_drafts`
- `max_drafts`

## UI

### Edit entry points

Two buttons that both open the same edit modal:

1. **`/teacher/assignment/<id>`** landing page — "Edit Assignment" button in the Assignment Details card header (next to the existing "Share to Assignment Bank" button)
2. **`/class`** page — "Edit" button in each assignment card's `.assignment-actions` row, between "View" and "Delete"

### Edit modal

Mirrors the existing create-assignment form, pre-filled with the assignment's current values. Differences from create:

- **Type field disabled** — read-only label ("Short Answer" or "Rubrics / Essay") with helper text "Type cannot be changed after creation."
- **File pickers** show "Current: <filename or 'uploaded'>" plus a file input labeled "Upload new (leave empty to keep current)"
- **API key fields are not shown** — existing keys preserved; teacher manages keys via existing settings flow
- **Submit button label**: "Save Changes" (instead of "Create Assignment")

### Landing page notices

On `/teacher/assignment/<id>`, two new pieces appear above the Assignment Details card when applicable:

1. **"Last edited" line** — always shown if `last_edited_at` is set:
   > ✏️ Last edited: 22 Apr 2026, 14:30

   Small grey single-line text, no border, placed just below the H1 / classroom code header.

2. **Bulkmark prompt banner** — shown only if `needs_remark = True`:
   > ⚠️ This assignment was edited with changes that may affect grading. Re-run **Bulk Mark** to update existing submissions.

   Yellow/amber background banner, includes a "Jump to Bulk Mark" button that scrolls to the existing Bulk Mark card on the same page. Banner disappears after a successful bulk-mark run completes.

## Backend Routes

### New: `POST /teacher/assignment/<assignment_id>/edit`

Logic:

1. Auth + ownership check (reuse `_check_assignment_ownership`)
2. Validate inputs (provider, model still required; title/subject can be empty)
3. Read submitted form into a dict; for each field, compare to current `Assignment` value
4. For file fields: if a new file was uploaded (`files[0].filename` truthy), read bytes and mark as changed; otherwise keep existing
5. Apply updates to the `Assignment` row
6. Set `last_edited_at = datetime.now(timezone.utc)`
7. If any major field changed → set `needs_remark = True`
8. Commit
9. Return JSON: `{success: true, major_change: bool, needs_remark: bool}`

Validation failures return `{success: false, error: "..."}` and apply NO changes (atomic save).

### Modified: `POST /bulk/mark` (and bulk-mark background job)

After successful completion of the bulk-mark job, set `assignment.needs_remark = False` and commit. Failed or aborted bulk-mark runs leave the flag unchanged.

### Modified: `GET /teacher/assignment/<id>`

Pass `assignment.last_edited_at` and `assignment.needs_remark` to the template — already passed via the `assignment` object once columns exist; template reads them directly.

### Unchanged

- `classroom_code`, `/submit/<assignment_id>`, and all student-facing routes
- Existing submissions and their results — preserved as-is

## Edge Cases

- **Concurrent edit + bulk-mark in progress**: In-flight job finishes against the old files (already loaded into memory). On completion it clears `needs_remark`, but the fresh edit immediately re-sets it. Net result: banner correctly appears after the stale bulk-mark finishes, prompting another run.
- **File upload validation**: New uploaded files validated the same way as in `teacher_create` (size, type). On failure, return error and apply no changes.
- **Empty file inputs**: All four file inputs are optional during edit; empty = keep existing bytes.
- **Provider change with no API key for new provider**: Block the save with an error, same as the create-time check.
- **Editing a rubrics-type assignment**: Rubrics file is required to remain present (can be replaced but not removed); answer_key field hidden.
- **Editing a short-answer assignment**: Answer key required to remain present; rubrics field hidden.
- **Ownership**: HOD can edit any assignment in dept mode — existing `_check_assignment_ownership` already handles this.

## Testing Scope

Manual UI verification (no automated test framework in this project):

- Edit a non-major field (e.g., title): banner does NOT appear, "last edited" timestamp updates
- Edit a major field (e.g., marking instructions): banner appears, persists across reload
- Run bulk-mark on an assignment with `needs_remark = True`: banner clears
- Edit again with a major change after bulk-mark: banner reappears
- Verify `/submit/<assignment_id>` still works after edit (link unchanged)
- Verify existing submissions and results are untouched
- Open edit modal from both `/teacher/assignment/<id>` and `/class` page — same modal, same behavior
- Try editing each file type (replace question paper, answer key, rubrics, reference) — old file is replaced cleanly
- Try saving edit modal with empty file inputs — files preserved
- Verify type field is disabled and cannot be submitted
