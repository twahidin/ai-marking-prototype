# Unified Assignment Page Design

**Date:** 2026-04-12
**Goal:** Consolidate create assignment, bulk marking, and single-student marking into a clear three-page workflow.

## Current Problem

The assignment workflow is fragmented across pages:
- `/class` tab 1: bulk marking (select assignment from dropdown)
- `/class` tab 2: create assignment + list assignments
- `/teacher/assignment/<id>`: view students, upload individual scripts
- `/dashboard`: read-only assignment progress cards

Users must navigate between pages to do related tasks. Bulk marking requires selecting class then assignment from dropdowns rather than navigating directly to an assignment.

## Design

### Page 1: My Classes (`/dashboard`)

**Purpose:** Class and student management + progress overview.

Content:
- List of classes with student count
- Upload class list button per class (CSV upload)
- View/manage students expandable per class
- Create class button (normal mode only)
- Assignment progress cards per class (read-only, click → assignment page)

### Page 2: Assignments (`/class`)

**Purpose:** Assignment creation and listing.

Content:
- API key management section (top, same as current)
- Create Assignment form (provider, model, title, subject, class, type, scoring, files, instructions)
- My Assignments list below (all assignments grouped by class, click → assignment page)

Removes: bulk marking tab (moves to assignment page).

### Page 3: Assignment Page (`/teacher/assignment/<id>`)

**Purpose:** All marking actions for one assignment.

Stacked layout (no tabs):

1. **Header** — title, subject, class name, classroom code (copyable), student submission link (copyable), type, scoring mode, results visibility, delete button
2. **Export bar** — Download All Reports (ZIP), Class Overview & Item Analysis
3. **Bulk Mark section** — upload PDF zone, default pages per student, "Set All" button, "Mark All Scripts" button
4. **Student table** — shared by bulk and individual marking:
   - Columns: index, name, page count (for bulk), status, score, upload button (individual), submitted time
   - Page count column used during bulk marking
   - Upload button per row for individual marking
   - Status shows: not submitted / marking / done / error
   - Auto-refresh when students are being processed

## What Moves Where

| Current location | New location |
|---|---|
| Class list upload (dept manage / hidden) | My Classes — per class |
| Create Assignment (`/class` tab 2) | Assignments — top of page |
| Bulk marking (`/class` tab 1) | Assignment Page — bulk section |
| Single student upload (teacher_detail) | Assignment Page — student table |
| Assignment list (`/class` tab 2) | Assignments — below create form |

## Routes

No new routes needed. Existing routes stay:
- `GET /dashboard` — My Classes (add class list upload UI)
- `GET /class` — Assignments (remove bulk tab, keep create + list)
- `GET /teacher/assignment/<id>` — Assignment Page (add bulk mark section)
- `POST /bulk/mark` — Bulk marking API (unchanged)
- `POST /teacher/assignment/<id>/submit/<student_id>` — Individual marking API (unchanged)
- `POST /class/<class_id>/students` — Class list upload API (unchanged)
- `POST /teacher/create` — Create assignment API (unchanged)

## Hub Card Updates

| Mode | Current | New |
|---|---|---|
| Normal | "Mark a Script" + "Mark a Class" | "Mark a Script" + "Assignments" |
| Dept | "Department" + "Manage" + "My Classes" + "Mark a Class" | "Department" + "Manage" + "My Classes" + "Assignments" |
