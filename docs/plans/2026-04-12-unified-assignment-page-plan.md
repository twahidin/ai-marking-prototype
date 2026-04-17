# Unified Assignment Page Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Consolidate create assignment, bulk marking, and single-student marking into a clear three-page workflow: My Classes (class/student management + progress), Assignments (create + list), Assignment Page (all marking).

**Architecture:** Refactor 3 existing templates (dashboard.html, class.html, teacher_detail.html) and update hub cards + nav links. No new routes needed — existing API endpoints stay unchanged. Move bulk marking HTML+JS from class.html into teacher_detail.html. Add class list upload UI to dashboard.html.

**Tech Stack:** Flask/Jinja2, vanilla JavaScript, existing CSS patterns from the app.

---

### Task 1: Update hub cards — rename "Mark a Class" to "Assignments"

**Files:**
- Modify: `templates/hub.html:64-72` (dept mode card)
- Modify: `templates/hub.html:116-121` (normal mode card)

**Step 1: Update dept mode hub card**

In `templates/hub.html`, change the "Mark a Class" card (around line 67-72):

```html
<a class="hub-card animate__animated animate__fadeInUp" style="animation-delay:0.3s;animation-duration:0.5s;" href="/class">
    <div class="icon">&#128218;</div>
    <h2>Assignments</h2>
    <p>Create assignments, set up AI marking, and manage all your assignments across classes.</p>
    <span class="tag">Create &amp; manage</span>
</a>
```

**Step 2: Update dept mode "My Classes" card description**

In `templates/hub.html`, update the My Classes card description (around line 61-66):

```html
<a class="hub-card animate__animated animate__fadeInUp" style="animation-delay:0.25s;animation-duration:0.5s;" href="/dashboard">
    <div class="icon">&#128203;</div>
    <h2>My Classes</h2>
    <p>Manage your classes, upload class lists, view students, and monitor assignment progress.</p>
    <span class="tag">Classes &amp; students</span>
</a>
```

**Step 3: Update normal mode hub card**

In `templates/hub.html`, change the normal mode "Mark a Class" card (around line 116-121):

```html
<a class="hub-card animate__animated animate__fadeInUp" style="animation-delay:0.2s;animation-duration:0.5s;" href="/class">
    <div class="icon">&#128218;</div>
    <h2>Assignments</h2>
    <p>Create assignments, set up AI marking, and manage all your assignments across classes.</p>
    <span class="tag">Create &amp; manage</span>
</a>
```

**Step 4: Update nav bar link text**

In `templates/base.html`, change the nav link text from "Assignments" to match (around line 29-30):

```html
<a href="/class">Assignments</a>
```

This line already says "Assignments" — verify and leave as-is.

**Step 5: Commit**

```bash
git add templates/hub.html
git commit -m "refactor: rename 'Mark a Class' hub cards to 'Assignments'"
```

---

### Task 2: Refactor class.html — remove bulk marking tab, keep create + list only

**Files:**
- Modify: `templates/class.html` (major refactor — remove tab system, remove bulk marking HTML+JS, keep create assignment form + assignment list)

**Step 1: Remove tab bar and bulk marking tab**

Replace the entire tab bar and Tab 1 (Upload All Scripts) section. The page should now show:
1. API key card (unchanged)
2. Create Assignment card (was tab 2, now always visible)
3. My Assignments list (was tab 2, now always visible)

Remove these sections:
- Tab bar div (lines 126-129: `<div class="tabs-bar">...</div>`)
- Tab 1 content div (lines 131-219: `<div class="tab-content active" id="tabUpload">...</div>`)
- Tab 2 wrapper divs — remove `<div class="tab-content" id="tabSubmissions">` opening and closing tags but KEEP the content inside

Remove tab-related CSS:
- `.tabs-bar`, `.tab-btn`, `.tab-content` styles (lines 7-22)
- `.progress-card`, `.results-card` styles (lines 75-76)

**Step 2: Update page title and header**

Change the page title and header:
```html
{% block title %}Assignments - {{ app_title }}{% endblock %}
```

Update the header text:
```html
<h1>{% if demo_mode %}Explore Features{% else %}Assignments{% endif %}</h1>
<p>{% if demo_mode %}Preview the class and assignment management interface{% else %}Create assignments and set up AI marking for your classes{% endif %}</p>
```

**Step 3: Remove bulk marking JavaScript**

Remove these JS functions (and their comments):
- `rebuildBulkProviderGroup()`
- `loadBulkClasses()`
- `loadBulkAssignments()`
- `loadBulkStudents()`
- `setAllPages()`
- `updateBulkTotalPages()`
- `showBulkError()`, `hideBulkError()`
- `startBulkMarking()`
- `submitBulkMarking()`
- `pollBulkStatus()`
- `renderBulkResults()`
- `downloadZip()`
- `downloadOverview()`
- `resetBulk()`

Remove these JS variables:
- `bulkJobId`
- `bulkStudents`

Remove tab-switching JS:
- `switchTab()`
- Hash change listener
- Hash restore IIFE

Remove from init section:
- `loadBulkClasses();` call

**Step 4: Remove "Back to Home" link (replace with consistent nav)**

Remove this line since the nav bar already provides navigation:
```html
<p style="margin-top:8px;">
    <a href="/" style="color:#667eea;font-weight:600;font-size:13px;">&larr; Back to Home</a>
</p>
```

**Step 5: Commit**

```bash
git add templates/class.html
git commit -m "refactor: remove bulk marking tab from assignments page"
```

---

### Task 3: Add bulk marking to assignment page (teacher_detail.html)

**Files:**
- Modify: `templates/teacher_detail.html` (add bulk mark section between export bar and student table)

**Step 1: Add bulk marking CSS**

Add to the `<style>` block in teacher_detail.html:

```css
.bulk-section { margin-bottom: 0; }
.bulk-page-input {
    width: 60px; padding: 6px 4px; text-align: center;
    border: 1px solid #ddd; border-radius: 6px; font-size: 13px;
}
.progress-bar-track {
    background: #f0f0f0; border-radius: 10px; height: 10px;
    width: 100%; overflow: hidden; margin-bottom: 12px;
}
.progress-bar-fill {
    background: linear-gradient(90deg, #667eea, #764ba2);
    border-radius: 10px; height: 100%; transition: width 0.3s; width: 0%;
}
.progress-status { font-size: 13px; color: #555; }
```

**Step 2: Add bulk mark section to HTML**

Insert after the stats-row div and before the submissions card. This section contains:
- Upload PDF zone
- Default pages per student + Set All button
- Mark All Scripts button
- Progress bar (hidden initially)

```html
<!-- Bulk Mark Section -->
<div class="card bulk-section">
    <h2>Bulk Mark</h2>
    <p style="font-size:13px;color:#888;margin-bottom:16px;">
        Upload a single PDF with all student scripts in class list order. Set pages per student, then mark all at once.
    </p>

    <div class="form-group">
        <label>All Student Scripts (single PDF, scanned in class list order)</label>
        <div class="upload-zone" style="max-width:100%;" onclick="this.querySelector('input').click()">
            <input type="file" id="bulk_scripts" accept=".pdf" onchange="fileSelected(this)">
            <div class="icon">&#128218;</div>
            <div class="label">Bulk Scripts PDF</div>
            <div class="filename"></div>
            <div class="hint">One PDF with all student scripts in order</div>
        </div>
    </div>

    <div class="form-group">
        <label for="bulk_pages_per_student">Default Pages Per Student</label>
        <div style="display:flex;gap:8px;align-items:center;">
            <input type="number" class="bulk-page-input" id="bulk_pages_per_student" min="0" max="50" placeholder="2" value="2" style="width:80px;">
            <button class="upload-btn" onclick="setAllPages()" style="white-space:nowrap;">Set All</button>
        </div>
        <p style="font-size:12px;color:#888;margin-top:4px;">Set individual page counts in the table below. Use 0 to skip a student.</p>
    </div>

    <div class="error-msg" id="bulkErrorMsg"></div>
    <button class="action-btn primary" id="bulkMarkBtn" onclick="startBulkMarking()" style="margin-top:12px;">Mark All Scripts</button>

    <!-- Progress (hidden until marking starts) -->
    <div id="bulkProgressCard" style="display:none;margin-top:16px;">
        <div class="progress-bar-track">
            <div class="progress-bar-fill" id="bulkProgressBar"></div>
        </div>
        <div class="progress-status" id="bulkProgressStatus">Starting...</div>
    </div>
</div>
```

**Step 3: Modify the student table to include page count column**

Update the submissions table to add a "Pages" column for bulk marking. Change the thead:

```html
<tr><th>Index</th><th>Name</th><th style="width:70px;text-align:center;">Pages</th><th>Status</th><th>Score</th><th>Submitted</th><th>Actions</th></tr>
```

Add a page count input cell to each student row (after Name, before Status):

```html
<td style="text-align:center;">
    <input type="number" class="bulk-page-input" data-idx="{{ loop.index0 }}" value="2" min="0" max="50" onchange="updateBulkTotalPages()" oninput="updateBulkTotalPages()">
</td>
```

**Step 4: Add total pages display below table**

Add after the table-wrap div closing tag:

```html
<div style="display:flex;justify-content:space-between;margin-top:8px;flex-wrap:wrap;gap:8px;">
    <span style="font-size:13px;color:#555;" id="bulkStudentCount">{{ students|length }} students</span>
    <span style="font-size:13px;font-weight:700;" id="bulkTotalPagesDisplay">Total: 0 pages</span>
</div>
```

**Step 5: Add bulk marking JavaScript**

Add the following functions to the `<script>` block:

```javascript
/* Bulk marking */
var bulkJobId = null;
var ASSIGNMENT_ID = '{{ assignment.id }}';

function setAllPages() {
    var defaultPages = parseInt(document.getElementById('bulk_pages_per_student').value) || 2;
    document.querySelectorAll('.bulk-page-input[data-idx]').forEach(function(inp) {
        inp.value = defaultPages;
    });
    updateBulkTotalPages();
}

function updateBulkTotalPages() {
    var inputs = document.querySelectorAll('.bulk-page-input[data-idx]');
    var total = 0, marking = 0;
    inputs.forEach(function(inp) {
        var v = parseInt(inp.value) || 0;
        total += v;
        if (v > 0) marking++;
    });
    document.getElementById('bulkTotalPagesDisplay').textContent =
        'Total: ' + total + ' pages (' + marking + ' to mark, ' + (inputs.length - marking) + ' skipped)';
}

function showBulkError(msg) { var el = document.getElementById('bulkErrorMsg'); el.textContent = msg; el.style.display = 'block'; }
function hideBulkError() { document.getElementById('bulkErrorMsg').style.display = 'none'; }

function startBulkMarking() {
    hideBulkError();
    var bs = document.getElementById('bulk_scripts').files;
    if (!bs.length) return showBulkError('Please upload the bulk scripts PDF.');

    var inputs = document.querySelectorAll('.bulk-page-input[data-idx]');
    if (inputs.length === 0) return showBulkError('No students found.');

    var pageCounts = [];
    var hasNonZero = false;
    inputs.forEach(function(inp) {
        var v = parseInt(inp.value) || 0;
        if (v < 0) v = 0;
        pageCounts.push(v);
        if (v > 0) hasNonZero = true;
    });

    if (!hasNonZero) return showBulkError('All students are set to skip (0 pages). Set at least one student to mark.');

    submitBulkMarking(pageCounts);
}

async function submitBulkMarking(pageCounts) {
    hideBulkError();
    var bs = document.getElementById('bulk_scripts').files;
    var fd = new FormData();
    fd.append('assignment_id', ASSIGNMENT_ID);
    fd.append('page_counts', JSON.stringify(pageCounts));
    for (var i = 0; i < bs.length; i++) fd.append('bulk_scripts', bs[i]);

    document.getElementById('bulkMarkBtn').disabled = true;
    document.getElementById('bulkProgressCard').style.display = 'block';
    document.getElementById('bulkProgressBar').style.width = '0%';
    document.getElementById('bulkProgressStatus').textContent = 'Uploading...';

    try {
        var res = await fetch('/bulk/mark', { method: 'POST', body: fd });
        var data = await res.json();
        if (!data.success) {
            document.getElementById('bulkProgressCard').style.display = 'none';
            showBulkError(data.error || 'Failed.');
            document.getElementById('bulkMarkBtn').disabled = false;
            return;
        }
        bulkJobId = data.job_id;
        pollBulkStatus();
    } catch (err) {
        document.getElementById('bulkProgressCard').style.display = 'none';
        showBulkError('Connection error.');
        document.getElementById('bulkMarkBtn').disabled = false;
    }
}

async function pollBulkStatus() {
    if (!bulkJobId) return;
    try {
        var res = await fetch('/status/' + bulkJobId);
        var data = await res.json();

        if (data.progress) {
            var pct = Math.round((data.progress.current / data.progress.total) * 100);
            document.getElementById('bulkProgressBar').style.width = pct + '%';
            document.getElementById('bulkProgressStatus').textContent =
                'Marking student ' + data.progress.current + ' of ' + data.progress.total + ' \u2014 ' + data.progress.current_name;
        }

        if (data.status === 'processing') {
            setTimeout(pollBulkStatus, 2000);
            return;
        }

        document.getElementById('bulkProgressCard').style.display = 'none';
        document.getElementById('bulkMarkBtn').disabled = false;

        if (data.status === 'error') {
            showBulkError(data.result?.error || 'Marking failed.');
            return;
        }

        // Reload page to show updated statuses
        window.location.reload();
    } catch (err) { setTimeout(pollBulkStatus, 3000); }
}

// Init page count display
updateBulkTotalPages();
```

**Step 6: Update back link**

Change the back link from `/class#submissions` to `/class`:

```html
<a href="/class" class="back-link">&larr; Back to Assignments</a>
```

**Step 7: Commit**

```bash
git add templates/teacher_detail.html
git commit -m "feat: add bulk marking to assignment page"
```

---

### Task 4: Add class list upload + student management to dashboard.html

**Files:**
- Modify: `templates/dashboard.html` (add class list upload UI and student list per class)

**Step 1: Add CSS for class management UI**

Add to the `<style>` block:

```css
.class-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.class-header h2 { margin-bottom: 0; }
.class-actions { display: flex; gap: 8px; }
.btn-sm {
    padding: 6px 14px; border: none; border-radius: 8px;
    font-size: 12px; font-weight: 600; cursor: pointer;
}
.btn-sm.primary { background: #667eea; color: white; }
.btn-sm.primary:hover { background: #5a6fd6; }
.btn-sm.secondary { background: #f0f2ff; color: #667eea; }
.btn-sm.secondary:hover { background: #e0e4ff; }

.student-section { margin-top: 16px; display: none; }
.student-section.visible { display: block; }
.student-table { width: 100%; font-size: 13px; border-collapse: collapse; }
.student-table th { text-align: left; padding: 8px; font-size: 12px; color: #888; border-bottom: 1px solid #eee; }
.student-table td { padding: 8px; border-bottom: 1px solid #f5f5f5; }

.upload-inline {
    display: flex; gap: 8px; align-items: center; margin-top: 12px; flex-wrap: wrap;
}
.upload-inline input[type="file"] { font-size: 13px; }
.student-count { font-size: 13px; color: #888; margin-left: 8px; }
```

**Step 2: Add student management UI per class**

For each class section, add a management bar and expandable student section. Replace the class header with:

```html
<div class="class-header">
    <h2>
        {% if cls.level %}{{ cls.level }} - {% endif %}<span style="color:#667eea;">{{ cls.name }}</span>
        <span class="student-count" id="count_{{ cls.id }}">{{ cls.student_count }} students</span>
    </h2>
    <div class="class-actions">
        <button class="btn-sm secondary" onclick="toggleStudents('{{ cls.id }}')">View Students</button>
        <label class="btn-sm primary" style="cursor:pointer;">
            Upload Class List
            <input type="file" accept=".csv,.xlsx,.xls" style="display:none;" onchange="uploadClassList('{{ cls.id }}', this)">
        </label>
    </div>
</div>

<div class="student-section" id="students_{{ cls.id }}">
    <table class="student-table">
        <thead><tr><th>#</th><th>Index</th><th>Name</th></tr></thead>
        <tbody id="studentBody_{{ cls.id }}"></tbody>
    </table>
</div>
```

**Step 3: Pass student_count in dashboard route data**

The dashboard route already has `student_counts_by_class`. Add `student_count` to each class_data dict in `app.py` (around line 1491):

```python
class_data.append({
    'id': cls.id,
    'name': cls.name,
    'level': cls.level,
    'student_count': student_counts_by_class.get(cls.id, 0),
    'assignments': asn_data,
})
```

**Step 4: Add JavaScript for student management**

Add a `<script>` block:

```javascript
async function toggleStudents(classId) {
    var section = document.getElementById('students_' + classId);
    if (section.classList.contains('visible')) {
        section.classList.remove('visible');
        return;
    }
    // Load students
    try {
        var res = await fetch('/class/' + classId + '/students');
        var data = await res.json();
        if (!data.success) return;
        var tbody = document.getElementById('studentBody_' + classId);
        tbody.innerHTML = '';
        data.students.forEach(function(s, i) {
            var tr = document.createElement('tr');
            tr.innerHTML = '<td>' + (i + 1) + '</td><td>' + esc(s.index) + '</td><td>' + esc(s.name) + '</td>';
            tbody.appendChild(tr);
        });
        section.classList.add('visible');
    } catch (e) { console.error(e); }
}

async function uploadClassList(classId, input) {
    if (!input.files.length) return;
    var fd = new FormData();
    fd.append('class_list', input.files[0]);

    try {
        var res = await fetch('/class/' + classId + '/students', { method: 'POST', body: fd });
        var data = await res.json();
        if (data.success) {
            document.getElementById('count_' + classId).textContent = data.count + ' students';
            // Refresh student list if visible
            var section = document.getElementById('students_' + classId);
            if (section.classList.contains('visible')) {
                section.classList.remove('visible');
                toggleStudents(classId);
            }
            input.value = '';
        } else {
            alert(data.error || 'Failed to upload class list.');
        }
    } catch (e) {
        alert('Connection error.');
    }
}

function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
```

**Step 5: Commit**

```bash
git add templates/dashboard.html app.py
git commit -m "feat: add class list upload and student management to My Classes"
```

---

### Task 5: Update dashboard route for normal mode

**Files:**
- Modify: `app.py:1419-1502` (dashboard route)

**Step 1: Allow dashboard in normal mode (not just dept mode)**

Currently the dashboard redirects non-dept mode users. Change it to work for both modes:

```python
@app.route('/dashboard')
def teacher_dashboard():
    if not _is_authenticated():
        return redirect(url_for('hub'))

    teacher = _current_teacher()
    if not teacher:
        return redirect(url_for('hub'))
```

Remove the `if not is_dept_mode()` check at the top.

**Step 2: Handle normal mode class loading**

In normal mode, classes come from TeacherClass relationship. The existing code uses `teacher.classes` which should work for both modes (the Teacher model's `classes` relationship). Verify this works or add a fallback:

If `teacher.classes` is empty in normal mode but classes exist, query via TeacherClass:

```python
if hasattr(teacher, 'classes') and teacher.classes:
    teacher_classes = teacher.classes
else:
    tc_ids = [tc.class_id for tc in TeacherClass.query.filter_by(teacher_id=teacher.id).all()]
    teacher_classes = Class.query.filter(Class.id.in_(tc_ids)).all() if tc_ids else []
```

**Step 3: Commit**

```bash
git add app.py
git commit -m "feat: enable My Classes dashboard for normal mode"
```

---

### Task 6: Final cleanup and verify

**Files:**
- Modify: `templates/class.html` — verify clean state after Task 2
- Modify: `templates/teacher_detail.html` — verify clean state after Task 3
- Verify: all routes work end-to-end

**Step 1: Test the full flow manually**

1. Go to `/` — verify hub cards say "Assignments" not "Mark a Class"
2. Go to `/dashboard` — verify class list upload works, student list toggles
3. Go to `/class` — verify no bulk marking tab, just create assignment + list
4. Create an assignment — verify it appears in the list
5. Click an assignment → `/teacher/assignment/<id>` — verify bulk mark section + individual upload both work
6. Upload a bulk PDF → verify marking works with progress bar

**Step 2: Verify demo mode still works**

1. With `DEMO_MODE=TRUE`, verify `/class` shows explore features without errors
2. Verify hub cards are correct in demo mode

**Step 3: Final commit**

```bash
git add -A
git commit -m "refactor: unified assignment workflow — three-page layout"
```
