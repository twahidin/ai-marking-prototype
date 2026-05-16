"""Schema migration tests for calibration edit intent columns.

SubjectStandard and SubjectTopicVocabulary tests removed 2026-05-16 (tables
deleted). topic_keys_status classification tests removed (logic removed from
_migrate_calibration_runtime).
"""

from sqlalchemy import inspect, text
from db import db


def test_feedback_edit_has_amend_answer_key_column(app):
    with app.app_context():
        cols = [c['name'] for c in inspect(db.engine).get_columns('feedback_edit')]
        assert 'amend_answer_key' in cols


def test_assignment_has_bank_pushed_at_column(app):
    """bank_pushed_at survives; topic_keys/topic_keys_status are dropped in commit 4."""
    with app.app_context():
        cols = [c['name'] for c in inspect(db.engine).get_columns('assignments')]
        assert 'bank_pushed_at' in cols
        assert 'topic_keys' not in cols
        assert 'topic_keys_status' not in cols


def test_feedback_edits_get_amendment_scope_on_migration(app, db_session):
    """_migrate_calibration_runtime now sets amend_answer_key=True
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
                          rubric_version='v1', active=True)
    new_fe = FeedbackEdit(submission_id=placeholder_sid_b, criterion_id='1', field='feedback',
                          original_text='x', edited_text='y',
                          edited_by=t.id, assignment_id=new_asn.id,
                          rubric_version='v1', active=True)
    db_session.add_all([old_fe, new_fe])
    db_session.commit()

    _migrate_calibration_runtime(__import__('app').app, force=True)

    db_session.refresh(old_fe)
    db_session.refresh(new_fe)
    # Both get amend_answer_key=True since the topic_keys_status legacy check is gone.
    assert old_fe.active is True
    assert old_fe.amend_answer_key is True
    assert new_fe.active is True
    assert new_fe.amend_answer_key is True


# --- 2026-05-16: drop_subject_standards migration -------------------------


def test_drop_migration_removes_subject_standards_table(app, db_session):
    """After migration, subject_standards table is absent."""
    from db import _migrate_drop_subject_standards
    db.session.execute(text(
        "CREATE TABLE IF NOT EXISTS subject_standards (id INTEGER PRIMARY KEY)"
    ))
    db.session.commit()
    _migrate_drop_subject_standards(app, force=True)
    rows = db.session.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='subject_standards'"
    )).fetchall()
    assert rows == [], 'subject_standards table should be dropped'


def test_drop_migration_removes_subject_topic_vocabulary_table(app, db_session):
    """After migration, subject_topic_vocabulary table is absent."""
    from db import _migrate_drop_subject_standards
    db.session.execute(text(
        "CREATE TABLE IF NOT EXISTS subject_topic_vocabulary (subject TEXT, topic_key TEXT)"
    ))
    db.session.commit()
    _migrate_drop_subject_standards(app, force=True)
    rows = db.session.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='subject_topic_vocabulary'"
    )).fetchall()
    assert rows == [], 'subject_topic_vocabulary table should be dropped'


def test_drop_migration_removes_feedback_edits_dead_columns(app, db_session):
    """After migration, the obsolete FeedbackEdit columns are gone."""
    from db import _migrate_drop_subject_standards
    _migrate_drop_subject_standards(app, force=True)
    cols = [
        r[1] for r in db.session.execute(text("PRAGMA table_info(feedback_edit)")).fetchall()
    ]
    for dead in ('scope', 'promoted_to_subject_standard_id',
                 'promoted_by', 'promoted_at'):
        assert dead not in cols, f'feedback_edits.{dead} should be dropped'


def test_drop_migration_removes_assignments_dead_columns(app, db_session):
    """After migration, the obsolete Assignment columns are gone."""
    from db import _migrate_drop_subject_standards
    _migrate_drop_subject_standards(app, force=True)
    cols = [
        r[1] for r in db.session.execute(text("PRAGMA table_info(assignments)")).fetchall()
    ]
    for dead in ('topic_keys', 'topic_keys_status'):
        assert dead not in cols, f'assignments.{dead} should be dropped'


def test_drop_migration_is_idempotent(app, db_session):
    """Running the migration twice is safe — second call no-ops."""
    from db import _migrate_drop_subject_standards
    _migrate_drop_subject_standards(app, force=True)
    _migrate_drop_subject_standards(app)
    rows = db.session.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('subject_standards', 'subject_topic_vocabulary')"
    )).fetchall()
    assert rows == []
