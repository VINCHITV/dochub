# backend/app/models.py
"""
SQLModel table definitions for DocHub.

Design decisions:
- All primary keys are UUID strings generated server-side (uuid4).
  Using str avoids SQLAlchemy UUID type portability issues between
  SQLite (no native UUID) and Postgres.
- `created_at` uses `default_factory=datetime.utcnow` (set once on insert).
- There is no `updated_at` on most models because mutations are append-style
  (new rows for stories/tickets) or in-place with explicit field updates on
  Project. If updated_at is needed for Project, add it with an onupdate trigger.
- `prd_json` on Project stores the serialized dict of all 7 PRD sections as a
  JSON string. This avoids a separate PRDSection table for the hackathon.
  Post-hackathon: extract to a proper JSONB column (Postgres) or separate table.
- `acceptance_criteria` and `validations` on UserStory are JSON-serialized
  strings. Route layer is responsible for json.loads/json.dumps on the boundary.
- Version fields on Project (prompt_version, generator_model, embedding_model,
  extractor_model) are stored at generation time using constants from
  services/versions.py. They default to current constants but are frozen once
  written, enabling post-hoc filtering by prompt version.
- `PRDMetadata.status` is a plain str ("active" | "superseded"), NOT the
  WorkflowStatus enum. It tracks the KB lifecycle of a PRD document separately
  from the pipeline workflow of a Project.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel

from app.services.versions import (
    EMBEDDING_MODEL,
    EXTRACTOR_MODEL,
    GENERATOR_MODEL,
    PRD_PROMPT_VERSION,
)
from app.services.workflow import WorkflowStatus


def _new_uuid() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class Project(SQLModel, table=True):
    """
    Root entity for the DocHub pipeline.

    One Project corresponds to one uploaded transcript processed through the
    full pipeline. Status advances through WorkflowStatus via advance_status()
    in services/workflow.py — never written directly in routes.

    Version fields capture the exact model/prompt configuration used at PRD
    generation time, enabling reproducibility audits and targeted regeneration
    after prompt updates.
    """

    __tablename__ = "project"  # type: ignore[assignment]

    id: str = Field(
        default_factory=_new_uuid,
        primary_key=True,
        description="UUID v4 string, server-generated on insert.",
    )
    name: str = Field(
        description="Human-readable project name provided by the user at upload."
    )
    transcript_text: Optional[str] = Field(
        default=None,
        description="Raw transcript text extracted from the uploaded file.",
    )
    prd_json: Optional[str] = Field(
        default=None,
        description=(
            "JSON-serialized dict mapping section keys to PRD section content. "
            "Set after PRD generation completes."
        ),
    )
    status: WorkflowStatus = Field(
        default=WorkflowStatus.TRANSCRIPT_UPLOADED,
        description="Current pipeline workflow state.",
    )
    created_at: datetime = Field(
        default_factory=_now,
        description="UTC timestamp of project creation (set once on insert).",
    )

    # -- User-provided answers to open questions (stored as JSON dict) --
    qa_answers: str = Field(
        default="{}",
        description=(
            "JSON-serialized dict mapping question text to PM answer. "
            "Populated via POST /projects/{id}/qa-answers. "
            "Used as context when regenerating the PRD (B3 loop)."
        ),
    )

    # -- Creator attribution --
    created_by: str = Field(
        default="",
        description="Name of the PM or user who created the project.",
    )

    # -- Version tags (frozen at PRD generation time) --
    prompt_version: str = Field(
        default=PRD_PROMPT_VERSION,
        description="PRD prompt schema version used during generation (e.g. 'prd-v1.2').",
    )
    generator_model: str = Field(
        default=GENERATOR_MODEL,
        description="Anthropic model used for PRD section and story generation.",
    )
    embedding_model: str = Field(
        default=EMBEDDING_MODEL,
        description="OpenAI embedding model used for RAG indexing and retrieval.",
    )
    extractor_model: str = Field(
        default=EXTRACTOR_MODEL,
        description="Anthropic model used for PRDMetadata extraction.",
    )


# ---------------------------------------------------------------------------
# UserStory
# ---------------------------------------------------------------------------


class UserStory(SQLModel, table=True):
    """
    A vertically-sliced user story generated from a Project's PRD.

    Acceptance criteria and validations are stored as JSON strings.
    The service layer serializes/deserializes with json.dumps/json.loads.

    Acceptance criteria format (JSON list of str):
        ["Happy path: ...", "Alt path: ...", "Error path: ..."]

    Validations format (JSON list of dict):
        [{"field": "email", "rule": "must be valid RFC 5322", "error_message": "..."}]
    """

    __tablename__ = "userstory"  # type: ignore[assignment]

    id: str = Field(
        default_factory=_new_uuid,
        primary_key=True,
    )
    project_id: str = Field(
        foreign_key="project.id",
        index=True,
        description="FK to Project.id.",
    )
    title: str = Field(description="Short story title (imperative mood).")
    description: str = Field(
        description=(
            "Full user story in 'As a <persona>, I want <action>, so that <benefit>' format."
        )
    )
    acceptance_criteria: str = Field(
        description="JSON-serialized list[str] of acceptance criteria (happy + alt + error paths).",
    )
    validations: str = Field(
        description="JSON-serialized list[dict] of field-level validation rules.",
    )
    priority: str = Field(
        default="medium",
        description="Story priority: 'high' | 'medium' | 'low'. Set by LLM at generation time.",
    )
    dependencies: str = Field(
        default="[]",
        description="JSON-serialized list[str] of dependent story titles or labels.",
    )
    reference_links: str = Field(
        default="[]",
        description="JSON-serialized list[str] of reference URLs or doc identifiers.",
    )
    story_status: str = Field(
        default="open",
        description="Lifecycle status: 'open' | 'done' | 'obsolete'. Updated by workflow events.",
    )
    size: str = Field(
        default="M",
        description="T-shirt size effort estimate: 'XS' | 'S' | 'M' | 'L' | 'XL'.",
    )
    transcript_references: str = Field(
        default="[]",
        description="JSON-serialized list[dict] of speaker-attributed transcript/KB references.",
    )
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------


class Transcript(SQLModel, table=True):
    """
    Stores a single uploaded transcript file for a Project.

    A Project may have multiple Transcript rows — one per file uploaded via
    POST /upload/transcripts/{project_id}.  The merged content of all rows
    (ordered by sort_order) is written back to Project.transcript_text after
    each batch upload so the PRD generation route always reads a single
    coherent string.

    doc_id format: '<project_id>-t<n>' where n is sort_order (0-based).
    This makes each transcript chunk traceable in RAG metadata.

    Design: Transcript rows are append-only.  Adding transcripts after
    PRD generation starts (status != TRANSCRIPT_UPLOADED) is rejected with
    HTTP 409 to prevent incoherence between the stored transcript and any
    already-generated PRD sections.
    """

    __tablename__ = "transcript"  # type: ignore[assignment]

    id: str = Field(
        default_factory=_new_uuid,
        primary_key=True,
        description="UUID v4 string, server-generated on insert.",
    )
    project_id: str = Field(
        foreign_key="project.id",
        index=True,
        description="FK to Project.id.",
    )
    filename: str = Field(
        description="Original filename as provided by the HTTP upload (e.g. 'meeting-2024.txt').",
    )
    raw_text: str = Field(
        description="Parsed plain-text content extracted from the uploaded file.",
    )
    doc_id: str = Field(
        description=(
            "Source-tracking identifier in the format '<project_id>-t<n>' where n is sort_order. "
            "Stored in RAG chunk metadata so retrieved nodes can be traced to a specific transcript."
        ),
    )
    sort_order: int = Field(
        default=0,
        description=(
            "0-based insertion index within this project's transcript set. "
            "Determines the order of concatenation when building Project.transcript_text."
        ),
    )
    created_at: datetime = Field(
        default_factory=_now,
        description="UTC timestamp of row creation.",
    )


# ---------------------------------------------------------------------------
# JiraTicket
# ---------------------------------------------------------------------------


class JiraTicket(SQLModel, table=True):
    """
    Tracks the Jira issue created for a UserStory.

    `issue_key` is written immediately after each individual ticket creation
    (not batch-end) so that the rollback handler can delete by stored key
    rather than relying on JQL (which has indexing lag).

    The `story_id` FK allows joining back to UserStory content for display.
    """

    __tablename__ = "jiraticket"  # type: ignore[assignment]

    id: str = Field(
        default_factory=_new_uuid,
        primary_key=True,
    )
    project_id: str = Field(
        foreign_key="project.id",
        index=True,
    )
    story_id: str = Field(
        foreign_key="userstory.id",
        index=True,
    )
    issue_key: Optional[str] = Field(
        default=None,
        description="Jira issue key (e.g. 'PROJ-42'). Set after successful creation.",
    )
    issue_url: Optional[str] = Field(
        default=None,
        description="Full Jira browse URL for the created issue.",
    )
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# PRDMetadata
# ---------------------------------------------------------------------------


class PRDMetadata(SQLModel, table=True):
    """
    Tracks the RAG knowledge base lifecycle for a generated PRD.

    One row per Project (unique FK). The `doc_id` is the identifier used in
    ChromaDB chunk metadata — allows targeted supersession without JQL or
    full-table scans.

    `status` is "active" | "superseded" — distinct from WorkflowStatus:
    - "active": chunks are included in hybrid retrieval queries.
    - "superseded": chunks are filtered out via MetadataFilter(key="status", value="active").
      Supersession happens when a new PRD for the same product_area is indexed.

    `embedding_model` is stored here (not only on Project) because future migration
    to a different embedding model requires re-indexing — having it on each
    metadata row allows per-document re-index targeting.
    """

    __tablename__ = "prdmetadata"  # type: ignore[assignment]

    id: str = Field(
        default_factory=_new_uuid,
        primary_key=True,
    )
    project_id: str = Field(
        foreign_key="project.id",
        unique=True,
        index=True,
        description="FK to Project.id. One PRDMetadata per Project.",
    )
    product_area: str = Field(
        description=(
            "Product area extracted from the PRD (e.g. 'Payments', 'Auth'). "
            "Used for supersession: when a new PRD is indexed for the same product_area, "
            "all existing active docs for that area are marked 'superseded'."
        )
    )
    doc_id: str = Field(
        description=(
            "Stable identifier stored in ChromaDB chunk metadata. "
            "Format: '<product_area_slug>-v<n>' (e.g. 'payments-v2'). "
            "Used to supersede all chunks from this document in the vector store."
        )
    )
    date: str = Field(
        description="ISO date string of PRD generation (e.g. '2024-06-15').",
    )
    status: str = Field(
        default="active",
        description="Knowledge base status: 'active' | 'superseded'.",
    )
    embedding_model: str = Field(
        description="Embedding model used when indexing this PRD's chunks.",
    )
