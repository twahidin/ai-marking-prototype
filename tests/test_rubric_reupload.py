"""UP-: rubric re-upload auto-carries amendments to new rubric_version."""

import uuid as _uuid
from io import BytesIO
from db import db, Teacher, Assignment, FeedbackEdit

# Minimal valid PDF blobs (pass the %PDF magic-byte check in _detect_mime).
_PDF_OLD = b'%PDF-1.4 old-rubric-content'
_PDF_NEW = b'%PDF-1.4 new-rubric-content-different'
_PDF_PLAIN = b'%PDF-1.4 plain-rubric'
_PDF_NEWER = b'%PDF-1.4 newer-rubric-content'


def test_reupload_carries_amendments_to_new_rubric_version(app, db_session, client):
    from ai_marking import _rubric_version_hash

    tid = 't-' + _uuid.uuid4().hex[:8]
    aid = 'ru-' + _uuid.uuid4().hex[:8]
    t = Teacher(id=tid, name='Joe', code='C' + _uuid.uuid4().hex[:6].upper(), role='owner')
    asn = Assignment(id=aid, classroom_code='RU' + _uuid.uuid4().hex[:4].upper(),
                     subject='biology', title='Bio', teacher_id=t.id,
                     assign_type='rubrics',
                     rubrics=_PDF_OLD,
                     provider='anthropic', model='claude-sonnet-4-6')
    asn.set_api_keys({'anthropic': 'sk-fake'})
    db_session.add_all([t, asn])
    db_session.commit()
    old_rv = _rubric_version_hash(asn)
    placeholder_sid = 9_500_000 + (int(_uuid.uuid4().int) % 100_000)
    db_session.add(FeedbackEdit(
        submission_id=placeholder_sid, criterion_id='3', field='feedback',
        original_text='X', edited_text='Accept powerhouse',
        edited_by=t.id, assignment_id=asn.id,
        rubric_version=old_rv,
        amend_answer_key=True, active=True,
    ))
    db_session.commit()

    with client.session_transaction() as s:
        s['teacher_id'] = t.id
        s['authenticated'] = True

    # Post a multipart edit with a new rubric file. Pin provider/model so the
    # /edit route's provider check passes (we set api_keys on the assignment).
    rv_resp = client.post(
        f'/teacher/assignment/{asn.id}/edit',
        data={
            'title': asn.title,
            'subject': asn.subject,
            'provider': asn.provider,
            'model': asn.model,
            'rubrics': (BytesIO(_PDF_NEW), 'rubric.pdf'),
        },
        content_type='multipart/form-data',
    )
    assert rv_resp.status_code == 200, rv_resp.get_data(as_text=True)
    payload = rv_resp.get_json() or {}
    assert payload.get('success') is True
    # Server should report carried amendments count.
    assert payload.get('carried_amendments') == 1

    db_session.refresh(asn)
    new_rv = _rubric_version_hash(asn)
    assert new_rv != old_rv
    fe = FeedbackEdit.query.filter_by(assignment_id=asn.id, active=True).first()
    assert fe is not None
    assert fe.rubric_version == new_rv


def test_no_amendments_no_carryover(app, db_session, client):
    """No active amend_answer_key edits → carried_amendments == 0."""
    tid = 't-' + _uuid.uuid4().hex[:8]
    aid = 'ru-' + _uuid.uuid4().hex[:8]
    t = Teacher(id=tid, name='Joe', code='C' + _uuid.uuid4().hex[:6].upper(), role='owner')
    asn = Assignment(id=aid, classroom_code='RU' + _uuid.uuid4().hex[:4].upper(),
                     subject='biology', title='Bio', teacher_id=t.id,
                     assign_type='rubrics',
                     rubrics=_PDF_PLAIN,
                     provider='anthropic', model='claude-sonnet-4-6')
    asn.set_api_keys({'anthropic': 'sk-fake'})
    db_session.add_all([t, asn])
    db_session.commit()

    with client.session_transaction() as s:
        s['teacher_id'] = t.id
        s['authenticated'] = True

    rv_resp = client.post(
        f'/teacher/assignment/{asn.id}/edit',
        data={
            'title': asn.title,
            'subject': asn.subject,
            'provider': asn.provider,
            'model': asn.model,
            'rubrics': (BytesIO(_PDF_NEWER), 'rubric.pdf'),
        },
        content_type='multipart/form-data',
    )
    assert rv_resp.status_code == 200
    payload = rv_resp.get_json() or {}
    assert payload.get('carried_amendments') == 0
