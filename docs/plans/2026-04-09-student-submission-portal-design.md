# Student Submission Portal Design

## Overview

Add a student-facing submission portal where teachers create assignments and share a link + classroom code with students. Students select their name, upload their script, and get auto-marked by AI.

## Teacher Flow (`/teacher`)

1. Log in with access code
2. Create assignment: upload question paper, answer key/rubrics, class list CSV, set subject, type, scoring, total marks
3. Toggle "Show results to students" on/off
4. Get shareable link (`/submit/<assignment_id>`) + auto-generated classroom code (e.g. `ENG3E`)
5. Dashboard shows all assignments with submission count
6. View all results, download bulk ZIP of reports

## Student Flow (`/submit/<assignment_id>`)

1. Open link, enter classroom code
2. Select name from dropdown (populated from class list)
3. Take photo (mobile camera) or upload PDF
4. Submit — see marking progress spinner
5. If results enabled: see feedback after AI finishes
6. If not: see "Submitted successfully" confirmation
7. Can re-submit (overwrites previous)

## Database (Postgres)

### assignments
- id, access_code, classroom_code, subject, assign_type, scoring_mode, total_marks
- question_paper (bytes), answer_key (bytes), rubrics (bytes), reference (bytes)
- review_instructions, marking_instructions, provider, model
- show_results (boolean), api_keys_json (encrypted)
- created_at

### students
- id, assignment_id, index_number, name

### submissions
- id, student_id, assignment_id
- script_bytes, status (pending/processing/done/error), result_json
- submitted_at, marked_at

## Key Decisions

- Teacher provides own API key (stored in assignment row)
- Classroom code: 6 chars, alphanumeric
- Auto-mark triggers on submit in background thread
- Re-submit replaces old submission
- Single marking (`/`) and bulk marking (`/bulk`) unchanged
- `PROVIDE_KEYS` env var still controls single marking mode
