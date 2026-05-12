"""UP-31: error-path canaries. These pin the response codes the audit care
about — wrong codes mean clients fail open or the user sees the wrong
error page."""


def test_unknown_route_returns_404(client):
    rv = client.get('/this-route-definitely-does-not-exist')
    assert rv.status_code == 404


def test_unauthenticated_api_call_blocked(client):
    """Hitting an authenticated API endpoint without a session must not
    leak data (any non-200 status is fine — we just don't want a 200)."""
    rv = client.get('/api/class/00000000-0000-0000-0000-000000000000/assignments')
    assert rv.status_code != 200


def test_anonymous_save_keys_returns_401(client):
    rv = client.post('/save-keys', json={})
    assert rv.status_code == 401
