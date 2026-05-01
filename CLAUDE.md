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

At least one AI provider API key must be set. Providers only appear in the UI if their key is configured.

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

1. **Lazy-fill at the closest write path.** If the value is derivable from existing data, compute it inline before the first INSERT/UPDATE that needs it, and persist it back to the parent row too. Never write `NULL` into a column the new feature filters on.
2. **Add a one-shot backfill** in the boot path (or as a `flask` CLI command). Idempotent — guard with `WHERE col IS NULL`, safe to re-run on every boot.
3. **Do NOT add `if row.col is None: skip` branches in readers.** If the reader needs the column populated, the writer + backfill are responsible. Branching in readers is how this gets unmaintainable.

**When adding a feature that reads new fields on existing rows:**

- Audit the write paths that produce those rows. If a write path can produce a row without the field, fix the write path — don't paper over it in the reader.
- If a value cannot be derived for legacy rows, the feature must tolerate that *explicitly* with a UI affordance ("not categorised yet — re-mark to enable"), never silently drop the row.

**When fixing a "old data doesn't work with new feature" bug:**

- Default fix shape: lazy-fill at write + one-shot backfill.
- Reader-side tolerance is a last resort, only when the value is genuinely irrecoverable for legacy rows.

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
- New optional fields (`correction_prompt`, `well_done`, `main_gap`, `theme_key`, etc.) added to one branch should not crash readers on older submissions.
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

## Canonical subjects

`subjects.py` is the single source of truth for the subject taxonomy. It defines:

- `SUBJECTS` — list of dicts with `key` (slugged DB value), `display` (human label), `aliases` (common typed strings).
- `SUBJECT_KEYS`, `SUBJECT_DISPLAY_NAMES`, `KEY_TO_DISPLAY` — derived lookups.
- `LEGACY_FAMILY_KEYS` — old taxonomy keys; only used by future migration code.
- `resolve_subject_key(text)` — fast alias→key resolver.
- `display_name(key)` — human label for a key.

Anywhere that needs subject-family logic — the assignment dropdown (via `canonical_subjects` in the template context), and any future AI classifier or aggregation page — must read from `subjects.py`. Don't hardcode subject lists or keys elsewhere.

**Future band axis (G1 / G2 / G3) — deferred.** When ready, add a `subject_band` column to `Assignment` (and any related tables), populate it on writes via the dropdown, include it in any future grouping query. Per the schema-evolution policy above, do NOT add the column until you're populating it on writes — a NULL-everywhere column is exactly the rot the policy is trying to prevent.
