# AI Feedback Systems

A Flask web app for AI-powered student script marking. Teachers create classes, upload class lists, and mark assignments using Claude, GPT, or Qwen. Supports single and bulk marking, student self-submission, department mode with HOD oversight, and a demo showcase mode.

## Quick Deploy

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/new/template?referralCode=)

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

Requires Python 3.10+ and [poppler](https://poppler.freedesktop.org/) for PDF-to-image conversion.

### 2. Set environment variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | At least one provider key | Anthropic API key for Claude models ([console.anthropic.com](https://console.anthropic.com)) |
| `OPENAI_API_KEY` | At least one provider key | OpenAI API key for GPT models ([platform.openai.com](https://platform.openai.com)) |
| `QWEN_API_KEY` | At least one provider key | Qwen API key for Qwen models |
| `FLASK_SECRET_KEY` | Yes (production) | Session secret + encryption key. Generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `APP_TITLE` | No | App title shown in nav, pages, PDFs (default: `AI Feedback Systems`) |
| `TEACHER_CODE` | No | Master access code for normal mode. Permanent key that always works. |
| `DEPT_MODE` | No | Set to `TRUE` for department mode with HOD/teacher roles |
| `DEMO_MODE` | No | Set to `TRUE` for demo mode (3 budget models, no auth) |
| `DATABASE_URL` | No | PostgreSQL connection URL (default: SQLite `marking.db`) |
| `PORT` | No | Server port (default: `5000`) |

### 3. Run

```bash
# Development
python app.py

# Production
gunicorn -w 1 --threads 100 --timeout 300 --bind 0.0.0.0:$PORT app:app
```

## Operating Modes

### Normal Mode
One teacher per deployment. Set `TEACHER_CODE` as your access code.

1. Log in with your code
2. Create a class and upload a class list (CSV)
3. Create assignments (upload question paper, answer key)
4. Mark students individually or in bulk
5. Share assignment links for student self-submission

### Department Mode (`DEPT_MODE=TRUE`)
HOD manages multiple teachers and classes.

- HOD creates classes, assigns teachers, manages access codes
- Each teacher sees only their assigned classes
- HOD dashboard with insights, analytics, CSV export
- HOD can revoke or purge teacher accounts
- `TEACHER_CODE` env var is the HOD master key

### Demo Mode (`DEMO_MODE=TRUE`)
For showcasing the app without real data.

- No authentication required
- Single-script marking with 3 budget models (Haiku, GPT Mini, Qwen 3.5)
- Class/assignment creation is session-only (not persisted)

### Demo + Department Mode (`DEMO_MODE=TRUE` + `DEPT_MODE=TRUE`)
Pre-seeded HOD dashboard showcase.

- Auto-login as demo HOD
- 3 classes, 3 teachers, 6 assignments with realistic student results
- Full insights and analytics from seed data

## Class List Format

CSV with two columns:

```
Index,Name
01,Alice Tan
02,Bob Lee
03,Charlie Ng
```

Header row is auto-detected and skipped.

## Bulk Marking

Upload a single PDF containing all student scripts. Set the number of pages per student (can vary per student). Set page count to 0 to skip a student and keep their existing result.

## Tech Stack

- **Backend**: Flask, SQLAlchemy, Gunicorn
- **AI Providers**: Anthropic Claude, OpenAI GPT, Alibaba Qwen
- **PDF**: ReportLab (generation), pypdf (splitting), pdf2image (conversion)
- **Frontend**: Vanilla JS, MathJax (LaTeX rendering), Animate.css
- **Database**: PostgreSQL (production) or SQLite (development)
