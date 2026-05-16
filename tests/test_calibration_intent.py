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
                 'mistake_type': 'terminology_precision'},
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
    # Scope by assignment_id too — other tests insert placeholder FeedbackEdit
    # rows with `submission_id=1` (FKs are off in SQLite), and a fresh Submission's
    # autoincrement id can collide with those placeholders.
    assert FeedbackEdit.query.filter_by(submission_id=sub.id, assignment_id=asn.id).count() == 0


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


def test_uncheck_both_without_text_edit_deactivates_prior(app, db_session, client):
    """Teacher saves with both boxes ticked, then re-opens and unchecks both
    without editing the text. The prior FeedbackEdit must be deactivated so
    the calibration is fully removed."""
    t, asn, _stu, sub = _make_chain(db_session)
    _login(client, t.id)

    with patch('subject_standards.extract_standard_topic_keys',
              return_value=['enzymes']):
        client.patch(
            f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
            json={'questions': [{'question_num': 1, 'feedback': 'Calibrated text',
                                  'amend_answer_key': True, 'update_subject_standards': True}]},
        )
    active_before = FeedbackEdit.query.filter_by(
        submission_id=sub.id, assignment_id=asn.id, active=True).count()
    assert active_before == 1

    rv = client.patch(
        f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
        json={'questions': [{'question_num': 1, 'feedback': 'Calibrated text',
                              'amend_answer_key': False, 'update_subject_standards': False}]},
    )
    assert rv.status_code == 200
    assert FeedbackEdit.query.filter_by(
        submission_id=sub.id, assignment_id=asn.id, active=True).count() == 0


def test_uncheck_one_without_text_edit_updates_prior_flags(app, db_session, client):
    """Teacher saves with both boxes ticked, then re-opens and unchecks only
    'Update subject standards' (keeps Amend). The previous re-affirm
    short-circuit ignored flag changes when text was unchanged — this guards
    that regression: prior is deactivated and a new row reflects the new
    flag state."""
    t, asn, _stu, sub = _make_chain(db_session)
    _login(client, t.id)

    with patch('subject_standards.extract_standard_topic_keys',
              return_value=['enzymes']):
        client.patch(
            f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
            json={'questions': [{'question_num': 1, 'feedback': 'Same text',
                                  'amend_answer_key': True, 'update_subject_standards': True}]},
        )
    initial = FeedbackEdit.query.filter_by(
        submission_id=sub.id, assignment_id=asn.id, active=True).first()
    assert initial.scope == 'both'

    rv = client.patch(
        f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
        json={'questions': [{'question_num': 1, 'feedback': 'Same text',
                              'amend_answer_key': True, 'update_subject_standards': False}]},
    )
    assert rv.status_code == 200
    actives = FeedbackEdit.query.filter_by(
        submission_id=sub.id, assignment_id=asn.id, active=True).all()
    assert len(actives) == 1
    assert actives[0].amend_answer_key is True
    assert actives[0].scope == 'amendment'


def test_toggle_promote_on_without_text_edit_creates_new_row(app, db_session, client):
    """Symmetric to the uncheck case: teacher saves with only Amend, then
    re-opens and adds Update subject standards on the same text. The new
    flag state must replace the prior row."""
    t, asn, _stu, sub = _make_chain(db_session)
    _login(client, t.id)

    client.patch(
        f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
        json={'questions': [{'question_num': 1, 'feedback': 'Same text',
                              'amend_answer_key': True, 'update_subject_standards': False}]},
    )
    initial = FeedbackEdit.query.filter_by(
        submission_id=sub.id, assignment_id=asn.id, active=True).first()
    assert initial.scope == 'amendment'

    with patch('subject_standards.extract_standard_topic_keys',
              return_value=['enzymes']):
        rv = client.patch(
            f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
            json={'questions': [{'question_num': 1, 'feedback': 'Same text',
                                  'amend_answer_key': True, 'update_subject_standards': True}]},
        )
    assert rv.status_code == 200
    actives = FeedbackEdit.query.filter_by(
        submission_id=sub.id, assignment_id=asn.id, active=True).all()
    assert len(actives) == 1
    assert actives[0].scope == 'both'


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


# ---------------------------------------------------------------------------
# Phase 16 Task 16.1 — end-to-end happy path integration test
# ---------------------------------------------------------------------------

def test_full_happy_path_amend_then_promote_then_retrieve(app, db_session, client):
    """End-to-end: tagged assignment → teacher edits with both intents →
    promotion (mocked tagger) → approved → retrieval pulls it on next marking.

    Uses canonical subject='biology' so server-side promote-suppression
    doesn't fire. Asserts on SubjectStandard.created_by=t.id to stay isolated
    from other tests' biology rows.
    """
    from unittest.mock import patch
    from db import Teacher, Assignment, Student, Submission, SubjectStandard
    from subject_standards import retrieve_subject_standards
    import json
    import uuid as _uuid

    tid = 't-' + _uuid.uuid4().hex[:8]
    aid = 'e2e-' + _uuid.uuid4().hex[:8]
    t = Teacher(id=tid, name='Joe', code='C' + _uuid.uuid4().hex[:6].upper(), role='hod')
    asn = Assignment(id=aid, classroom_code='C' + _uuid.uuid4().hex[:6].upper(),
                     subject='biology', title='Bio E2E',
                     teacher_id=t.id,
                     topic_keys=json.dumps([['enzymes', 'terminology_precision']]),
                     topic_keys_status='tagged',
                     provider='anthropic',
                     model='claude-sonnet-4-6')
    db_session.add_all([t, asn])
    db_session.commit()
    stu = Student(assignment_id=asn.id, index_number='1', name='Stu')
    db_session.add(stu)
    db_session.commit()
    sub = Submission(assignment_id=asn.id, student_id=stu.id,
                     result_json=json.dumps({'questions': [
                         {'question_num': 1, 'feedback': 'Correct - heat affects enzyme rate.',
                          'mistake_type': 'terminology_precision'},
                     ]}))
    db_session.add(sub)
    db_session.commit()

    with client.session_transaction() as s:
        s['teacher_id'] = t.id
        s['authenticated'] = True

    # 1. Teacher saves edit with BOTH intents.
    unique_text = "Must say 'temperature', not 'heat'. (e2e marker " + _uuid.uuid4().hex[:6] + ')'
    with patch('subject_standards.extract_standard_topic_keys',
               return_value=['enzymes', 'terminology_precision']):
        rv = client.patch(
            f'/teacher/assignment/{asn.id}/submission/{sub.id}/result',
            json={'questions': [{'question_num': 1,
                                  'feedback': unique_text,
                                  'amend_answer_key': True,
                                  'update_subject_standards': True}]},
        )
    assert rv.status_code == 200

    # 2. SubjectStandard inserted as pending_review under this teacher.
    ss = SubjectStandard.query.filter_by(subject='biology', created_by=t.id).first()
    assert ss is not None
    assert ss.status == 'pending_review'

    # 3. HOD approves it.
    rv = client.post(f'/api/subject_standards/{ss.id}/approve')
    assert rv.status_code == 200
    db_session.refresh(ss)
    assert ss.status == 'active'

    # 4. Retrieval pulls the now-active standard on the next marking.
    out = retrieve_subject_standards(
        subject='biology',
        per_question_topic_keys=[['enzymes']],
    )
    # The standard with our unique marker must be in the retrieved set.
    assert any(unique_text in s.text for s in out), \
        f'Expected promoted standard with marker in retrieval; got texts: {[s.text for s in out]}'
