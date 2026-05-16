# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Feedback Systems — a Flask web app that uses AI vision APIs to mark/grade student assignment scripts. Teachers create classes, upload class lists, create assignments, then mark student scripts individually or in bulk. Students can self-submit via assignment links. Results include per-question feedback with status/marks, downloadable as styled PDF reports.

## Running the App

```bash
# Install dependencies (requires Python 3.10+, poppler for pdf2image)
pip install -r requirements.txt

# Run locally (port 5000)
python app.py

# Production (Heroku-style via Procfile)
gunicorn -w 1 --threads 100 --timeout 300 --bind 0.0.0.0:$PORT app:app
```

## Environment Variables

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Enables Anthropic (Claude) provider |
| `OPENAI_API_KEY` | Enables OpenAI (GPT) provider |
| `QWEN_API_KEY` | Enables Qwen provider (via OpenAI-compatible API) |
| `APP_TITLE` | Configurable app title (default: `AI Feedback Systems`) |
| `TEACHER_CODE` | Master access code for normal mode teacher (permanent, always works) |
| `ACCESS_CODE` | Legacy gate code (fallback if `TEACHER_CODE` not set) |
| `DEPT_MODE` | `TRUE` enables department mode with HOD/teacher roles |
| `DEMO_MODE` | `TRUE` enables demo mode (3 models, no auth, session-only) |
| `FLASK_SECRET_KEY` | Session secret + Fernet encryption key for stored API keys |
| `FLASK_DEBUG` | `true` enables debug mode (default: `false`) |
| `FLASK_ENV` | Set to `development` to disable `SESSION_COOKIE_SECURE` |
| `DATABASE_URL` | PostgreSQL URL (default: SQLite `marking.db`) |
| `PORT` | Server port (default: `5000`) |
| `STUDENT_GROUPING_UI_ENABLED` | `TRUE` un-hides the student-facing "By mistake type" toggle / grouped view. Default `FALSE`. The categorisation pipeline (`mistake_type` on `result_json`, calibration Tier 1, propagation) still runs regardless — this flag only controls the student UI surface. |
| `TEACHER_THEME_UI_ENABLED` | `TRUE` re-enables the teacher-facing inline theme/category dropdown on each marked criterion. Default `FALSE` since categorisation accuracy has proven sufficient. The data pipeline (`mistake_type` on criteria, FeedbackEdit inheritance, calibration retrieval) runs regardless — this flag only controls the teacher correction UI surface. Parallel to `STUDENT_GROUPING_UI_ENABLED`. |

At least one AI provider API key must be set. Providers only appear in the UI if their key is configured.

## Branch workflow

`staging` and the `sandbox_*` branches have **parallel cherry-picked histories** that have diverged intentionally. Treat them as separate trunks:

- **Never `git merge` between `staging` and any `sandbox_*` branch.** Cherry-pick individual commits across when needed.
- **Never push to `staging` without explicit confirmation from the user.** Sandbox branches are the working surface for upgrade work; `staging` is closer to production.
- When in doubt about which branch a change belongs on, ask before pushing.

## Three Operating Modes

### Normal Mode (`DEPT_MODE=FALSE`, `DEMO_MODE=FALSE`)
- One teacher per deployment, authenticated via `TEACHER_CODE`
- Teacher creates classes → uploads class lists → creates assignments → marks
- Single marking: class → assignment → student → upload script (warns before override)
- Bulk marking: class → assignment → upload PDF → variable page counts (0=skip)
- Student self-submission via assignment links with classroom codes

### Department Mode (`DEPT_MODE=TRUE`)
- HOD manages teachers, classes, and API keys
- Each teacher gets a unique access code (HOD can revoke/purge)
- `TEACHER_CODE` env var is HOD master key (always works)
- Teachers see only their assigned classes
- HOD dashboard with insights, analytics, CSV export
- Same marking flow as normal mode

### Demo Mode (`DEMO_MODE=TRUE`)
- No authentication required
- "Try AI Marking": standalone single-script marking with 3 budget models (Haiku, GPT Mini, Qwen 3.5)
- "Explore Features": session-only class/assignment creation (no DB persistence)
- No bulk marking, no student submissions

### Demo + Department Mode (`DEMO_MODE=TRUE` + `DEPT_MODE=TRUE`)
- Pre-seeded fake data (3 classes, 3 teachers, 6 assignments, ~83 submissions)
- Auto-login as demo HOD
- Showcases HOD dashboard, insights, analytics with realistic data
- Student submissions disabled

## Architecture

**6 files, multi-page app:**

- **`app.py`** — Flask routes, auth, background job system. Routes organized by: auth/hub, single marking, department management, teacher dashboard, bulk marking, student submission, API endpoints.
- **`db.py`** — SQLAlchemy models (Teacher, Class, TeacherClass, Assignment, Student, Submission, DepartmentConfig). Auto-migrations for new columns.
- **`ai_marking.py`** — AI provider abstraction. Builds multimodal prompts, calls Anthropic/OpenAI/Qwen APIs, parses JSON responses with 3 fallback strategies.
- **`pdf_generator.py`** — LuaLaTeX-based PDF report + class overview generation. Compiles a generated `.tex` string in a temp dir via `lualatex`, returns the PDF bytes. Maths render natively via amsmath; CJK and Tamil via `luaotfload` fallback to Noto fonts. Outputs are memoised in an in-process LRU cache keyed on `sha256(kind, result, subject, app_title, assignment_name)`.
- **`seed_data.py`** — Fake data generator for demo+dept mode.
- **`templates/`** — 16 Jinja2 templates extending `base.html`. Key pages: `hub.html` (teacher home), `class.html` (class/assignment/marking view), `dashboard.html` (HOD dashboard), `submit.html` (student submission portal), `setup_wizard.html` (first-run setup), `settings.html` (teacher settings). Department pages: `department*.html`. Auth: `_gate.html`, `index.html`.
- **`docs/plans/`** — Design documents for features (student submission portal, algorithm flow, setup wizard).

### Calibration system (subject standards)

Calibration edits split by intent at save time:
- **Amend answer key** — `FeedbackEdit.amend_answer_key=true`, scoped to the assignment. Merged into the effective answer key on every marking job for that assignment via `subject_standards.build_effective_answer_key`. Local-only — does not generalise.
- **Update subject standards** — promoted to `SubjectStandard` via `subject_standards.promote_to_subject_standard`. AI-tagged with topic_keys from `config/subject_topics/<subject>.py`. Requires HOD / subject-lead approval before going active. Edit rights granted to `Teacher.role in ('hod','subject_head','lead','owner')`.

Marking-time retrieval is `subject_standards.retrieve_subject_standards` — topic overlap with the assignment's per-question topic_keys, per-topic quota of 3, hard cap of 30. Bank size is effectively unbounded; prompt size stays constant. Assembly into the marking prompt happens at `app._build_calibration_block_for(asn)`; the prompt builders in `ai_marking.py` continue to consume a single `calibration_block` string.

Topic tagging:
- New assignments start with `topic_keys_status='pending'`. First open in `teacher_assignment_detail` triggers `_kick_off_topic_tagging` (synchronous Haiku call via `extract_assignment_topic_keys`). Marking is never blocked — failures swallowed, status stays `pending`, retrieval skipped.
- `SubjectTopicVocabulary` is seeded from `config/subject_topics/*.py` on boot (`seed_subject_topic_vocabulary`).

Migration (one-shot at boot, see `db._migrate_calibration_runtime` guarded by `MigrationFlag`):
- Assignments older than 5 days at deploy → `topic_keys_status='legacy'`; FeedbackEdits on them set `active=False`.
- Assignments within 5 days → `topic_keys_status='pending'`; lazy AI tagging on first open; FeedbackEdits converted to `amend_answer_key=true` amendments (`scope='amendment'`).
- All `MarkingPrinciplesCache` rows marked `is_stale=True`.
- The standards bank always starts empty.

`MarkingPrinciplesCache` is deprecated — table preserved for audit but no longer regenerated or applied. `ai_marking.build_calibration_block` is a no-op stub.

Spec: `docs/superpowers/specs/2026-05-13-calibration-edit-intent-design.md`. Plan: `docs/superpowers/plans/2026-05-13-calibration-edit-intent-plan.md`.

## Key Data Model

- **Class list at class level**: Students belong to classes (not assignments). Upload once, reuse across assignments.
- **Assignment links to class**: `Assignment.class_id` FK. Files (question paper, answer key, rubrics) stored as blobs on the assignment.
- **Submissions link student + assignment**: `Submission.student_id` + `Submission.assignment_id`. Results stored as JSON.
- **Override rules**: Single marking warns before override. Bulk marking overrides except page_count=0 (skip). Student re-submission overwrites.

## Key Design Decisions

- **Qwen uses the OpenAI SDK** with a custom `base_url`.
- **OpenAI GPT-5+ uses `max_completion_tokens`** instead of `max_tokens`.
- **Anthropic supports native PDF** via document content blocks. OpenAI/Qwen convert PDFs to page images.
- **Two assignment types**: `short_answer` and `rubrics` (essay with rubric criteria).
- **Two scoring modes**: `status` (correct/partial/incorrect) and `marks` (numerical).
- **KaTeX 0.16.x** renders LaTeX in the browser. `base.html` ships a `window.MathJax.typesetPromise` shim over KaTeX's `renderMathInElement` so legacy call sites that invoke `MathJax.typesetPromise([elem])` keep working unchanged. The mhchem extension is loaded for chemistry markup; both browser KaTeX and PDF LuaLaTeX use the same `\ce{}` syntax.
- **Security**: `secrets` module for codes, ownership checks on all assignment routes, HTML escaping for AI output, thread-safe rate limiting.
- **API key resolution**: Assignment keys → Department keys → Env vars.
- **Teacher roles**: `owner` (normal mode), `teacher` (dept mode), `hod` (dept mode). `TEACHER_CODE` env var is permanent master key.

## System Dependencies

System dependencies are pinned in `Dockerfile` (Railway uses Dockerfile mode via `railway.json`'s `"builder": "DOCKERFILE"`). The two-layer split keeps the heavy TeX Live install separately cacheable from the lighter font + utility layer.

- **poppler-utils** — required for `pdf2image` (PDF-to-image conversion for OpenAI/Qwen marking).
- **fontconfig + fonts-noto + fonts-noto-cjk + fonts-noto-extra** — Noto family for Latin / CJK / Tamil / Devanagari rendering. `fontconfig` must be in the apt list because `--no-install-recommends` skips the font packages' Recommends.
- **texlive-luatex + texlive-latex-recommended + texlive-latex-extra + texlive-fonts-recommended + texlive-fonts-extra + texlive-lang-cjk + texlive-lang-other + texlive-pictures + texlive-plain-generic + texlive-science** — the LuaLaTeX engine plus every package the PDF preamble loads (`tcolorbox`, `tabularx`, `enumitem`, `titlesec`, `ulem`, `mhchem`, `fontspec`).
- **libheif1** — `pillow-heif` HEIC decoder for iOS-shot student uploads.

For local development on macOS: `brew install poppler` for `pdf2image`, plus a TeX Live install (BasicTeX + `tlmgr install titlesec tcolorbox enumitem ulem mhchem chemgreek tex-gyre tools` for the missing packages BasicTeX doesn't ship).

**Adding a new system dependency**: every Dockerfile change invalidates the apt layer cache, so the next build is 5–8 min. Bundle related additions into one commit; don't trickle-add packages across multiple commits if you can avoid it.

## Schema evolution policy

This app is in active use. Any code change that adds or relies on a new column / field / data shape MUST consider that prior rows do not have it. Old assignments and old submissions are real data, not edge cases.

**When adding a column that ANY query filters or groups on:**

1. **Lazy-fill at the closest write path.** If the value is derivable (e.g. `Submission.categorisation_status` from a successful mistake_type write, or `Assignment.title` from `subject`), compute it inline before the first INSERT/UPDATE that needs it, and persist it back to the parent row too. Never write `NULL` into a column the new feature filters on.
2. **Add a one-shot backfill** in the boot path (or as a `flask` CLI command). Idempotent — guard with `WHERE col IS NULL`, safe to re-run on every boot.
3. **Do NOT add `if row.col is None: skip` branches in readers.** If the reader needs the column populated, the writer + backfill are responsible. Branching in readers is how this gets unmaintainable.

**When adding a feature that reads new fields on existing rows:**

- Audit the write paths that produce those rows. If a write path can produce a row without the field, fix the write path — don't paper over it in the reader.
- If a value cannot be derived for legacy rows, the feature must tolerate that *explicitly* with a UI affordance ("not categorised yet — re-mark to enable"), never silently drop the row.

**When fixing a "old data doesn't work with new feature" bug:**

- Default fix shape: lazy-fill at write + one-shot backfill.
- Reader-side tolerance is a last resort, only when the value is genuinely irrecoverable for legacy rows.

**Currently load-bearing fields** (treat as required, not optional, on writes):

- `Assignment.subject` — drives the canonical subject taxonomy (see `subjects.py`), marking-patterns aggregation grouping, and per-subject calibration lookup. `subject_family` and `subject_bucket` columns were **dropped** (see `db.py:_migrate_add_columns`); do not reintroduce them.
- `FeedbackEdit.mistake_type` — drives Tier-1 calibration retrieval and student-facing "Group by Mistake Type" (UI gated by `STUDENT_GROUPING_UI_ENABLED`, but pipeline always populates). Legacy name was `theme_key`; renamed 2026-05-16.
- `Submission.categorisation_status` — gates UI rendering of the category line.
- `Assignment.title` — required by the PDF generator's header row (see "load-bearing" note below).

## Backwards-compatibility policy

Beyond schema evolution, every change should respect the following stable surfaces. These are the things that have bitten us before — keep them in mind on every PR.

### Public function signatures (`pdf_generator.py`, `ai_marking.py`)

When adding a new parameter to a function called from multiple places (especially `generate_report_pdf`, `generate_overview_pdf`, `mark_script`, `get_available_providers`):

1. **Add it as a keyword argument with a sensible default.** No positional additions. Existing callers keep working without edits.
2. **Update every callsite to pass the new value.** A `grep -n "function_name(" app.py` audit is mandatory before commit. If a callsite genuinely doesn't have the data, document why and let the default fire.
3. **Plumb the new value into the result-relevant cache key.** `pdf_generator.py` memoises on `(kind, result, subject, app_title, assignment_name)` — adding a parameter without adding it to the key produces stale-cache bugs.

### `Submission.result_json` shape

`result_json` is the AI-generated marking output. Its shape evolves over time, but **old submissions stay in the DB forever**. Readers must tolerate older shapes.

- Read with `q.get('field', default)` — never `q['field']`. The AI sometimes omits fields entirely; older submissions definitely will.
- New optional fields (`correction_prompt`, `well_done`, `main_gap`, `mistake_type`, etc.) added to one branch should not crash readers on older submissions.
- The PDF generator has an example to copy: `if not body_rows: body = …(no detail)…` for a question that has no fields populated.
- If a reader needs a field to be present, **fix the writer** (the AI prompt or the post-processing that fills in defaults). Don't litter readers with `if x.get('field') is None: skip`.

### Frontend library swaps

When replacing a JS library that has many call sites (e.g. MathJax → KaTeX), provide a **shim** so the replacement looks like the original API to existing code. The current `base.html` defines `window.MathJax.typesetPromise` over KaTeX's `renderMathInElement` so we didn't have to touch six template files.

### In-process caches

Anything stored in module-level globals (e.g. `pdf_generator._PDF_CACHE`, `ai_marking._PROVIDER_CACHE`) resets on container restart. **This is by design** — it aligns cache invalidation with code deploys, so a logic change automatically clears stale caches.

- Don't depend on cache survival across restarts.
- Don't add filesystem-backed caches for ephemeral state — Railway containers are immutable.
- When extending a cache key, add to the SHA-256 input chain in `_cache_key()` rather than introducing a parallel cache.

### Build / deploy compatibility

- **Dockerfile changes** invalidate the heavy apt + texlive layers and trigger a 5–8 minute rebuild. Bundle related system-package additions into one commit.
- **`requirements.txt` changes** invalidate the pip layer (~30 seconds). Cheap.
- **Code-only changes** rebuild only the `COPY . .` layer (~5 seconds). Most deploys should land here.
- **`luaotfload-tool --update --force` runs at image-build time** to populate the font db. Don't remove this — by-name font lookups on Railway intermittently fail without it.

### `Assignment.title` is required-load-bearing

The PDF generator's "Assignment" header row reads `Assignment.title`. The boot-time backfill (`_migrate_add_columns`) fills empty titles with `subject` (or `Assignment <classroom_code>` if both are empty). When adding new code that creates assignments, populate `title` at write time — don't rely on the backfill to catch you on the next boot.

## Page-load performance

Every listing page we've ever shipped started slow and got optimised after a user complained. The root cause is always the same three patterns, and we now have helpers in `perf.py` so new pages start optimised. **Apply this checklist before declaring any listing route done — don't wait for the page to feel slow.**

### Why this matters

- `Assignment` has four `LargeBinary` columns (`question_paper`, `answer_key`, `rubrics`, `reference`). A typical question paper is 500 KB–5 MB. `Assignment.query.all()` loads every byte of every blob for every row.
- `Submission` has `script_bytes` (the original upload) plus three large JSON-text columns (`script_pages_json`, `extracted_text_json`, `student_text_json`). Same problem at scale.
- Jinja's `{{ obj.relationship | length }}` triggers a fresh SQL query per row. With deferred-elsewhere blobs, that lazy-load drags them back into memory.
- Post-paint XHR calls (`fetch('/api/classes')` in `DOMContentLoaded`) feel like an extra "loading…" state. If the data is small and the route already runs a session check, pass it inline.

### The checklist (every new route that renders ≥1 DB row of metadata)

1. **List queries go through `perf.py`.**
   - `light_assignment_query()` → use this instead of `Assignment.query` for any listing/detail page that doesn't display the raw file bytes.
   - `light_submission_query()` → use this instead of `Submission.query` for any page that reads `status` / `result_json` / `submitted_at` without rendering `script_bytes` or the per-page image dump. `result_json` stays eager — it's what most pages need.
   - If a deferred column is actually needed downstream, SQLAlchemy lazy-loads it on first attribute access. You don't have to pre-empt that — but if you know you need it, drop the helper and write an explicit `Assignment.query.options(...)` with a narrower defer set.
2. **Counts come from `perf.py`, not from Jinja `| length`.**
   - `submission_counts(asn_ids)` → `{assignment_id: int}` in one `GROUP BY`.
   - `student_counts_for_assignments(assignments)` → `{assignment_id: int}` handling both dept-mode (`Student.class_id`) and legacy (`Student.assignment_id`) shapes.
   - `student_counts_for_classes(class_ids)` → `{class_id: int}` for class-dropdown payloads.
   - In the template: `{{ submission_counts.get(asn.id, 0) }}`, NOT `{{ asn.submissions | length }}`. The `| length` form re-introduces N+1 even when the query was deferred.
3. **Inline first-paint payloads.** If the page makes an XHR to `/api/foo` immediately on `DOMContentLoaded` and `/api/foo` only needs the session that's already established, render the result as a JS constant in the template (`const PRELOADED_FOO = {{ payload | tojson }};`) and treat the XHR as a fallback. Saves one round trip.
4. **Bulk-resolve names by ID once.** When the page renders `Created by {{ name }}` per row, do not lazy-load the related model. Collect IDs, do one `Teacher.query.filter(Teacher.id.in_(ids))`, then dict-lookup in the loop.

### Anti-patterns to grep for before committing

- `Assignment.query.all()` / `Assignment.query.order_by(...).all()` without `.options(defer(...))` → use `light_assignment_query()`.
- `Submission.query.filter_by(...).all()` (returning many rows) without defers → use `light_submission_query()`.
- `obj.relationship | length` inside a Jinja `{% for %}` loop → batch with one of the count helpers.
- `Model.query.filter_by(...).first()` *inside* a Python `for` loop → batch outside the loop into a dict.
- `fetch('/api/...').then(render)` inside `DOMContentLoaded` for data the server already has → inline as render context.

### When the helpers don't fit

`teacher_assignment_detail` defers only the four `LargeBinary` blobs and intentionally keeps `api_keys_json` + the review/marking instructions eager (the edit form + `_resolve_api_keys` read them). That's a valid narrower variant — write the explicit `defer(...)` inline with a one-line comment, don't bend the helper to accept opt-outs.

If you find a fourth pattern recurring across routes, add it to `perf.py` and update this section.

## Canonical subjects

`subjects.py` is the single source of truth for the subject taxonomy. It defines:

- `SUBJECTS` — list of dicts with `key` (slugged DB value), `display` (human label), `aliases` (common typed strings).
- `SUBJECT_KEYS`, `SUBJECT_DISPLAY_NAMES`, `KEY_TO_DISPLAY` — derived lookups.
- `LEGACY_FAMILY_KEYS` — old taxonomy keys; only used by the boot-time backfill to detect rows that need re-classification.
- `resolve_subject_key(text)` — fast alias→key resolver to skip the AI call when a typed subject matches a known display name or alias. Writers call this before persisting `Assignment.subject`.
- `display_name(key)` — human label for a key, used in marking-patterns headers.

Anywhere that needs subject-family logic — the assignment dropdown (via `canonical_subjects` in the template context), the AI classifier, the marking-patterns page header, the backfill — must read from `subjects.py`. Don't hardcode subject lists or keys elsewhere.

**Future band axis (G1 / G2 / G3) — deferred.** When ready, add a `subject_band` column to `Assignment` and `FeedbackEdit`, populate it on writes via the dropdown, include it in the calibration-lookup grouping. Per the schema-evolution policy above, do NOT add the column until you're populating it on writes — a NULL-everywhere column is exactly the rot the policy is trying to prevent.
