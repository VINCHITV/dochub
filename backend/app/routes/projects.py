"""
Project-level routes mounted at /projects.

GET  /projects/{project_id}         — rehydration endpoint for frontend
POST /projects/{project_id}/approve — advance PRD_GENERATED → PRD_APPROVED
"""

from __future__ import annotations

import json
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.database import get_session
from app.models import Project
from app.services.workflow import WorkflowStatus, advance_status

logger = structlog.get_logger()

router = APIRouter()


@router.get("/{project_id}")
async def get_project(
    project_id: str,
    db: Annotated[Session, Depends(get_session)],
) -> dict:
    """Return a Project row as JSON for frontend rehydration."""
    statement = select(Project).where(Project.id == project_id)
    project: Optional[Project] = db.exec(statement).first()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )

    prd_parsed: Optional[dict] = None
    if project.prd_json:
        try:
            prd_parsed = json.loads(project.prd_json)
        except json.JSONDecodeError:
            prd_parsed = None

    return {
        "id": project.id,
        "name": project.name,
        "status": project.status.value,
        "transcript_text": project.transcript_text,
        "prd_json": prd_parsed,
        "prompt_version": project.prompt_version,
        "generator_model": project.generator_model,
        "embedding_model": project.embedding_model,
        "extractor_model": project.extractor_model,
        "created_at": project.created_at.isoformat(),
    }


@router.post("/{project_id}/approve", status_code=status.HTTP_200_OK)
async def approve_prd(
    project_id: str,
    db: Annotated[Session, Depends(get_session)],
) -> dict:
    """Advance PRD_GENERATED → PRD_APPROVED."""
    updated_project = advance_status(
        project_id=project_id,
        expected_current=WorkflowStatus.PRD_GENERATED,
        db=db,
    )
    logger.info("projects.prd_approved", project_id=project_id)
    return {"status": updated_project.status.value, "project_id": project_id}
