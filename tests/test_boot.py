"""UP-31: boot path + idempotent migrations.

These are the minimum-viable canary tests: if any of them goes red, the
app probably won't boot in production either.
"""


def test_app_imports(app):
    """The fixture itself imports app, but assert one well-known route is
    registered so we catch a missing-decorator regression."""
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert '/' in rules
    assert '/verify-code' in rules


def test_hub_responds(client):
    """Anonymous GET / either renders the gate (200) or redirects to the
    first-run setup wizard (302). Anything else means routing broke."""
    rv = client.get('/')
    assert rv.status_code in (200, 302)


def test_security_headers_present(client):
    """UP-29: every response carries CSP + HSTS + X-Content-Type-Options."""
    rv = client.get('/')
    assert rv.headers.get('Content-Security-Policy', '').startswith('default-src')
    assert 'Strict-Transport-Security' in rv.headers
    assert rv.headers.get('X-Content-Type-Options') == 'nosniff'


def test_migrations_are_idempotent(app):
    """UP-31: running `_migrate_add_columns` twice in a row must be a no-op
    (no IntegrityError, no schema drift). This is the cheapest test that
    would have caught the kind of "ALTER TABLE ADD COLUMN ... IF NOT EXISTS"
    bugs that have bitten us before."""
    from db import _migrate_add_columns
    with app.app_context():
        _migrate_add_columns(app)
        _migrate_add_columns(app)  # would raise if not idempotent
