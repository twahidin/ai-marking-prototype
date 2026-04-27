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
        # Fall back to DB-stored secret key (auto-generated on first boot)
        try:
            cfg = DepartmentConfig.query.filter_by(key='flask_secret_key').first()
            if cfg and cfg.value:
                key = cfg.value
        except Exception:
            pass
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
            if 'extracted_text_json' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN extracted_text_json TEXT'))
                db.session.commit()
                logger.info('Added extracted_text_json column to submissions table')
            if 'student_text_json' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN student_text_json TEXT'))
                db.session.commit()
                logger.info('Added student_text_json column to submissions table')
            if 'student_amended' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN student_amended BOOLEAN DEFAULT FALSE'))
                db.session.commit()
                logger.info('Added student_amended column to submissions table')
            if 'draft_number' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN draft_number INTEGER DEFAULT 1 NOT NULL'))
                db.session.commit()
                logger.info('Added draft_number column to submissions table')
            if 'is_final' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN is_final BOOLEAN DEFAULT TRUE NOT NULL'))
                db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_submissions_is_final ON submissions (is_final)'))
                db.session.commit()
                db.session.execute(text('UPDATE submissions SET draft_number = 1 WHERE draft_number IS NULL'))
                db.session.execute(text('UPDATE submissions SET is_final = TRUE WHERE is_final IS NULL'))
                db.session.commit()
                logger.info('Added is_final column to submissions table and backfilled defaults')
            if 'feedback_opened_at' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN feedback_opened_at TIMESTAMP'))
                db.session.commit()
                logger.info('Added feedback_opened_at column to submissions table')
            if 'correction_submitted_at' not in columns:
                db.session.execute(text('ALTER TABLE submissions ADD COLUMN correction_submitted_at TIMESTAMP'))
                db.session.commit()
                logger.info('Added correction_submitted_at column to submissions table')
            if 'categorisation_status' not in columns:
                db.session.execute(text("ALTER TABLE submissions ADD COLUMN categorisation_status VARCHAR(20) DEFAULT 'pending'"))
                db.session.commit()
                logger.info('Added categorisation_status column to submissions table')
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
            # Widen role column for new roles (subject_head, lead, manager)
            for col in inspector.get_columns('teachers'):
                if col['name'] == 'role' and hasattr(col['type'], 'length') and col['type'].length and col['type'].length < 20:
                    try:
                        db.session.execute(text("ALTER TABLE teachers ALTER COLUMN role TYPE VARCHAR(20)"))
                        db.session.commit()
                        logger.info('Widened role column to VARCHAR(20)')
                    except Exception:
                        db.session.rollback()
                    break
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
            if 'allow_drafts' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN allow_drafts BOOLEAN DEFAULT FALSE NOT NULL'))
                db.session.commit()
                logger.info('Added allow_drafts column to assignments table')
            if 'max_drafts' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN max_drafts INTEGER DEFAULT 3 NOT NULL'))
                db.session.commit()
                logger.info('Added max_drafts column to assignments table')
            if 'last_edited_at' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN last_edited_at TIMESTAMP'))
                db.session.commit()
                logger.info('Added last_edited_at column to assignments table')
            if 'needs_remark' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN needs_remark BOOLEAN DEFAULT FALSE NOT NULL'))
                db.session.commit()
                logger.info('Added needs_remark column to assignments table')
            if 'exemplar_analysis_json' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN exemplar_analysis_json TEXT'))
                db.session.commit()
                logger.info('Added exemplar_analysis_json column to assignments table')
            if 'exemplar_analyzed_at' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN exemplar_analyzed_at TIMESTAMP'))
                db.session.commit()
                logger.info('Added exemplar_analyzed_at column to assignments table')
            if 'subject_family' not in columns:
                db.session.execute(text('ALTER TABLE assignments ADD COLUMN subject_family VARCHAR(40)'))
                db.session.commit()
                logger.info('Added subject_family column to assignments table')
        if 'feedback_edit' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('feedback_edit')]
            if 'propagation_status' not in columns:
                db.session.execute(text("ALTER TABLE feedback_edit ADD COLUMN propagation_status VARCHAR(20) DEFAULT 'none'"))
                db.session.commit()
                logger.info('Added propagation_status column to feedback_edit table')
            if 'propagated_to' not in columns:
                db.session.execute(text("ALTER TABLE feedback_edit ADD COLUMN propagated_to TEXT DEFAULT '[]'"))
                db.session.commit()
                logger.info('Added propagated_to column to feedback_edit table')
            if 'propagated_at' not in columns:
                db.session.execute(text('ALTER TABLE feedback_edit ADD COLUMN propagated_at TIMESTAMP'))
                db.session.commit()
                logger.info('Added propagated_at column to feedback_edit table')
            if 'mistake_pattern' not in columns:
                db.session.execute(text('ALTER TABLE feedback_edit ADD COLUMN mistake_pattern VARCHAR(80)'))
                db.session.commit()
                logger.info('Added mistake_pattern column to feedback_edit table')
            if 'correction_principle' not in columns:
                db.session.execute(text('ALTER TABLE feedback_edit ADD COLUMN correction_principle VARCHAR(300)'))
                db.session.commit()
                logger.info('Added correction_principle column to feedback_edit table')
            if 'transferability' not in columns:
                db.session.execute(text('ALTER TABLE feedback_edit ADD COLUMN transferability VARCHAR(10)'))
                db.session.commit()
                logger.info('Added transferability column to feedback_edit table')


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
    role = db.Column(db.String(20), default='teacher')  # hod, subject_head, lead, manager, teacher
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
    # Resolved once at creation time by a lightweight AI classify call — one of
    # the keys in SUBJECT_FAMILIES (science / humanities_seq / humanities_sbq /
    # literature / mother_tongue_comprehension / _composition / _translation).
    # Null for assignments that pre-date this feature.
    subject_family = db.Column(db.String(40), nullable=True)
    assign_type = db.Column(db.String(20), default='short_answer')
    scoring_mode = db.Column(db.String(20), default='marks')
    total_marks = db.Column(db.String(20), default='')
    provider = db.Column(db.String(20), default='anthropic')
    model = db.Column(db.String(100), default='')
    show_results = db.Column(db.Boolean, default=True)
    allow_drafts = db.Column(db.Boolean, default=False)
    max_drafts = db.Column(db.Integer, default=3)
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
    last_edited_at = db.Column(db.DateTime, nullable=True)
    needs_remark = db.Column(db.Boolean, default=False, nullable=False)
    exemplar_analysis_json = db.Column(db.Text)
    exemplar_analyzed_at = db.Column(db.DateTime)

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


class AssignmentBank(db.Model):
    __tablename__ = 'assignment_bank'

    id = db.Column(db.String(36), primary_key=True)
    title = db.Column(db.String(300), default='')
    subject = db.Column(db.String(200), default='')
    level = db.Column(db.String(20), default='')  # Sec 1, Sec 2, ... Sec 5
    tags = db.Column(db.Text, default='')  # comma-separated hashtags
    assign_type = db.Column(db.String(20), default='short_answer')
    scoring_mode = db.Column(db.String(20), default='marks')
    total_marks = db.Column(db.String(20), default='')
    review_instructions = db.Column(db.Text, default='')
    marking_instructions = db.Column(db.Text, default='')

    question_paper = db.Column(db.LargeBinary)
    answer_key = db.Column(db.LargeBinary)
    rubrics = db.Column(db.LargeBinary)
    reference = db.Column(db.LargeBinary)

    created_by = db.Column(db.String(36), db.ForeignKey('teachers.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    creator = db.relationship('Teacher', backref='bank_items', lazy=True)

    def get_tags_list(self):
        if not self.tags:
            return []
        return [t.strip().lstrip('#') for t in self.tags.split(',') if t.strip()]

    def set_tags_list(self, tags):
        self.tags = ','.join('#' + t.strip().lstrip('#') for t in tags if t.strip())


class Student(db.Model):
    __tablename__ = 'students'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    class_id = db.Column(db.String(36), db.ForeignKey('classes.id'), nullable=True, index=True)
    assignment_id = db.Column(db.String(36), db.ForeignKey('assignments.id'), nullable=True, index=True)
    index_number = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(200), nullable=False)

    submissions = db.relationship('Submission', backref='student', lazy=True, cascade='all, delete-orphan')


class Submission(db.Model):
    __tablename__ = 'submissions'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False, index=True)
    assignment_id = db.Column(db.String(36), db.ForeignKey('assignments.id'), nullable=False, index=True)
    script_bytes = db.Column(db.LargeBinary)
    script_pages_json = db.Column(db.Text)  # JSON list of base64-encoded file bytes
    status = db.Column(db.String(20), default='pending')  # pending, extracting, preview, processing, done, error
    result_json = db.Column(db.Text)
    extracted_text_json = db.Column(db.Text)  # AI-extracted answers (original)
    student_text_json = db.Column(db.Text)  # Student-confirmed answers (may be edited)
    student_amended = db.Column(db.Boolean, default=False)  # True if student edited extracted text
    draft_number = db.Column(db.Integer, default=1, nullable=False)
    is_final = db.Column(db.Boolean, default=True, nullable=False, index=True)
    submitted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    marked_at = db.Column(db.DateTime)
    feedback_opened_at = db.Column(db.DateTime)  # first time the student opened the tiered feedback page
    correction_submitted_at = db.Column(db.DateTime)  # first time the student submitted a "Now You Try" correction
    # Async "Group by Mistake Type" categorisation — pending on kick-off,
    # done once the background thread writes categorisation + group_habits
    # into result_json, failed if the AI call errored.
    categorisation_status = db.Column(db.String(20), default='pending')

    assignment = db.relationship('Assignment', backref=db.backref('submissions', cascade='all, delete-orphan'))

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

    def get_extracted_text(self):
        try:
            return json.loads(self.extracted_text_json or '[]')
        except (json.JSONDecodeError, TypeError):
            return []

    def set_extracted_text(self, answers_list):
        self.extracted_text_json = json.dumps(answers_list)

    def get_student_text(self):
        try:
            return json.loads(self.student_text_json or '[]')
        except (json.JSONDecodeError, TypeError):
            return []

    def set_student_text(self, answers_list):
        self.student_text_json = json.dumps(answers_list)


class FeedbackLog(db.Model):
    __tablename__ = 'feedback_log'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    submission_id = db.Column(db.Integer, db.ForeignKey('submissions.id'), nullable=False, index=True)
    criterion_id = db.Column(db.String(64), nullable=False)
    field = db.Column(db.String(20), nullable=False)  # 'feedback' | 'improvement'
    version = db.Column(db.Integer, nullable=False)   # 1 = AI original, 2+ = teacher edits
    feedback_text = db.Column(db.Text, nullable=False, default='')
    author_type = db.Column(db.String(10), nullable=False)  # 'ai' | 'teacher'
    author_id = db.Column(db.String(36), nullable=True)     # NULL for AI; teacher.id otherwise
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint('submission_id', 'criterion_id', 'field', 'version',
                            name='uq_feedback_log_sub_crit_field_ver'),
    )


class FeedbackEdit(db.Model):
    __tablename__ = 'feedback_edit'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    submission_id = db.Column(db.Integer, db.ForeignKey('submissions.id'), nullable=False, index=True)
    criterion_id = db.Column(db.String(64), nullable=False)
    field = db.Column(db.String(20), nullable=False)  # 'feedback' | 'improvement'
    original_text = db.Column(db.Text, nullable=False, default='')
    edited_text = db.Column(db.Text, nullable=False, default='')
    edited_by = db.Column(db.String(36), db.ForeignKey('teachers.id'), nullable=False, index=True)
    subject_family = db.Column(db.String(40), nullable=True)
    theme_key = db.Column(db.String(40), nullable=True)
    assignment_id = db.Column(db.String(36), db.ForeignKey('assignments.id'), nullable=False, index=True)
    rubric_version = db.Column(db.String(64), nullable=False, default='')
    # FUTURE: department-level promotion logic goes here.
    scope = db.Column(db.String(20), nullable=False, default='individual')
    promoted_by = db.Column(db.String(36), nullable=True)
    promoted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    propagation_status = db.Column(db.String(20), nullable=False, default='none')
    propagated_to = db.Column(db.Text, nullable=False, default='[]')
    propagated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    mistake_pattern = db.Column(db.String(80), nullable=True)
    correction_principle = db.Column(db.String(300), nullable=True)
    transferability = db.Column(db.String(10), nullable=True)

    __table_args__ = (
        db.Index('ix_feedback_edit_lookup', 'edited_by', 'active', 'subject_family', 'theme_key'),
        db.Index('ix_feedback_edit_assignment', 'assignment_id', 'rubric_version'),
    )


class MarkingPrinciplesCache(db.Model):
    __tablename__ = 'marking_principles_cache'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    subject_family = db.Column(db.String(40), nullable=False, unique=True)
    markdown_text = db.Column(db.Text, nullable=False, default='')
    generated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_stale = db.Column(db.Boolean, nullable=False, default=False)
    edit_count_at_gen = db.Column(db.Integer, nullable=False, default=0)
    has_conflicts = db.Column(db.Boolean, nullable=False, default=False)
