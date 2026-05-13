"""UP-: two-checkbox intent (Amend answer key / Update subject standards)."""

import json
import uuid as _uuid
from unittest.mock import patch

from db import db, Teacher, Assignment, Student, Submission, FeedbackEdit


def _make_chain(db_session, *, subject='biology', role='owner', topic_keys_status='tagged'):
    """Build Teacher → Assignment → Student → Submission chain.

    Defaults to a canonical subject, owner role, tagged status so that
    both intent checkboxes are accepted. Override per-test as needed.
    """
    tid = 't-' + _uuid.uuid4().hex[:8]
    aid = 'a-' + _uuid.uuid4().hex[:8]
    t = Teacher(id=tid, name='Joe', code='C' + _uuid.uuid4().hex[:6].upper(), role=role)
    db_session.add(t)
    asn = Assignment(
        id=aid,
        classroom_code='C' + _uuid.uuid4().hex[:6].upper(),
        subject=subject,
        title='Test',
        teacher_id=t.id,
        topic_keys=json.dumps([['enzymes']]),
        topic_keys_status=topic_keys_status,
        provider='anthropic',
        model='claude-sonnet-4-6',
    )
    db_session.add(asn)
    db_session.commit()
    stu = Student(assignment_id=asn.id, index_number='1', name='Stu')
    db_session.add(stu)
    db_session.commit()
    sub = Submission(
        assignment_id=asn.id,
        student_id=stu.id,
        result_json=json.dumps({
            'questions': [
                {'question_num': 1, 'feedback': 'Correct.',
                 'theme_key': 'terminology_precision'},
            ],
        }),
    )
    db_session.add(sub)
    db_session.commit()
    return t, asn, stu, sub


def _login(client, teacher_id):
    with client.session_transaction() as s:
        s['teacher_id'] = teacher_id
        s['authenticated'] = True


def test_neither_box_ticked_writes_no_feedback_edit(app, db_session, client):
    t, asn, _stu, sub = _make_chain(db_session)
    _login(client, t.id)

    rv = client.patch(
        f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
        json={'questions': [{'question_num': 1, 'feedback': 'New text',
                              'amend_answer_key': False, 'update_subject_standards': False}]},
    )
    assert rv.status_code == 200
    assert FeedbackEdit.query.filter_by(submission_id=sub.id).count() == 0


def test_amend_answer_key_only_writes_feedback_edit_with_flag(app, db_session, client):
    t, asn, _stu, sub = _make_chain(db_session)
    _login(client, t.id)

    rv = client.patch(
        f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
        json={'questions': [{'question_num': 1, 'feedback': 'Accept "powerhouse of the cell"',
                              'amend_answer_key': True, 'update_subject_standards': False}]},
    )
    assert rv.status_code == 200
    fe = FeedbackEdit.query.filter_by(submission_id=sub.id).first()
    assert fe is not None
    assert fe.amend_answer_key is True
    assert fe.scope == 'amendment'
    assert fe.promoted_to_subject_standard_id is None


def test_update_subject_standards_only_triggers_promotion(app, db_session, client):
    t, asn, _stu, sub = _make_chain(db_session)
    _login(client, t.id)

    with patch('subject_standards.extract_standard_topic_keys',
              return_value=['enzymes', 'terminology_precision']):
        rv = client.patch(
            f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
            json={'questions': [{'question_num': 1, 'feedback': "Must say 'temperature'",
                                  'amend_answer_key': False, 'update_subject_standards': True}]},
        )
    assert rv.status_code == 200
    fe = FeedbackEdit.query.filter_by(submission_id=sub.id).first()
    assert fe is not None
    assert fe.amend_answer_key is False
    assert fe.scope == 'promoted'
    assert fe.promoted_to_subject_standard_id is not None


def test_both_boxes_ticked_writes_scope_both(app, db_session, client):
    t, asn, _stu, sub = _make_chain(db_session)
    _login(client, t.id)

    with patch('subject_standards.extract_standard_topic_keys',
              return_value=['enzymes']):
        rv = client.patch(
            f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
            json={'questions': [{'question_num': 1, 'feedback': 'Both flags edit',
                                  'amend_answer_key': True, 'update_subject_standards': True}]},
        )
    assert rv.status_code == 200
    fe = FeedbackEdit.query.filter_by(submission_id=sub.id).first()
    assert fe is not None
    assert fe.amend_answer_key is True
    assert fe.scope == 'both'
    assert fe.promoted_to_subject_standard_id is not None


def test_legacy_assignment_drops_update_subject_standards(app, db_session, client):
    """Per spec §4.1: on legacy assignments, the 'Update subject standards'
    intent is hidden and must not promote. 'Amend answer key' still works."""
    t, asn, _stu, sub = _make_chain(db_session, topic_keys_status='legacy')
    _login(client, t.id)

    rv = client.patch(
        f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
        json={'questions': [{'question_num': 1, 'feedback': 'Legacy edit',
                              'amend_answer_key': True, 'update_subject_standards': True}]},
    )
    assert rv.status_code == 200
    fe = FeedbackEdit.query.filter_by(submission_id=sub.id).first()
    assert fe is not None
    assert fe.amend_answer_key is True
    # On legacy, promotion was silently dropped → scope='amendment' (not 'both')
    assert fe.scope == 'amendment'
    assert fe.promoted_to_subject_standard_id is None


# ---------------------------------------------------------------------------
# Phase 7 Task 7.1 — build_effective_answer_key
# ---------------------------------------------------------------------------

def test_effective_answer_key_appends_amendments(app, db_session):
    from subject_standards import build_effective_answer_key
    from ai_marking import _rubric_version_hash
    import uuid as _uuid

    tid = 'tea-' + _uuid.uuid4().hex[:8]
    t = Teacher(id=tid, name='Joe', code='C' + _uuid.uuid4().hex[:6].upper(), role='owner')
    db_session.add(t)
    asn = Assignment(id='asn-' + _uuid.uuid4().hex[:8],
                     classroom_code='C' + _uuid.uuid4().hex[:6].upper(),
                     subject='biology', title='Bio',
                     rubrics=b'shared-rubric-bytes')
    db_session.add(asn)
    db_session.commit()
    rv = _rubric_version_hash(asn)

    db_session.add_all([
        FeedbackEdit(
            submission_id=1, criterion_id='3', field='feedback',
            original_text='X', edited_text='Accept "powerhouse of the cell"',
            edited_by=t.id, assignment_id=asn.id, rubric_version=rv,
            scope='amendment', amend_answer_key=True, active=True,
        ),
        FeedbackEdit(
            submission_id=1, criterion_id='5', field='feedback',
            original_text='X', edited_text='Diagram is a fish, not a bird',
            edited_by=t.id, assignment_id=asn.id, rubric_version=rv,
            scope='amendment', amend_answer_key=True, active=True,
        ),
    ])
    db_session.commit()

    merged = build_effective_answer_key(
        assignment=asn,
        original_answer_key_text='Q1: mitochondria\nQ2: ATP',
    )
    assert 'Teacher clarifications' in merged
    assert 'Q3' in merged
    assert 'Q5' in merged
    assert 'powerhouse' in merged
    assert 'mitochondria' in merged


def test_effective_answer_key_no_amendments_returns_original(app, db_session):
    from subject_standards import build_effective_answer_key
    import uuid as _uuid
    asn = Assignment(id='asn-' + _uuid.uuid4().hex[:8],
                     classroom_code='C' + _uuid.uuid4().hex[:6].upper(),
                     subject='biology', title='Bio',
                     rubrics=b'other-rubric-bytes')
    db_session.add(asn)
    db_session.commit()
    merged = build_effective_answer_key(assignment=asn, original_answer_key_text='Q1: x')
    assert merged.strip() == 'Q1: x'


def test_effective_answer_key_only_active_amend_edits_included(app, db_session):
    """Edits with active=False or amend_answer_key=False (e.g. pure promotions)
    must be ignored when building the effective answer key."""
    from subject_standards import build_effective_answer_key
    from ai_marking import _rubric_version_hash
    import uuid as _uuid

    tid = 'tea-' + _uuid.uuid4().hex[:8]
    t = Teacher(id=tid, name='Joe', code='C' + _uuid.uuid4().hex[:6].upper(), role='owner')
    db_session.add(t)
    asn = Assignment(id='asn-' + _uuid.uuid4().hex[:8],
                     classroom_code='C' + _uuid.uuid4().hex[:6].upper(),
                     subject='biology', title='Bio',
                     rubrics=b'rubric-active-only')
    db_session.add(asn)
    db_session.commit()
    rv = _rubric_version_hash(asn)

    db_session.add_all([
        # Active amend → included
        FeedbackEdit(submission_id=1, criterion_id='1', field='feedback',
                     original_text='X', edited_text='AMEND TEXT INCLUDED',
                     edited_by=t.id, assignment_id=asn.id, rubric_version=rv,
                     scope='amendment', amend_answer_key=True, active=True),
        # Inactive → excluded
        FeedbackEdit(submission_id=1, criterion_id='2', field='feedback',
                     original_text='X', edited_text='INACTIVE TEXT EXCLUDED',
                     edited_by=t.id, assignment_id=asn.id, rubric_version=rv,
                     scope='amendment', amend_answer_key=True, active=False),
        # Promotion-only (not an amendment) → excluded
        FeedbackEdit(submission_id=1, criterion_id='3', field='feedback',
                     original_text='X', edited_text='PROMOTION ONLY TEXT EXCLUDED',
                     edited_by=t.id, assignment_id=asn.id, rubric_version=rv,
                     scope='promoted', amend_answer_key=False, active=True),
    ])
    db_session.commit()

    merged = build_effective_answer_key(assignment=asn, original_answer_key_text='ORIG')
    assert 'AMEND TEXT INCLUDED' in merged
    assert 'INACTIVE TEXT EXCLUDED' not in merged
    assert 'PROMOTION ONLY TEXT EXCLUDED' not in merged
