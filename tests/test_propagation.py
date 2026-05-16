"""Field-aware + marks-aware propagation (spec 2026-05-16 §4.4)."""

import json
import uuid as _uuid
from unittest.mock import patch

from db import db, Teacher, Assignment, Student, Submission, FeedbackEdit


def _make_chain_two_subs(db_session):
    """Build a single Teacher / Assignment with TWO students and submissions.

    Both submissions have the same wrong answer on Q1. The second submission
    is the propagation target.
    """
    tid = 't-' + _uuid.uuid4().hex[:8]
    aid = 'a-' + _uuid.uuid4().hex[:8]
    t = Teacher(id=tid, name='Joe', code='C' + _uuid.uuid4().hex[:6].upper(), role='owner')
    db_session.add(t)
    asn = Assignment(
        id=aid,
        classroom_code='C' + _uuid.uuid4().hex[:6].upper(),
        subject='biology',
        title='Test',
        teacher_id=t.id,
        provider='anthropic',
        model='claude-sonnet-4-6',
    )
    db_session.add(asn)
    db_session.commit()

    stu1 = Student(assignment_id=asn.id, index_number='1', name='Alice')
    stu2 = Student(assignment_id=asn.id, index_number='2', name='Bob')
    db_session.add_all([stu1, stu2])
    db_session.commit()

    base_q = {
        'question_num': 1,
        'student_answer': 'A wrong answer about enzymes.',
        'correct_answer': 'Enzymes are biological catalysts.',
        'feedback': 'Incorrect — does not mention catalyst function.',
        'improvement': 'Mention that enzymes are biological catalysts.',
        'marks_awarded': 0,
        'marks_total': 2,
        'status': 'incorrect',
        'feedback_source': 'original_ai',
    }
    sub1 = Submission(
        assignment_id=asn.id, student_id=stu1.id, status='done',
        result_json=json.dumps({'questions': [dict(base_q)]}),
    )
    sub2 = Submission(
        assignment_id=asn.id, student_id=stu2.id, status='done',
        result_json=json.dumps({'questions': [dict(base_q)]}),
    )
    db_session.add_all([sub1, sub2])
    db_session.commit()
    return t, asn, sub1, sub2


def _make_edit(db_session, sub, asn, teacher, field, edited_text):
    fe = FeedbackEdit(
        submission_id=sub.id,
        criterion_id='1',
        field=field,
        original_text='original',
        edited_text=edited_text,
        edited_by=teacher.id,
        assignment_id=asn.id,
        rubric_version='rv1',
        amend_answer_key=True,
        active=True,
        propagation_status='none',
    )
    db_session.add(fe)
    db_session.commit()
    return fe


def test_propagation_feedback_edit_only_rewrites_feedback(app, db_session):
    """Edit on `feedback` → only `feedback` rewritten on target. `improvement` untouched."""
    from app import _run_propagation_worker
    t, asn, sub1, sub2 = _make_chain_two_subs(db_session)
    fe = _make_edit(db_session, sub1, asn, t, 'feedback', 'Stricter: must say "biological catalyst".')

    fake = {'feedback': 'NEW FEEDBACK FROM HAIKU', 'marks_awarded': None}
    with patch('ai_marking.refresh_criterion_feedback', return_value=fake):
        _run_propagation_worker(app, fe.id, [sub2.id])

    db_session.expire_all()
    sub2_fresh = Submission.query.get(sub2.id)
    q = json.loads(sub2_fresh.result_json)['questions'][0]
    assert q['feedback'] == 'NEW FEEDBACK FROM HAIKU'
    # improvement must NOT have changed
    assert q['improvement'] == 'Mention that enzymes are biological catalysts.'
    assert q['feedback_source'] == 'propagated'


def test_propagation_improvement_edit_only_rewrites_improvement(app, db_session):
    """Edit on `improvement` → only `improvement` rewritten. `feedback` untouched."""
    from app import _run_propagation_worker
    t, asn, sub1, sub2 = _make_chain_two_subs(db_session)
    fe = _make_edit(db_session, sub1, asn, t, 'improvement', 'Be specific: name the catalyst function.')

    fake = {'improvement': 'NEW IMPROVEMENT FROM HAIKU', 'marks_awarded': None}
    with patch('ai_marking.refresh_criterion_feedback', return_value=fake):
        _run_propagation_worker(app, fe.id, [sub2.id])

    db_session.expire_all()
    sub2_fresh = Submission.query.get(sub2.id)
    q = json.loads(sub2_fresh.result_json)['questions'][0]
    assert q['improvement'] == 'NEW IMPROVEMENT FROM HAIKU'
    assert q['feedback'] == 'Incorrect — does not mention catalyst function.'
    assert q['feedback_source'] == 'propagated'


def test_propagation_lowers_marks_when_haiku_returns_lower_value(app, db_session):
    """Haiku decides marks_awarded=0 → target's marks drop."""
    from app import _run_propagation_worker
    t, asn, sub1, sub2 = _make_chain_two_subs(db_session)
    # Start sub2 with partial credit so we can observe a decrease.
    res2 = json.loads(sub2.result_json)
    res2['questions'][0]['marks_awarded'] = 1
    sub2.result_json = json.dumps(res2)
    db_session.commit()
    fe = _make_edit(db_session, sub1, asn, t, 'feedback', 'Stricter standard.')

    fake = {'feedback': 'F', 'marks_awarded': 0}
    with patch('ai_marking.refresh_criterion_feedback', return_value=fake):
        _run_propagation_worker(app, fe.id, [sub2.id])

    db_session.expire_all()
    q = json.loads(Submission.query.get(sub2.id).result_json)['questions'][0]
    assert q['marks_awarded'] == 0


def test_propagation_raises_marks_when_haiku_returns_higher_value(app, db_session):
    """Haiku decides marks_awarded=2 → target's marks rise."""
    from app import _run_propagation_worker
    t, asn, sub1, sub2 = _make_chain_two_subs(db_session)
    fe = _make_edit(db_session, sub1, asn, t, 'feedback', 'More lenient standard.')

    fake = {'feedback': 'F', 'marks_awarded': 2}
    with patch('ai_marking.refresh_criterion_feedback', return_value=fake):
        _run_propagation_worker(app, fe.id, [sub2.id])

    db_session.expire_all()
    q = json.loads(Submission.query.get(sub2.id).result_json)['questions'][0]
    assert q['marks_awarded'] == 2


def test_propagation_keeps_marks_when_haiku_returns_none(app, db_session):
    """Haiku returns marks_awarded=None → target's marks unchanged."""
    from app import _run_propagation_worker
    t, asn, sub1, sub2 = _make_chain_two_subs(db_session)
    fe = _make_edit(db_session, sub1, asn, t, 'feedback', 'Just clearer phrasing.')

    fake = {'feedback': 'F', 'marks_awarded': None}
    with patch('ai_marking.refresh_criterion_feedback', return_value=fake):
        _run_propagation_worker(app, fe.id, [sub2.id])

    db_session.expire_all()
    q = json.loads(Submission.query.get(sub2.id).result_json)['questions'][0]
    assert q['marks_awarded'] == 0  # unchanged from base_q


def test_propagation_passes_target_field_to_refresh_call(app, db_session):
    """Verify the worker invokes refresh_criterion_feedback with target_field=edit.field."""
    from app import _run_propagation_worker
    t, asn, sub1, sub2 = _make_chain_two_subs(db_session)
    fe = _make_edit(db_session, sub1, asn, t, 'improvement', 'edited')

    fake = {'improvement': 'X', 'marks_awarded': None}
    with patch('ai_marking.refresh_criterion_feedback', return_value=fake) as mock_refresh:
        _run_propagation_worker(app, fe.id, [sub2.id])

    assert mock_refresh.called, 'refresh_criterion_feedback should have been called'
    kwargs = mock_refresh.call_args.kwargs
    assert kwargs.get('target_field') == 'improvement'
