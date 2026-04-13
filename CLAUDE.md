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
- **`pdf_generator.py`** — ReportLab PDF report + class overview generation. Converts LaTeX to Unicode via `clean_for_pdf()`.
- **`seed_data.py`** — Fake data generator for demo+dept mode.
- **`templates/`** — 13 Jinja2 templates extending `base.html`.

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
- **MathJax** enabled on all feedback surfaces for LaTeX rendering.
- **Security**: `secrets` module for codes, ownership checks on all assignment routes, HTML escaping for AI output, thread-safe rate limiting.
- **API key resolution**: Assignment keys → Department keys → Env vars.
- **Teacher roles**: `owner` (normal mode), `teacher` (dept mode), `hod` (dept mode). `TEACHER_CODE` env var is permanent master key.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `python3 -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` to keep the graph current
