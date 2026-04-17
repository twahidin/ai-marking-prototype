# Draft History Support — Design

**Date:** 2026-04-17
**Status:** Approved, ready for implementation plan

## Goal

Let students submit multiple drafts of the same assignment (e.g., Draft 1 → corrections → Draft 2) and receive AI feedback on each, while keeping the current single-submission flow as the default for teachers who don't need drafts.

## Motivation

User feedback (Priscilla, CTSS, 2026-04-17): "Can students submit their work to the same assignment like draft 2 / corrections and receive feedback?"

Current behavior (`app.py:3417-3421`) deletes the previous submission when a new one is uploaded, losing the old feedback. Teachers running iterative revision cycles cannot review a student's progression across drafts.

## Scope

**In scope:**
- Per-assignment opt-in for drafts with a teacher-configurable cap
- Student and teacher uploads both create drafts
- Teacher can pick which draft is "final" (counts for dashboard/analytics)
- Student portal shows draft history with per-draft feedback
- Works in normal and department modes, all scoring modes (status, marks), both assignment types (short_answer, rubrics)

**Out of scope (v1, YAGNI):**
- Side-by-side draft comparison UI
- Per-draft feedback diffing
- Student-initiated draft deletion
- Draft feature in demo mode (submissions already disabled there)

## Data Model

**`submissions` table — add two columns:**
- `draft_number INTEGER DEFAULT 1` — 1-indexed, ascending per student+assignment
- `is_final BOOLEAN DEFAULT TRUE` — exactly one final per student+assignment

**`assignments` table — add two columns:**
- `allow_drafts BOOLEAN DEFAULT FALSE` — teacher opt-in
- `max_drafts INTEGER DEFAULT 3` — cap, min 2, max 10

Remove the implicit uniqueness of `(student_id, assignment_id)` — multiple rows now allowed when `allow_drafts=True`.

**Migration (auto-run, additive):** follows existing pattern in `db.py:_migrate_add_columns`. Backfill all existing submissions with `draft_number=1, is_final=True`.

## Teacher: Assignment Setup

Add to the assignment create/edit modal in `class.html`:

- Checkbox: "Allow students to submit multiple drafts" → `allow_drafts`
- Number input (visible only when checkbox ON): "Maximum drafts per student" → `max_drafts`, default 3, min 2, max 10

## Upload Flow

**When `allow_drafts=False`** (default, legacy behavior):
- New submission deletes existing submission (unchanged)

**When `allow_drafts=True`:**
- Count existing drafts for `(student_id, assignment_id)`.
- If count ≥ `max_drafts`: reject with error ("Draft limit reached — delete an existing draft to free a slot").
- Otherwise: create new row with `draft_number = max(existing) + 1, is_final=True`. Flip all prior drafts for this student to `is_final=False`.

Applies uniformly to:
- Student self-submission (`/submit/<assignment_id>/upload`)
- Teacher single upload (`/teacher/assignment/<assignment_id>/submit/<student_id>`)
- Teacher bulk PDF upload (per-student page ranges)

Bulk upload: if a student hits the cap mid-bulk, skip that student and include in the job summary report.

## Student Portal

After classroom code verification + name selection:

**`allow_drafts=False`:** current UI unchanged (single "Review" or "Submit").

**`allow_drafts=True`:** new "Drafts" panel:
- List of drafts, newest first: `Draft 3 · submitted 14 Apr 2pm · Final ⭐` / `Draft 2 · …` / `Draft 1 · …`
- Each row: "View feedback" button (only if `show_results=True` on the assignment) → opens that draft's report
- "Submit new draft" button at top — disabled and replaced with "3/3 drafts used" when at cap
- ⭐ marks whichever draft the teacher set as `is_final`

Review endpoint (`/submit/<assignment_id>/review/<submission_id>`) already works per-submission; extend it to accept any draft owned by the same assignment.

## Teacher: Student Detail View

On the class/assignment page, when a teacher clicks into a student's feedback:

- Main view: the `is_final=True` draft's feedback (unchanged default)
- New "Drafts" strip: `Draft 1 · Draft 2 · Draft 3 ⭐` — click any to switch the view
- "Set as final" button on non-final drafts → flips `is_final`, recomputes dashboard counters
- "Delete draft" button (with confirm) on any draft
  - If deleting the final draft, auto-promote latest remaining draft to `is_final=True`
  - If deleting the last remaining draft, the student returns to "not submitted" status

## Analytics & Downloads

All existing queries that aggregate per-student results add a `WHERE is_final=True` filter:
- Class heatmap / dashboard
- Submitted count, score bands, status breakdown
- CSV export
- HOD dashboard (department mode)
- Reports ZIP download (one PDF per student, using final draft)

## Edge Cases

- **Turning `allow_drafts` OFF after drafts exist:** existing history kept read-only (no hard delete). New submissions for a student delete only that student's current `is_final=True` row and create one new row with `draft_number = max(existing) + 1, is_final=True`. Older `is_final=False` drafts are preserved but no longer shown in the student portal (teacher can still see them in the draft strip). Teacher confirmation required when toggling off.
- **Lowering `max_drafts` below current count:** allowed. No auto-deletion. New submissions blocked until teacher deletes drafts to go back under cap.
- **Concurrent extraction/marking:** existing overlap guard applies — a new draft cannot start while the previous one is still `extracting` or `marking`.
- **`student_amended` text:** per-draft (each draft row has its own amended text if edited). No change needed.
- **Scoring/assignment type:** no branching — works identically for status/marks and short_answer/rubrics.

## Files to Touch (preview — exact list for the plan)

- `db.py` — model columns + migration
- `app.py` — upload routes (student + teacher single + bulk), review route, assignment create/edit, analytics filters, new endpoints for "set as final" and "delete draft"
- `templates/class.html` — assignment form fields, student detail drafts strip, set-final / delete controls
- `templates/submit.html` — drafts panel in student portal
- `seed_data.py` — optional: seed a few multi-draft submissions for demo+dept mode

## Success Criteria

- [ ] Teacher can enable drafts + set cap on a new or existing assignment
- [ ] Students can submit up to `max_drafts` drafts and view feedback for each
- [ ] Teacher sees draft strip on student view, can switch view and change final
- [ ] Dashboard/analytics count only the final draft
- [ ] Turning drafts off preserves history, new submission overwrites final
- [ ] Cap is enforced across student, teacher single, and teacher bulk upload paths
- [ ] Existing assignments with `allow_drafts=False` behave identically to today
