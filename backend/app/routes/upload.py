# backend/app/routes/upload.py
"""
Upload routes for DocHub.

POST /upload
    Accepts a multipart upload of a .txt or .docx transcript file plus a
    project_name form field.  Creates a new Project row in the database.

    Special case — DOCX re-upload of a previously exported PRD:
    If the .docx contains a hidden [DOCHUB_METADATA] paragraph (written by
    docx_builder._append_hidden_metadata), and the referenced project_id still
    exists in the database, the endpoint returns the existing project rather
    than creating a duplicate.  This auto-linking step requires no manual input
    from the user.

GET /projects/{project_id}
    Returns the full Project row as JSON.  Used by the frontend's
    rehydrateFromServer() call on page reload so Zustand can restore wizard
    state without re-querying every sub-resource.

All business logic lives in services.  This module only handles HTTP concerns:
parsing the request, calling services, returning the response.
"""

from __future__ import annotations

from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from sqlmodel import Session, select

from app.database import get_session
from app.models import Project
from app.services.docx_parser import extract_embedded_metadata
from app.services.file_parser import parse_file
from app.services.workflow import WorkflowStatus

logger = structlog.get_logger()

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /upload
# ---------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_transcript(
    file: UploadFile,
    project_name: Annotated[str, Form()],
    db: Annotated[Session, Depends(get_session)],
) -> dict:
    """
    Parse an uploaded transcript file and create a Project in the database.

    Accepted file types: .txt, .docx

    For .docx uploads, attempts to extract the hidden [DOCHUB_METADATA] paragraph
    written by docx_builder.  If a valid project_id is found and still exists in
    the database, that existing Project is returned (no duplicate created).

    Returns
    -------
    {
        "project_id": str,
        "transcript_text": str,
        "status": str,
        "is_reupload": bool   # true when auto-linked to an existing project
    }
    """
    filename: str = file.filename or ""
    content: bytes = await file.read()

    # --- Parse file content ---
    try:
        transcript_text = parse_file(filename, content)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # --- DOCX re-upload: attempt auto-link to existing project ---
    if filename.lower().endswith(".docx"):
        embedded_meta = extract_embedded_metadata(content)
        if embedded_meta and isinstance(embedded_meta.get("project_id"), str):
            ref_project_id: str = embedded_meta["project_id"]
            statement = select(Project).where(Project.id == ref_project_id)
            existing_project: Optional[Project] = db.exec(statement).first()
            if existing_project is not None:
                logger.info(
                    "upload.docx_reupload_linked",
                    project_id=ref_project_id,
                    filename=filename,
                )
                return {
                    "project_id": existing_project.id,
                    "transcript_text": existing_project.transcript_text or "",
                    "status": existing_project.status.value,
                    "is_reupload": True,
                }

    # --- Create new Project ---
    project = Project(
        name=project_name,
        transcript_text=transcript_text,
        status=WorkflowStatus.TRANSCRIPT_UPLOADED,
    )

    db.add(project)
    db.commit()
    db.refresh(project)

    logger.info(
        "upload.project_created",
        project_id=project.id,
        project_name=project_name,
        filename=filename,
        transcript_len=len(transcript_text),
    )

    return {
        "project_id": project.id,
        "transcript_text": transcript_text,
        "status": project.status.value,
        "is_reupload": False,
    }

