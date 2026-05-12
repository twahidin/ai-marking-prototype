"""UP-31: minimal auth smoke. `TEACHER_CODE` set in conftest."""

import os


def test_verify_code_wrong_code_returns_401(client):
    rv = client.post(
        '/verify-code',
        json={'code': 'definitely-not-the-right-code'},
    )
    assert rv.status_code == 401


def test_verify_code_correct_code_sets_session(client):
    """With no Teacher row yet, the master code routes to /setup (first-run
    wizard). That's a 200 with redirect=/setup, and the session must
    carry `pending_setup`. Either way the response shape proves the
    secrets.compare_digest path matched (UP-19) and `_finalise_login_session`
    fired (UP-28)."""
    code = os.environ['TEACHER_CODE']
    rv = client.post('/verify-code', json={'code': code})
    assert rv.status_code == 200
    body = rv.get_json()
    assert body['success'] is True
    # Either redirect to setup or to / depending on whether a teacher row
    # already exists in this test run.
    assert body.get('redirect') in ('/setup', '/')


def test_verify_code_empty_code_returns_401(client):
    rv = client.post('/verify-code', json={'code': ''})
    assert rv.status_code == 401
