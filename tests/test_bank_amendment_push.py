"""UP-: bank amendment push with optimistic concurrency."""

import json
import uuid as _uuid
from datetime import datetime, timezone
from db import db, Teacher, Assignment, AssignmentBank, FeedbackEdit


def _setup(db_session):
    tid = 't-' + _uuid.uuid4().hex[:8]
    t = Teacher(id=tid, name='Joe',
                code='C' + _uuid.uuid4().hex[:6].upper(), role='owner')
    db_session.add(t)
    bank = AssignmentBank(id='b-' + _uuid.uuid4().hex[:8], title='Bio', subject='biology',
                          answer_key=b'OLD BANK ANSWER KEY')
    db_session.add(bank)
    asn = Assignment(
        id='a-' + _uuid.uuid4().hex[:8],
        classroom_code='C' + _uuid.uuid4().hex[:6].upper(),
        subject='biology', title='Bio',
        teacher_id=t.id,
        rubrics=b'rubric',
        answer_key=b'Original AK text\n',
    )
    db_session.add(asn)
    db_session.commit()
    return t, bank, asn


def _login(client, teacher_id):
    with client.session_transaction() as s:
        s['teacher_id'] = teacher_id
        s['authenticated'] = True


def test_push_writes_merged_answer_key_to_bank(app, db_session, client):
    from ai_marking import _rubric_version_hash

    t, bank, asn = _setup(db_session)
    rv = _rubric_version_hash(asn)
    # High submission_id avoids cross-test contamination: other tests may
    # autoincrement Submission ids starting from 1 and then run
    # `FeedbackEdit.query.filter_by(submission_id=sub.id).count() == 0`,
    # which would otherwise pick up this placeholder row.
    placeholder_sid = 9_000_000 + (int(_uuid.uuid4().int) % 100_000)
    db_session.add(FeedbackEdit(
        submission_id=placeholder_sid, criterion_id='1', field='feedback',
        original_text='X', edited_text='Accept "powerhouse of the cell"',
        edited_by=t.id, assignment_id=asn.id, rubric_version=rv,
        scope='amendment', amend_answer_key=True, active=True,
    ))
    db_session.commit()

    _login(client, t.id)

    rv_resp = client.post(
        f'/teacher/assignment/{asn.id}/push-amendments-to-bank',
        json={'bank_id': bank.id, 'last_known_bank_pushed_at': None},
    )
    assert rv_resp.status_code == 200, rv_resp.get_data(as_text=True)
    db_session.refresh(asn)
    db_session.refresh(bank)
    assert asn.bank_pushed_at is not None
    # Bank's answer_key now contains the merged text including the amendment.
    assert bank.answer_key is not None
    assert b'powerhouse' in bank.answer_key


def test_push_returns_409_on_concurrent_write(app, db_session, client):
    t, bank, asn = _setup(db_session)
    # Simulate someone else having pushed at 2026-05-01.
    asn.bank_pushed_at = datetime(2026, 5, 1, tzinfo=timezone.utc)
    db_session.commit()

    _login(client, t.id)

    rv_resp = client.post(
        f'/teacher/assignment/{asn.id}/push-amendments-to-bank',
        json={
            'bank_id': bank.id,
            # Client thinks the bank was last pushed before that — stale.
            'last_known_bank_pushed_at': '2026-04-15T00:00:00Z',
        },
    )
    assert rv_resp.status_code == 409


def test_push_returns_409_when_server_has_ts_and_client_does_not(app, db_session, client):
    t, bank, asn = _setup(db_session)
    asn.bank_pushed_at = datetime(2026, 5, 1, tzinfo=timezone.utc)
    db_session.commit()
    _login(client, t.id)

    rv_resp = client.post(
        f'/teacher/assignment/{asn.id}/push-amendments-to-bank',
        json={'bank_id': bank.id, 'last_known_bank_pushed_at': None},
    )
    assert rv_resp.status_code == 409


def test_push_invalid_payload_400(app, db_session, client):
    t, bank, asn = _setup(db_session)
    _login(client, t.id)

    # Missing bank_id
    rv_resp = client.post(
        f'/teacher/assignment/{asn.id}/push-amendments-to-bank',
        json={'last_known_bank_pushed_at': None},
    )
    assert rv_resp.status_code == 400
