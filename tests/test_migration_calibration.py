"""Schema migration tests for calibration edit intent columns.

SubjectStandard and SubjectTopicVocabulary tests removed 2026-05-16 (tables
deleted). topic_keys_status classification tests removed (logic removed from
_migrate_calibration_runtime).
"""

from sqlalchemy import inspect
from db import db


def test_feedback_edit_has_amend_answer_key_column(app):
    with app.app_context():
        cols = [c['name'] for c in inspect(db.engine).get_columns('feedback_edit')]
        assert 'amend_answer_key' in cols


def test_assignment_has_topic_keys_columns(app):
    """topic_keys and topic_keys_status columns survive until commit 4 (DROP COLUMN)."""
    with app.app_context():
        cols = [c['name'] for c in inspect(db.engine).get_columns('assignments')]
        assert 'topic_keys' in cols
        assert 'topic_keys_status' in cols
        assert 'bank_pushed_at' in cols


def test_feedback_edits_get_amendment_scope_on_migration(app, db_session):
    """_migrate_calibration_runtime now sets amend_answer_key=True and scope='amendment'
    on all active FeedbackEdits whose parent assignment exists, regardless of assignment age.
    """
    from datetime import datetime, timezone, timedelta
    from db import Teacher, Assignment, FeedbackEdit, _migrate_calibration_runtime
    import uuid as _uuid

    tid = 't-' + _uuid.uuid4().hex[:8]
    t = Teacher(id=tid, name='Joe', code='C' + _uuid.uuid4().hex[:6].upper(), role='teacher')
    db_session.add(t)
    old_asn = Assignment(id='old-' + _uuid.uuid4().hex[:8],
                         classroom_code='OL' + _uuid.uuid4().hex[:4].upper(),
                         subject='biology', title='Old',
                         created_at=datetime.now(timezone.utc) - timedelta(days=30))
    new_asn = Assignment(id='new-' + _uuid.uuid4().hex[:8],
                         classroom_code='NE' + _uuid.uuid4().hex[:4].upper(),
                         subject='biology', title='New',
                         created_at=datetime.now(timezone.utc) - timedelta(days=2))
    db_session.add_all([old_asn, new_asn])
    db_session.commit()
    placeholder_sid_a = 9_100_000 + (int(_uuid.uuid4().int) % 100_000)
    placeholder_sid_b = 9_200_000 + (int(_uuid.uuid4().int) % 100_000)
    old_fe = FeedbackEdit(submission_id=placeholder_sid_a, criterion_id='1', field='feedback',
                          original_text='x', edited_text='y',
                          edited_by=t.id, assignment_id=old_asn.id,
                          rubric_version='v1', scope='individual', active=True)
    new_fe = FeedbackEdit(submission_id=placeholder_sid_b, criterion_id='1', field='feedback',
                          original_text='x', edited_text='y',
                          edited_by=t.id, assignment_id=new_asn.id,
                          rubric_version='v1', scope='individual', active=True)
    db_session.add_all([old_fe, new_fe])
    db_session.commit()

    _migrate_calibration_runtime(__import__('app').app, force=True)

    db_session.refresh(old_fe)
    db_session.refresh(new_fe)
    # Both get amend_answer_key=True since the topic_keys_status legacy check is gone.
    assert old_fe.active is True
    assert old_fe.amend_answer_key is True
    assert old_fe.scope == 'amendment'
    assert new_fe.active is True
    assert new_fe.amend_answer_key is True
    assert new_fe.scope == 'amendment'
