# Calibration simplification: drop topic tagging + subject standards, keep field-aware propagation

**Status:** Draft for review
**Author(s):** Joe Tay (product), Claude (design)
**Date:** 2026-05-16
**Branch:** sandbox_upgraded
**Supersedes (partially):** [2026-05-13 calibration edit intent](2026-05-13-calibration-edit-intent-design.md) — keeps the `Amend answer key` half, removes the `Update subject standards` half and everything that supports it.

## 1. Problem

The two-checkbox calibration intent shipped 2026-05-13 introduced three pipelines:

1. **Amend answer key** — assignment-scoped clarifications merged into the marking prompt's answer key.
2. **Update subject standards** — topic-tagged, HOD-reviewable, cross-assignment marking principles bank.
3. **Topic tagging** — AI extraction of topic keys per assignment + per edit, used to retrieve standards into the marking prompt.

Pipelines 2 and 3 are not ready to ship to teachers:

- Topic tagging accuracy is insufficient — the per-subject controlled vocab in `config/subject_topics/*.py` produces noisy retrieval.
- The HOD review queue UI exists (`templates/subject_standards.html`) but the workflow has not been validated with real teachers.
- Cross-assignment retrieval can leak edits made on one assignment into unrelated marking jobs.
- Two checkboxes confuse teachers about what each one actually does.

Pipeline 1 (amend answer key + same-assignment Haiku propagation) is well-defined, validated, and useful. It also has two correctness bugs that need fixing before release:

- Propagation always rewrites *both* `feedback` and `improvement` on target submissions, regardless of which field the teacher actually edited.
- Propagation never adjusts `marks_awarded` — even when the new calibration would justify a different score.

Goal: ship pipeline 1 cleanly to teachers, scrap pipelines 2 and 3 entirely. Rebuild them from scratch later if needed.

Out of scope:
- Any redesign of pipelines 2 and 3 — they are being deleted, not refactored.
- Changes to the marking pipeline itself (prompt structure, provider routing).
- New UI for HOD oversight.

## 2. High-level approach

Replace the two-checkbox intent with a single checkbox. Tighten propagation so it routes to the correct field and updates marks. Drop every table, column, function, route, template, and config file that supports the deleted pipelines.

| Element | Status |
|---|---|
| Checkbox: `Amend answer key/rubric for this assignment` | **Kept** — sole calibration intent |
| Checkbox: `Update subject standards` | **Removed** |
| `SubjectStandard` table + 7 API routes + page | **Dropped** |
| `SubjectTopicVocabulary` table + seed pipeline | **Dropped** |
| `Assignment.topic_keys` + `Assignment.topic_keys_status` columns | **Dropped** |
| `config/subject_topics/` (all 8 subject vocab files + `__init__.py`) | **Deleted** |
| AI calls: `extract_assignment_topic_keys`, `extract_assignment_topic_keys_from_pdf`, `extract_standard_topic_keys` | **Deleted** |
| `_kick_off_topic_tagging` (app.py) | **Deleted** |
| `subject_standards.py` functions: `promote_to_subject_standard`, `find_similar_standard`, `retrieve_subject_standards`, `find_related_standards`, `seed_subject_topic_vocabulary`, `_text_similarity` | **Deleted** |
| `subject_standards.build_effective_answer_key` | **Kept** — sole surviving function in that file |
| Propagation (`_find_propagation_candidates`, `_run_propagation_worker`, `/feedback/propagate*` routes) | **Kept**, with field-routing + marks-update fix |
| Insight extraction (`_run_insight_extraction_worker`, `extract_correction_insight`) | **Kept** — improves propagation quality |
| `refresh_criterion_feedback` (ai_marking.py) | **Rewritten** — field-aware, marks-aware |

After the simplification, calibration is **strictly per-assignment**. There is no cross-assignment, cross-teacher, or cross-class learning. Each assignment is calibrated only by edits made on its own submissions.

## 3. Data model changes

### 3.1 `FeedbackEdit` columns

**Kept (unchanged semantics):**
- `id`, `submission_id`, `criterion_id`, `field`, `original_text`, `edited_text`, `edited_by`, `assignment_id`, `rubric_version`, `mistake_type`, `active`, `created_at`

**Kept (still load-bearing):**
- `amend_answer_key` (bool, default false) — drives both the answer-key merge AND propagation trigger. When False, the row is a personal note with no downstream effect on other submissions.
- `propagation_status` (str: 'none' / 'pending' / 'complete' / 'partial' / 'skipped')
- `propagated_to` (JSON list of `{submission_id, status, error?}`)
- `propagated_at` (timestamp)
- `mistake_pattern`, `correction_principle`, `transferability` — populated by insight worker, fed into propagation prompt

**Constraint added at write time:** `field` is restricted to `'feedback'` or `'improvement'`. The JS UI already enforces this; the server now also validates.

**Dropped:**
- `scope` — no longer needed; `amend_answer_key` carries the only intent that exists.
- `promoted_to_subject_standard_id` — dangling FK, target table is gone.
- `promoted_by`, `promoted_at` — orphan columns from the original 2026-04-25 design.

### 3.2 `Assignment` columns

**Dropped:**
- `topic_keys` (JSON) — no readers after `retrieve_subject_standards` is gone.
- `topic_keys_status` (str) — gated topic tagging kickoff; no longer relevant.

### 3.3 Tables dropped

- `subject_standards`
- `subject_topic_vocabulary`

Use `db.engine.execute('DROP TABLE IF EXISTS ...')` guarded by `MigrationFlag` so it runs exactly once.

### 3.4 `MarkingPrinciplesCache`

Already a deprecated table (per CLAUDE.md). Leave it alone — preserved for audit, no longer written or read. Not in scope for this change.

## 4. Workflow

### 4.1 Teacher edits a single field, checkbox OFF

1. Teacher edits `feedback` text on submission S, question Q.
2. Blur fires autosave → `POST /result` with `{questions: [{question_num: Q, feedback: '...', amend_answer_key: false}]}`.
3. Server writes a `FeedbackEdit(submission_id=S, criterion_id=Q, field='feedback', amend_answer_key=False, active=True)`.
4. Server updates `Submission.result_json` for S only.
5. Response includes `edit_meta` so the UI shows the "✓ Saved" tag with a Retire link.
6. **No propagation. No principle extraction.** This is a personal correction.

### 4.2 Teacher edits a single field, checkbox ON

1–4 same as 4.1 but with `amend_answer_key=True`.
5. Server kicks off two background workers (both best-effort, never block the response):
   - **Insight worker** — extracts `mistake_pattern` / `correction_principle` / `transferability` from the edit, writes back into the row.
   - **Propagation worker** — scans candidates, re-marks each one.
6. Response includes:
   - `edit_meta` with `amend_answer_key=true`
   - `auto_propagation: {edit_id, candidate_count, criterion_name}` for the progress banner.
7. UI shows: `✓ Amended answer key for this assignment` + propagation banner.

### 4.3 Propagation candidate detection (unchanged from current)

For each other submission S' on the same assignment:
- `S'.status == 'done'`
- S' has a criterion matching `edit.criterion_id`
- That criterion lost marks (by marks comparison OR by status ≠ 'correct')
- That criterion's `feedback_source ∈ {None, 'original_ai', 'propagated'}` — never overwrite a `'teacher_edit'`.

### 4.4 Field-aware Haiku re-mark (NEW behavior)

The current `refresh_criterion_feedback` always returns `{feedback, improvement}` and the worker overwrites both. New contract:

```python
def refresh_criterion_feedback(
    provider, model, session_keys, subject,
    criterion_name, student_answer, correct_answer,
    marks_awarded, marks_total,
    calibration_edit,             # FeedbackEdit instance
    target_field,                 # 'feedback' or 'improvement' (= calibration_edit.field)
) -> dict:
    """
    Returns {target_field: str, 'marks_awarded': int|None}.
    Only the field the teacher edited is rewritten.
    marks_awarded may be unchanged, increased, or decreased based on new calibration.
    """
```

Worker change (`_run_propagation_worker`):

```python
refreshed = refresh_criterion_feedback(..., target_field=edit.field)

# Route to the correct field only
target_q[edit.field] = refreshed[edit.field] or target_q.get(edit.field) or ''

# Update marks if Haiku changed them
if refreshed.get('marks_awarded') is not None:
    target_q['marks_awarded'] = refreshed['marks_awarded']

target_q['feedback_source'] = 'propagated'
target_q['propagated_from_edit'] = edit.id
```

The Haiku prompt is rewritten to (i) name the target field, (ii) allow but not force a marks change.

### 4.5 Effective answer key (unchanged)

`subject_standards.build_effective_answer_key(assignment, original_text)` still walks active `FeedbackEdit` rows for the assignment + rubric_version with `amend_answer_key=True`, appends them as "Teacher clarifications" to the answer key passed to the marking AI. This is the surviving cross-submission calibration mechanism (for *new* submissions on the same assignment).

## 5. Migration plan

One-shot at boot, guarded by `MigrationFlag('drop_subject_standards_2026_05_16')`:

```python
def _migrate_drop_subject_standards():
    # Use raw SQL throughout: by this commit, the ORM no longer maps
    # `scope`, `promoted_to_subject_standard_id`, `topic_keys`, or
    # `topic_keys_status`, so SQLAlchemy queries against those columns
    # would fail at parse time. Raw SQL bypasses the ORM and operates
    # directly against the live schema.

    # 1. Deactivate FeedbackEdit rows that exist only because of the
    #    promote intent (scope='promoted' with amend_answer_key=False).
    #    Done via raw SQL because the `scope` column was already
    #    removed from the ORM in commit 3.
    db.engine.execute(
        "UPDATE feedback_edits "
        "SET active = 0 "
        "WHERE scope = 'promoted' AND amend_answer_key = 0 AND active = 1"
    )

    # 2. Drop the tables.
    db.engine.execute('DROP TABLE IF EXISTS subject_standards')
    db.engine.execute('DROP TABLE IF EXISTS subject_topic_vocabulary')

    # 3. Drop columns from FeedbackEdit + Assignment.
    #    SQLite path: alter via table rebuild (existing helpers handle this).
    #    Postgres path: ALTER TABLE ... DROP COLUMN.
    _drop_columns('feedback_edits', [
        'scope', 'promoted_to_subject_standard_id',
        'promoted_by', 'promoted_at',
    ])
    _drop_columns('assignments', ['topic_keys', 'topic_keys_status'])
```

`_drop_columns` does not exist yet in `db.py` (grep confirmed). Add it next to `_migrate_add_columns` as part of commit 4. It must handle:
- **SQLite** (default dev environment): no native `DROP COLUMN`. Use the table-rebuild dance — `CREATE TABLE new_x (...)`, `INSERT INTO new_x SELECT (kept cols) FROM x`, `DROP TABLE x`, `ALTER TABLE new_x RENAME TO x`. Recreate indexes.
- **Postgres** (production via `DATABASE_URL`): native `ALTER TABLE ... DROP COLUMN col_a, DROP COLUMN col_b`.

Detect backend via `db.engine.dialect.name`. Both paths must be idempotent — `WHERE column exists` check via `PRAGMA table_info` (SQLite) or `information_schema.columns` (Postgres) before attempting drop.

## 6. Implementation sequence (4 commits)

Each commit lands on `sandbox_upgraded`. Each is independently revertable; cherry-pickable to `staging` only by user direction.

**Commit 1 — UI: hide second checkbox + propagation field routing in JS**
- Remove the "Update subject standards" intent row from `static/js/feedback_render.js`.
- Always send only `amend_answer_key` in the PATCH payload; drop `update_subject_standards`.
- Update the "✓ Saved" indicator tag (no more "and promoted to subject standards" variant).
- Frontend no longer reads `ASSIGNMENT_HAS_CANONICAL_SUBJECT` or `ASSIGNMENT_TOPIC_KEYS_STATUS`.
- Visual smoke check: open `/feedback/...?token=...` for an existing submission, confirm one checkbox.

**Commit 2 — Backend: field-aware + marks-aware propagation**
- Rewrite `refresh_criterion_feedback` per §4.4. New signature, new prompt, new return shape.
- Update `_run_propagation_worker` to pass `target_field` and route the result.
- Server-side validation: reject POST `/result` payloads with `amend_answer_key=true` on fields other than `feedback` / `improvement`.
- Update `tests/test_propagation.py` (if it exists; otherwise add) to assert field routing and marks behavior.

**Commit 3 — Backend deletions: subject_standards, topic tagging, routes, templates**
- Delete files: `templates/subject_standards.html`, `config/subject_topics/__init__.py`, `config/subject_topics/biology.py`, `chemistry.py`, `english.py`, `geography.py`, `history.py`, `lower_secondary_science.py`, `mathematics.py`, `physics.py`.
- Remove from `subject_standards.py`: `seed_subject_topic_vocabulary`, `_text_similarity`, `find_similar_standard`, `promote_to_subject_standard`, `retrieve_subject_standards`, `find_related_standards`. Keep only `build_effective_answer_key`.
- Remove from `ai_marking.py`: `extract_assignment_topic_keys`, `extract_assignment_topic_keys_from_pdf`, `extract_standard_topic_keys`.
- Remove from `app.py`: `_kick_off_topic_tagging`, `_can_edit_subject_standards`, `_normalize_topic_keys` (only used by the deleted block-2 of `_build_calibration_block_for`), 7 `/api/subject_standards/*` routes, `/teacher/subject-standards` page, the topic-tagging kickoff at `app.py:7817`, the `topic_keys_status` filter at `app.py:9075`.
- **Shrink** `_build_calibration_block_for` (do not delete — its two callers at `app.py:5614` and `app.py:6223` stay): drop section 2 ("Subject standards retrieval"), keep section 1 ("Teacher clarifications" from `build_effective_answer_key`). The function returns the amendments text or `''`. Its public signature stays unchanged so the marking-prompt builder in `ai_marking.py` (`calibration_block` parameter) keeps working without edits.
- Remove from `db.py`: `SubjectStandard` model class, `SubjectTopicVocabulary` model class, `_seed_calibration_intent_assignments`, the legacy backfill in `_migrate_calibration_runtime` that sets `topic_keys_status='legacy'` (keep the rest of that migration).
- Delete tests: `tests/test_subject_standards.py`, `tests/test_subject_topic_vocab.py`. Refactor `tests/test_calibration_intent.py` to amend-only assertions.
- Update CLAUDE.md to remove the "topic tagging," "SubjectStandard," and "second checkbox" references; keep the "amend answer key" section.

**Commit 4 — Schema migration: DROP TABLE + DROP COLUMN**
- Add `_migrate_drop_subject_standards` per §5.
- Wire it into the boot path, guarded by `MigrationFlag('drop_subject_standards_2026_05_16')`.
- Add a test in `tests/test_migration_calibration.py` that asserts the migration is idempotent and that the dropped tables/columns are absent after boot.

Commit sequencing rationale: 1 → 2 → 3 → 4 means at every checkpoint the app builds and runs. Commit 2 (the propagation fix) is the highest-value commit and lands without touching the doomed code. Commit 4 (the destructive DDL) lands last, so a revert of 4 alone restores the old schema if something is wrong.

## 7. Capability changes for teachers

**Removed capabilities:**
- No HOD review queue. The Subject Standards page is gone.
- No cross-assignment calibration. Each assignment is calibrated only by edits on its own submissions.
- No cross-teacher learning. Teacher A's edits don't influence Teacher B's marking on a different class.
- No topic-based retrieval of marking principles.
- No "promoted" indicator on saved edits.
- No "Update subject standards" checkbox.

**Preserved or improved capabilities:**
- Amend the assignment's answer key with a single checkbox.
- Propagation auto-fires when the checkbox is on, re-marking similar wrong answers.
- **NEW:** Propagation routes the Haiku output to the same field the teacher edited (no more cross-contamination of feedback / improvement).
- **NEW:** Propagation may update `marks_awarded` based on the new calibration.
- Retire an edit (deactivates the row, removes its amendment from the answer key).
- Per-submission manual override of any propagated result.

## 8. Testing strategy

**Delete:**
- `tests/test_subject_standards.py` (727 lines)
- `tests/test_subject_topic_vocab.py`

**Refactor `tests/test_calibration_intent.py`:**
- Drop any test referencing `update_subject_standards`, `promoted_to_subject_standard_id`, `scope='promoted'`, `scope='both'`.
- Keep tests that exercise the `amend_answer_key=True` path; remove any setup code that sets `scope` (the column is gone post-commit-4).

**Add tests:**
- `test_propagation.py`:
  - Edit on `feedback` field → only `feedback` rewritten on target; `improvement` untouched.
  - Edit on `improvement` field → only `improvement` rewritten on target; `feedback` untouched.
  - Haiku returns lower marks → `marks_awarded` reduced on target.
  - Haiku returns higher marks → `marks_awarded` increased on target.
  - Haiku returns `marks_awarded=None` → marks unchanged on target.
- `test_migration_calibration.py`:
  - Boot path drops `subject_standards` table if present.
  - Boot path drops `subject_topic_vocabulary` table if present.
  - Boot path drops `assignment.topic_keys`, `assignment.topic_keys_status`, `feedback_edits.scope`, `feedback_edits.promoted_to_subject_standard_id`, `feedback_edits.promoted_by`, `feedback_edits.promoted_at`.
  - `MigrationFlag` is set after first run; second run is a no-op.
  - Legacy `FeedbackEdit(scope='promoted', amend_answer_key=False)` rows have `active=False` after migration.

Verification before merging each commit: run `pytest tests/` and confirm green.

## 9. Rollback

- Commit 1 (UI checkbox hide): revert the JS commit. Server still accepts the old payload.
- Commit 2 (propagation field-routing): revert. Old non-field-aware behavior returns.
- Commit 3 (backend deletions): revert. Code returns; the tables it would query still exist if commit 4 hasn't landed.
- Commit 4 (DDL): irreversible without a database restore. Run on `sandbox_upgraded` first, observe one boot cycle, only cherry-pick to `staging` after confirming the migration ran cleanly.

If commit 4 needs to be undone after deploy, restore the dropped tables/columns from a backup and revert commits 1–3 to restore reader code paths.

## 10. Open questions

None at design time. All five decisions captured in §1–§4 came from the 2026-05-16 brainstorming with Joe:
1. Marks during propagation: Haiku decides freely.
2. Field scope: feedback + improvement only.
3. Legacy data: drop tables + columns cleanly.
4. Trigger: auto-fire on save.
5. Insight worker: kept, improves propagation quality.

## 11. Related documents

- [2026-05-13 calibration edit intent design](2026-05-13-calibration-edit-intent-design.md) — original two-checkbox design. This document supersedes the "Update subject standards" half.
- [2026-04-27 feedback propagation design](2026-04-27-feedback-propagation-design.md) — original propagation design. Still in force; this document tightens the field-routing and marks contract.
- [2026-04-25 feedback edit log design](2026-04-25-feedback-edit-log-design.md) — original FeedbackEdit table.
