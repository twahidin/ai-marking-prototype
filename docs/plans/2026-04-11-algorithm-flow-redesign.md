# Algorithm Flow Redesign

## Overview

Redesign the app's algorithm flow across three operating modes (Normal, Department, Demo) with persistent class/assignment management, configurable app title, LaTeX-enabled feedback, and security fixes from code review.

## Operating Modes

| | Normal Mode | Department Mode | Demo Mode | Demo+Dept Mode |
|---|---|---|---|---|
| Env vars | `DEPT_MODE=FALSE` | `DEPT_MODE=TRUE` | `DEMO_MODE=TRUE` | `DEMO_MODE=TRUE` + `DEPT_MODE=TRUE` |
| Auth | `TEACHER_CODE` env var (master key) | HOD manages teacher codes | No auth required | No auth required |
| Who creates classes | The teacher | HOD only | Session-only, no DB | Pre-seeded fake data |
| Class list upload | Teacher (at class level) | HOD or teacher (at class level) | Session-only | Pre-seeded |
| Single marking | Yes (class -> assignment -> student) | Same | Yes, 3 models only (Haiku, GPT Mini, Qwen 3.5) | Yes, 3 models only |
| Bulk marking | Yes (variable page counts, 0=skip) | Same | Disabled | Disabled |
| Student submission | Yes (via assignment link) | Same | Disabled | Disabled |
| DB persistence | Yes | Yes | No | No |
| Insights | Per-teacher only | HOD sees all | N/A | Pre-seeded sample insights |

## APP_TITLE Configuration

- Env var: `APP_TITLE`, default: `"AI Feedback Systems"`
- Injected into `base.html` navbar, page titles, PDF report headers
- Examples: "Math Department", "AI Marking Demo", "English Faculty"

## LaTeX-Enabled Feedback

- MathJax rendering enabled on all feedback display surfaces (single marking results, student submission results, teacher assignment detail, bulk marking results)
- AI responses containing LaTeX notation (e.g., `$x^2$`, `\frac{a}{b}`) render properly in the browser
- PDF reports use `clean_for_pdf()` to convert LaTeX to Unicode (already exists in pdf_generator.py)
- Ensure MathJax `typeset()` is called after dynamic content insertion on all pages

## Normal Mode Flow

```
Teacher enters TEACHER_CODE (env var = permanent master key)
    |
First visit? -> Setup screen (set display name, optional custom code)
    |
Hub -> "My Classes" / "Mark a Script"
    |
Create Class (name, level) -> Upload Class List (CSV) -> Students saved to DB
    |
Create Assignment under Class (question paper, answer key/rubrics, provider, model, settings)
    |
Three paths:
  +-- Single Mark: Pick class -> assignment -> student -> upload script
  |     -> WARN if existing result (show date, source) -> mark -> save to DB
  |
  +-- Bulk Mark: Pick class -> assignment -> upload bulk PDF
  |     -> Set page counts per student (0 = skip, keep existing)
  |     -> Mark all non-skipped -> save to DB (overrides existing)
  |
  +-- Student Submit: Share assignment link + classroom code
        -> Student enters code -> selects name -> uploads script -> auto-marked
```

### Auth Details (Normal Mode)
- `TEACHER_CODE` env var is the permanent master key that always works
- On first login, teacher sets their display name (stored in DB as Teacher record)
- Teacher can optionally set a custom code from the app, but env var code always works as fallback
- One teacher per deployment — for multi-teacher, use Department Mode
- Teacher sees only their own classes and assignments

### Override Rules
- **Single marking**: warns "This student was marked on [date] via [single/bulk]. Override?" before proceeding
- **Bulk marking**: overwrites all existing results EXCEPT students with page count = 0 (those are skipped, existing results preserved)
- **Student submission**: overwrites their own previous submission (with warning)

### Class List at Class Level
- Class list (CSV) is uploaded once when creating a class
- Students are stored as DB records linked to the class
- All assignments under that class share the same student list
- Teacher can update the class list later (add/remove students)

## Department Mode Flow

```
HOD deploys with DEPT_MODE=TRUE, TEACHER_CODE=<master key>
    |
First visit -> HOD setup (name, department name)
    |
HOD creates classes, assigns teachers (each gets unique code)
    |
HOD or teacher uploads class list to a class
    |
Teacher logs in with their code -> sees assigned classes only
    |
Teacher creates assignments, does single/bulk marking, manages submissions
    (same marking flow as Normal Mode)
    |
HOD can: manage teachers (create/revoke/purge), view insights, export CSV
HOD can also: access teacher dashboard for their own assigned classes
```

### HOD Teacher Management
- Create teacher with name + auto-generated code
- Reset teacher code
- Revoke access: disable code, keep data (teacher account and their assignments/results remain)
- Purge account: delete teacher + optionally their assignments/results
- `TEACHER_CODE` env var always works for HOD access (permanent master key)

## Demo Mode Flow

```
DEMO_MODE=TRUE -> No auth gate
    |
Hub shows two sections:
  +-- "Try AI Marking" -> Upload question paper, answer key, script
  |     Models restricted to: Haiku, GPT Mini, Qwen 3.5
  |     Single script only, no bulk
  |     Full marking result displayed with LaTeX rendering
  |
  +-- "Explore Features" -> Create classes, assignments (session-only, no DB)
        Can browse full UI but cannot trigger any marking from here
        Student submission links show "disabled in demo mode"
```

### Demo Mode Constraints
- No authentication required
- Marking restricted to 3 budget-friendly models
- Only single script marking available (no bulk, no student submission)
- Class/assignment creation works in-session but nothing persists to DB
- All session data cleared on logout/browser close

## Demo + Department Mode Flow

```
DEMO_MODE=TRUE + DEPT_MODE=TRUE -> No auth gate
    |
Pre-seeded fake data loaded:
  - 3 classes: "Sec 3A Math", "Sec 3B Math", "Sec 4A Math"
  - 3 teachers assigned across classes
  - 2 assignments per class with fake student results
  - Mix of scores (60-95%) for realistic insights
    |
Participant sees HOD dashboard with real-looking data
  - Browse department overview, class details
  - View insights/analytics with charts from seed data
  - Can create additional classes/teachers (session-only)
  - Student submission links show "disabled in demo mode"
  - "Try AI Marking" demo still available (3 models)
```

### Seed Data Spec
- Classes: "Sec 3A Math" (15 students), "Sec 3B Math" (18 students), "Sec 4A Math" (12 students)
- Teachers: "Ms. Chen" (3A, 3B), "Mr. Rahman" (4A), "Ms. Tan" (3B, 4A)
- Assignments: "Mid-Year Exam" and "Quiz 3" per class
- Student results: randomized scores with realistic distribution (mean ~75%, std ~12%)
- Question-level data: 5-8 questions per assignment with varied difficulty

## Data Model Changes

### Class List at Class Level
- Move class list storage from Assignment to Class
- `Class.students` relationship (1:M) — students belong to the class, not the assignment
- `Student.class_id` FK replaces `Student.assignment_id` as the primary link
- `Submission` links to both `student_id` and `assignment_id` (composite)

### New/Modified Fields
- `Teacher.is_active` (Boolean, default True) — for revoke without delete
- `Class.class_list_csv` (LargeBinary) — store original CSV for re-download
- `APP_TITLE` env var — no DB change needed
- `Assignment.class_id` FK — already exists, just enforce NOT NULL

### Index Additions
- `Student.class_id` (index=True)
- `Submission.assignment_id` (index=True)
- `Submission.student_id` (index=True)
- `Assignment.teacher_id` (index=True)
- `Assignment.class_id` (index=True)

## Security Fixes (from Code Review)

### Critical
1. **Add ownership checks** on all `/teacher/assignment/` routes — verify `asn.teacher_id == current_teacher.id` (HOD bypasses)
2. **Validate `teacher_id`** in `dept_unassign_teacher` with `get_or_404`

### High
3. **Replace `session.get('authenticated')` with `_is_authenticated()`** across all routes
4. **Use `secrets.choice`** for teacher codes and classroom codes
5. **Fix `debug=True`** — use `FLASK_DEBUG` env var
6. **Escape `dotLabel`, `marks_awarded`, `marks_total`** in template innerHTML constructions
7. **Add threading.Lock** around `_check_rate_limit` and job dict mutations
8. **Fix N+1 queries** with `joinedload`/`subqueryload` on dashboard routes

### Medium
9. Add `db.session.rollback()` before error-path commits in background threads
10. Add `index=True` to FK columns (listed above)
11. Add `SESSION_COOKIE_SECURE = True`
12. Cap class list CSV to 500 rows / 1MB

## Key Changes Summary

| Area | Current | New |
|---|---|---|
| Class list | Per-assignment upload | Per-class, reused across assignments |
| Normal mode storage | In-memory, ephemeral | Persistent DB (same models as dept mode) |
| Single marking | Standalone, no context | Tied to class -> assignment -> student |
| Auth (normal) | Single `ACCESS_CODE` | `TEACHER_CODE` env var (master key) + optional custom code |
| Demo marking | Full access to all models | 3 models only, single script, no bulk |
| Demo classes | N/A | Session-only, no persistence |
| Demo+Dept | N/A | Pre-seeded fake data for HOD showcase |
| App title | Hardcoded | `APP_TITLE` env var, default "AI Feedback Systems" |
| Override behavior | Silent overwrite | Warning with timestamp before override |
| Feedback rendering | Partial MathJax | Full LaTeX/MathJax on all feedback surfaces |
| Security | Multiple gaps | Ownership checks, CSRF awareness, escaped HTML, secrets module |
