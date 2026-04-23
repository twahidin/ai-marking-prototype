# Per-Student Re-Mark Design

**Date:** 2026-04-23
**Status:** Approved

## Problem

After a teacher edits an assignment (major change), the `needs_remark` flag is set on the assignment and a banner on the teacher detail page directs them to re-run Bulk Mark. But Bulk Mark requires re-uploading the combined student PDF even though every submission's script is already stored server-side (`Submission.script_pages_json`). Teachers who want to re-mark a single student — or who don't have the original bulk PDF — have no way to do so without a fresh upload.

## Goal

Let a teacher re-mark a single student's existing submission with one click, reusing the already-stored script pages. No new upload required.

## Scope

### In
- New "Re-mark" button on each eligible student row in `teacher_detail.html`.
- New endpoint that kicks off a background re-mark using the stored script pages.
- UI polling so the row reflects marking status and result without a full page reload.
- Auto-clear of the assignment's `needs_remark` flag when the last stale `done` submission has been re-marked.

### Out
- Bulk "Re-mark all" button (teachers can already use existing Bulk Mark for that, and per-student covers the single-student workflow they asked for).
- Changes to student submissions or student-facing portal.
- Changes to bulk-mark behavior.
- Any change to how `needs_remark` is initially set.

## Design

### UI

- In the Actions cell of each student row (`teacher_detail.html` ~line 364), add a **"Re-mark"** button between the existing "Download" link and "Re-upload" button.
- **Visibility:** shown only when `s.status in ('done', 'error')` and `s.submission_id` is present. Not shown for `not_submitted`, `pending`, `processing`, `extracting`, `preview`.
- **Styling:** reuse the `.upload-btn` class; add a subtle hint color variant `.remark-btn` so it visually reads as a distinct action.
- **Interaction:**
  1. Click → button disabled, label changes to "Marking…".
  2. JS POSTs to the remark endpoint.
  3. JS polls the existing `/teacher/assignment/<aid>/submission/<sid>/result` endpoint every 3 seconds.
  4. When `status` becomes `done` or `error`, the page reloads so the row's status badge, score, and Actions reflect the new state.

### Endpoint

`POST /teacher/assignment/<aid>/submission/<sid>/remark`

- Ownership check via `_check_assignment_ownership(asn)`.
- Validates `sub.assignment_id == assignment_id`.
- Validates `sub.get_script_pages()` returns a non-empty list (otherwise 400 "No stored script available to re-mark").
- Sets `sub.status = 'pending'`, clears `sub.result_json`, commits.
- Launches `_run_submission_marking(app, sub.id, assignment_id)` in a daemon thread.
- Returns `{success: true}` immediately.

No draft is created. The existing submission row is updated in place. (Per user decision: drafts are for new uploads; a re-mark doesn't change the script.)

### `needs_remark` auto-clear

After `_run_submission_marking` sets `sub.status = 'done'` and commits, and only when this was triggered from a re-mark (not from the existing upload flows), check: are there any `done` submissions for this assignment where `marked_at < asn.last_edited_at`?

- If **no stale `done` submissions remain**, clear `asn.needs_remark` and commit.
- Otherwise leave the flag set.

**Why:** the flag's intent is "there is out-of-date marking on this assignment." Once every done submission has a `marked_at` newer than the last edit, the flag is no longer informative.

**Implementation wrinkle:** `_run_submission_marking` is shared with the initial upload flow (where clearing `needs_remark` doesn't apply because the submission is new, not re-marked). Rather than thread a boolean parameter through every call site, the clear logic runs *after* the marking returns — and relies on the invariant "all `done` submissions have `marked_at >= asn.last_edited_at` ⇒ no stale marking." That invariant holds regardless of whether the submission was a re-mark or a fresh upload. So the clear logic can run unconditionally at the end of `_run_submission_marking`; it is a no-op when there's still stale marking elsewhere.

Error submissions (`status == 'error'`) are treated as "not stale in a way that blocks the flag" — they're a separate problem that the teacher needs to address via re-upload or another re-mark, not by being counted as "still needs re-marking."

## Data Model

No DB migration needed. All required columns exist (`Submission.status`, `Submission.marked_at`, `Submission.result_json`, `Submission.script_pages_json`, `Assignment.needs_remark`, `Assignment.last_edited_at`).

## Security

- Ownership check on the endpoint (same pattern as sibling teacher_* routes).
- No user-controlled input other than URL path parameters.
- No new file uploads.
- Rate limiting: inherits the existing app-level rate limiting. A malicious/rapid re-mark click is effectively throttled by the background thread's own duration.

## Edge Cases

- **Submission has no stored script pages** (rare legacy row or empty upload): endpoint returns 400 with a clear message; UI shows an error toast and re-enables the button.
- **Submission is already in `pending` or `processing`** when clicked: UI shouldn't allow the click (button only shown for `done`/`error`). Defense-in-depth on the server: if status is already `pending`/`processing`, endpoint returns 409 "Already marking."
- **Assignment deleted or archived between click and server receipt:** ownership check returns 404, UI shows error.
- **Model or API key missing/invalid:** handled by existing `_run_submission_marking` error path; submission ends in `status='error'` with an error message in `result_json`. UI polling sees `done`/`error`, reloads, shows error badge. No special handling required.

## Testing

Manual verification only (no frontend test runner in this repo):

1. Edit an assignment to trigger `needs_remark`.
2. On `teacher_detail.html`, confirm "Re-mark" button appears on `done`/`error` rows only.
3. Click Re-mark on one done submission — confirm status flips, button becomes "Marking…", page reloads with updated score/feedback.
4. Re-mark every done submission on an assignment with the flag set; after the last one completes, confirm the `needs_remark` banner disappears on next page load.
5. Try Re-mark on an `error` submission — confirm it runs and either succeeds or errors again with the new config.
6. Regression: fresh student upload and bulk mark still work unchanged.
