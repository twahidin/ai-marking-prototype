import os
import json
import base64
import hashlib
import logging
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

try:
    from cryptography.fernet import Fernet, InvalidToken
    FERNET_AVAILABLE = True
except ImportError:
    FERNET_AVAILABLE = False

logger = logging.getLogger(__name__)

db = SQLAlchemy()


def _get_fernet():
    """Derive a Fernet key from FLASK_SECRET_KEY for encrypting API keys at rest."""
    if not FERNET_AVAILABLE:
        return None
    key = os.getenv('FLASK_SECRET_KEY', '')
    if not key:
        return None
    derived = hashlib.sha256(key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def _migrate_add_columns(app):
    """Add missing columns to existing tables (create_all only creates new tables)."""
    from sqlalchemy import text, inspect
    with app.app_context():
        inspector = inspect(db.engine)
        if 'submissions' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('submissions')]
            if 'script_pages_json' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN script_pages_json TEXT'))
                db.session.commit()
                logger.info('Added script_pages_json column to submissions table')
        if 'students' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('students')]
            if 'class_id' not in columns:
                db.session.execute(text("ALTER TABLE students ADD COLUMN class_id VARCHAR(36)"))
                db.session.commit()
                logger.info('Added class_id column to students table')
            if 'assignment_id' not in columns:
                db.session.execute(text("ALTER TABLE students ADD COLUMN assignment_id VARCHAR(36)"))
                db.session.commit()
                logger.info('Added assignment_id column to students table')
            else:
                # Ensure assignment_id is nullable (students belong to classes, not assignments)
                col_info = next((c for c in inspector.get_columns('students') if c['name'] == 'assignment_id'), None)
                if col_info and not col_info.get('nullable', True):
                    db.session.execute(text("ALTER TABLE students ALTER COLUMN assignment_id DROP NOT NULL"))
                    db.session.commit()
                    logger.info('Made assignment_id nullable on students table')
        if 'teachers' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('teachers')]
            if 'is_active' not in columns:
                db.session.execute(text("ALTER TABLE teachers ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
                db.session.commit()
                logger.info('Added is_active column to teachers table')
        if 'assignments' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('assignments')]
            if 'title' not in columns:
                db.session.execute(text("ALTER TABLE assignments ADD COLUMN title VARCHAR(300) DEFAULT ''"))
                db.session.commit()
                logger.info('Added title column to assignments table')
            if 'class_id' not in columns:
                db.session.execute(text("ALTER TABLE assignments ADD COLUMN class_id VARCHAR(36)"))
                db.session.commit()
                logger.info('Added class_id column to assignments table')
            if 'teacher_id' not in columns:
                db.session.execute(text("ALTER TABLE assignments ADD COLUMN teacher_id VARCHAR(36)"))
                db.session.commit()
                logger.info('Added teacher_id column to assignments table')


def init_db(app):
    """Configure and initialize database."""
    db_url = os.getenv('DATABASE_URL', '')
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    if not db_url:
        db_url = 'sqlite:///marking.db'
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()
        _migrate_add_columns(app)


class Teacher(db.Model):
    __tablename__ = 'teachers'
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    role = db.Column(db.String(10), default='teacher')  # 'hod' or 'teacher'
    is_active = db.Column(db.Boolean, default=True)
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
    students = db.relationship('Student', backref='student_class', lazy=True, cascade='all, delete-orphan')


class TeacherClass(db.Model):
    __tablename__ = 'teacher_classes'
    teacher_id = db.Column(db.String(36), db.ForeignKey('teachers.id'), primary_key=True)
    class_id = db.Column(db.String(36), db.ForeignKey('classes.id'), primary_key=True)


class DepartmentConfig(db.Model):
    __tablename__ = 'department_config'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, default='')


class Assignment(db.Model):
    __tablename__ = 'assignments'

    id = db.Column(db.String(36), primary_key=True)
    classroom_code = db.Column(db.String(10), unique=True, nullable=False, index=True)
    title = db.Column(db.String(300), default='')
    subject = db.Column(db.String(200), default='')
    assign_type = db.Column(db.String(20), default='short_answer')
    scoring_mode = db.Column(db.String(20), default='status')
    total_marks = db.Column(db.String(20), default='')
    provider = db.Column(db.String(20), default='anthropic')
    model = db.Column(db.String(100), default='')
    show_results = db.Column(db.Boolean, default=True)
    review_instructions = db.Column(db.Text, default='')
    marking_instructions = db.Column(db.Text, default='')

    # Department mode foreign keys
    class_id = db.Column(db.String(36), db.ForeignKey('classes.id'), nullable=True, index=True)
    teacher_id = db.Column(db.String(36), db.ForeignKey('teachers.id'), nullable=True, index=True)

    # File storage as binary
    question_paper = db.Column(db.LargeBinary)
    answer_key = db.Column(db.LargeBinary)
    rubrics = db.Column(db.LargeBinary)
    reference = db.Column(db.LargeBinary)

    # API keys (JSON string, encrypted with Fernet when FLASK_SECRET_KEY is set)
    api_keys_json = db.Column(db.Text, default='{}')

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    students = db.relationship('Student', backref='assignment', lazy=True, cascade='all, delete-orphan')

    def get_api_keys(self):
        raw = self.api_keys_json or '{}'
        f = _get_fernet()
        if f:
            try:
                decrypted = f.decrypt(raw.encode()).decode()
                return json.loads(decrypted)
            except (InvalidToken, Exception):
                pass  # Fall through to plaintext (pre-encryption data)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_api_keys(self, keys_dict):
        plaintext = json.dumps(keys_dict)
        f = _get_fernet()
        if f:
            self.api_keys_json = f.encrypt(plaintext.encode()).decode()
        else:
            self.api_keys_json = plaintext


class Student(db.Model):
    __tablename__ = 'students'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    class_id = db.Column(db.String(36), db.ForeignKey('classes.id'), nullable=True, index=True)
    assignment_id = db.Column(db.String(36), db.ForeignKey('assignments.id'), nullable=True, index=True)
    index_number = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(200), nullable=False)

    submission = db.relationship('Submission', backref='student', uselist=False, lazy=True, cascade='all, delete-orphan')


class Submission(db.Model):
    __tablename__ = 'submissions'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False, index=True)
    assignment_id = db.Column(db.String(36), db.ForeignKey('assignments.id'), nullable=False, index=True)
    script_bytes = db.Column(db.LargeBinary)
    script_pages_json = db.Column(db.Text)  # JSON list of base64-encoded file bytes
    status = db.Column(db.String(20), default='pending')  # pending, processing, done, error
    result_json = db.Column(db.Text)
    submitted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    marked_at = db.Column(db.DateTime)

    assignment = db.relationship('Assignment', backref='submissions')

    def get_script_pages(self):
        """Return list of file bytes for all uploaded pages."""
        if self.script_pages_json:
            pages = json.loads(self.script_pages_json)
            return [base64.b64decode(p) for p in pages]
        if self.script_bytes:
            return [self.script_bytes]
        return []

    def set_script_pages(self, pages_list):
        """Store list of file bytes as base64 JSON."""
        self.script_pages_json = json.dumps([base64.b64encode(p).decode() for p in pages_list])

    def get_result(self):
        try:
            return json.loads(self.result_json or '{}')
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_result(self, result_dict):
        self.result_json = json.dumps(result_dict)
