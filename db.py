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
            # One-shot backfill: any assignment with no title gets its
            # subject as a sensible fallback (or "Assignment <code>" if
            # the subject is also empty). This keeps the new "Assignment"
            # row in the PDF generator from showing a dash for legacy
            # rows. Idempotent — guarded by WHERE title IS NULL OR title=''.
            try:
                db.session.execute(text(
                    "UPDATE assignments SET title = subject "
                    "WHERE (title IS NULL OR title = '') "
                    "AND subject IS NOT NULL AND subject != ''"
                ))
                db.session.execute(text(
                    "UPDATE assignments SET title = 'Assignment ' || classroom_code "
                    "WHERE title IS NULL OR title = ''"
                ))
                db.session.commit()
            except Exception as _e:
                db.session.rollback()
                logger.warning(f'Assignment.title backfill skipped: {_e}')
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

        # feedback_edit may be a leftover from earlier deploys (e.g.
        # feed_forward_beta) that have a different column set. Make sure
        # every column our current model SELECTs/INSERTs exists, so
        # SQLAlchemy doesn't error with "column does not exist" mid-query.
        if 'feedback_edit' in inspector.get_table_names():
            fe_cols = {c['name'] for c in inspector.get_columns('feedback_edit')}
            ensure = [
                ('subject_bucket', 'VARCHAR(40)'),
                ('propagation_status', "VARCHAR(20) DEFAULT 'none' NOT NULL"),
                ('propagated_to', "TEXT DEFAULT '[]' NOT NULL"),
                ('propagated_at', 'TIMESTAMP'),
                ('rubric_version', "VARCHAR(64) DEFAULT '' NOT NULL"),
                ('scope', "VARCHAR(20) DEFAULT 'individual' NOT NULL"),
            ]
            for col, ddl in ensure:
                if col not in fe_cols:
                    try:
                        db.session.execute(text(f'ALTER TABLE feedback_edit ADD COLUMN {col} {ddl}'))
                        db.session.commit()
                        logger.info(f'Added {col} column to feedback_edit table')
                    except Exception as _e:
                        db.session.rollback()
                        logger.error(f'feedback_edit ALTER ADD {col} failed: {_e}')


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
        # Belt-and-suspenders: confirm feedback_edit table actually exists.
        # If create_all silently failed for any reason, force-create it now
        # so calibration writes don't go to /dev/null.
        from sqlalchemy import inspect as _inspect
        existing = set(_inspect(db.engine).get_table_names())
        if 'feedback_edit' not in existing:
            logger.error('feedback_edit table missing after create_all — forcing creation')
            try:
                FeedbackEdit.__table__.create(db.engine)
                logger.error('feedback_edit table created via fallback')
            except Exception as _ce:
                logger.error(f'fallback feedback_edit create failed: {_ce}')
        else:
            logger.info('feedback_edit table present at boot')


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


class FeedbackEdit(db.Model):
    """Calibration bank — one row per teacher edit saved with the
    \"Save to calibration bank\" checkbox. Drives the calibration block
    prepended to future marking prompts and the propagation candidate
    detection that fires after each save.
    """
    __tablename__ = 'feedback_edit'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    submission_id = db.Column(db.Integer, db.ForeignKey('submissions.id'), nullable=False, index=True)
    criterion_id = db.Column(db.String(64), nullable=False)
    field = db.Column(db.String(20), nullable=False)  # 'feedback' | 'improvement'
    original_text = db.Column(db.Text, nullable=False, default='')
    edited_text = db.Column(db.Text, nullable=False, default='')
    edited_by = db.Column(db.String(36), db.ForeignKey('teachers.id'), nullable=False, index=True)
    assignment_id = db.Column(db.String(36), db.ForeignKey('assignments.id'), nullable=False, index=True)
    rubric_version = db.Column(db.String(64), nullable=False, default='')
    subject_bucket = db.Column(db.String(40), nullable=True, index=True)
    scope = db.Column(db.String(20), nullable=False, default='individual')
    active = db.Column(db.Boolean, nullable=False, default=True)
    propagation_status = db.Column(db.String(20), nullable=False, default='none')
    propagated_to = db.Column(db.Text, nullable=False, default='[]')
    propagated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.Index('ix_feedback_edit_lookup', 'edited_by', 'active', 'subject_bucket'),
        db.Index('ix_feedback_edit_assignment', 'assignment_id', 'rubric_version'),
    )
