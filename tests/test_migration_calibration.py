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


def test_subject_standard_table_exists(app):
    with app.app_context():
        names = inspect(db.engine).get_table_names()
        assert 'subject_standard' in names
        cols = {c['name'] for c in inspect(db.engine).get_columns('subject_standard')}
        for required in (
            'id', 'uuid', 'subject', 'text', 'topic_keys', 'theme_key',
            'reinforcement_count', 'status', 'created_by',
            'created_at', 'updated_at', 'last_seen_at',
            'reviewed_by', 'reviewed_at',
            'source_feedback_edit_ids', 'metadata_json',
        ):
            assert required in cols, f"missing column {required}"


def test_subject_standard_insert_round_trip(app, db_session):
    from db import SubjectStandard, Teacher
    t = Teacher(id='ss-teacher-1', name='Joe', code='SST1', role='teacher')
    db_session.add(t)
    db_session.commit()
    s = SubjectStandard(
        subject='biology',
        text='Accept "temperature" but reject "heat".',
        topic_keys='["enzymes", "terminology_precision"]',
        theme_key='terminology_precision',
        status='pending_review',
        created_by=t.id,
    )
    db_session.add(s)
    db_session.commit()
    fetched = SubjectStandard.query.filter_by(subject='biology', created_by=t.id).first()
    assert fetched is not None
    assert fetched.uuid  # auto-generated
    assert fetched.uuid.startswith('ss_')
    assert fetched.reinforcement_count == 1
