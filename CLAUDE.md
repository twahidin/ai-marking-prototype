# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Marking Demo — a Flask web app that uses AI vision APIs to mark/grade student assignment scripts. Teachers upload a question paper, answer key, and student script (as images or PDFs), and the app sends them to an AI provider for evaluation, returning per-question feedback with status/marks. Results can be downloaded as a styled PDF report.

## Running the App

```bash
# Install dependencies (requires Python 3.10+, poppler for pdf2image)
pip install -r requirements.txt

# Run locally (debug mode on port 5000)
python app.py

# Production (Heroku-style via Procfile)
gunicorn -w 1 --threads 100 --timeout 300 --bind 0.0.0.0:$PORT app:app
```

## Environment Variables

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Enables Anthropic (Claude) provider |
| `OPENAI_API_KEY` | Enables OpenAI (GPT) provider |
| `QWEN_API_KEY` | Enables Qwen provider (via OpenAI-compatible API at dashscope-intl.aliyuncs.com) |
| `ACCESS_CODE` | Gate code for users (default: `DEMO2026`) |
| `FLASK_SECRET_KEY` | Session secret (default: dev key) |
| `PORT` | Server port (default: `5000`) |

At least one AI provider API key must be set. Providers only appear in the UI if their key is configured.

## Architecture

**4 files, no tests, single-page app:**

- **`app.py`** — Flask routes and background job system. Marking requests spawn a daemon thread; the frontend polls `/status/<job_id>` until complete. Jobs stored in an in-memory dict with 1hr TTL.
- **`ai_marking.py`** — AI provider abstraction and marking logic. Builds multimodal prompts (images + system prompt), calls Anthropic/OpenAI/Qwen APIs, and parses JSON responses. Contains robust JSON repair for truncated or malformed AI output.
- **`pdf_generator.py`** — ReportLab-based PDF report generation. Converts LaTeX math notation from AI responses to Unicode for PDF rendering via `clean_for_pdf()`.
- **`templates/index.html`** — Single-page frontend with inline CSS/JS. Handles access gate, file uploads (multi-image, up to 5 per field), provider/model selection, async marking with progress modal, paginated results display, and MathJax rendering.

## Key Design Decisions

- **Qwen uses the OpenAI SDK** with a custom `base_url`. It requires `openai` package to be installed (`OPENAI_AVAILABLE` flag).
- **OpenAI GPT-5+ uses `max_completion_tokens`** instead of `max_tokens`. Qwen still uses `max_tokens`. This distinction is in `make_ai_api_call()`.
- **Anthropic supports native PDF** via document content blocks. OpenAI/Qwen convert PDFs to page images using `pdf2image`/`poppler`.
- **Two assignment types**: `short_answer` (question-by-question) and `rubrics` (essay with rubric criteria).
- **Two scoring modes**: `status` (correct/partial/incorrect) and `marks` (numerical marks with totals).
- **AI response parsing** (`parse_ai_response`) has 3 fallback strategies: direct parse → truncation repair → individual object extraction. This handles markdown fences, smart quotes, and Qwen's thinking text.
- **No database** — everything is in-memory and ephemeral. Jobs expire after 1 hour.
