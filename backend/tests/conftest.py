"""
backend/tests/conftest.py

Shared pytest fixtures for DocHub test suite.

Design decisions:
- In-memory SQLite engine is created fresh per test session; each test that needs
  DB access gets a function-scoped session so state never leaks between tests.
- The FastAPI TestClient uses dependency_overrides to swap get_session for the
  in-memory session, ensuring no production DB is touched.
- All external service interactions (Anthropic, OpenAI, Jira) are prevented at
  the fixture level; individual test files apply additional mocks as needed.
- sys.path manipulation ensures `app.*` imports resolve when pytest is run from
  the `backend/` directory.
"""

from __future__ import annotations

import sys
import os
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

# ---------------------------------------------------------------------------
# Path setup — ensure `app` package is importable from `backend/tests/`.
# ---------------------------------------------------------------------------
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# ---------------------------------------------------------------------------
# Dummy env vars — prevent startup_check() warnings and avoid real API calls.
# Must be set BEFORE importing app.main (which loads dotenv at import time).
# ---------------------------------------------------------------------------
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key-not-real")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key-not-real")
os.environ.setdefault("JIRA_BASE_URL", "https://test.atlassian.net")
os.environ.setdefault("JIRA_EMAIL", "test@example.com")
os.environ.setdefault("JIRA_API_TOKEN", "test-jira-token-not-real")
os.environ.setdefault("JIRA_PROJECT_KEY", "TEST")
# Redirect log output to a temp file so tests don't fail when dochub.log is
# a directory (Docker bind-mount artefact) or when the CWD is read-only.
import tempfile as _tempfile
os.environ.setdefault("LOG_FILE", os.path.join(_tempfile.gettempdir(), "dochub_test.log"))


# ---------------------------------------------------------------------------
# In-memory SQLite engine — function-scoped so each test starts clean.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def engine():
    """
    Create a fresh in-memory SQLite engine with all tables.

    Uses StaticPool so the same in-memory DB is reused within the same
    connection (required for SQLite :memory: to work across multiple
    SQLModel Session instances within one test).
    """
    # Import models here to ensure SQLModel metadata is populated.
    import app.models  # noqa: F401

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)
    yield test_engine
    SQLModel.metadata.drop_all(test_engine)


@pytest.fixture(scope="function")
def db_session(engine) -> Generator[Session, None, None]:
    """
    Provide a SQLModel Session backed by the in-memory test engine.

    Each test gets a fresh session; the session is automatically closed
    at the end of the test. No production DB is touched.
    """
    with Session(engine) as session:
        yield session


# ---------------------------------------------------------------------------
# FastAPI TestClient with DB override.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def test_client(engine) -> Generator[TestClient, None, None]:
    """
    FastAPI TestClient with get_session overridden to use in-memory SQLite.

    The override is applied before the test runs and removed after, so it
    never affects other tests or the production application object.
    """
    from app.main import app
    from app.database import get_session

    def _override_get_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
