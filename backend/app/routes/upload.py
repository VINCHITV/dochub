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

from typing import Annotated, List, Optional

import structlog
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from sqlmodel import Session, select

from app.database import get_session
from app.models import Project, Transcript
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
    creator_name: Annotated[str, Form()] = "",
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
        created_by=creator_name,
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


# ---------------------------------------------------------------------------
# POST /upload/transcripts/{project_id}
# ---------------------------------------------------------------------------

@router.post(
    "/transcripts/{project_id}",
    status_code=status.HTTP_201_CREATED,
)
async def add_transcripts(
    project_id: str,
    files: List[UploadFile],
    db: Annotated[Session, Depends(get_session)],
) -> dict:
    """
    Append one or more transcript files to an existing project.

    Each file is parsed with ``parse_file`` (same logic as POST /upload).
    A ``Transcript`` row is created for each file with a stable ``doc_id``
    of the form ``<project_id>-t<n>`` where n is the global sort_order across
    all transcripts for this project (0-based, increments across calls).

    After inserting, all Transcript rows for the project are re-read in
    sort_order order and concatenated with ``--- [Source: <filename>] ---`` markers.
    The merged string is written to ``Project.transcript_text`` so downstream
    PRD generation always reads a single coherent string.

    Constraints
    -----------
    - Project must exist: HTTP 404 if not found.
    - Project status must be ``TRANSCRIPT_UPLOADED``: HTTP 409 if not.
      Once PRD generation has started the transcript corpus is frozen.
    - At least one file must be provided: HTTP 422 if ``files`` is empty.

    Returns
    -------
    {
        "project_id": str,
        "transcript_ids": list[str],   # IDs of the newly created Transcript rows
        "merged_length": int,           # len(Project.transcript_text) after merge
        "transcript_count": int,        # total Transcript rows for this project
    }
    """
    # --- Load project ---
    project: Optional[Project] = db.exec(
        select(Project).where(Project.id == project_id)
    ).first()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found.",
        )

    # --- Guard: transcripts may only be added before PRD generation starts ---
    if project.status != WorkflowStatus.TRANSCRIPT_UPLOADED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot add transcripts to project '{project_id}' in status "
                f"'{project.status.value}'. Transcripts are locked once PRD "
                "generation has started."
            ),
        )

    # --- Require at least one file ---
    if not files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one file must be provided.",
        )

    # --- Determine starting sort_order from existing Transcript count ---
    existing_transcripts: list[Transcript] = list(
        db.exec(
            select(Transcript)
            .where(Transcript.project_id == project_id)
            .order_by(Transcript.sort_order)  # type: ignore[arg-type]
        ).all()
    )
    next_sort_order: int = len(existing_transcripts)

    # --- Parse, validate, and create a Transcript row for each file ---
    new_transcript_ids: list[str] = []
    new_transcripts: list[Transcript] = []

    for file in files:
        filename: str = file.filename or ""
        content: bytes = await file.read()

        try:
            raw_text = parse_file(filename, content)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

        transcript = Transcript(
            project_id=project_id,
            filename=filename,
            raw_text=raw_text,
            doc_id=f"{project_id}-t{next_sort_order}",
            sort_order=next_sort_order,
        )
        db.add(transcript)
        new_transcripts.append(transcript)
        next_sort_order += 1

    # Flush to get IDs without committing — needed to collect new_transcript_ids
    db.flush()
    for t in new_transcripts:
        db.refresh(t)
        new_transcript_ids.append(t.id)

    # --- Merge all transcripts (existing + new) in sort_order order ---
    all_transcripts: list[Transcript] = list(
        db.exec(
            select(Transcript)
            .where(Transcript.project_id == project_id)
            .order_by(Transcript.sort_order)  # type: ignore[arg-type]
        ).all()
    )

    # Each segment is prefixed with its source filename header.
    # "\n\n".join() handles inter-segment whitespace; the header sits on its
    # own line at the top of each segment so the LLM can distinguish sources.
    labelled_segments: list[str] = [
        f"--- [Source: {t.filename}] ---\n\n{t.raw_text}"
        for t in all_transcripts
    ]
    merged_text = "\n\n".join(labelled_segments)

    # --- Persist merged text to Project ---
    project.transcript_text = merged_text
    db.add(project)
    db.commit()

    logger.info(
        "upload.transcripts_added",
        project_id=project_id,
        new_count=len(new_transcript_ids),
        total_count=len(all_transcripts),
        merged_len=len(merged_text),
    )

    return {
        "project_id": project_id,
        "transcript_ids": new_transcript_ids,
        "merged_length": len(merged_text),
        "transcript_count": len(all_transcripts),
    }
