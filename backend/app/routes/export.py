# backend/app/routes/export.py
"""
Export and approval routes for DocHub.

GET /export/{project_id}/docx
    Streams a branded .docx file for the project's PRD.
    Requires PRD_GENERATED or later status — the PRD must exist.

POST /projects/{project_id}/approve
    Advances the project from PRD_GENERATED → PRD_APPROVED.
    Idempotent: returns 200 if already in PRD_APPROVED state.

Note on router prefix: main.py mounts this router at /export, so:
    GET  /export/{project_id}/docx     → route decorator: "/{project_id}/docx"
    POST /export/projects/{id}/approve → route decorator: "/projects/{id}/approve"
"""

from __future__ import annotations

import json
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlmodel import Session, select

from app.database import get_session
from app.models import Project
from app.services.docx_builder import build_prd_docx

logger = structlog.get_logger()

router = APIRouter()

# MIME type for .docx files
_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# ---------------------------------------------------------------------------
# GET /{project_id}/docx
# ---------------------------------------------------------------------------


@router.get("/{project_id}/docx")
async def export_docx(
    project_id: str,
    db: Annotated[Session, Depends(get_session)],
) -> Response:
    """
    Generate and return a branded .docx file for the project's PRD.

    The .docx is built in memory from the stored prd_json and streamed as an
    attachment.  The hidden [DOCHUB_METADATA] paragraph is appended by
    docx_builder so that re-uploads of this file auto-link to this project.

    The project must have a generated PRD (status >= PRD_GENERATED).

    Returns
    -------
    Response
        application/vnd.openxmlformats-officedocument.wordprocessingml.document
        Content-Disposition: attachment; filename="prd-{project_name}.docx"

    Raises
    ------
    404  — project not found
    422  — PRD not yet generated (prd_json is null)
    """
    statement = select(Project).where(Project.id == project_id)
    project: Optional[Project] = db.exec(statement).first()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )

    if not project.prd_json:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "PRD not yet generated for this project. "
                "Call POST /generate/prd first."
            ),
        )

    try:
        prd_sections: dict = json.loads(project.prd_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Stored prd_json is corrupted: {exc}",
        ) from exc

    # build_prd_docx accepts `Any` for project (avoids circular import in service layer)
    docx_bytes: bytes = build_prd_docx(project=project, prd_sections=prd_sections)

    # Sanitise project name for use in filename (replace spaces + special chars)
    safe_name = "".join(
        c if c.isalnum() or c in ("-", "_") else "-"
        for c in project.name
    ).strip("-") or "prd"

    filename = f"prd-{safe_name}.docx"

    logger.info(
        "export.docx_generated",
        project_id=project_id,
        filename=filename,
        bytes_size=len(docx_bytes),
    )

    return Response(
        content=docx_bytes,
        media_type=_DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

