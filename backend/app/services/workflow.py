# backend/app/services/workflow.py
"""
Workflow state machine for DocHub's pipeline.

Design notes:
- WorkflowStatus is a str enum so SQLModel stores it as a plain VARCHAR —
  no need for a Postgres ENUM type, no migration pain on SQLite.
- VALID_TRANSITIONS is the single source of truth for legal state moves.
  Any code that advances state MUST call advance_status() — never write
  project.status directly in routes or services.
- advance_status() is intentionally synchronous and performs a read-then-write
  without any await in between. Under SQLite single-worker (the only supported
  topology for ChromaDB HNSW), SQLite serializes writes at the file-lock level,
  making this safe without optimistic locking.
  Migration note for Postgres: wrap read+write in a SELECT ... FOR UPDATE within
  an explicit transaction to guarantee serializable isolation.
- Idempotency: if the project is already at the desired next status, the function
  returns it without error. This covers the case of a client retry after a timeout
  where the first write succeeded.
"""

from __future__ import annotations

import enum
from typing import Optional

from fastapi import HTTPException, status
from sqlmodel import Session, select

# Forward-declare Project to avoid circular import at module load time.
# The actual import happens inside advance_status() body.


class WorkflowStatus(str, enum.Enum):
    """
    Ordered pipeline states for a DocHub Project.

    String values match what is stored in SQLite and returned in API responses.
    Using str as the mixin makes JSON serialization automatic (FastAPI / Pydantic
    will render these as strings, not numeric ordinals).
    """

    TRANSCRIPT_UPLOADED = "TRANSCRIPT_UPLOADED"
    PRD_GENERATED = "PRD_GENERATED"
    PRD_APPROVED = "PRD_APPROVED"
    STORIES_GENERATED = "STORIES_GENERATED"
    JIRA_PUSH_PENDING = "JIRA_PUSH_PENDING"
    JIRA_PUSH_SUCCESS = "JIRA_PUSH_SUCCESS"
    COMPLETED = "COMPLETED"


# ---------------------------------------------------------------------------
# Transition table — single source of truth for legal state moves.
# Each key maps to the SINGLE allowed successor state.
# Terminal states (COMPLETED) are omitted — no transitions out.
# ---------------------------------------------------------------------------
VALID_TRANSITIONS: dict[WorkflowStatus, WorkflowStatus] = {
    WorkflowStatus.TRANSCRIPT_UPLOADED: WorkflowStatus.PRD_GENERATED,
    WorkflowStatus.PRD_GENERATED: WorkflowStatus.PRD_APPROVED,
    WorkflowStatus.PRD_APPROVED: WorkflowStatus.STORIES_GENERATED,
    WorkflowStatus.STORIES_GENERATED: WorkflowStatus.JIRA_PUSH_PENDING,
    WorkflowStatus.JIRA_PUSH_PENDING: WorkflowStatus.JIRA_PUSH_SUCCESS,
    WorkflowStatus.JIRA_PUSH_SUCCESS: WorkflowStatus.COMPLETED,
}


def advance_status(
    project_id: str,
    expected_current: WorkflowStatus,
    db: Session,
) -> "Project":  # type: ignore[name-defined]  # noqa: F821
    """
    Atomically advance a Project's workflow status to its next legal state.

    Args:
        project_id:       UUID string of the target project.
        expected_current: The status the caller believes the project is in now.
                          Acts as an optimistic guard — raises 409 on mismatch.
        db:               Active SQLModel Session (caller owns the transaction).

    Returns:
        The updated Project instance (already committed to the session).

    Raises:
        HTTPException 404  — project not found.
        HTTPException 409  — project status does not match expected_current
                             AND is not already at the desired next status
                             (non-idempotent conflict).
        HTTPException 422  — expected_current has no valid successor (terminal
                             state or unknown state).

    Idempotency:
        If the project is already at the desired next status (e.g. because a
        previous request succeeded but the client timed out and retried), the
        function returns the project without raising an error or writing again.

    SQLite / single-worker safety:
        No await between the SELECT and the UPDATE. SQLite serializes concurrent
        writers at the file-lock level. For Postgres migration: wrap this entire
        function body in `db.exec(text("BEGIN"))` + `SELECT ... FOR UPDATE`.
    """
    # Deferred import to avoid circular dependency at module load time.
    from app.models import Project  # noqa: PLC0415

    next_status: Optional[WorkflowStatus] = VALID_TRANSITIONS.get(expected_current)
    if next_status is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Status '{expected_current.value}' is a terminal state or has no "
                "defined successor in VALID_TRANSITIONS."
            ),
        )

    # Single SELECT — no await between here and the write below.
    statement = select(Project).where(Project.id == project_id)
    project: Optional[Project] = db.exec(statement).first()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )

    # Idempotency guard: already advanced (client retry after timeout).
    if project.status == next_status:
        return project

    # Conflict guard: project is at an unexpected status.
    if project.status != expected_current:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot advance project '{project_id}': "
                f"expected status '{expected_current.value}' but found '{project.status.value}'. "
                "Concurrent modification or out-of-order request detected."
            ),
        )

    project.status = next_status
    db.add(project)
    db.commit()
    db.refresh(project)

    return project
