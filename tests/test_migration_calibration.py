"""UP-: schema migration for calibration edit intent + subject standards."""

from sqlalchemy import inspect
from db import db


def test_feedback_edit_has_amend_answer_key_column(app):
    with app.app_context():
        cols = [c['name'] for c in inspect(db.engine).get_columns('feedback_edit')]
        assert 'amend_answer_key' in cols
        assert 'promoted_to_subject_standard_id' in cols


def test_assignment_has_topic_keys_columns(app):
    with app.app_context():
        cols = [c['name'] for c in inspect(db.engine).get_columns('assignments')]
        assert 'topic_keys' in cols
        assert 'topic_keys_status' in cols
        assert 'bank_pushed_at' in cols
