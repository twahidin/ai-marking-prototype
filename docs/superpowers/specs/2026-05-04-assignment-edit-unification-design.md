# Assignment Edit Unification — Design Spec

**Date:** 2026-05-04
**Branch:** `sandbox_testing`
**Status:** Approved for plan-writing

## Problem

The Assignment Bank Edit flow is broken in three ways:

1. **The Edit option is permission-gated.** Only roles in `('hod', 'subject_head', 'lead', 'owner')` see it (`bank.html:175`). Regular teachers in dept mode see only Delete.
2. **Click sometimes does nothing.** The kebab menu's Edit button uses an inline `onclick` with seven stringified arguments (`bank.html:183`). Any title/subject/tag containing `'`, `"`, or other special characters gets HTML-escaped into `&#39;`/`&quot;`, which the browser then unescapes inside the JS string literal — producing a syntax error and silently swallowing the click.
3. **The edit modal is too narrow.** Even when it works, only six text fields are editable (title, subject, level, tags, review/marking instructions). Teachers cannot replace the question paper, answer key, rubrics, or reference PDFs — even after spotting that the wrong file was uploaded.

The class-side assignment edit modal (`teacher_detail.html:601`) is more complete but doesn't quite mirror the create form, has free-text Subject (instead of canonical dropdown), allows Scoring Mode to be changed (which invalidates marked submissions), and offers no way to preview the currently-uploaded PDFs before deciding to replace them.

The two edit screens look and feel different, even though they edit conceptually the same thing — increasing cognitive load for teachers.

## Goals

- **Reliability:** the kebab Edit button always shows for all teachers and always opens the modal when clicked.
- **Universal edit + delete in the bank:** any teacher can edit or delete any bank item. Trust-based collaboration; no role gates.
- **Field parity:** both edit modals contain every field on the create form (provider, model, drafts, pinyin, etc.), with `assign_type` and `scoring_mode` rendered as locked read-only badges.
- **PDF management:** all four PDFs (question paper, answer key, rubrics, reference) are previewable inline (open in new tab) and replaceable from both modals.
- **One interface to learn:** the bank edit modal and the class assignment edit modal use the same Jinja partial. The only visible difference is that the bank version also shows Level + Tags rows.
- **Backwards compatible:** editing a bank item never reaches into already-assigned class assignments. Editing a class assignment never invalidates past submissions.

## Non-goals

- The `Assignment` model does not gain a `tags` column. Tags stay a bank-only concept.
- The create form (`class.html`) is not rewritten — only the two edit modals are unified.
- No audit trail / "last edited by" / "edit history" feature.
- No change to the Use-in-Class flow's UX.
- The kebab menu pattern stays. Friction-as-feature was an explicit user request.

## Architecture

### Shared form partial

New file: `templates/_assignment_form_fields.html`. Renders the full field block once, parameterised by:

- `mode`: `'bank'` or `'class'`
- `assignment` or `bank_item`: the row being edited (for value pre-population)
- `canonical_subjects`: list of subject options for the dropdown
- `providers`: list of available AI providers (from `get_available_providers()`)

Bank-only fields (`level`, `tags`) render under `{% if mode == 'bank' %}`. Class-only context (the `editAssignmentId` hidden input, ownership-tied API key resolution hints) renders under `{% if mode == 'class' %}`.

The partial is included from:

- `templates/bank.html` — inside the `#editModal` element.
- `templates/teacher_detail.html` — inside the existing `#editModal` element (replacing the current hand-rolled markup at `teacher_detail.html:601-734`).

### Schema additions to `AssignmentBank`

Add columns in `db.py`:

```python
provider       = db.Column(db.String(50), default='')
model          = db.Column(db.String(100), default='')
pinyin_mode    = db.Column(db.String(20), default='off')
show_results   = db.Column(db.Boolean, default=True)
allow_drafts   = db.Column(db.Boolean, default=False)
max_drafts     = db.Column(db.Integer, default=3)
```

These mirror the equivalent fields on `Assignment`. Defaults match what `Assignment` rows get today, so the migration can backfill existing bank rows safely.

**Boot-time migration** in `_migrate_add_columns()`:

```python
# Add columns if missing (SQLite + Postgres)
ALTER TABLE assignment_bank ADD COLUMN provider VARCHAR(50) DEFAULT '';
ALTER TABLE assignment_bank ADD COLUMN model VARCHAR(100) DEFAULT '';
ALTER TABLE assignment_bank ADD COLUMN pinyin_mode VARCHAR(20) DEFAULT 'off';
ALTER TABLE assignment_bank ADD COLUMN show_results BOOLEAN DEFAULT TRUE;
ALTER TABLE assignment_bank ADD COLUMN allow_drafts BOOLEAN DEFAULT FALSE;
ALTER TABLE assignment_bank ADD COLUMN max_drafts INTEGER DEFAULT 3;
```

**One-shot backfill** (idempotent, runs in the same boot path):

```sql
UPDATE assignment_bank SET pinyin_mode = 'off' WHERE pinyin_mode IS NULL OR pinyin_mode = '';
UPDATE assignment_bank SET show_results = TRUE WHERE show_results IS NULL;
UPDATE assignment_bank SET allow_drafts = FALSE WHERE allow_drafts IS NULL;
UPDATE assignment_bank SET max_drafts = 3 WHERE max_drafts IS NULL;
-- provider and model deliberately stay '' for legacy rows; bank_use() falls back to the first available provider, matching today's behavior.
```

### `bank_use()` change

Update `app.py:7906` so the cloned `Assignment` prefers the bank's stored values:

```python
asn = Assignment(
    ...,
    provider=item.provider or next(iter(api_keys)),
    model=item.model or '',
    pinyin_mode=item.pinyin_mode or 'off',
    show_results=item.show_results if item.show_results is not None else True,
    allow_drafts=item.allow_drafts if item.allow_drafts is not None else False,
    max_drafts=item.max_drafts or 3,
    ...
)
```

This is the lazy-fill-at-write path required by the schema-evolution policy in `CLAUDE.md`.

### Routes

| Route | Change | Notes |
|---|---|---|
| `POST /bank/edit/<bank_id>` | Drop role gate. Accept multipart form data with the new fields and the four PDFs. PDFs are replace-only — empty file input keeps current. `assign_type` and `scoring_mode` ignored if posted (locked). | `app.py:8126` |
| `POST /bank/delete/<bank_id>` | Drop role gate. Universal delete. | Existing route |
| `POST /teacher/assignment/<id>/edit` | Already supports most fields. Ensure `scoring_mode` is **not** writable (currently is — see `teacher_detail.html:654`). Add canonical-subject validation. | `app.py:5249` |
| `GET /teacher/assignment/<id>/file-inline/<file_type>` | **New.** Mirrors `/bank/<id>/file-inline/<file_type>` (`app.py:8208`). Streams the PDF blob inline. Gated by `_check_assignment_ownership(asn)`. | New |

`file_type` ∈ `{'question_paper', 'answer_key', 'rubrics', 'reference'}`. Returns 404 if the column is null.

### Bank kebab JS rewrite

`templates/bank.html`:

1. Drop the `can_edit = teacher.role in (...)` gate (line 175). Compute `can_edit = bool(teacher)` instead.
2. Remove the inline `onclick(...)` with seven escaped arguments. Add a hidden JSON block per card:

   ```html
   <script type="application/json" class="bank-card-data">{{ {
       'id': item.id, 'title': item.title or '',
       'subject': item.subject or '', 'level': item.level or '',
       'tags': item.tags or '',
       'review_instructions': item.review_instructions or '',
       'marking_instructions': item.marking_instructions or '',
       'assign_type': item.assign_type, 'scoring_mode': item.scoring_mode,
       'total_marks': item.total_marks or '',
       'provider': item.provider or '', 'model': item.model or '',
       'pinyin_mode': item.pinyin_mode or 'off',
       'show_results': item.show_results, 'allow_drafts': item.allow_drafts,
       'max_drafts': item.max_drafts or 3,
       'has_question_paper': item.question_paper is not none,
       'has_answer_key': item.answer_key is not none,
       'has_rubrics': item.rubrics is not none,
       'has_reference': item.reference is not none
   } | tojson }}</script>
   ```

   `tojson` is the Jinja filter that handles all escaping correctly (no apostrophe bugs).

3. The Edit menu item becomes:

   ```html
   <button type="button" role="menuitem" data-action="edit" data-bank-id="{{ item.id }}">Edit</button>
   ```

   A single delegated handler reads the `bank-card-data` JSON for the clicked card and populates the modal:

   ```javascript
   document.addEventListener('click', function(ev) {
       const btn = ev.target.closest('[data-action="edit"]');
       if (!btn) return;
       const card = btn.closest('.bank-card');
       const data = JSON.parse(card.querySelector('.bank-card-data').textContent);
       openEditModal(data);
       closeAllCardMenus();
   });
   ```

### PDF preview wiring inside the modal

Each PDF field in the partial renders as:

```
[Field label]
  ┌──────────────────────────────────────────────┐
  │ ✓ Currently uploaded · [Preview ↗]           │
  └──────────────────────────────────────────────┘
  [Replace: choose file]
```

`Preview` is an `<a target="_blank" rel="noopener">` to either `/bank/<id>/file-inline/<type>` or `/teacher/assignment/<id>/file-inline/<type>` depending on `mode`. If no file is uploaded yet, only the file-picker shows.

### Locked fields display

`assign_type` and `scoring_mode` render as static labelled badges:

```
Type:        [ Short Answer ]   🔒 cannot be changed
Scoring:     [ Marks ]          🔒 cannot be changed
```

They are not posted from the form; the server route ignores them on edit.

## Data flow

### Bank edit save

1. User clicks ⋮ on a card → menu opens.
2. User clicks Edit → handler reads JSON from the card's `<script>` block, populates `#editModal` inputs and PDF preview links, displays the modal.
3. User edits fields, optionally replaces PDFs, clicks Save.
4. JS submits a `multipart/form-data` POST to `/bank/edit/<bank_id>`.
5. Server validates (drop role gate), updates fields, replaces PDFs only when the file part is present and non-empty, commits, returns `{'success': True}`.
6. Page reloads.

### Class assignment edit save

Same flow as bank edit, against `/teacher/assignment/<id>/edit`. Server-side rejection of any attempt to change `assign_type` or `scoring_mode` (defensive — UI already locks them). Existing ownership check preserved.

### PDF preview click

Browser navigates to `/.../file-inline/<file_type>` in a new tab. Server fetches the row, returns the blob with `Content-Type: application/pdf` and `Content-Disposition: inline`.

## Error handling

- **Missing bank item / assignment:** 404 with JSON `{'success': False, 'error': 'Not found'}`.
- **No file in a PDF column when previewing:** 404. The Preview link only renders when the column is non-null, so this is defensive.
- **Auth fail on file-inline:** 403, identical shape to existing bank inline route.
- **Multipart parse failure:** existing Flask 400 handling; surfaced in `#editErrorMsg`.
- **Schema migration failure on boot:** log + continue. The columns have defaults, so the app keeps working with missing data; the next boot retries the migration.

## Testing

Manual verification (no test suite in this repo today):

1. **Visibility:** as a regular teacher in dept mode, open `/bank` and confirm the kebab menu shows Edit and Delete on every card.
2. **Click reliability:** create a bank item with title `Bob's "test" item, #algebra`, click Edit, confirm modal opens with all fields populated.
3. **Field parity:** open the bank edit modal and the class assignment edit modal side by side; verify the same fields appear in both, with bank also showing Level + Tags.
4. **PDF replace:** edit a bank item; replace the question paper; save; click Preview; confirm the new PDF opens.
5. **PDF preview without replace:** click Preview without picking a file; confirm the existing PDF opens in a new tab.
6. **Lock enforcement:** confirm `assign_type` and `scoring_mode` are visible but disabled in both modals.
7. **Backwards compat — bank-side:** create a class assignment via Use-in-Class; then edit the bank item (change title, replace PDF); confirm the class assignment still shows the original title and original PDF.
8. **Backwards compat — class-side:** mark a submission; then edit the assignment's PDF; confirm the existing marked submission's report still renders correctly.
9. **Migration:** drop the new columns from a Postgres dev DB, restart the app, confirm columns are recreated with sensible defaults and the page renders normally.

## Backwards compatibility (CLAUDE.md alignment)

- **Schema evolution:** new columns added with defaults, lazy-filled at the `bank_use()` write path, one-shot backfill in `_migrate_add_columns`. No reader does `if row.col is None: skip` — all readers see populated values.
- **Public function signatures:** no changes to `generate_report_pdf`, `generate_overview_pdf`, or `mark_script`. The new file-inline route is purely additive.
- **`result_json` shape:** untouched.
- **Frontend swap:** none.
- **In-process caches:** untouched. The PDF cache key (`pdf_generator._PDF_CACHE`) does not depend on the assignment's PDF blob — it keys on the marking result — so replacing PDFs does not need cache busting on existing reports.
- **`Assignment.title` required:** unchanged. Edit cannot null it (UI prevents empty).

## Files changed

| File | Change |
|---|---|
| `db.py` | Add 6 columns to `AssignmentBank`; extend `_migrate_add_columns` |
| `app.py` | Drop role gates on `/bank/edit` and `/bank/delete`. Extend `/bank/edit` to accept multipart + PDFs + new fields. Update `bank_use()` field copy. Add `/teacher/assignment/<id>/file-inline/<file_type>` route. Tighten `/teacher/assignment/<id>/edit` to reject `scoring_mode` changes |
| `templates/_assignment_form_fields.html` | New shared partial |
| `templates/bank.html` | Replace inline-onclick kebab with data-attribute pattern; embed per-card JSON; rebuild edit modal to include shared partial |
| `templates/teacher_detail.html` | Replace hand-rolled edit modal markup with shared partial; lock `scoring_mode`; add PDF preview links |

## Open questions

None. All resolved during brainstorming:

- Field set on bank: option (B) — extend `AssignmentBank` schema (confirmed by user).
- Delete permission: universal — anyone can delete (confirmed by user).
- Kebab vs always-visible buttons: keep kebab for friction (confirmed by user).
- Class-edit scope: full unification via shared partial, not a smaller polish pass (confirmed by user).
