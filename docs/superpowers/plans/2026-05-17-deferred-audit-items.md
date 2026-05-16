# Deferred Audit Items — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax for tracking. Update checkboxes as you complete steps.

**Origin:** Comprehensive code audit of `sandbox_upgraded` on 2026-05-17 (commit `3059a46`). Eight items were fixed in that pass; **three were deferred for separate sessions** because each is risky/architectural. This file is the spec for those three.

**Branch policy:** All commits land on `sandbox_upgraded`. Never push to `staging`. Per CLAUDE.md: parallel cherry-picked histories — never merge across.

**Tech Stack:** Python 3.10+, Flask, SQLAlchemy, pytest. SQLite (dev), PostgreSQL (prod via `DATABASE_URL`).

---

## Suggested order

1. **Item #11 first** (security architecture, ~half day, low blast radius if done now)
2. **Item #16 Stage A** (bank routes only — small win, low risk, ~half day)
3. **Item #16 Stage B** (department routes, ~1 day)
4. **Item #2** — only if Railway metrics show DB connection waits, or after onboarding multiple schools
5. **Item #16 Stage C** (teacher-marking routes — highest risk, do last)

Each item below is self-contained. Pick one, complete it fully (steps + tests + commit + push), then move to the next.

---

## Item #11 — Split FLASK_SECRET_KEY into session-signing + Fernet-encryption keys

**Goal:** Today, `FLASK_SECRET_KEY` is used both to sign Flask session cookies and to derive the Fernet key that encrypts stored AI API keys in `DepartmentConfig`. It is also auto-persisted into the DB in plaintext as a fallback. This means: (a) one DB read exposure compromises both sessions and API keys, and (b) rotating the secret destroys all encrypted API keys and invalidates all sessions simultaneously. Split the responsibilities and stop persisting the secret.

**Spec details**

- Today's surface:
  - `db.py:_get_fernet()` — reads `FLASK_SECRET_KEY` from env, falls back to a value persisted in `department_config.flask_secret_key`. SHA-256-derives the Fernet key.
  - `app.py:~173` — boot-time auto-persist of `FLASK_SECRET_KEY` into the DB.
  - Three encrypt/decrypt sites: `ai_marking._resolve_api_key`, `app._get_dept_keys`, `app._resolve_api_keys` (post the 2026-05-17 audit they all return `None` on decrypt fail, no longer leak ciphertext).

- After the change:
  - **`FLASK_SECRET_KEY`** — session signing only. Required for Flask sessions to work.
  - **`FLASK_API_KEY_ENCRYPTION_KEY`** — new env var for API key encryption. Required if any API keys are stored via the wizard.
  - No DB auto-persist of either secret.
  - On boot, if `FLASK_API_KEY_ENCRYPTION_KEY` is missing but encrypted API keys exist in `department_config`, log a clear error and refuse to start (don't silently leak).

**Files to change**

| File | Change |
|---|---|
| `db.py` | Rename `_get_fernet()` → keep name, change to read `FLASK_API_KEY_ENCRYPTION_KEY` (no DB fallback). Add new helper `_get_api_key_fernet()` if you prefer separating responsibilities, or keep one name and update all 5+ callers. |
| `app.py` (~line 173, in setup-complete bootstrapping) | Delete the block that auto-persists `FLASK_SECRET_KEY` into `department_config`. |
| `app.py` (~line 1061 `_get_dept_keys`, ~1119 `_resolve_api_keys`) | Update to use the new fernet getter. |
| `ai_marking.py` (~line 335 `_resolve_api_key`) | Same update. |
| `CLAUDE.md` (Environment Variables table) | Add `FLASK_API_KEY_ENCRYPTION_KEY`. Keep `FLASK_SECRET_KEY` but update its description ("session signing only"). |
| New: a one-shot migration in `db.py` to re-encrypt existing API keys with the new key | See migration step below. |

**Implementation steps**

- [ ] Step 1: Add `FLASK_API_KEY_ENCRYPTION_KEY` to env handling. If unset and no encrypted keys exist in DB, app starts normally. If unset but encrypted keys exist, log a clear startup warning telling the operator to set it.
- [ ] Step 2: Update `_get_fernet()` (or add `_get_api_key_fernet()`) to read the new env var. Remove the DB-fallback branch.
- [ ] Step 3: One-shot migration in `_migrate_add_columns` or a new sibling function: for each `department_config.api_key_*` row, try to decrypt with the NEW key first; if that fails, try the OLD key (`FLASK_SECRET_KEY`-derived); if OLD succeeds, re-encrypt with the new key and persist. Guard with a `MigrationFlag` so it only runs once.
- [ ] Step 4: Remove the auto-persist of `FLASK_SECRET_KEY` into `department_config` at `app.py:~173`.
- [ ] Step 5: Update CLAUDE.md env var table.
- [ ] Step 6: Add tests:
  - `tests/test_secret_split.py`: assert that with new env var set, encrypted keys decrypt; assert that without new env var but with encrypted keys, app boot logs a warning (don't crash — graceful refusal to use those rows).
  - Migration test: seed an old-style ciphertext, set both env vars, boot, verify the row is re-encrypted.
- [ ] Step 7: `python3 -m pytest --deselect tests/test_dept_seed_backfill.py::test_seed_inserts_correct_subject_mapping` — all green.
- [ ] Step 8: Commit + push.

**Deployment runbook (operator)**

1. Generate a new Fernet key locally: `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. Set `FLASK_API_KEY_ENCRYPTION_KEY=<that value>` in Railway.
3. Deploy. The one-shot migration re-encrypts existing rows on first boot.
4. After confirming all keys still work, you can safely rotate `FLASK_SECRET_KEY` independently (only invalidates sessions, no longer touches API keys).

**Risks**

- If migration fails partway, some rows are old-encrypted, some new-encrypted. The decrypt helper must try BOTH during transition — keep the OLD-key path active for one deploy cycle, then remove in a follow-up commit.
- If `FLASK_SECRET_KEY` is rotated before `FLASK_API_KEY_ENCRYPTION_KEY` is set, existing keys become permanently unrecoverable. Document this in CLAUDE.md.

---

## Item #16 — Extract route blueprints from `app.py`

**Goal:** `app.py` is 11,127 lines after the 2026-05-17 dedup. Three cohesive route groups can move into existing blueprints under `routes/` (the UP-39/40/41 pattern already in place for `student.py`, `insights.py`, `bulk.py`). Total expected reduction: ~2,750 lines.

**Stage A — Bank routes (~450 lines, lowest risk, do first)**

Move all `/bank/*` routes to `routes/bank.py`.

- [ ] Step A1: Identify the bank routes. Grep `@app.route('/bank` and `@app.route('/api/bank`. Expect ~12 routes including `bank_page`, `bank_preview`, `bank_answer_key_amendments`, `bank_bulk_upload`, etc.
- [ ] Step A2: Identify shared helpers. Grep each bank route body for `_is_authenticated`, `_current_teacher`, `_resolve_api_keys`, etc. These should stay in `app.py` and be deferred-imported from `routes/bank.py` (`from app import _is_authenticated` inside route bodies).
- [ ] Step A3: Create `routes/bank.py` with `bp = Blueprint('bank', __name__)`. Mirror the docstring convention of `routes/student.py`.
- [ ] Step A4: Move routes one at a time. After each route moves: `python3 -c "import app"` to verify; `python3 -m pytest -k bank` to verify behaviour preserved.
- [ ] Step A5: Register the blueprint at the bottom of `app.py` (next to the other `app.register_blueprint(...)` calls).
- [ ] Step A6: Verify nothing in templates or JS uses hardcoded route strings — `url_for('bank.bank_page')` style works after the blueprint move (route names get the `bank.` prefix).
- [ ] Step A7: Full pytest pass. Commit + push.

**Stage B — Department routes (~1,400 lines, medium risk)**

Move department-management and dept-insights routes to `routes/department.py`.

- [ ] Step B1: Identify routes. Grep `@app.route('/department` — expect ~30 routes covering setup, manage, insights, goals, CSV export, term-schedule. The `/department/insights/*` routes split between `routes/insights.py` (already extracted) and `app.py` (still resident) — keep them where they are or consolidate in a follow-up step; don't move twice.
- [ ] Step B2: Identify helpers — `_require_hod`, `_require_management`, `_require_insights_access`, `_get_dept_keys`. Decide: leave in `app.py` (most reuse) or move with the dept routes. Recommend: leave in `app.py`.
- [ ] Step B3: Create `routes/department.py` with `bp = Blueprint('department', __name__)`.
- [ ] Step B4: Move routes. Cross-check template `url_for('department_page')` → `url_for('department.department_page')` for every template that links to a dept route. Sweep with grep.
- [ ] Step B5: Full pytest pass. Manual smoke test: log in as HOD, walk the department dashboard, settings page, add/remove a teacher, view insights. (Tests don't cover all UI; brief manual pass needed.)
- [ ] Step B6: Commit + push.

**Stage C — Teacher-marking routes (~900 lines, highest risk)**

Move single-marking and per-submission routes to `routes/teacher_marking.py`. This includes:
- `teacher_submission_result_patch` (the 385-line monster)
- `teacher_submission_review`, `teacher_submission_remark`, `teacher_submission_force_remark`
- `teacher_delete_draft`
- The propagation triplet (`_find_propagation_candidates`, `_run_propagation_worker`, `_check_edit_owner`) — they're internal helpers but live near these routes.

- [ ] Step C1: Read CLAUDE.md "Backwards-compatibility policy" before starting — `Submission.result_json` shape tolerance is critical here.
- [ ] Step C2: Identify routes. Most live around `app.py:7000–9000`.
- [ ] Step C3: Identify helpers that cluster with these routes: `_apply_question_edits` (if you split out the 385-line function while you're here — recommended), `_check_edit_owner`, `_process_text_edit`, `_build_text_edit_meta`, the propagation helpers.
- [ ] Step C4: Create `routes/teacher_marking.py`. Move routes + their tightly-coupled helpers. Helpers shared with other parts of `app.py` (`_resolve_api_keys`, `_check_assignment_ownership`, `_submit_marking`) stay in `app.py` and are deferred-imported.
- [ ] Step C5: Verify the propagation `_submit_marking` calls still resolve correctly across module boundaries.
- [ ] Step C6: Full pytest pass — pay special attention to `test_propagation.py` (the import path is `from app import _run_propagation_worker`; you may need to re-export from `app.py` or update test imports).
- [ ] Step C7: Manual smoke test: mark a script, edit feedback, tick "Amend answer key", verify propagation kicks off to other submissions.
- [ ] Step C8: Commit + push.

**Cross-stage conventions**

- Each blueprint module starts with a docstring matching `routes/student.py:1-15` style (UP-39/40/41 reference, deferred-import rationale, dependency notes).
- Use `from flask import Blueprint, ...` at module top. Use deferred `from app import ...` inside route bodies for helpers that live in `app.py` — this avoids the circular-import problem at module load time.
- Don't move helpers cross-blueprint unless they're genuinely shared by more than one blueprint.
- The `register_blueprint` calls in `app.py` (~line 11,330) stay at the bottom and grow with each stage.

**Risks**

- Template `url_for('route_name')` calls break when a route gets a blueprint prefix. Sweep templates after each stage and grep for hardcoded URL strings.
- Tests that do `from app import some_route_handler` break when the handler moves. Either re-export from `app.py` (`from routes.bank import some_handler`) or update test imports.
- The duplicate-function bug fixed in commit `3059a46` happened because functions drifted to far ends of a giant file. Watch for the same anti-pattern in the new modules — don't let `teacher_marking.py` itself grow to 2k lines.

---

## Item #2 — Release SQLAlchemy session during AI calls

**Goal:** In `_run_submission_marking` (and its bulk and single-mark cousins), the worker thread reads all the assignment blobs, then calls the AI provider (20–60 s on Sonnet 4.6), then writes the result. During the AI call the thread is still attached to a DB session that holds a pool connection. With 4 marking workers × 30 s each, you're parking 4 pool connections out of 50 for ~30 s at a time. Not catastrophic with today's pool, but wasteful and the single biggest architectural concurrency liability if usage grows.

**Spec details**

- Current pattern (`app.py:_run_submission_marking`, ~line 5979):
  1. `sub.status = 'processing'`; commit
  2. Read `asn.question_paper`, `asn.answer_key`, `asn.rubrics`, `asn.reference`, `sub.get_script_pages()`
  3. `_build_calibration_block_for(asn, sub=sub)` — reads `FeedbackEdit` rows
  4. `mark_script(...)` — 20–60 s
  5. Read `consume_last_usage()`, apply `band_overrides`, defensive marks clamping
  6. `sub.set_result(result)`; `sub.status = ...`; commit

- Target pattern:
  1. Snapshot every DB-derived input into local vars (no SQLAlchemy attribute access on `sub`/`asn` after this).
  2. `sub.status = 'processing'`; commit; **`db.session.close()`** to release the pool connection.
  3. `mark_script(...)` — connection-free.
  4. Re-query `Submission.query.get(submission_id)` for the write; apply the result; commit.

**Files to change**

| File | Change |
|---|---|
| `app.py` (`_run_submission_marking`, ~line 5979) | Refactor as above. |
| `app.py` (`run_marking_job`, ~line 1178) | Same pattern — single-marking entry. |
| `app.py` (`run_bulk_marking_job`, search for it) | Same pattern — per-student loop. The inner loop already does N AI calls; each iteration should close/reopen. |
| `app.py` (`_run_propagation_worker`, ~line 10960 after dedup) | Same pattern — each iteration does an AI call. |

**Implementation steps**

- [ ] Step 1: Read the four functions end-to-end. Identify every attribute access on a SQLAlchemy-mapped object that happens AFTER the AI call. Each must either be snapshotted before the close, or re-queried after the AI returns.
- [ ] Step 2: Refactor `_run_submission_marking` first. Pattern:
  ```python
  # ---- pre-AI: snapshot inputs ----
  ai_inputs = {
      'provider': asn.provider,
      'subject': asn.subject,
      # ... every field mark_script reads from asn ...
      'session_keys': _resolve_api_keys(asn),
  }
  qp = [asn.question_paper] if asn.question_paper else []
  # ... ak, rub, ref, script, calibration_block ...
  sub_id = sub.id  # capture before close
  sub.status = 'processing'; db.session.commit()
  db.session.close()  # release pool connection

  # ---- AI call (no DB) ----
  try:
      result = mark_script(**ai_inputs, ...)
  except MarkingError as e:
      _persist_error(sub_id, e); return

  # ---- post-AI: re-open and write ----
  sub = Submission.query.get(sub_id)
  if not sub: return  # deleted while marking — graceful
  sub.set_result(result); sub.status = ...; db.session.commit()
  ```
- [ ] Step 3: Add a small `_persist_marking_error(submission_id, exc)` helper to avoid repeating the error-write block.
- [ ] Step 4: Same refactor for the other three callers. Bulk's inner loop closes+reopens per student.
- [ ] Step 5: Test: existing `test_propagation.py` should still pass. Add a test that explicitly verifies the connection is released — use SQLAlchemy events or check `pool.checkedout()` before/after.
- [ ] Step 6: Manual test: mark a real script via the UI; mark a bulk class; verify status transitions look identical to before.
- [ ] Step 7: Commit + push. Watch Railway logs / metrics after deploy for any session-detached errors.

**Risks**

- **Detached instance errors** — if any code after the close still references `sub.foo` or `asn.foo`, SQLAlchemy raises `DetachedInstanceError`. Grep carefully.
- **Lazy-loaded relationships** — `sub.assignment` is a relationship; accessing it after close fails. The snapshot must include any cross-table reads.
- **Calibration block** — `_build_calibration_block_for(asn, sub=sub)` internally does DB reads via `subject_standards.build_effective_answer_key`. Must run before the close.
- **Bulk worker** — the inner loop currently re-uses one open session. Switching to open-close-open per student adds overhead (~5–20 ms per iteration); negligible compared to a 30 s AI call but worth noting.

**When to actually do this**

- Defer until ONE of these is true:
  - Railway shows DB pool `QueuePool checkedout=` consistently near limit
  - Teachers report slow page loads during peak marking
  - You onboard a second school
- Until then, the pool config from 2026-05-17 (`pool_size=20, max_overflow=30`) is enough.

---

## Conventions for this work

- One item per session; don't try to do all three at once.
- Run `python3 -m pytest --deselect tests/test_dept_seed_backfill.py::test_seed_inserts_correct_subject_mapping` after every meaningful change. (The deselected test is a known pre-existing failure unrelated to these items.)
- Commit per item (or per stage for #16) with a clear message. Use the prefix style already in git log: `refactor:`, `fix:`, `chore:`, `feat:`.
- Push to `origin/sandbox_upgraded` after the tests pass and you're confident. NEVER push to `staging`.
- After completing an item, update the top of this file (`Suggested order` section) to mark it done.

## When you're stuck

- Re-read `CLAUDE.md` — the Schema-evolution policy, Backwards-compatibility policy, and Page-load performance section have the load-bearing constraints.
- Re-read the relevant blueprint's docstring (`routes/student.py`, `routes/insights.py`, `routes/bulk.py`) — they document the deferred-import pattern.
- The 2026-05-17 audit commit (`3059a46`) shows the style for low-risk surgical fixes; mirror it.
