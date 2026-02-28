# backend/app/main.py
"""
FastAPI application entry point for DocHub.

Responsibilities:
- Configure structlog to write JSON to dochub.log AND human-readable to stdout.
- Load .env via python-dotenv before any other module reads os.environ.
- Run startup_check() during lifespan — logs warnings for missing env vars
  (does NOT crash the process, so the app can run without Jira configured
  during development or unit testing).
- Call create_db_and_tables() during lifespan (idempotent).
- Mount all API routers under their canonical prefixes.
- Expose GET /health for Railway health checks.
- Apply CORS middleware with open origins (hackathon; tighten for production).

Structlog design:
  Two processors chains share the same bound logger factory:
  1. File handler  → JSONRenderer → dochub.log  (machine-readable)
  2. Stream handler → ConsoleRenderer → stdout   (human-readable during dev)

  We use structlog's stdlib integration so that uvicorn's internal Python
  logging (access log, error log) is also captured to dochub.log in JSON.

Import order matters:
  1. dotenv.load_dotenv()     — must be first so os.environ is populated
  2. structlog.configure()    — must precede any structlog.get_logger() call
  3. model imports            — needed so SQLModel metadata is populated before
                               create_db_and_tables()
  4. router imports           — after models, so FK references resolve
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# 1. Load environment variables from backend/.env (or project-root .env).
#    This must happen before any os.environ read in imported modules.
# ---------------------------------------------------------------------------
load_dotenv()

# ---------------------------------------------------------------------------
# 2. Configure structlog.
#    Strategy: configure once at import time so that all modules that call
#    structlog.get_logger() at module level get correctly configured loggers.
# ---------------------------------------------------------------------------

_LOG_FILE = os.environ.get("LOG_FILE", "dochub.log")

# --- stdlib root logger: captures uvicorn, httpx, chromadb log output ---
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.DEBUG)

# File handler — JSON, one object per line.
_file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)

# Stdout handler — plain text for developer ergonomics.
_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setLevel(logging.INFO)

logging.basicConfig(handlers=[_file_handler, _stream_handler], level=logging.DEBUG, force=True)

# Shared processors run on every log call before the renderer.
_shared_processors: list[structlog.types.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]

structlog.configure(
    processors=[
        *_shared_processors,
        # Route stdlib log records through the same structlog chain.
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

# File handler formatter — JSON renderer.
_file_formatter = structlog.stdlib.ProcessorFormatter(
    processors=[
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.processors.JSONRenderer(),
    ],
    foreign_pre_chain=_shared_processors,
)
_file_handler.setFormatter(_file_formatter)

# Stdout formatter — human-readable console renderer.
_console_formatter = structlog.stdlib.ProcessorFormatter(
    processors=[
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
        structlog.dev.ConsoleRenderer(),
    ],
    foreign_pre_chain=_shared_processors,
)
_stream_handler.setFormatter(_console_formatter)

# Module-level logger — used for startup events in this file.
logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# 3. Application imports — after env + logging are configured.
#    Import models explicitly so SQLModel.metadata is populated before
#    create_db_and_tables() runs inside the lifespan handler.
# ---------------------------------------------------------------------------
import app.models  # noqa: F401, E402 — side-effect import for SQLModel metadata registration
from app.database import create_db_and_tables  # noqa: E402
from app.services.versions import PRD_PROMPT_VERSION  # noqa: E402

# ---------------------------------------------------------------------------
# Required environment variables.
# Jira vars are WARNING-only — the app can run without Jira (dev / unit tests).
# AI vars are also WARNING-only to allow CI import checks without real keys.
# ---------------------------------------------------------------------------
_REQUIRED_VARS: list[tuple[str, str]] = [
    ("ANTHROPIC_API_KEY", "warning"),
    ("OPENAI_API_KEY", "warning"),
    ("JIRA_BASE_URL", "warning"),
    ("JIRA_EMAIL", "warning"),
    ("JIRA_API_TOKEN", "warning"),
    ("JIRA_PROJECT_KEY", "warning"),
]


def startup_check() -> None:
    """
    Validate required environment variables at application startup.

    Logs a structured warning for each missing variable rather than raising,
    so the application can start in development or CI without all credentials
    configured. Routes that depend on missing credentials will raise at
    request time with a descriptive error.
    """
    missing: list[str] = []
    for var_name, _level in _REQUIRED_VARS:
        if not os.environ.get(var_name):
            missing.append(var_name)
            logger.warning(
                "startup_check.missing_env_var",
                var=var_name,
                impact="requests requiring this credential will fail at runtime",
            )

    if not missing:
        logger.info("startup_check.ok", checked_vars=[v for v, _ in _REQUIRED_VARS])
    else:
        logger.warning(
            "startup_check.incomplete",
            missing_vars=missing,
            message="Application started with missing env vars — some endpoints will be unavailable.",
        )


# ---------------------------------------------------------------------------
# 4. Lifespan — startup + shutdown logic.
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    FastAPI lifespan context manager.

    Startup:
      1. startup_check() — log warnings for missing env vars.
      2. create_db_and_tables() — idempotent DDL (CREATE TABLE IF NOT EXISTS).

    Shutdown:
      Currently no teardown needed (SQLite handles connection close on GC;
      ChromaDB PersistentClient is process-scoped and cleaned up by OS).
      Add explicit cleanup here if migrating to Postgres connection pools.
    """
    logger.info("dochub.startup", prompt_version=PRD_PROMPT_VERSION)
    startup_check()
    create_db_and_tables()
    logger.info("dochub.ready", message="Database tables created/verified. Application ready.")

    yield

    logger.info("dochub.shutdown")


# ---------------------------------------------------------------------------
# 5. FastAPI application instance.
# ---------------------------------------------------------------------------

app = FastAPI(
    title="DocHub API",
    description=(
        "Turns meeting transcripts into context-aware PRDs, "
        "then into Jira tickets via hybrid RAG."
    ),
    version=PRD_PROMPT_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------------------------------------------------------------------------
# 6. CORS middleware — open for hackathon; restrict origins in production.
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 7. Router mounts.
#    Routers are imported here (not at module top) to avoid circular imports:
#    routes import services, services import models, models import versions/workflow —
#    all of which must be fully loaded before any FastAPI route decorator runs.
# ---------------------------------------------------------------------------
from app.routes import export, generate, jira, projects, upload  # noqa: E402

app.include_router(upload.router, prefix="/upload", tags=["upload"])
app.include_router(generate.router, prefix="/generate", tags=["generate"])
app.include_router(export.router, prefix="/export", tags=["export"])
app.include_router(jira.router, prefix="/jira", tags=["jira"])
app.include_router(projects.router, prefix="/projects", tags=["projects"])

# ---------------------------------------------------------------------------
# 8. Health endpoint — no authentication, no DB call (Railway health check).
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    """
    Lightweight health check endpoint.

    Returns the current PRD prompt version so the deployment pipeline can
    verify the correct artifact was deployed.
    """
    return {"status": "ok", "version": PRD_PROMPT_VERSION}
