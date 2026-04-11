# Algorithm Flow Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesign the app's algorithm flow across three modes (Normal, Department, Demo) with persistent class/assignment management, configurable app title, LaTeX-enabled feedback, and critical security fixes.

**Architecture:** Incremental refactor of existing app.py, db.py, and templates. Move class list from per-assignment to per-class. Add `APP_TITLE` env var. Restrict demo mode to 3 models. Add seed data module for demo+dept mode. Fix critical security issues from code review.

**Tech Stack:** Flask, SQLAlchemy, Jinja2, MathJax, ReportLab, Python 3.10+

---

### Task 1: Add `APP_TITLE` env var and thread through all templates

**Files:**
- Modify: `app.py:33-35` (env vars section)
- Modify: `app.py:45-51` (context processor)
- Modify: `templates/base.html:6,15`
- Modify: `templates/hub.html:36,80`
- Modify: `pdf_generator.py` (report header)

**Step 1: Add env var and inject into templates**

In `app.py`, after line 35 (DEPT_MODE), add:
```python
APP_TITLE = os.getenv('APP_TITLE', 'AI Feedback Systems')
```

In `app.py` context processor `inject_dept_context()`, add `app_title` to both return dicts:
```python
def inject_dept_context():
    if DEPT_MODE:
        teacher = _current_teacher()
        return {'dept_mode': True, 'current_teacher': teacher, 'app_title': APP_TITLE, 'demo_mode': DEMO_MODE}
    return {'dept_mode': False, 'current_teacher': None, 'app_title': APP_TITLE, 'demo_mode': DEMO_MODE}
```

**Step 2: Update templates**

In `templates/base.html`, replace hardcoded "AI Marking Demo" in title and "AI Marking" in navbar brand:
```html
<title>{% block title %}{{ app_title }}{% endblock %}</title>
...
<a href="/" class="dept-nav-brand">{{ app_title }}</a>
```

In `templates/hub.html`, replace hardcoded "AI Marking Demo" (lines 36, 80):
```html
{% set gate_title = 'Department Login' if dept_mode else app_title %}
...
<h1>{{ app_title }}</h1>
```

**Step 3: Update PDF generator**

In `pdf_generator.py`, accept optional `app_title` parameter and use it in report headers instead of hardcoded text. Thread `APP_TITLE` through from app.py when calling `generate_report_pdf()` and `generate_overview_pdf()`.

**Step 4: Commit**
```bash
git add app.py templates/base.html templates/hub.html pdf_generator.py
git commit -m "feat: add APP_TITLE env var, default 'AI Feedback Systems'"
```

---

### Task 2: Add `TEACHER_CODE` env var and normal mode auth flow

**Files:**
- Modify: `app.py:33-35` (env vars)
- Modify: `app.py:109-115` (`_is_authenticated`)
- Modify: `app.py:270-290` (`verify_code`)
- Modify: `app.py:209-220` (`hub` route)
- Modify: `templates/hub.html`
- Modify: `templates/_gate.html`

**Step 1: Replace ACCESS_CODE with TEACHER_CODE**

In `app.py`, replace:
```python
ACCESS_CODE = os.getenv('ACCESS_CODE', '').strip()
```
with:
```python
ACCESS_CODE = os.getenv('ACCESS_CODE', '').strip()  # legacy compat
TEACHER_CODE = os.getenv('TEACHER_CODE', '').strip() or ACCESS_CODE
```

**Step 2: Update `_is_authenticated()` and `verify_code()`**

`_is_authenticated()` — for normal mode, check `session.get('teacher_id')` (same as dept mode) when `TEACHER_CODE` is set:
```python
def _is_authenticated():
    if DEPT_MODE:
        return session.get('teacher_id') is not None
    if TEACHER_CODE:
        return session.get('teacher_id') is not None
    if not ACCESS_CODE:
        return True
    return session.get('authenticated', False)
```

`verify_code()` — for normal mode with TEACHER_CODE, verify code, then find-or-create Teacher record:
```python
if not DEPT_MODE and TEACHER_CODE:
    if code == TEACHER_CODE:
        # Master key — find or create the default teacher
        teacher = Teacher.query.filter_by(role='owner').first()
        if not teacher:
            # First time — redirect to setup
            session['pending_setup'] = True
            return jsonify({'success': True, 'redirect': '/setup'})
        session['teacher_id'] = teacher.id
        session['teacher_name'] = teacher.name
        return jsonify({'success': True, 'redirect': '/'})
    # Also check custom code
    teacher = Teacher.query.filter_by(code=code, role='owner').first()
    if teacher:
        session['teacher_id'] = teacher.id
        session['teacher_name'] = teacher.name
        return jsonify({'success': True, 'redirect': '/'})
    return jsonify({'success': False, 'error': 'Invalid code'}), 401
```

**Step 3: Add normal mode setup route**

Add `/setup` GET/POST route — shows a form for the teacher to enter their display name. Creates a Teacher record with `role='owner'`. Redirects to hub.

```python
@app.route('/setup', methods=['GET', 'POST'])
def teacher_setup():
    if DEPT_MODE:
        return redirect(url_for('hub'))
    if not session.get('pending_setup') and not session.get('teacher_id'):
        return redirect(url_for('hub'))
    if request.method == 'POST':
        data = request.get_json()
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'success': False, 'error': 'Name is required'}), 400
        teacher = Teacher(
            id=str(uuid.uuid4()),
            name=name,
            code=TEACHER_CODE,
            role='owner',
        )
        db.session.add(teacher)
        db.session.commit()
        session.pop('pending_setup', None)
        session['teacher_id'] = teacher.id
        session['teacher_name'] = teacher.name
        return jsonify({'success': True, 'redirect': '/'})
    return render_template('teacher_setup.html')
```

**Step 4: Create `templates/teacher_setup.html`**

Simple form: name input, submit button. Extends `base.html`. Styled like `department_setup.html`.

**Step 5: Update hub route and template**

In `hub()`, pass `teacher` for normal mode too:
```python
teacher = _current_teacher() if (DEPT_MODE or TEACHER_CODE) else None
```

In `hub.html`, update the non-dept authenticated section to show teacher name and class-based cards (My Classes, Mark a Script, Mark a Class).

**Step 6: Commit**
```bash
git add app.py templates/hub.html templates/_gate.html templates/teacher_setup.html
git commit -m "feat: add TEACHER_CODE env var and normal mode teacher auth"
```

---

### Task 3: Move class list from per-assignment to per-class

**Files:**
- Modify: `db.py:83-90` (Class model)
- Modify: `db.py:162-168` (Student model)
- Modify: `db.py:31-55` (migration)
- Modify: `app.py` (routes that create students)

**Step 1: Update Class model**

Add to Class model in `db.py`:
```python
class Class(db.Model):
    __tablename__ = 'classes'
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    level = db.Column(db.String(100), default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    teachers = db.relationship('Teacher', secondary='teacher_classes', back_populates='classes')
    assignments = db.relationship('Assignment', backref='dept_class', lazy=True)
    students = db.relationship('Student', backref='student_class', lazy=True, cascade='all, delete-orphan')
```

**Step 2: Update Student model**

Add `class_id` FK to Student, make `assignment_id` nullable (students now belong to class, not assignment):
```python
class Student(db.Model):
    __tablename__ = 'students'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    class_id = db.Column(db.String(36), db.ForeignKey('classes.id'), nullable=True, index=True)
    assignment_id = db.Column(db.String(36), db.ForeignKey('assignments.id'), nullable=True)
    index_number = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    submission = db.relationship('Submission', backref='student', uselist=False, lazy=True, cascade='all, delete-orphan')
```

**Step 3: Add migration for `class_id` column on students**

In `_migrate_add_columns`, add:
```python
if 'students' in inspector.get_table_names():
    columns = [c['name'] for c in inspector.get_columns('students')]
    if 'class_id' not in columns:
        db.session.execute(text("ALTER TABLE students ADD COLUMN class_id VARCHAR(36)"))
        db.session.commit()
        logger.info('Added class_id column to students table')
```

**Step 4: Add class list upload route**

Add `/class/<class_id>/students` POST route for uploading class list CSV to a class:
```python
@app.route('/class/<class_id>/students', methods=['POST'])
def upload_class_list(class_id):
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    cls = Class.query.get_or_404(class_id)
    
    # Ownership check
    teacher = _current_teacher()
    if teacher and not _is_hod():
        tc = TeacherClass.query.filter_by(teacher_id=teacher.id, class_id=class_id).first()
        if not tc:
            return jsonify({'success': False, 'error': 'Not assigned to this class'}), 403
    
    cl_file = request.files.get('class_list')
    if not cl_file or not cl_file.filename:
        return jsonify({'success': False, 'error': 'Please upload a class list CSV'}), 400
    
    file_bytes = cl_file.read()
    if len(file_bytes) > 1024 * 1024:  # 1MB cap
        return jsonify({'success': False, 'error': 'Class list too large (max 1MB)'}), 400
    
    students_data = _parse_class_list(file_bytes, cl_file.filename)
    if not students_data:
        return jsonify({'success': False, 'error': 'Could not parse class list'}), 400
    if len(students_data) > 500:
        return jsonify({'success': False, 'error': 'Maximum 500 students per class'}), 400
    
    # Remove existing students (only those without submissions)
    existing = Student.query.filter_by(class_id=class_id).all()
    for s in existing:
        has_sub = Submission.query.filter_by(student_id=s.id).first()
        if not has_sub:
            db.session.delete(s)
    
    # Add new students
    for s in students_data:
        # Check if student already exists in this class
        existing_student = Student.query.filter_by(class_id=class_id, index_number=s['index']).first()
        if not existing_student:
            db.session.add(Student(class_id=class_id, index_number=s['index'], name=s['name']))
    
    db.session.commit()
    return jsonify({'success': True, 'count': len(students_data)})
```

**Step 5: Add normal mode class creation route**

For normal mode (non-dept), teacher creates their own classes:
```python
@app.route('/my/class/create', methods=['POST'])
def create_class():
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    data = request.get_json()
    name = (data.get('name') or '').strip()
    level = (data.get('level') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Class name is required'}), 400
    
    teacher = _current_teacher()
    cls = Class(id=str(uuid.uuid4()), name=name, level=level)
    db.session.add(cls)
    
    if teacher:
        db.session.add(TeacherClass(teacher_id=teacher.id, class_id=cls.id))
    
    db.session.commit()
    return jsonify({'success': True, 'class_id': cls.id, 'name': cls.name, 'level': cls.level})
```

**Step 6: Commit**
```bash
git add db.py app.py
git commit -m "feat: move class list from per-assignment to per-class level"
```

---

### Task 4: Refactor assignment creation to require class

**Files:**
- Modify: `app.py:1344-1467` (`teacher_create` route)
- Modify: `templates/class.html` (assignment creation form)

**Step 1: Refactor `teacher_create` to require class_id**

Remove `class_list` file upload from assignment creation. Instead, require `class_id` (the class must already have students uploaded). Assignment creation now:
1. Validates class exists and has students
2. Creates Assignment linked to class
3. Links existing class students to the assignment via `class_id`
4. No longer creates Student records (they already exist at class level)

```python
@app.route('/teacher/create', methods=['POST'])
def teacher_create():
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    teacher = _current_teacher()
    class_id = request.form.get('class_id')
    if not class_id:
        return jsonify({'success': False, 'error': 'Please select a class'}), 400
    
    cls = Class.query.get(class_id)
    if not cls:
        return jsonify({'success': False, 'error': 'Class not found'}), 404
    
    # Check class has students
    students = Student.query.filter_by(class_id=class_id).all()
    if not students:
        return jsonify({'success': False, 'error': 'This class has no students. Upload a class list first.'}), 400
    
    # Ownership check (non-dept or dept teacher)
    if teacher and not (DEPT_MODE and teacher.role == 'hod'):
        tc = TeacherClass.query.filter_by(teacher_id=teacher.id, class_id=class_id).first()
        if not tc:
            return jsonify({'success': False, 'error': 'Not assigned to this class'}), 403
    
    # ... rest of assignment creation (question paper, answer key, etc.)
    # ... but WITHOUT class_list upload and WITHOUT Student creation
    
    # Create assignment linked to class
    asn = Assignment(
        id=str(uuid.uuid4()),
        classroom_code=_generate_classroom_code(),
        title=request.form.get('title', ''),
        subject=request.form.get('subject', ''),
        # ... other fields ...
        class_id=class_id,
        teacher_id=teacher.id if teacher else None,
    )
    # ... set API keys, save, commit
```

**Step 2: Update Submission queries**

Submissions now link via `student_id` (student belongs to class) + `assignment_id`. Queries that previously used `Student.assignment_id` must be updated to use `Student.class_id` and filter submissions by `assignment_id` separately.

**Step 3: Update class.html form**

Remove class_list upload field from assignment creation tab. Add a class dropdown (populated from teacher's classes). Show "No students — upload class list first" warning if selected class is empty.

**Step 4: Commit**
```bash
git add app.py templates/class.html
git commit -m "refactor: assignment creation requires existing class with students"
```

---

### Task 5: Redesign single marking flow (class → assignment → student)

**Files:**
- Modify: `app.py:229-241` (`single_mark_page`)
- Modify: `app.py:317-371` (`mark` POST route)
- Modify: `templates/index.html`
- Add API routes: `/api/classes`, `/api/class/<id>/assignments`, `/api/assignment/<id>/students`

**Step 1: Add API routes for cascading dropdowns**

```python
@app.route('/api/classes')
def api_classes():
    if not _is_authenticated():
        return jsonify([])
    teacher = _current_teacher()
    if teacher:
        classes = teacher.classes
    else:
        classes = Class.query.all()
    return jsonify([{'id': c.id, 'name': c.name, 'level': c.level} for c in classes])

@app.route('/api/class/<class_id>/assignments')
def api_class_assignments(class_id):
    if not _is_authenticated():
        return jsonify([])
    assignments = Assignment.query.filter_by(class_id=class_id).order_by(Assignment.created_at.desc()).all()
    return jsonify([{'id': a.id, 'title': a.title or a.subject or 'Untitled', 'created_at': a.created_at.isoformat()} for a in assignments])

@app.route('/api/assignment/<assignment_id>/students')
def api_assignment_students(assignment_id):
    if not _is_authenticated():
        return jsonify([])
    asn = Assignment.query.get_or_404(assignment_id)
    students = _sort_by_index(Student.query.filter_by(class_id=asn.class_id).all())
    result = []
    for s in students:
        sub = Submission.query.filter_by(student_id=s.id, assignment_id=assignment_id).first()
        result.append({
            'id': s.id,
            'index': s.index_number,
            'name': s.name,
            'has_submission': sub is not None,
            'submission_status': sub.status if sub else None,
            'submitted_at': sub.submitted_at.isoformat() if sub and sub.submitted_at else None,
            'marked_at': sub.marked_at.isoformat() if sub and sub.marked_at else None,
            'source': 'bulk' if sub and sub.script_pages_json and not sub.script_bytes else 'single' if sub else None,
        })
    return jsonify(result)
```

**Step 2: Redesign single marking page**

Update `templates/index.html` to show:
1. Three cascading dropdowns: Class → Assignment → Student
2. When student selected, show warning if existing submission exists (date, source)
3. Upload only the script (question paper/answer key come from assignment)
4. Override confirmation dialog
5. On submit, POST to `/mark` with `assignment_id`, `student_id`, and script files

**Step 3: Refactor `/mark` POST route**

```python
@app.route('/mark', methods=['POST'])
def mark():
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    assignment_id = request.form.get('assignment_id')
    student_id = request.form.get('student_id')
    
    if not assignment_id or not student_id:
        return jsonify({'success': False, 'error': 'Assignment and student are required'}), 400
    
    asn = Assignment.query.get_or_404(assignment_id)
    student = Student.query.get_or_404(int(student_id))
    
    # Validate script upload
    script_files = request.files.getlist('script')
    if not script_files or not script_files[0].filename:
        return jsonify({'success': False, 'error': 'Please upload a script'}), 400
    
    script_pages = [f.read() for f in script_files if f.filename]
    
    # Delete existing submission
    existing = Submission.query.filter_by(student_id=student.id, assignment_id=assignment_id).first()
    if existing:
        db.session.delete(existing)
        db.session.flush()
    
    # Create new submission
    sub = Submission(
        student_id=student.id,
        assignment_id=assignment_id,
        status='pending',
    )
    sub.set_script_pages(script_pages)
    db.session.add(sub)
    db.session.commit()
    
    # Start marking in background
    thread = threading.Thread(
        target=_run_submission_marking,
        args=(app, sub.id, assignment_id),
        daemon=True,
    )
    thread.start()
    
    return jsonify({'success': True, 'submission_id': sub.id, 'assignment_id': assignment_id})
```

**Step 4: Commit**
```bash
git add app.py templates/index.html
git commit -m "feat: single marking now uses class -> assignment -> student flow"
```

---

### Task 6: Refactor bulk marking with override/skip logic

**Files:**
- Modify: `app.py:1061-1215` (`bulk_mark` route and `run_bulk_marking_job`)
- Modify: `app.py:953-968` (`_split_pdf_variable`)
- Modify: `templates/class.html`

**Step 1: Update bulk_mark to use class students**

Remove class_list upload from bulk marking. Instead, require `class_id` and `assignment_id`. Students come from the class. Variable page counts array includes 0 for skip.

```python
@app.route('/bulk/mark', methods=['POST'])
def bulk_mark():
    if not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    assignment_id = request.form.get('assignment_id')
    if not assignment_id:
        return jsonify({'success': False, 'error': 'Assignment is required'}), 400
    
    asn = Assignment.query.get_or_404(assignment_id)
    students = _sort_by_index(Student.query.filter_by(class_id=asn.class_id).all())
    
    # Parse page counts (one per student, 0 = skip)
    page_counts_json = request.form.get('page_counts', '')
    page_counts = json.loads(page_counts_json)
    
    if len(page_counts) != len(students):
        return jsonify({'success': False, 'error': f'Page counts ({len(page_counts)}) != students ({len(students)})'}), 400
    
    # Build list of students to mark (skip those with page_count=0)
    students_to_mark = []
    page_counts_to_split = []
    for student, pc in zip(students, page_counts):
        if pc > 0:
            students_to_mark.append({'index': student.index_number, 'name': student.name, 'db_id': student.id})
            page_counts_to_split.append(pc)
    
    if not students_to_mark:
        return jsonify({'success': False, 'error': 'All students are set to skip (0 pages)'}), 400
    
    # Split PDF using only non-zero page counts
    bulk_pdf = request.files.get('bulk_scripts').read()
    student_scripts, pdf_total = _split_pdf_variable(bulk_pdf, page_counts_to_split)
    
    if sum(page_counts_to_split) != pdf_total:
        return jsonify({'success': False, 'error': f'Allocated pages ({sum(page_counts_to_split)}) != PDF pages ({pdf_total})'}), 400
    
    # ... spawn bulk marking thread with students_to_mark and student_scripts
```

**Step 2: Update `run_bulk_marking_job` to handle overrides**

Delete existing submissions for students being re-marked (before spawning thread). Skip students with page_count=0.

**Step 3: Update class.html bulk marking tab**

- Remove class_list upload
- Add class and assignment dropdowns
- Show student list with page count inputs (default to pages_per_student, editable to 0 for skip)
- Show existing submission status next to each student

**Step 4: Commit**
```bash
git add app.py templates/class.html
git commit -m "feat: bulk marking uses class students, variable page counts, 0=skip"
```

---

### Task 7: Demo mode — restricted single marking with 3 models

**Files:**
- Modify: `app.py:209-220` (hub route)
- Modify: `app.py:229-241` (single_mark_page)
- Modify: `app.py:317-371` (mark POST)
- Modify: `templates/hub.html`
- Modify: `templates/index.html`

**Step 1: Define demo models constant**

In `app.py` after mode vars:
```python
DEMO_MODELS = {
    'anthropic': {'claude-haiku-4-5-20251001': 'Claude Haiku 4.5'},
    'openai': {'gpt-5.4-mini': 'GPT-5.4 Mini'},
    'qwen': {'qwen3.5-plus-2026-02-15': 'Qwen 3.5 Plus'},
}
```

**Step 2: Demo hub layout**

In hub.html, when `demo_mode` and not `dept_mode`:
- Show "Try AI Marking" card → links to `/mark` (demo single marking)
- Show "Explore Features" card → links to `/class` (session-only class/assignment creation, no actual marking)

**Step 3: Demo single marking page**

When `DEMO_MODE`:
- Show standalone upload form (question paper, answer key/rubrics, script) — no class/assignment/student selection needed
- Provider/model dropdowns restricted to `DEMO_MODELS`
- No bulk marking available
- Results displayed inline, downloadable as PDF
- All in-memory (no DB persistence)

**Step 4: Demo class/assignment exploration**

When `DEMO_MODE` on `/class`:
- Class/assignment creation UI works but stores in session only (no `db.session.add`)
- "Mark" buttons disabled with tooltip "Marking disabled in demo mode"
- Student submission links show "Disabled in demo mode"

**Step 5: Commit**
```bash
git add app.py templates/hub.html templates/index.html templates/class.html
git commit -m "feat: demo mode with 3 restricted models, session-only class creation"
```

---

### Task 8: Demo + Department mode — seed data and showcase

**Files:**
- Create: `seed_data.py`
- Modify: `app.py` (load seed data when DEMO+DEPT)

**Step 1: Create `seed_data.py`**

Module that generates fake classes, teachers, assignments, and student results:

```python
import random
import uuid
from datetime import datetime, timezone, timedelta

def generate_seed_data():
    """Return seed data dicts for demo+dept mode."""
    classes = [
        {'id': str(uuid.uuid4()), 'name': 'Sec 3A', 'level': 'Mathematics'},
        {'id': str(uuid.uuid4()), 'name': 'Sec 3B', 'level': 'Mathematics'},
        {'id': str(uuid.uuid4()), 'name': 'Sec 4A', 'level': 'Mathematics'},
    ]
    
    teachers = [
        {'id': str(uuid.uuid4()), 'name': 'Ms. Chen', 'code': 'DEMO0001', 'role': 'teacher'},
        {'id': str(uuid.uuid4()), 'name': 'Mr. Rahman', 'code': 'DEMO0002', 'role': 'teacher'},
        {'id': str(uuid.uuid4()), 'name': 'Ms. Tan', 'code': 'DEMO0003', 'role': 'teacher'},
    ]
    
    # Teacher-class assignments
    teacher_classes = [
        (teachers[0]['id'], classes[0]['id']),  # Chen -> 3A
        (teachers[0]['id'], classes[1]['id']),  # Chen -> 3B
        (teachers[1]['id'], classes[2]['id']),  # Rahman -> 4A
        (teachers[2]['id'], classes[1]['id']),  # Tan -> 3B
        (teachers[2]['id'], classes[2]['id']),  # Tan -> 4A
    ]
    
    # Generate students per class
    students_per_class = {
        classes[0]['id']: _generate_students(15),
        classes[1]['id']: _generate_students(18),
        classes[2]['id']: _generate_students(12),
    }
    
    # Generate assignments with fake results
    assignments = []
    for cls in classes:
        for title in ['Mid-Year Exam', 'Quiz 3']:
            asn_id = str(uuid.uuid4())
            asn = {
                'id': asn_id,
                'title': title,
                'subject': 'Mathematics',
                'class_id': cls['id'],
                'assign_type': 'short_answer',
                'scoring_mode': 'marks',
                'total_marks': '50',
                'provider': 'anthropic',
                'model': 'claude-haiku-4-5-20251001',
            }
            assignments.append(asn)
    
    # Generate fake results with realistic distribution
    submissions = []
    for asn in assignments:
        class_students = students_per_class[asn['class_id']]
        for student in class_students:
            if random.random() < 0.9:  # 90% submission rate
                score_pct = max(0, min(100, random.gauss(75, 12)))
                submissions.append({
                    'student_id': student['id'],
                    'assignment_id': asn['id'],
                    'status': 'done',
                    'result': _generate_fake_result(score_pct, num_questions=8 if 'Exam' in asn['title'] else 5),
                })
    
    return {
        'classes': classes,
        'teachers': teachers,
        'teacher_classes': teacher_classes,
        'students_per_class': students_per_class,
        'assignments': assignments,
        'submissions': submissions,
    }

def _generate_students(count):
    """Generate fake student records."""
    first_names = ['Alex', 'Jordan', 'Sam', 'Casey', 'Riley', 'Morgan', 'Taylor', 'Drew',
                   'Avery', 'Quinn', 'Blake', 'Cameron', 'Dakota', 'Emery', 'Finley',
                   'Harper', 'Jamie', 'Kendall', 'Logan', 'Peyton']
    last_names = ['Lim', 'Tan', 'Ng', 'Lee', 'Wong', 'Chen', 'Goh', 'Chua',
                  'Ong', 'Koh', 'Teo', 'Sim', 'Ho', 'Yeo', 'Poh',
                  'Foo', 'Soh', 'Toh', 'Ang', 'Wee']
    students = []
    for i in range(count):
        students.append({
            'id': i + 1,
            'index_number': str(i + 1).zfill(2),
            'name': f'{random.choice(first_names)} {random.choice(last_names)}',
        })
    return students

def _generate_fake_result(score_pct, num_questions):
    """Generate a fake marking result with realistic question data."""
    total_marks = num_questions * 5
    target_score = score_pct / 100 * total_marks
    
    questions = []
    remaining = target_score
    for i in range(num_questions):
        max_marks = 5
        if i < num_questions - 1:
            awarded = max(0, min(max_marks, round(remaining / (num_questions - i) + random.gauss(0, 0.8))))
        else:
            awarded = max(0, min(max_marks, round(remaining)))
        remaining -= awarded
        
        status = 'correct' if awarded == max_marks else 'partial' if awarded > 0 else 'incorrect'
        questions.append({
            'question_number': str(i + 1),
            'marks_awarded': awarded,
            'marks_total': max_marks,
            'status': status,
            'feedback': f'Question {i+1} feedback placeholder.',
            'recommended_action': 'Review this topic.' if status != 'correct' else 'Well done.',
        })
    
    return {'questions': questions}
```

**Step 2: Load seed data on startup when DEMO+DEPT**

In `app.py`, after `init_db(app)`:
```python
if DEMO_MODE and DEPT_MODE:
    from seed_data import generate_seed_data
    _seed = generate_seed_data()
    # Store in app config for use by routes
    app.config['DEMO_SEED'] = _seed
```

**Step 3: Wire insights and department routes to use seed data**

When `DEMO_MODE and DEPT_MODE`, department routes serve data from `app.config['DEMO_SEED']` instead of DB queries. The insights data endpoint returns computed stats from the seed data.

**Step 4: Commit**
```bash
git add seed_data.py app.py
git commit -m "feat: demo+dept mode with pre-seeded fake data for HOD showcase"
```

---

### Task 9: LaTeX/MathJax rendering on all feedback surfaces

**Files:**
- Modify: `templates/base.html` (add MathJax CDN)
- Modify: `templates/index.html` (trigger MathJax after results render)
- Modify: `templates/submit.html` (trigger MathJax after results render)
- Modify: `templates/teacher_detail.html` (trigger MathJax for result display)

**Step 1: Add MathJax to base.html**

In `templates/base.html`, add before closing `</head>`:
```html
<script>
MathJax = {
    tex: { inlineMath: [['$', '$'], ['\\(', '\\)']], displayMath: [['$$', '$$'], ['\\[', '\\]']] },
    startup: { typeset: false }
};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js" async></script>
```

**Step 2: Add MathJax typeset calls after dynamic content**

In every template that renders AI feedback results dynamically (via JavaScript), call `MathJax.typesetPromise()` after inserting HTML:

```javascript
// After inserting result HTML into the DOM:
if (window.MathJax && MathJax.typesetPromise) {
    MathJax.typesetPromise();
}
```

Add this to:
- `index.html` — after `displayResults()` function renders questions
- `submit.html` — after student results are displayed
- `teacher_detail.html` — after any result expansion/modal

**Step 3: Commit**
```bash
git add templates/base.html templates/index.html templates/submit.html templates/teacher_detail.html
git commit -m "feat: enable MathJax LaTeX rendering on all feedback surfaces"
```

---

### Task 10: Critical security fixes

**Files:**
- Modify: `app.py:1-10` (imports — add `secrets`, `threading`)
- Modify: `app.py:68-79` (rate limiter)
- Modify: `app.py:489-495` (`_generate_teacher_code`)
- Modify: `app.py:1283-1289` (`_generate_classroom_code`)
- Modify: `app.py:631-641` (`dept_unassign_teacher`)
- Modify: `app.py:1470-1619` (teacher assignment routes — add ownership checks)
- Modify: `app.py:1722-1724` (debug mode)
- Modify: `templates/submit.html:348` (escape dotLabel)
- Modify: `templates/index.html:436` (escape dotLabel)

**Step 1: Use `secrets` for code generation**

Replace `import random` usage for codes:
```python
import secrets

def _generate_teacher_code():
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(secrets.choice(chars) for _ in range(8))
        if not Teacher.query.filter_by(code=code).first():
            return code

def _generate_classroom_code():
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(secrets.choice(chars) for _ in range(6))
        if not Assignment.query.filter_by(classroom_code=code).first():
            return code
```

**Step 2: Add thread lock to rate limiter**

```python
import threading
_rate_lock = threading.Lock()

def _check_rate_limit(key):
    with _rate_lock:
        now = time.time()
        _rate_limits.setdefault(key, [])
        _rate_limits[key] = [t for t in _rate_limits[key] if now - t < 60]
        if len(_rate_limits[key]) >= 10:
            return False
        _rate_limits[key].append(now)
        return True
```

**Step 3: Validate teacher_id in unassign route**

```python
@app.route('/department/class/<class_id>/unassign', methods=['POST'])
def dept_unassign_teacher(class_id):
    err = _require_hod()
    if err:
        return err
    data = request.get_json()
    teacher_id = data.get('teacher_id')
    if not teacher_id:
        return jsonify({'success': False, 'error': 'Teacher ID required'}), 400
    Teacher.query.get_or_404(teacher_id)
    TeacherClass.query.filter_by(teacher_id=teacher_id, class_id=class_id).delete()
    db.session.commit()
    return jsonify({'success': True})
```

**Step 4: Add ownership checks to teacher assignment routes**

Add helper:
```python
def _check_assignment_ownership(asn):
    """Return error response if current user doesn't own this assignment, or None if OK."""
    teacher = _current_teacher()
    if not teacher:
        # Non-dept mode — check _is_authenticated only
        if not _is_authenticated():
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401
        return None
    # HOD can access all
    if teacher.role == 'hod':
        return None
    if asn.teacher_id != teacher.id:
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    return None
```

Apply to: `teacher_assignment_detail`, `teacher_download_all`, `teacher_overview`, `teacher_submit_for_student`, `teacher_delete_assignment`.

**Step 5: Replace all `session.get('authenticated')` with `_is_authenticated()`**

Search-and-replace across all routes in app.py. Affected routes:
- `/mark` POST (line 319)
- `/status/<job_id>` (line 376)
- `/download/<job_id>` (line 397)
- `/save-keys` (line 296)
- `/teacher/create` (line 1346)
- `/teacher/assignment/*` (lines 1472, 1511, 1537, 1571, 1613)

**Step 6: Fix debug mode**

```python
if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true')
```

**Step 7: Escape dotLabel and marks in templates**

In `templates/submit.html` and `templates/index.html`, wrap `dotLabel` with `esc()`:
```javascript
dots += '<div class="q-dot ' + esc(s) + '" ...>' + esc(String(dotLabel)) + '</div>';
```

And for marks badges:
```javascript
`<span class="status-badge status-${esc(s)}">${esc(String(q.marks_awarded))}/${esc(String(q.marks_total || '?'))}</span>`
```

**Step 8: Add db.session.rollback() to background thread error handlers**

In `_run_submission_marking` and `run_bulk_marking_job`, add `db.session.rollback()` before error-path commits.

**Step 9: Add FK indexes**

In `db.py`, add `index=True` to:
- `Student.assignment_id`
- `Submission.assignment_id`
- `Submission.student_id`
- `Assignment.teacher_id`
- `Assignment.class_id`

**Step 10: Commit**
```bash
git add app.py db.py templates/submit.html templates/index.html
git commit -m "fix: critical security fixes — ownership checks, secrets, escaping, debug mode"
```

---

### Task 11: HOD teacher revoke and purge

**Files:**
- Modify: `db.py:73-80` (Teacher model — add `is_active`)
- Modify: `app.py` (add revoke/purge routes)
- Modify: `templates/department_manage.html`

**Step 1: Add `is_active` to Teacher model**

```python
class Teacher(db.Model):
    ...
    is_active = db.Column(db.Boolean, default=True)
```

Add migration in `_migrate_add_columns`:
```python
if 'teachers' in inspector.get_table_names():
    columns = [c['name'] for c in inspector.get_columns('teachers')]
    if 'is_active' not in columns:
        db.session.execute(text("ALTER TABLE teachers ADD COLUMN is_active BOOLEAN DEFAULT 1"))
        db.session.commit()
```

**Step 2: Add revoke route**

```python
@app.route('/department/teacher/<teacher_id>/revoke', methods=['POST'])
def dept_revoke_teacher(teacher_id):
    err = _require_hod()
    if err:
        return err
    t = Teacher.query.get_or_404(teacher_id)
    if t.role == 'hod':
        return jsonify({'success': False, 'error': 'Cannot revoke HOD'}), 400
    t.is_active = False
    db.session.commit()
    return jsonify({'success': True})
```

**Step 3: Add purge route**

```python
@app.route('/department/teacher/<teacher_id>/purge', methods=['POST'])
def dept_purge_teacher(teacher_id):
    err = _require_hod()
    if err:
        return err
    t = Teacher.query.get_or_404(teacher_id)
    if t.role == 'hod':
        return jsonify({'success': False, 'error': 'Cannot purge HOD'}), 400
    data = request.get_json()
    keep_data = data.get('keep_data', False)
    if not keep_data:
        # Delete teacher's assignments and submissions
        assignments = Assignment.query.filter_by(teacher_id=t.id).all()
        for asn in assignments:
            Submission.query.filter_by(assignment_id=asn.id).delete()
            Student.query.filter_by(assignment_id=asn.id).delete()
            db.session.delete(asn)
    TeacherClass.query.filter_by(teacher_id=t.id).delete()
    db.session.delete(t)
    db.session.commit()
    return jsonify({'success': True})
```

**Step 4: Update verify_code to check is_active**

```python
if DEPT_MODE:
    teacher = Teacher.query.filter_by(code=code).first()
    if not teacher:
        return jsonify({'success': False, 'error': 'Invalid code'}), 401
    if not teacher.is_active:
        return jsonify({'success': False, 'error': 'Account has been deactivated. Contact your HOD.'}), 403
```

**Step 5: Update department_manage.html**

Add revoke/reactivate toggle and purge button (with confirmation dialog) to each teacher card.

**Step 6: Commit**
```bash
git add db.py app.py templates/department_manage.html
git commit -m "feat: HOD can revoke and purge teacher accounts"
```

---

### Task 12: Add `SESSION_COOKIE_SECURE` and fix N+1 queries

**Files:**
- Modify: `app.py:29-31` (session config)
- Modify: `app.py:428-464` (department_page — N+1 fix)
- Modify: `app.py:880-930` (teacher_dashboard — N+1 fix)
- Modify: `app.py:686-765` (insights_data — N+1 fix)
- Modify: `app.py:768-827` (export_csv — N+1 fix)

**Step 1: Add SESSION_COOKIE_SECURE**

```python
app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV') != 'development'
```

**Step 2: Fix N+1 in department_page**

Use `joinedload` for assignments and students:
```python
from sqlalchemy.orm import joinedload

classes = Class.query.options(
    joinedload(Class.assignments).joinedload(Assignment.submissions)
).order_by(Class.name).all()
```

**Step 3: Fix N+1 in teacher_dashboard**

Pre-load all assignments with submissions for the teacher's classes in one query.

**Step 4: Fix N+1 in insights_data and export_csv**

Join Assignment, Class, Student in a single query instead of per-submission lookups.

**Step 5: Commit**
```bash
git add app.py
git commit -m "fix: add SESSION_COOKIE_SECURE, fix N+1 queries on dashboard routes"
```

---

### Task 13: Update CLAUDE.md and env var documentation

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update env vars table**

Add `APP_TITLE`, `TEACHER_CODE`, update `ACCESS_CODE` as legacy, document `DEMO_MODE` + `DEPT_MODE` combinations.

**Step 2: Update architecture section**

Document the three modes, class-level student list, override logic, seed data module.

**Step 3: Commit**
```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with new env vars and algorithm flow"
```

---

## Execution Order & Dependencies

```
Task 1 (APP_TITLE)           — independent
Task 10 (Security fixes)     — independent
Task 9 (MathJax)            — independent

Task 2 (TEACHER_CODE auth)   — independent
Task 3 (Class-level students) — depends on Task 2
Task 4 (Assignment requires class) — depends on Task 3
Task 5 (Single marking flow)  — depends on Task 4
Task 6 (Bulk marking refactor) — depends on Task 4

Task 7 (Demo mode)           — depends on Task 5
Task 8 (Demo+Dept seed data) — depends on Task 7

Task 11 (Revoke/purge)       — depends on Task 10
Task 12 (N+1 + cookie)       — depends on Task 10

Task 13 (Docs)               — last
```

**Parallel waves:**
- Wave 1: Tasks 1, 2, 9, 10 (all independent)
- Wave 2: Task 3 (depends on 2)
- Wave 3: Tasks 4, 11 (depend on 3, 10)
- Wave 4: Tasks 5, 6, 12 (depend on 4, 10)
- Wave 5: Task 7 (depends on 5)
- Wave 6: Task 8 (depends on 7)
- Wave 7: Task 13 (last)
