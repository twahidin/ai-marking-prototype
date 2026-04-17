# Department Mode Design

## Overview

Add a department deployment mode (`DEPT_MODE=TRUE`) where an HOD manages classes, assigns teachers, and monitors marking across the department. Teachers work within their assigned classes. When `DEPT_MODE` is not set or is `FALSE`, the app behaves exactly as it does today.

## Data Model

### New Models

**Teacher**
- `id` — PK (string UUID)
- `name` — display name
- `code` — unique invite code (used as login credential)
- `role` — enum: `hod` or `teacher`
- `created_at` — timestamp

**Class**
- `id` — PK (string UUID)
- `name` — e.g. "3A"
- `level` — e.g. "Sec 3" (optional)
- `created_at` — timestamp

**TeacherClass** (many-to-many join)
- `teacher_id` — FK to Teacher
- `class_id` — FK to Class

### Modified Models

**Assignment** — two new nullable foreign keys:
- `class_id` — FK to Class (null in default mode, required in dept mode)
- `teacher_id` — FK to Teacher (null in default mode, required in dept mode)

## Authentication & Access Control

### Default mode (`DEPT_MODE` unset or `FALSE`)
- Current behavior unchanged: shared `ACCESS_CODE` gate, no identity
- Single mark + bulk mark both available
- No department UI visible at all

### Department mode (`DEPT_MODE=TRUE`)
- Login via personal code (looked up in `Teacher` table)
- Session stores `teacher_id` and `role`
- HOD is a Teacher row with `role='hod'`

### Route access in department mode

| Route | Teacher | HOD |
|---|---|---|
| `/` hub | Dashboard link | Department link |
| `/mark` single mark | Hidden | Hidden |
| `/class` bulk mark | Own classes only | All classes |
| `/dashboard` | Own classes/assignments | N/A |
| `/department` | N/A | Overview of everything |
| `/department/classes` | N/A | Manage classes & teachers |
| `/department/insights` | N/A | Analytics & export |
| `/s/<code>` student submit | Accessible | Accessible |

### Demo mode + department mode
- Teachers can see class allocation UI (read-only demo data)
- HOD insights pages show "Not available in demo mode"

## HOD Dashboard (`/department`)

### Overview
- Summary cards: total classes, teachers, assignments, submissions
- Per-class breakdown: class name, teacher(s), assignment count, submission count, completion rate
- Assignment status: pending / in-progress / completed

### Class & Teacher Management (`/department/classes`)
- Create/edit/delete classes
- Create teachers (auto-generates unique invite code)
- Assign/unassign teachers to classes (many-to-many)
- View teacher list with codes

### Insights & Export (`/department/insights`)
Disabled in demo mode.

**Performance analytics:**
- Score distribution per assignment (histogram)
- Average marks per class for a given assignment (bar chart, e.g. 3A vs 3B)
- Pass/fail rates per class
- Per-question breakdown: which questions students struggled with most

**Export/collate:**
- CSV export: all results for an assignment across classes, or all assignments for a class
- PDF overview: combined report (reuses existing `generate_overview_pdf`)
- Compare view: side-by-side class performance on the same assignment

### API Key Management
- HOD sets API keys via department settings
- Stored in `DepartmentConfig` table
- All teachers use department keys (no per-teacher key management)

## Teacher Dashboard (`/dashboard`)

- List of assigned classes
- Per-class: assignments created, submission progress, average scores
- Quick actions: create assignment, view results, download reports

## Hub Changes (Department Mode)

- Login: single code input, looks up Teacher table, redirects by role
- Teacher sees: "My Dashboard" link
- HOD sees: "Department Dashboard" link
- Both see: "Bulk Mark" (filtered to their classes)
- Student submission link unchanged
- Nav shows logged-in teacher name, role, logout option

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `DEPT_MODE` | `FALSE` | Enables department mode. Only active when explicitly `TRUE`. |
| `DEMO_MODE` | `FALSE` | Demo layer: disables HOD insights in dept mode |
| `ACCESS_CODE` | (empty) | Not used in dept mode (replaced by per-teacher codes) |

## Constraints

- Zero impact on default mode — all dept features gated behind `DEPT_MODE=TRUE`
- SQLite still works (no Postgres-specific features required)
- A teacher can teach multiple classes; a class can have multiple teachers
- Existing student submission flow unchanged
