# Department Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a department deployment mode (`DEPT_MODE=TRUE`) where an HOD manages classes, assigns teachers, and monitors marking — while teachers work within their assigned classes only.

**Architecture:** New models (Teacher, Class, TeacherClass, DepartmentConfig) in `db.py`. Auth refactored to look up personal codes in dept mode. New templates for department dashboard, teacher dashboard, and class/teacher management. All gated behind `DEPT_MODE` env var — zero impact on default mode.

**Tech Stack:** Flask, SQLAlchemy, Jinja2, inline CSS/JS (matches existing patterns)

---

### Task 1: Add New Database Models

**Files:**
- Modify: `db.py:65-162`

**Step 1: Add Teacher, Class, TeacherClass, DepartmentConfig models to `db.py`**

Add after the existing imports and before `class Assignment`:

```python
class Teacher(db.Model):
    __tablename__ = 'teachers'

    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    role = db.Column(db.String(10), default='teacher')  # 'hod' or 'teacher'
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    classes = db.relationship('Class', secondary='teacher_classes', back_populates='teachers')


class Class(db.Model):
    __tablename__ = 'classes'

    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    level = db.Column(db.String(100), default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    teachers = db.relationship('Teacher', secondary='teacher_classes', back_populates='classes')
    assignments = db.relationship('Assignment', backref='dept_class', lazy=True)


class TeacherClass(db.Model):
    __tablename__ = 'teacher_classes'

    teacher_id = db.Column(db.String(36), db.ForeignKey('teachers.id'), primary_key=True)
    class_id = db.Column(db.String(36), db.ForeignKey('classes.id'), primary_key=True)


class DepartmentConfig(db.Model):
    __tablename__ = 'department_config'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, default='')
```

**Step 2: Add foreign keys to Assignment model**

Add two new columns to `class Assignment` after the existing fields:

```python
    class_id = db.Column(db.String(36), db.ForeignKey('classes.id'), nullable=True)
    teacher_id = db.Column(db.String(36), db.ForeignKey('teachers.id'), nullable=True)
```

**Step 3: Update `_migrate_add_columns` to handle new FKs on existing DBs**

Add migration logic for `class_id` and `teacher_id` columns on `assignments` table:

```python
        if 'assignments' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('assignments')]
            # ... existing title migration ...
            if 'class_id' not in columns:
                db.session.execute(text("ALTER TABLE assignments ADD COLUMN class_id VARCHAR(36)"))
                db.session.commit()
                logger.info('Added class_id column to assignments table')
            if 'teacher_id' not in columns:
                db.session.execute(text("ALTER TABLE assignments ADD COLUMN teacher_id VARCHAR(36)"))
                db.session.commit()
                logger.info('Added teacher_id column to assignments table')
```

**Step 4: Commit**

```bash
git add db.py
git commit -m "feat: add Teacher, Class, TeacherClass, DepartmentConfig models for dept mode"
```

---

### Task 2: Auth Refactor for Department Mode

**Files:**
- Modify: `app.py:1-17` (imports)
- Modify: `app.py:33-34` (env vars)
- Modify: `app.py:84-103` (auth functions)
- Modify: `app.py:174-183` (verify-code route)

**Step 1: Add `DEPT_MODE` env var and import new models**

In `app.py`, add after line 34 (`DEMO_MODE = ...`):

```python
DEPT_MODE = os.getenv('DEPT_MODE', 'FALSE').upper() == 'TRUE'
```

Update the import on line 17:

```python
from db import db, init_db, Assignment, Student, Submission, Teacher, Class, TeacherClass, DepartmentConfig
```

**Step 2: Refactor `_is_authenticated()` and add dept-mode helpers**

Replace `_is_authenticated` and add new helpers:

```python
def _is_authenticated():
    """Check if user is authenticated."""
    if DEPT_MODE:
        return session.get('teacher_id') is not None
    if not ACCESS_CODE:
        return True
    return session.get('authenticated', False)


def _current_teacher():
    """Get the currently logged-in teacher (dept mode only). Returns None if not logged in."""
    if not DEPT_MODE:
        return None
    teacher_id = session.get('teacher_id')
    if not teacher_id:
        return None
    return Teacher.query.get(teacher_id)


def _is_hod():
    """Check if current user is HOD."""
    teacher = _current_teacher()
    return teacher and teacher.role == 'hod'


def _require_hod():
    """Return error response if not HOD, or None if OK."""
    if not DEPT_MODE or not _is_authenticated():
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    if not _is_hod():
        return jsonify({'success': False, 'error': 'HOD access required'}), 403
    return None
```

**Step 3: Update `/verify-code` to handle dept mode login**

```python
@app.route('/verify-code', methods=['POST'])
def verify_code():
    if not _check_rate_limit(f'verify:{request.remote_addr}'):
        return jsonify({'success': False, 'error': 'Too many attempts. Please wait.'}), 429
    data = request.get_json()
    code = (data.get('code') or '').strip()

    if DEPT_MODE:
        teacher = Teacher.query.filter_by(code=code).first()
        if not teacher:
            return jsonify({'success': False, 'error': 'Invalid code'}), 401
        session['teacher_id'] = teacher.id
        session['teacher_role'] = teacher.role
        session['teacher_name'] = teacher.name
        redirect_url = '/department' if teacher.role == 'hod' else '/dashboard'
        return jsonify({'success': True, 'redirect': redirect_url})

    if code == ACCESS_CODE:
        session['authenticated'] = True
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Invalid access code'}), 401
```

**Step 4: Commit**

```bash
git add app.py
git commit -m "feat: refactor auth for dept mode with per-teacher codes"
```

---

### Task 3: Hub Page — Department Mode Variant

**Files:**
- Modify: `app.py:140-143` (hub route)
- Modify: `templates/hub.html`

**Step 1: Update hub route to pass dept mode context**

```python
@app.route('/')
def hub():
    authenticated = _is_authenticated()
    teacher = _current_teacher() if DEPT_MODE else None
    return render_template('hub.html',
                           authenticated=authenticated,
                           dept_mode=DEPT_MODE,
                           demo_mode=DEMO_MODE,
                           teacher=teacher)
```

**Step 2: Update `hub.html` to show dept-mode cards**

Replace the hub-cards section (lines 40-64) with conditional content:

```html
{% if not authenticated %}
{% set gate_title = 'AI Marking Demo' if not dept_mode else 'Department Login' %}
{% include '_gate.html' %}

{% else %}
<div class="hub">
    <div class="hub-container">
        {% if dept_mode and teacher %}
        <h1>{{ teacher.name }}</h1>
        <p class="subtitle">{{ 'Head of Department' if teacher.role == 'hod' else 'Teacher' }}</p>

        <div class="hub-cards">
            {% if teacher.role == 'hod' %}
            <a class="hub-card" href="/department">
                <div class="icon">&#127979;</div>
                <h2>Department</h2>
                <p>Overview of all classes, teachers, assignments, and student performance across the department.</p>
                <span class="tag">Dashboard</span>
            </a>
            <a class="hub-card" href="/department/classes">
                <div class="icon">&#9881;</div>
                <h2>Manage</h2>
                <p>Create classes, add teachers, assign teachers to classes, and configure API keys.</p>
                <span class="tag">Setup</span>
            </a>
            {% else %}
            <a class="hub-card" href="/dashboard">
                <div class="icon">&#128203;</div>
                <h2>My Dashboard</h2>
                <p>View your assigned classes, create assignments, and monitor student submissions.</p>
                <span class="tag">Your classes</span>
            </a>
            {% endif %}
            <a class="hub-card" href="/class">
                <div class="icon">&#128218;</div>
                <h2>Mark a Class</h2>
                <p>Upload all scripts in one PDF, or create an assignment link and let students submit their own work.</p>
                <span class="tag">Bulk &amp; submissions</span>
            </a>
        </div>

        <div class="hub-footer">
            <a href="/logout" style="color:#667eea; text-decoration:none;">Logout</a>
        </div>

        {% else %}
        <h1>AI Marking Demo</h1>
        <p class="subtitle">Choose how you'd like to mark</p>

        <div class="hub-cards">
            <a class="hub-card" href="/mark">
                <div class="icon">&#128221;</div>
                <h2>Mark a Script</h2>
                <p>Upload one student script with a question paper and answer key. Get instant AI feedback and a downloadable report.</p>
                <span class="tag">Quick &amp; simple</span>
            </a>
            <a class="hub-card" href="/class">
                <div class="icon">&#128218;</div>
                <h2>Mark a Class</h2>
                <p>Upload all scripts in one PDF, or create an assignment link and let students submit their own work.</p>
                <span class="tag">Bulk &amp; submissions</span>
            </a>
        </div>

        <div class="hub-footer">
            Powered by Anthropic Claude, OpenAI GPT &amp; Alibaba Qwen
        </div>
        {% endif %}
    </div>
</div>
{% endif %}
```

**Step 3: Add logout route in `app.py`**

```python
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('hub'))
```

**Step 4: Update gate JS to handle dept mode redirect**

In `_gate.html` or `common.js`, ensure `verifyAccessCode()` checks for a `redirect` field in the response and navigates there instead of reloading:

```javascript
// In the verifyAccessCode success handler:
if (data.redirect) {
    window.location.href = data.redirect;
} else {
    window.location.reload();
}
```

**Step 5: Hide `/mark` in dept mode**

In the `single_mark_page` route, add at the top:

```python
@app.route('/mark')
def single_mark_page():
    if DEPT_MODE:
        return redirect(url_for('hub'))
    # ... rest unchanged
```

**Step 6: Commit**

```bash
git add app.py templates/hub.html templates/_gate.html static/common.js
git commit -m "feat: hub page shows dept-mode cards based on teacher role"
```

---

### Task 4: HOD Department Dashboard (`/department`)

**Files:**
- Create: `templates/department.html`
- Modify: `app.py` (add route)

**Step 1: Add `/department` route in `app.py`**

```python
@app.route('/department')
def department_page():
    err = _require_hod()
    if err:
        return redirect(url_for('hub'))

    teacher = _current_teacher()
    classes = Class.query.order_by(Class.name).all()
    teachers = Teacher.query.filter_by(role='teacher').order_by(Teacher.name).all()

    class_data = []
    for cls in classes:
        assignments = Assignment.query.filter_by(class_id=cls.id).all()
        total_submissions = 0
        total_students = 0
        done_submissions = 0
        for asn in assignments:
            students = Student.query.filter_by(assignment_id=asn.id).all()
            total_students += len(students)
            subs = Submission.query.filter_by(assignment_id=asn.id).all()
            total_submissions += len(subs)
            done_submissions += sum(1 for s in subs if s.status == 'done')

        class_data.append({
            'id': cls.id,
            'name': cls.name,
            'level': cls.level,
            'teachers': [t.name for t in cls.teachers],
            'assignment_count': len(assignments),
            'total_students': total_students,
            'total_submissions': total_submissions,
            'done_submissions': done_submissions,
            'completion_pct': round(done_submissions / total_students * 100) if total_students > 0 else 0,
        })

    return render_template('department.html',
                           teacher=teacher,
                           classes=class_data,
                           total_teachers=len(teachers),
                           total_classes=len(classes),
                           dept_mode=DEPT_MODE,
                           demo_mode=DEMO_MODE)
```

**Step 2: Create `templates/department.html`**

Template extends `base.html`. Shows:
- Summary cards row: Total Classes, Total Teachers, Total Assignments, Total Submissions
- Table of classes with columns: Class, Level, Teacher(s), Assignments, Submissions, Completion %
- Each class name links to a filtered view of its assignments
- Navigation: back to hub, link to `/department/classes` (manage), link to `/department/insights`
- If `DEMO_MODE`: show banner "Insights disabled in demo mode" on insights link

Full template with inline CSS following the existing card/table patterns in the codebase. Use the same color scheme (#667eea primary, white cards, #f5f5f7 background).

**Step 3: Commit**

```bash
git add app.py templates/department.html
git commit -m "feat: HOD department overview dashboard"
```

---

### Task 5: Class & Teacher Management (`/department/classes`)

**Files:**
- Create: `templates/department_manage.html`
- Modify: `app.py` (add CRUD routes)

**Step 1: Add management routes in `app.py`**

```python
@app.route('/department/classes')
def department_manage():
    err = _require_hod()
    if err:
        return redirect(url_for('hub'))

    teacher = _current_teacher()
    classes = Class.query.order_by(Class.name).all()
    teachers = Teacher.query.order_by(Teacher.role.desc(), Teacher.name).all()

    return render_template('department_manage.html',
                           teacher=teacher,
                           classes=classes,
                           teachers=teachers,
                           dept_mode=DEPT_MODE,
                           demo_mode=DEMO_MODE)


def _generate_teacher_code():
    """Generate a unique 8-char teacher code."""
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(chars, k=8))
        if not Teacher.query.filter_by(code=code).first():
            return code


@app.route('/department/teacher/create', methods=['POST'])
def dept_create_teacher():
    err = _require_hod()
    if err:
        return err

    data = request.get_json()
    name = (data.get('name') or '').strip()
    role = data.get('role', 'teacher')
    if not name:
        return jsonify({'success': False, 'error': 'Name is required'}), 400
    if role not in ('teacher', 'hod'):
        return jsonify({'success': False, 'error': 'Invalid role'}), 400

    teacher = Teacher(
        id=str(uuid.uuid4()),
        name=name,
        code=_generate_teacher_code(),
        role=role,
    )
    db.session.add(teacher)
    db.session.commit()

    return jsonify({'success': True, 'teacher': {
        'id': teacher.id, 'name': teacher.name,
        'code': teacher.code, 'role': teacher.role,
    }})


@app.route('/department/teacher/<teacher_id>/delete', methods=['POST'])
def dept_delete_teacher(teacher_id):
    err = _require_hod()
    if err:
        return err

    teacher = Teacher.query.get_or_404(teacher_id)
    if teacher.role == 'hod':
        return jsonify({'success': False, 'error': 'Cannot delete HOD'}), 400
    db.session.delete(teacher)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/department/class/create', methods=['POST'])
def dept_create_class():
    err = _require_hod()
    if err:
        return err

    data = request.get_json()
    name = (data.get('name') or '').strip()
    level = (data.get('level') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Class name is required'}), 400

    cls = Class(id=str(uuid.uuid4()), name=name, level=level)
    db.session.add(cls)
    db.session.commit()

    return jsonify({'success': True, 'class': {
        'id': cls.id, 'name': cls.name, 'level': cls.level,
    }})


@app.route('/department/class/<class_id>/delete', methods=['POST'])
def dept_delete_class(class_id):
    err = _require_hod()
    if err:
        return err

    cls = Class.query.get_or_404(class_id)
    # Remove teacher associations
    TeacherClass.query.filter_by(class_id=class_id).delete()
    db.session.delete(cls)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/department/class/<class_id>/assign', methods=['POST'])
def dept_assign_teacher(class_id):
    err = _require_hod()
    if err:
        return err

    data = request.get_json()
    teacher_id = data.get('teacher_id')
    if not teacher_id:
        return jsonify({'success': False, 'error': 'Teacher ID required'}), 400

    cls = Class.query.get_or_404(class_id)
    teacher = Teacher.query.get_or_404(teacher_id)

    existing = TeacherClass.query.filter_by(teacher_id=teacher_id, class_id=class_id).first()
    if not existing:
        db.session.add(TeacherClass(teacher_id=teacher_id, class_id=class_id))
        db.session.commit()

    return jsonify({'success': True})


@app.route('/department/class/<class_id>/unassign', methods=['POST'])
def dept_unassign_teacher(class_id):
    err = _require_hod()
    if err:
        return err

    data = request.get_json()
    teacher_id = data.get('teacher_id')
    TeacherClass.query.filter_by(teacher_id=teacher_id, class_id=class_id).delete()
    db.session.commit()
    return jsonify({'success': True})
```

**Step 2: Add API key management routes**

```python
@app.route('/department/keys', methods=['POST'])
def dept_save_keys():
    err = _require_hod()
    if err:
        return err

    data = request.get_json()
    for prov in ('anthropic', 'openai', 'qwen'):
        val = (data.get(prov) or '').strip()
        cfg = DepartmentConfig.query.filter_by(key=f'api_key_{prov}').first()
        if val:
            if not cfg:
                cfg = DepartmentConfig(key=f'api_key_{prov}')
                db.session.add(cfg)
            # Encrypt if possible
            f = _get_fernet()
            cfg.value = f.encrypt(val.encode()).decode() if f else val
        elif cfg:
            db.session.delete(cfg)
    db.session.commit()
    return jsonify({'success': True})
```

Import `_get_fernet` from db or inline the helper.

**Step 3: Create `templates/department_manage.html`**

Two-column layout:
- Left: Classes list with add form, each class shows assigned teachers with unassign button, assign-teacher dropdown
- Right: Teachers list with add form, each shows name/role/code, delete button
- Bottom section: API key configuration (3 inputs for anthropic/openai/qwen)

Follow existing card styling patterns.

**Step 4: Commit**

```bash
git add app.py templates/department_manage.html
git commit -m "feat: HOD class and teacher management with API key config"
```

---

### Task 6: Teacher Dashboard (`/dashboard`)

**Files:**
- Create: `templates/dashboard.html`
- Modify: `app.py` (add route)

**Step 1: Add `/dashboard` route**

```python
@app.route('/dashboard')
def teacher_dashboard():
    if not DEPT_MODE or not _is_authenticated():
        return redirect(url_for('hub'))

    teacher = _current_teacher()
    if not teacher or teacher.role == 'hod':
        return redirect(url_for('hub'))

    # Get teacher's assigned classes
    class_data = []
    for cls in teacher.classes:
        assignments = Assignment.query.filter_by(class_id=cls.id, teacher_id=teacher.id).all()
        asn_data = []
        for asn in assignments:
            students = Student.query.filter_by(assignment_id=asn.id).all()
            subs = Submission.query.filter_by(assignment_id=asn.id).all()
            done = [s for s in subs if s.status == 'done']

            avg_score = None
            if done:
                scores = []
                for s in done:
                    result = s.get_result()
                    qs = result.get('questions', [])
                    if qs:
                        has_marks = any(q.get('marks_awarded') is not None for q in qs)
                        if has_marks:
                            total_a = sum(q.get('marks_awarded', 0) for q in qs)
                            total_p = sum(q.get('marks_total', 0) for q in qs)
                            scores.append(total_a / total_p * 100 if total_p else 0)
                        else:
                            correct = sum(1 for q in qs if q.get('status') == 'correct')
                            scores.append(correct / len(qs) * 100 if qs else 0)
                if scores:
                    avg_score = round(sum(scores) / len(scores), 1)

            asn_data.append({
                'id': asn.id,
                'title': asn.title or asn.subject,
                'subject': asn.subject,
                'classroom_code': asn.classroom_code,
                'total_students': len(students),
                'submitted': len(subs),
                'done': len(done),
                'avg_score': avg_score,
                'created_at': asn.created_at,
            })

        class_data.append({
            'id': cls.id,
            'name': cls.name,
            'level': cls.level,
            'assignments': sorted(asn_data, key=lambda a: a['created_at'], reverse=True),
        })

    return render_template('dashboard.html',
                           teacher=teacher,
                           classes=class_data,
                           dept_mode=DEPT_MODE,
                           demo_mode=DEMO_MODE)
```

**Step 2: Create `templates/dashboard.html`**

Shows:
- Header with teacher name
- Per-class sections, each containing:
  - Class name + level
  - Assignment cards showing: title, classroom code, submission progress bar, avg score
  - Each card links to existing `/teacher/assignment/<id>` detail page
  - "Create Assignment" button per class
- If no classes assigned: "No classes assigned. Contact your HOD."

**Step 3: Commit**

```bash
git add app.py templates/dashboard.html
git commit -m "feat: teacher dashboard showing assigned classes and assignments"
```

---

### Task 7: Wire Up Assignment Creation in Dept Mode

**Files:**
- Modify: `app.py:642-742` (`teacher_create` route)
- Modify: `app.py:158-171` (`class_page` route)

**Step 1: Update `teacher_create` to set `class_id` and `teacher_id`**

In the `teacher_create` route, when creating the Assignment object, add:

```python
    # In dept mode, require class_id and set teacher_id
    if DEPT_MODE:
        class_id = request.form.get('class_id')
        if not class_id:
            return jsonify({'success': False, 'error': 'Class is required'}), 400
        teacher = _current_teacher()
        if not teacher:
            return jsonify({'success': False, 'error': 'Not authenticated'}), 401
        # Verify teacher is assigned to this class
        tc = TeacherClass.query.filter_by(teacher_id=teacher.id, class_id=class_id).first()
        if not tc and teacher.role != 'hod':
            return jsonify({'success': False, 'error': 'Not assigned to this class'}), 403
```

And add to the Assignment constructor:

```python
        class_id=class_id if DEPT_MODE else None,
        teacher_id=teacher.id if DEPT_MODE else None,
```

**Step 2: Update `class_page` to filter assignments by teacher in dept mode**

```python
@app.route('/class')
def class_page():
    authenticated = _is_authenticated()
    sk = _get_session_keys()
    providers = get_available_providers(session_keys=sk)
    assignments = []
    teacher = None

    if authenticated:
        if DEPT_MODE:
            teacher = _current_teacher()
            if teacher and teacher.role == 'hod':
                assignments = Assignment.query.order_by(Assignment.created_at.desc()).all()
            elif teacher:
                # Teacher sees only their assignments
                assignments = Assignment.query.filter_by(teacher_id=teacher.id)\
                    .order_by(Assignment.created_at.desc()).all()
        else:
            assignments = Assignment.query.order_by(Assignment.created_at.desc()).all()

    return render_template('class.html',
                           authenticated=authenticated,
                           providers=providers,
                           demo_mode=DEMO_MODE,
                           dept_mode=DEPT_MODE,
                           teacher=teacher,
                           all_providers=PROVIDERS,
                           assignments=assignments)
```

**Step 3: Update `class.html` to show class dropdown in dept mode**

In the create assignment form, add a class selector when `dept_mode` is true:

```html
{% if dept_mode and teacher %}
<div class="form-group">
    <label>Class</label>
    <select name="class_id" required>
        <option value="">Select a class...</option>
        {% for cls in teacher.classes %}
        <option value="{{ cls.id }}">{{ cls.name }}{% if cls.level %} ({{ cls.level }}){% endif %}</option>
        {% endfor %}
    </select>
</div>
{% endif %}
```

**Step 4: In dept mode, use department API keys as fallback**

Update `_effective_keys` or the assignment creation to check `DepartmentConfig` for API keys when in dept mode:

```python
def _get_dept_keys():
    """Get department-level API keys from DepartmentConfig."""
    if not DEPT_MODE:
        return {}
    keys = {}
    for prov in ('anthropic', 'openai', 'qwen'):
        cfg = DepartmentConfig.query.filter_by(key=f'api_key_{prov}').first()
        if cfg and cfg.value:
            f = _get_fernet()
            if f:
                try:
                    keys[prov] = f.decrypt(cfg.value.encode()).decode()
                    continue
                except Exception:
                    pass
            keys[prov] = cfg.value
    return keys
```

**Step 5: Commit**

```bash
git add app.py templates/class.html
git commit -m "feat: wire assignment creation to classes and teachers in dept mode"
```

---

### Task 8: HOD Insights Page (`/department/insights`)

**Files:**
- Create: `templates/department_insights.html`
- Modify: `app.py` (add routes)

**Step 1: Add insights routes**

```python
@app.route('/department/insights')
def department_insights():
    err = _require_hod()
    if err:
        return redirect(url_for('hub'))

    if DEMO_MODE:
        return render_template('department_insights.html',
                               teacher=_current_teacher(),
                               demo_mode=True, dept_mode=True,
                               classes=[], assignments=[], insights=None)

    teacher = _current_teacher()
    classes = Class.query.order_by(Class.name).all()
    assignments = Assignment.query.filter(Assignment.class_id.isnot(None)).order_by(Assignment.created_at.desc()).all()

    return render_template('department_insights.html',
                           teacher=teacher,
                           classes=classes,
                           assignments=assignments,
                           demo_mode=DEMO_MODE,
                           dept_mode=DEPT_MODE)


@app.route('/department/insights/data')
def department_insights_data():
    """API endpoint returning analytics data for charts."""
    err = _require_hod()
    if err:
        return err
    if DEMO_MODE:
        return jsonify({'success': False, 'error': 'Not available in demo mode'}), 403

    assignment_id = request.args.get('assignment_id')
    class_id = request.args.get('class_id')

    # Build query
    query = Submission.query.filter_by(status='done')
    if assignment_id:
        query = query.filter_by(assignment_id=assignment_id)

    submissions = query.all()

    # Compute per-class averages for comparison
    class_scores = {}
    question_stats = {}

    for sub in submissions:
        asn = Assignment.query.get(sub.assignment_id)
        if not asn or not asn.class_id:
            continue
        if class_id and asn.class_id != class_id:
            continue

        result = sub.get_result()
        questions = result.get('questions', [])
        if not questions:
            continue

        cls_name = Class.query.get(asn.class_id).name if asn.class_id else 'Unknown'
        has_marks = any(q.get('marks_awarded') is not None for q in questions)

        if has_marks:
            total_a = sum(q.get('marks_awarded', 0) for q in questions)
            total_p = sum(q.get('marks_total', 0) for q in questions)
            pct = (total_a / total_p * 100) if total_p > 0 else 0
        else:
            correct = sum(1 for q in questions if q.get('status') == 'correct')
            pct = (correct / len(questions) * 100) if questions else 0

        class_scores.setdefault(cls_name, []).append(pct)

        # Per-question stats
        for i, q in enumerate(questions):
            qnum = q.get('question_number', i + 1)
            question_stats.setdefault(qnum, {'correct': 0, 'total': 0})
            question_stats[qnum]['total'] += 1
            if q.get('status') == 'correct' or (q.get('marks_awarded', 0) == q.get('marks_total', 1)):
                question_stats[qnum]['correct'] += 1

    # Format response
    comparison = {name: round(sum(scores) / len(scores), 1)
                  for name, scores in class_scores.items()}

    all_scores = [s for scores in class_scores.values() for s in scores]
    distribution = {'0-20': 0, '21-40': 0, '41-60': 0, '61-80': 0, '81-100': 0}
    for s in all_scores:
        if s <= 20: distribution['0-20'] += 1
        elif s <= 40: distribution['21-40'] += 1
        elif s <= 60: distribution['41-60'] += 1
        elif s <= 80: distribution['61-80'] += 1
        else: distribution['81-100'] += 1

    q_difficulty = {str(qnum): round(stats['correct'] / stats['total'] * 100, 1) if stats['total'] else 0
                    for qnum, stats in sorted(question_stats.items())}

    return jsonify({
        'success': True,
        'class_comparison': comparison,
        'score_distribution': distribution,
        'question_difficulty': q_difficulty,
        'total_students': len(all_scores),
        'overall_avg': round(sum(all_scores) / len(all_scores), 1) if all_scores else 0,
    })
```

**Step 2: Add export routes**

```python
@app.route('/department/export/csv')
def department_export_csv():
    """Export results as CSV — filter by assignment_id and/or class_id."""
    err = _require_hod()
    if err:
        return err
    if DEMO_MODE:
        return jsonify({'success': False, 'error': 'Not available in demo mode'}), 403

    assignment_id = request.args.get('assignment_id')
    class_id = request.args.get('class_id')

    query = Submission.query.filter_by(status='done')
    if assignment_id:
        query = query.filter_by(assignment_id=assignment_id)

    submissions = query.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Class', 'Student Index', 'Student Name', 'Assignment', 'Score', 'Percentage'])

    for sub in submissions:
        asn = Assignment.query.get(sub.assignment_id)
        if not asn:
            continue
        if class_id and asn.class_id != class_id:
            continue

        student = Student.query.get(sub.student_id)
        if not student:
            continue

        cls = Class.query.get(asn.class_id) if asn.class_id else None
        result = sub.get_result()
        questions = result.get('questions', [])
        has_marks = any(q.get('marks_awarded') is not None for q in questions)

        if has_marks:
            ta = sum(q.get('marks_awarded', 0) for q in questions)
            tp = sum(q.get('marks_total', 0) for q in questions)
            score = f"{ta}/{tp}"
            pct = round(ta / tp * 100, 1) if tp else 0
        else:
            correct = sum(1 for q in questions if q.get('status') == 'correct')
            score = f"{correct}/{len(questions)}"
            pct = round(correct / len(questions) * 100, 1) if questions else 0

        writer.writerow([
            cls.name if cls else '',
            student.index_number,
            student.name,
            asn.title or asn.subject,
            score,
            f"{pct}%",
        ])

    buf = io.BytesIO(output.getvalue().encode('utf-8-sig'))
    return send_file(buf, mimetype='text/csv', as_attachment=True,
                     download_name='department_results.csv')
```

**Step 3: Create `templates/department_insights.html`**

Layout:
- If `demo_mode`: show "Insights are not available in demo mode" banner, return
- Filter bar: dropdown for assignment, dropdown for class, "Apply" button
- Summary row: total students, overall average, pass rate
- Charts section (using simple CSS bar charts or inline SVG — no external JS library needed):
  - Class comparison: horizontal bar chart
  - Score distribution: vertical bar chart (5 bins)
  - Question difficulty: horizontal bars showing % correct per question
- Export buttons: "Download CSV", "Download PDF Overview"

**Step 4: Commit**

```bash
git add app.py templates/department_insights.html
git commit -m "feat: HOD insights page with analytics and CSV export"
```

---

### Task 9: Department-Mode API Key Fallback

**Files:**
- Modify: `app.py` (update key resolution)

**Step 1: Update key resolution chain for dept mode**

When marking in dept mode, keys should resolve: assignment-stored keys → department config keys → env vars.

Add helper and update `_run_submission_marking`:

```python
def _resolve_api_keys(assignment):
    """Resolve API keys with fallback: assignment → department config → env vars."""
    keys = assignment.get_api_keys()
    if keys:
        return keys

    if DEPT_MODE:
        dept_keys = _get_dept_keys()
        if dept_keys:
            return dept_keys

    return None  # Will fall back to env vars in ai_marking.py
```

Update `_run_submission_marking` line 622:

```python
            session_keys=_resolve_api_keys(asn),
```

**Step 2: Update `teacher_create` to not require per-assignment keys in dept mode**

In dept mode, if dept keys are configured, don't require API keys in the form:

```python
    if DEPT_MODE:
        dept_keys = _get_dept_keys()
        if dept_keys:
            api_keys = dept_keys
```

**Step 3: Commit**

```bash
git add app.py
git commit -m "feat: dept mode API key resolution chain (assignment → dept config → env)"
```

---

### Task 10: First-Run HOD Setup

**Files:**
- Modify: `app.py` (add setup route)
- Create: `templates/department_setup.html`

**Step 1: Add first-run setup route**

When `DEPT_MODE=TRUE` and no HOD exists yet, show a setup page:

```python
@app.route('/department/setup', methods=['GET', 'POST'])
def department_setup():
    if not DEPT_MODE:
        return redirect(url_for('hub'))

    # If HOD already exists, redirect
    existing_hod = Teacher.query.filter_by(role='hod').first()
    if existing_hod:
        return redirect(url_for('hub'))

    if request.method == 'POST':
        data = request.get_json()
        name = (data.get('name') or '').strip()
        code = (data.get('code') or '').strip()
        if not name or not code:
            return jsonify({'success': False, 'error': 'Name and code are required'}), 400
        if len(code) < 4:
            return jsonify({'success': False, 'error': 'Code must be at least 4 characters'}), 400

        hod = Teacher(
            id=str(uuid.uuid4()),
            name=name,
            code=code,
            role='hod',
        )
        db.session.add(hod)
        db.session.commit()

        session['teacher_id'] = hod.id
        session['teacher_role'] = hod.role
        session['teacher_name'] = hod.name

        return jsonify({'success': True, 'redirect': '/department'})

    return render_template('department_setup.html')
```

Update hub route to redirect to setup if no HOD exists:

```python
@app.route('/')
def hub():
    if DEPT_MODE:
        if not Teacher.query.filter_by(role='hod').first():
            return redirect(url_for('department_setup'))
    # ... rest
```

**Step 2: Create `templates/department_setup.html`**

Simple centered card (like the access gate):
- Title: "Department Setup"
- Subtitle: "Create the Head of Department account"
- Fields: Name input, Access Code input
- Submit button
- After success, redirects to `/department`

**Step 3: Commit**

```bash
git add app.py templates/department_setup.html
git commit -m "feat: first-run HOD setup flow for dept mode"
```

---

### Task 11: Navigation & Base Template Updates

**Files:**
- Modify: `templates/base.html`
- Modify: `static/styles.css`

**Step 1: Add nav bar to base template for dept mode**

Add optional nav bar that shows when `dept_mode` and authenticated:

```html
{% if dept_mode and teacher %}
<nav class="dept-nav">
    <div class="dept-nav-inner">
        <a href="/" class="dept-nav-brand">AI Marking</a>
        <div class="dept-nav-links">
            {% if teacher.role == 'hod' %}
            <a href="/department">Dashboard</a>
            <a href="/department/classes">Manage</a>
            <a href="/department/insights">Insights</a>
            {% else %}
            <a href="/dashboard">My Classes</a>
            {% endif %}
            <a href="/class">Bulk Mark</a>
        </div>
        <div class="dept-nav-user">
            <span>{{ teacher.name }}</span>
            <a href="/logout">Logout</a>
        </div>
    </div>
</nav>
{% endif %}
```

**Step 2: Add nav CSS to `styles.css`**

```css
/* --- Department Nav --- */
.dept-nav { background: white; border-bottom: 2px solid #e0e0e0; padding: 0 20px; }
.dept-nav-inner { max-width: 1100px; margin: 0 auto; display: flex; align-items: center; height: 56px; gap: 32px; }
.dept-nav-brand { font-weight: 700; font-size: 16px; color: #667eea; text-decoration: none; }
.dept-nav-links { display: flex; gap: 20px; flex: 1; }
.dept-nav-links a { color: #666; text-decoration: none; font-size: 14px; font-weight: 500; }
.dept-nav-links a:hover { color: #667eea; }
.dept-nav-user { font-size: 13px; color: #888; display: flex; gap: 12px; align-items: center; }
.dept-nav-user a { color: #667eea; text-decoration: none; font-weight: 600; }
```

**Step 3: Commit**

```bash
git add templates/base.html static/styles.css
git commit -m "feat: department navigation bar in base template"
```

---

### Task 12: Integration Testing & Polish

**Step 1: Manual test — default mode unchanged**

Run the app without `DEPT_MODE`:
```bash
python app.py
```
- Verify hub shows "Mark a Script" and "Mark a Class" cards
- Verify access code gate works as before
- Verify no department UI is visible anywhere

**Step 2: Manual test — dept mode first run**

```bash
DEPT_MODE=TRUE python app.py
```
- Verify redirect to `/department/setup`
- Create HOD account
- Verify redirect to `/department`

**Step 3: Manual test — HOD flow**

- Create classes and teachers at `/department/classes`
- Assign teachers to classes
- Configure API keys
- View dashboard at `/department`

**Step 4: Manual test — teacher flow**

- Login with teacher code
- Verify hub shows "My Dashboard" card
- Verify `/mark` redirects to hub
- Create assignment within assigned class
- Verify assignment appears on teacher dashboard

**Step 5: Manual test — insights (non-demo)**

```bash
DEPT_MODE=TRUE python app.py
```
- After some assignments are marked, visit `/department/insights`
- Check class comparison, score distribution, question difficulty
- Export CSV

**Step 6: Manual test — demo + dept mode**

```bash
DEPT_MODE=TRUE DEMO_MODE=TRUE python app.py
```
- Verify insights page shows "Not available in demo mode"
- Verify class management UI is visible but functional

**Step 7: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration test fixes for dept mode"
```

---

## Summary

| Task | What | Files |
|------|------|-------|
| 1 | Database models | `db.py` |
| 2 | Auth refactor | `app.py` |
| 3 | Hub page variant | `app.py`, `templates/hub.html`, `common.js` |
| 4 | HOD dashboard | `app.py`, `templates/department.html` |
| 5 | Class/teacher CRUD | `app.py`, `templates/department_manage.html` |
| 6 | Teacher dashboard | `app.py`, `templates/dashboard.html` |
| 7 | Assignment wiring | `app.py`, `templates/class.html` |
| 8 | Insights & export | `app.py`, `templates/department_insights.html` |
| 9 | API key fallback | `app.py` |
| 10 | First-run setup | `app.py`, `templates/department_setup.html` |
| 11 | Nav & base template | `templates/base.html`, `static/styles.css` |
| 12 | Integration testing | All files |
