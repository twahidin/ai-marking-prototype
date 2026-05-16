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
            'id', 'uuid', 'subject', 'text', 'topic_keys', 'mistake_type',
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
        mistake_type='terminology_precision',
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


def test_subject_topic_vocabulary_table_exists(app):
    with app.app_context():
        names = inspect(db.engine).get_table_names()
        assert 'subject_topic_vocabulary' in names


def test_subject_topic_vocabulary_round_trip(app, db_session):
    from db import SubjectTopicVocabulary
    # Use a synthetic key that the boot seed will never insert so the test
    # remains independent of which subject files are present.
    v = SubjectTopicVocabulary(
        subject='test_subject_vocab',
        topic_key='test_topic_key',
        display_name='Test Topic',
    )
    db_session.add(v)
    db_session.commit()
    got = SubjectTopicVocabulary.query.filter_by(
        subject='test_subject_vocab', topic_key='test_topic_key'
    ).first()
    assert got is not None
    assert got.display_name == 'Test Topic'
    assert got.active is True


def test_legacy_assignments_get_legacy_status(app, db_session):
    from datetime import datetime, timezone, timedelta
    from db import Assignment, _migrate_calibration_runtime
    import uuid as _uuid

    old_id = 'old-' + _uuid.uuid4().hex[:8]
    new_id = 'new-' + _uuid.uuid4().hex[:8]
    old = Assignment(id=old_id, classroom_code='OLD' + _uuid.uuid4().hex[:4].upper(),
                     subject='biology', title='Old',
                     created_at=datetime.now(timezone.utc) - timedelta(days=30))
    new = Assignment(id=new_id, classroom_code='NEW' + _uuid.uuid4().hex[:4].upper(),
                     subject='biology', title='New',
                     created_at=datetime.now(timezone.utc) - timedelta(days=2))
    db_session.add_all([old, new])
    db_session.commit()

    _migrate_calibration_runtime(__import__('app').app, force=True)

    db_session.refresh(old)
    db_session.refresh(new)
    assert old.topic_keys_status == 'legacy'
    assert new.topic_keys_status == 'pending'


def test_legacy_feedback_edits_deactivated(app, db_session):
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
    old_fe_id = old_fe.id
    new_fe_id = new_fe.id

    _migrate_calibration_runtime(__import__('app').app, force=True)

    db_session.refresh(old_fe)
    db_session.refresh(new_fe)
    assert old_fe.active is False
    assert new_fe.active is True
    assert new_fe.amend_answer_key is True
    assert new_fe.scope == 'amendment'
