import os
import json
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def init_db(app):
    """Configure and initialize database."""
    db_url = os.getenv('DATABASE_URL', '')
    # Railway Postgres uses postgres:// but SQLAlchemy needs postgresql://
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    if not db_url:
        # Fallback to SQLite for local dev
        db_url = 'sqlite:///marking.db'
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    with app.app_context():
        db.create_all()


class Assignment(db.Model):
    __tablename__ = 'assignments'

    id = db.Column(db.String(36), primary_key=True)
    classroom_code = db.Column(db.String(10), unique=True, nullable=False, index=True)
    subject = db.Column(db.String(200), default='')
    assign_type = db.Column(db.String(20), default='short_answer')
    scoring_mode = db.Column(db.String(20), default='status')
    total_marks = db.Column(db.String(20), default='')
    provider = db.Column(db.String(20), default='anthropic')
    model = db.Column(db.String(100), default='')
    show_results = db.Column(db.Boolean, default=True)
    review_instructions = db.Column(db.Text, default='')
    marking_instructions = db.Column(db.Text, default='')

    # File storage as binary
    question_paper = db.Column(db.LargeBinary)
    answer_key = db.Column(db.LargeBinary)
    rubrics = db.Column(db.LargeBinary)
    reference = db.Column(db.LargeBinary)

    # API keys (JSON string, encrypted at rest by Postgres)
    api_keys_json = db.Column(db.Text, default='{}')

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    students = db.relationship('Student', backref='assignment', lazy=True, cascade='all, delete-orphan')

    def get_api_keys(self):
        try:
            return json.loads(self.api_keys_json or '{}')
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_api_keys(self, keys_dict):
        self.api_keys_json = json.dumps(keys_dict)


class Student(db.Model):
    __tablename__ = 'students'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    assignment_id = db.Column(db.String(36), db.ForeignKey('assignments.id'), nullable=False)
    index_number = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(200), nullable=False)

    submission = db.relationship('Submission', backref='student', uselist=False, lazy=True, cascade='all, delete-orphan')


class Submission(db.Model):
    __tablename__ = 'submissions'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)
    assignment_id = db.Column(db.String(36), db.ForeignKey('assignments.id'), nullable=False)
    script_bytes = db.Column(db.LargeBinary)
    status = db.Column(db.String(20), default='pending')  # pending, processing, done, error
    result_json = db.Column(db.Text)
    submitted_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    marked_at = db.Column(db.DateTime)

    assignment = db.relationship('Assignment', backref='submissions')

    def get_result(self):
        try:
            return json.loads(self.result_json or '{}')
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_result(self, result_dict):
        self.result_json = json.dumps(result_dict)
