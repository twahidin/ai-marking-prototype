"""UP-31: shared test fixtures.

Tests import the real Flask app (`app.app`) but redirect the database to a
per-session SQLite file in a tmp dir. `init_db` runs against that file
during `import app`, so the env vars below MUST be set before any test
module imports `app`. We do that here at conftest load time, which pytest
runs strictly before collecting any test file.
"""

import os
import sys
import tempfile
from pathlib import Path

# Repo root on sys.path so `import app` works when pytest is run from the
# repo root or from inside tests/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Redirect SQLite to a throwaway file. `:memory:` would work too, but each
# new connection in a process gets its own in-memory DB which makes the
# Flask app + a follow-up `db.session` from a test fixture see different
# databases under some SQLAlchemy versions. A real file path is boring
# and reliable.
_TMP_DB = tempfile.NamedTemporaryFile(prefix='ai_marking_test_', suffix='.db', delete=False)
_TMP_DB.close()
os.environ['DATABASE_URL'] = f'sqlite:///{_TMP_DB.name}'

# Deterministic auth + crypto. Tests assert on these literal values.
os.environ.setdefault('TEACHER_CODE', 'TEST_TEACHER_CODE_1234')
os.environ.setdefault('FLASK_SECRET_KEY', '0123456789abcdef0123456789abcdef')
os.environ.setdefault('FLASK_ENV', 'development')  # disables SESSION_COOKIE_SECURE in test client
# Make sure demo/dept fakes don't seed mid-import.
os.environ['DEMO_MODE'] = 'FALSE'
os.environ['DEPT_MODE'] = 'FALSE'

import pytest  # noqa: E402

# Import the real app after env is set.
from app import app as flask_app  # noqa: E402
from db import db as _db  # noqa: E402


@pytest.fixture(scope='session')
def app():
    """Session-scoped real Flask app, CSRF disabled for ergonomic test posts."""
    flask_app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,  # tests post JSON directly; UP-04 covered separately
    )
    yield flask_app


@pytest.fixture()
def client(app):
    """Fresh test client per test so cookies/session don't leak."""
    return app.test_client()


@pytest.fixture()
def db_session(app):
    """Hand a test the SQLAlchemy session inside an app context."""
    with app.app_context():
        yield _db.session
        _db.session.rollback()
