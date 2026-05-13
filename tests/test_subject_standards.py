"""UP-: AI topic tagging + SubjectStandard retrieval / promotion."""

from unittest.mock import patch


def test_extract_assignment_topic_keys_returns_list_per_question(app):
    from ai_marking import extract_assignment_topic_keys
    import json
    fake_response = {
        'questions': [
            {'question_num': 1, 'topic_keys': ['enzymes', 'terminology_precision']},
            {'question_num': 2, 'topic_keys': ['cellular_respiration']},
        ]
    }
    with app.app_context():
        with patch('ai_marking._simple_completion', return_value=json.dumps(fake_response)):
            result = extract_assignment_topic_keys(
                provider='anthropic',
                model='claude-haiku-4-5',
                session_keys={'anthropic': 'sk-fake'},
                subject='biology',
                questions=[
                    {'question_num': 1, 'text': 'State one factor affecting enzyme activity.', 'answer_key': 'temperature, pH'},
                    {'question_num': 2, 'text': 'Explain ATP production.', 'answer_key': 'mitochondria, ATP synthase'},
                ],
            )
    assert result == [
        ['enzymes', 'terminology_precision'],
        ['cellular_respiration'],
    ]


def test_extract_assignment_topic_keys_filters_unknown_keys(app):
    from ai_marking import extract_assignment_topic_keys
    import json
    fake_response = {
        'questions': [
            {'question_num': 1, 'topic_keys': ['enzymes', 'flux_capacitor']},
        ]
    }
    with app.app_context():
        with patch('ai_marking._simple_completion', return_value=json.dumps(fake_response)):
            result = extract_assignment_topic_keys(
                provider='anthropic',
                model='claude-haiku-4-5',
                session_keys={'anthropic': 'sk-fake'},
                subject='biology',
                questions=[{'question_num': 1, 'text': 'x', 'answer_key': 'y'}],
            )
    assert result == [['enzymes']]


def test_extract_assignment_topic_keys_returns_empty_on_failure(app):
    from ai_marking import extract_assignment_topic_keys
    with app.app_context():
        with patch('ai_marking._simple_completion', side_effect=Exception('network')):
            result = extract_assignment_topic_keys(
                provider='anthropic',
                model='claude-haiku-4-5',
                session_keys={'anthropic': 'sk-fake'},
                subject='biology',
                questions=[{'question_num': 1, 'text': 'x', 'answer_key': 'y'}],
            )
    assert result == [[]]


def test_extract_standard_topic_keys_from_edit(app):
    from ai_marking import extract_standard_topic_keys
    import json
    fake_response = {'topic_keys': ['enzymes', 'terminology_precision']}
    with app.app_context():
        with patch('ai_marking._simple_completion', return_value=json.dumps(fake_response)):
            keys = extract_standard_topic_keys(
                provider='anthropic',
                model='claude-haiku-4-5',
                session_keys={'anthropic': 'sk-fake'},
                subject='biology',
                question_text='State one factor affecting enzyme activity.',
                original_feedback='Correct - heat affects enzyme rate.',
                edited_feedback="Must say 'temperature', not 'heat'.",
                theme_key='terminology_precision',
            )
    assert keys == ['enzymes', 'terminology_precision']


# ---------------------------------------------------------------------------
# Task 4.1: promote_to_subject_standard helpers
# ---------------------------------------------------------------------------

def _seed_chain(db_session, *, subject='biology'):
    """Helper to set up Teacher → Assignment → Student → Submission chain.
    Returns (teacher, assignment, student, submission)."""
    from db import Teacher, Assignment, Student, Submission
    import uuid as _uuid
    t = Teacher(id='t-' + _uuid.uuid4().hex[:8], name='Joe',
                code='C' + _uuid.uuid4().hex[:6].upper(), role='teacher')
    db_session.add(t)
    asn = Assignment(id='a-' + _uuid.uuid4().hex[:8],
                     classroom_code='C' + _uuid.uuid4().hex[:6].upper(),
                     subject=subject, title='Test')
    db_session.add(asn)
    db_session.commit()
    stu = Student(assignment_id=asn.id, index_number='1', name='Stu')
    db_session.add(stu)
    db_session.commit()
    sub = Submission(assignment_id=asn.id, student_id=stu.id, result_json='{}')
    db_session.add(sub)
    db_session.commit()
    return t, asn, stu, sub


def test_promote_creates_new_standard_when_no_similar_exists(app, db_session):
    from db import SubjectStandard, FeedbackEdit
    from subject_standards import promote_to_subject_standard
    from unittest.mock import patch
    import json, uuid as _uuid

    # Use a unique subject so this test is isolated from any leftover DB rows.
    unique_subject = 'biology_' + _uuid.uuid4().hex[:8]
    t, asn, _stu, sub = _seed_chain(db_session, subject=unique_subject)
    fe = FeedbackEdit(
        submission_id=sub.id, criterion_id='1', field='feedback',
        original_text='Correct - heat affects enzyme rate.',
        edited_text="Must say 'temperature', not 'heat'.",
        edited_by=t.id, theme_key='terminology_precision',
        assignment_id=asn.id, rubric_version='v1', scope='promoted', active=True,
    )
    db_session.add(fe)
    db_session.commit()

    with patch('subject_standards.extract_standard_topic_keys',
              return_value=['enzymes', 'terminology_precision']):
        ss_id = promote_to_subject_standard(
            feedback_edit_id=fe.id,
            provider='anthropic', model='claude-haiku-4-5',
            session_keys={'anthropic': 'sk-fake'},
        )

    ss = SubjectStandard.query.get(ss_id)
    assert ss is not None
    assert ss.subject == unique_subject
    assert ss.status == 'pending_review'
    assert ss.reinforcement_count == 1
    assert json.loads(ss.topic_keys) == ['enzymes', 'terminology_precision']


def test_promote_reinforces_existing_similar_standard(app, db_session):
    from db import SubjectStandard, FeedbackEdit
    from subject_standards import promote_to_subject_standard
    from unittest.mock import patch
    import uuid as _uuid

    # Use a unique subject so this test is isolated from any leftover DB rows.
    unique_subject = 'biology_' + _uuid.uuid4().hex[:8]
    t, asn, _stu, sub = _seed_chain(db_session, subject=unique_subject)
    pre = SubjectStandard(
        subject=unique_subject,
        text="Must say 'temperature', not 'heat'.",
        topic_keys='["enzymes", "terminology_precision"]',
        theme_key='terminology_precision',
        status='active', created_by=t.id, reinforcement_count=3,
    )
    db_session.add(pre)
    db_session.commit()
    pre_reinforcement_count = pre.reinforcement_count  # capture before promote

    fe = FeedbackEdit(
        submission_id=sub.id, criterion_id='1', field='feedback',
        original_text='Heat is fine.',
        edited_text="Must say temperature, not heat.",
        edited_by=t.id, theme_key='terminology_precision',
        assignment_id=asn.id, rubric_version='v1', scope='promoted', active=True,
    )
    db_session.add(fe)
    db_session.commit()

    with patch('subject_standards.extract_standard_topic_keys',
              return_value=['enzymes', 'terminology_precision']):
        ss_id = promote_to_subject_standard(
            feedback_edit_id=fe.id,
            provider='anthropic', model='claude-haiku-4-5',
            session_keys={'anthropic': 'sk-fake'},
        )

    # Only one standard should exist for this unique subject (no new row created).
    assert SubjectStandard.query.filter_by(subject=unique_subject).count() == 1
    # The returned standard must be the pre-seeded one (by text match).
    returned = SubjectStandard.query.get(ss_id)
    assert returned is not None
    assert returned.text == pre.text
    # Reinforcement count must have incremented by 1.
    db_session.refresh(returned)
    assert returned.reinforcement_count == pre_reinforcement_count + 1


# ---------------------------------------------------------------------------
# Task 4.2: retrieve_subject_standards
# ---------------------------------------------------------------------------

def test_retrieve_subject_standards_returns_topic_matched_active(app, db_session):
    from db import SubjectStandard
    from subject_standards import retrieve_subject_standards
    import uuid as _uuid

    subj = f'biology_retrieve_match_{_uuid.uuid4().hex[:6]}'

    db_session.add_all([
        SubjectStandard(subject=subj, text='A', topic_keys='["enzymes"]',
                        status='active', created_by='t-fake-1', reinforcement_count=5),
        SubjectStandard(subject=subj, text='B', topic_keys='["genetics"]',
                        status='active', created_by='t-fake-1', reinforcement_count=10),
        SubjectStandard(subject=subj, text='C', topic_keys='["enzymes"]',
                        status='pending_review', created_by='t-fake-1', reinforcement_count=20),
    ])
    db_session.commit()

    out = retrieve_subject_standards(
        subject=subj,
        per_question_topic_keys=[['enzymes', 'terminology_precision']],
    )
    texts = [s.text for s in out]
    assert 'A' in texts
    assert 'B' not in texts
    assert 'C' not in texts


def test_retrieve_subject_standards_respects_per_topic_quota_and_cap(app, db_session):
    from db import SubjectStandard
    from subject_standards import retrieve_subject_standards
    import uuid as _uuid

    subj = f'biology_quota_{_uuid.uuid4().hex[:6]}'

    for i in range(5):
        db_session.add(SubjectStandard(
            subject=subj, text=f'enzymes-{i}',
            topic_keys='["enzymes"]', status='active',
            created_by='t-fake-2', reinforcement_count=i,
        ))
    db_session.commit()

    out = retrieve_subject_standards(
        subject=subj,
        per_question_topic_keys=[['enzymes']],
    )
    assert len(out) == 3
    assert out[0].text == 'enzymes-4'
    assert out[2].text == 'enzymes-2'


def test_retrieve_returns_empty_when_no_topics(app, db_session):
    from subject_standards import retrieve_subject_standards
    out = retrieve_subject_standards(subject='biology', per_question_topic_keys=[[]])
    assert out == []
