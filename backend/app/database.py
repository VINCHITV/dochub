# backend/app/database.py
"""
SQLite engine and FastAPI session dependency for DocHub.

Design decisions:
- Database path is `./dochub.db` relative to the working directory (i.e. the
  `backend/` directory when running `uvicorn app.main:app --reload` from there).
  For Railway deployment, mount a persistent volume at the backend directory so
  this file survives container restarts.
- `check_same_thread=False` is required for SQLite when FastAPI's async event
  loop spawns sync DB calls from a threadpool. SQLModel/SQLAlchemy manage
  connection-level thread safety internally.
- `create_db_and_tables()` is called once from main.py lifespan — idempotent
  because SQLModel uses `CREATE TABLE IF NOT EXISTS` semantics.
- `get_session()` is a FastAPI dependency that yields one Session per request
  inside a context manager. The session is committed/rolled back by the
  context manager on exit; routes and services must not call db.commit()
  outside of explicit state-mutation logic (advance_status handles its own commit).

Post-hackathon Postgres migration:
  Replace the SQLite engine line with:
      engine = create_engine(os.environ["DATABASE_URL"], pool_size=10, max_overflow=20)
  No other changes needed — SQLModel's Session API is database-agnostic.
"""

from typing import Generator

from sqlalchemy import event, text as sqlalchemy_text
from sqlmodel import Session, SQLModel, create_engine

DATABASE_URL: str = "sqlite:///./dochub.db"

# `check_same_thread=False` is SQLite-specific; ignored by other dialects.
connect_args: dict[str, bool] = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    # echo=True,  # Uncomment for SQL query debugging during development.
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection: object, connection_record: object) -> None:  # noqa: ARG001
    """
    Enable WAL mode and foreign key enforcement for every new SQLite connection.

    WAL (Write-Ahead Logging) allows concurrent readers during writes — important
    because FastAPI's threadpool may hold multiple connections simultaneously.
    Foreign key enforcement is OFF by default in SQLite and must be set per-connection.
    """
    # Type narrowing: sqlite3 connections have `cursor()`.
    cursor = dbapi_connection.cursor()  # type: ignore[union-attr]
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_db_and_tables() -> None:
    """
    Create all SQLModel tables that do not yet exist.

    Idempotent — safe to call on every application startup.
    All models must be imported before this call so SQLModel's metadata
    registry includes their table definitions. main.py imports models
    before calling this function via the lifespan handler.
    """
    SQLModel.metadata.create_all(engine)


def run_migrations() -> None:
    """
    Apply additive schema migrations that `CREATE TABLE IF NOT EXISTS` cannot handle.

    SQLModel's create_all() only creates missing tables — it never adds columns to
    existing ones. This function uses raw SQL ALTER TABLE with error suppression to
    idempotently add new columns introduced after the initial schema was deployed.

    Pattern: `ALTER TABLE t ADD COLUMN c TYPE DEFAULT v` — SQLite returns
    `OperationalError: duplicate column name` when the column already exists, which
    we catch and ignore. This makes each migration step idempotent.

    Add one entry per new column following the same pattern when future columns
    are introduced.
    """
    migrations: list[str] = [
        # C1: Structured story format fields (Phase 1)
        "ALTER TABLE userstory ADD COLUMN priority TEXT DEFAULT 'medium'",
        "ALTER TABLE userstory ADD COLUMN dependencies TEXT DEFAULT '[]'",
        "ALTER TABLE userstory ADD COLUMN reference_links TEXT DEFAULT '[]'",
        "ALTER TABLE userstory ADD COLUMN story_status TEXT DEFAULT 'open'",
        # A1: Multi-transcript upload (Phase A1)
        # The `transcript` table is a new table introduced in A1 and is created
        # automatically by create_db_and_tables() via SQLModel.metadata.create_all().
        # No ALTER TABLE is needed here. This comment documents the A1 boundary so
        # future migration authors know the table was added in this phase.
        # Open Questions answering: user-provided Q&A answers stored on Project
        "ALTER TABLE project ADD COLUMN qa_answers TEXT DEFAULT '{}'",
        # T-shirt sizing and transcript references on UserStory
        "ALTER TABLE userstory ADD COLUMN size TEXT DEFAULT 'M'",
        "ALTER TABLE userstory ADD COLUMN transcript_references TEXT DEFAULT '[]'",
        # Creator attribution on Project
        "ALTER TABLE project ADD COLUMN created_by TEXT DEFAULT ''",
    ]

    with engine.connect() as conn:
        for ddl in migrations:
            try:
                conn.execute(sqlalchemy_text(ddl))
            except Exception:
                # Column already exists — safe to ignore.
                pass
        conn.commit()


def get_session() -> Generator[Session, None, None]:
    """
    FastAPI dependency that provides one SQLModel Session per request.

    Usage in route:
        @router.get("/example")
        def example(db: Session = Depends(get_session)):
            ...

    The session is closed automatically when the generator is exhausted
    (i.e. after the response is sent). Uncommitted changes are rolled back
    on exception by SQLModel's Session context manager.
    """
    with Session(engine) as session:
        yield session
