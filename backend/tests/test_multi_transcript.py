# backend/tests/test_multi_transcript.py
"""
Tests for A1: multi-transcript upload feature.

Covers:
  - Two-file upload creates two Transcript rows with correct doc_ids.
  - Project.transcript_text is updated with source-labelled merged content.
  - Second call appends new transcripts and re-merges (sort_order continues).
  - Uploading to a project in PRD_GENERATED status returns HTTP 409.
  - Response shape: transcript_ids, merged_length, transcript_count are correct.
  - Uploading to a non-existent project returns HTTP 404.
  - Single-file upload works correctly.

All tests use the TestClient fixture from conftest.py backed by an in-memory
SQLite engine. No external services are called.
"""

from __future__ import annotations

import io
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import Project, Transcript
from app.services.workflow import WorkflowStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_txt_file(content: str, filename: str = "test.txt") -> tuple[str, tuple]:
    """Build a (field_name, (filename, file_obj, content_type)) tuple for multipart."""
    return ("files", (filename, io.BytesIO(content.encode("utf-8")), "text/plain"))


def _create_project(db_session: Session, status: WorkflowStatus = WorkflowStatus.TRANSCRIPT_UPLOADED) -> Project:
    """Insert a bare Project row and return it."""
    project = Project(
        name="Test Project",
        transcript_text="initial transcript",
        status=status,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAddTranscripts:
    """POST /upload/transcripts/{project_id}"""

    def test_two_files_creates_two_transcript_rows(
        self,
        test_client: TestClient,
        db_session: Session,
    ) -> None:
        """Each uploaded file must result in exactly one Transcript row."""
        project = _create_project(db_session)

        response = test_client.post(
            f"/upload/transcripts/{project.id}",
            files=[
                _make_txt_file("Meeting notes alpha.", "alpha.txt"),
                _make_txt_file("Meeting notes beta.", "beta.txt"),
            ],
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert len(body["transcript_ids"]) == 2
        assert body["transcript_count"] == 2

        # Verify rows in DB
        rows: list[Transcript] = list(
            db_session.exec(
                select(Transcript).where(Transcript.project_id == project.id)
            ).all()
        )
        assert len(rows) == 2

    def test_doc_ids_follow_project_t_n_format(
        self,
        test_client: TestClient,
        db_session: Session,
    ) -> None:
        """doc_id must be '<project_id>-t<sort_order>' (0-based, sequential)."""
        project = _create_project(db_session)

        test_client.post(
            f"/upload/transcripts/{project.id}",
            files=[
                _make_txt_file("Content A", "a.txt"),
                _make_txt_file("Content B", "b.txt"),
            ],
        )

        rows: list[Transcript] = list(
            db_session.exec(
                select(Transcript)
                .where(Transcript.project_id == project.id)
                .order_by(Transcript.sort_order)  # type: ignore[arg-type]
            ).all()
        )
        assert rows[0].doc_id == f"{project.id}-t0"
        assert rows[1].doc_id == f"{project.id}-t1"

    def test_merged_text_contains_source_labels(
        self,
        test_client: TestClient,
        db_session: Session,
    ) -> None:
        """Project.transcript_text must include [Source: filename] markers."""
        project = _create_project(db_session)

        test_client.post(
            f"/upload/transcripts/{project.id}",
            files=[
                _make_txt_file("Alpha content here.", "alpha.txt"),
                _make_txt_file("Beta content here.", "beta.txt"),
            ],
        )

        # Refresh project from DB to see the updated transcript_text
        db_session.refresh(project)
        merged = project.transcript_text or ""

        assert "--- [Source: alpha.txt] ---" in merged
        assert "--- [Source: beta.txt] ---" in merged
        assert "Alpha content here." in merged
        assert "Beta content here." in merged

    def test_merged_length_matches_response(
        self,
        test_client: TestClient,
        db_session: Session,
    ) -> None:
        """Response merged_length must equal len(project.transcript_text)."""
        project = _create_project(db_session)

        response = test_client.post(
            f"/upload/transcripts/{project.id}",
            files=[
                _make_txt_file("Hello world.", "one.txt"),
                _make_txt_file("Goodbye world.", "two.txt"),
            ],
        )

        body = response.json()
        db_session.refresh(project)
        assert body["merged_length"] == len(project.transcript_text or "")

    def test_second_call_appends_and_increments_sort_order(
        self,
        test_client: TestClient,
        db_session: Session,
    ) -> None:
        """
        A second upload call must continue sort_order from where the first left off
        (i.e. not restart at 0) and re-merge all transcripts.
        """
        project = _create_project(db_session)

        # First upload: 2 files -> sort_order 0, 1
        test_client.post(
            f"/upload/transcripts/{project.id}",
            files=[
                _make_txt_file("First batch file.", "first.txt"),
            ],
        )

        # Second upload: 1 more file -> sort_order 1
        response = test_client.post(
            f"/upload/transcripts/{project.id}",
            files=[
                _make_txt_file("Second batch file.", "second.txt"),
            ],
        )

        assert response.status_code == 201
        body = response.json()
        assert body["transcript_count"] == 2

        rows: list[Transcript] = list(
            db_session.exec(
                select(Transcript)
                .where(Transcript.project_id == project.id)
                .order_by(Transcript.sort_order)  # type: ignore[arg-type]
            ).all()
        )
        assert rows[0].sort_order == 0
        assert rows[0].doc_id == f"{project.id}-t0"
        assert rows[1].sort_order == 1
        assert rows[1].doc_id == f"{project.id}-t1"

        # Merged text must contain both sources
        db_session.refresh(project)
        merged = project.transcript_text or ""
        assert "first.txt" in merged
        assert "second.txt" in merged

    def test_returns_409_when_prd_already_generated(
        self,
        test_client: TestClient,
        db_session: Session,
    ) -> None:
        """Adding transcripts to a project past TRANSCRIPT_UPLOADED must return 409."""
        project = _create_project(db_session, status=WorkflowStatus.PRD_GENERATED)

        response = test_client.post(
            f"/upload/transcripts/{project.id}",
            files=[
                _make_txt_file("Late transcript.", "late.txt"),
            ],
        )

        assert response.status_code == 409
        assert "status" in response.json()["detail"].lower() or "transcript" in response.json()["detail"].lower()

    def test_returns_409_for_all_post_upload_statuses(
        self,
        test_client: TestClient,
        db_session: Session,
    ) -> None:
        """Spot-check a non-initial status that is not TRANSCRIPT_UPLOADED."""
        project = _create_project(db_session, status=WorkflowStatus.STORIES_GENERATED)

        response = test_client.post(
            f"/upload/transcripts/{project.id}",
            files=[_make_txt_file("Too late.", "toolate.txt")],
        )

        assert response.status_code == 409

    def test_returns_404_for_unknown_project(
        self,
        test_client: TestClient,
    ) -> None:
        """Request for a non-existent project_id must return 404."""
        response = test_client.post(
            "/upload/transcripts/00000000-0000-0000-0000-000000000000",
            files=[_make_txt_file("Content.", "f.txt")],
        )
        assert response.status_code == 404

    def test_single_file_upload_works(
        self,
        test_client: TestClient,
        db_session: Session,
    ) -> None:
        """Single-file upload is the degenerate case — must work correctly."""
        project = _create_project(db_session)

        response = test_client.post(
            f"/upload/transcripts/{project.id}",
            files=[_make_txt_file("Only transcript.", "solo.txt")],
        )

        assert response.status_code == 201
        body = response.json()
        assert body["transcript_count"] == 1
        assert len(body["transcript_ids"]) == 1
        assert body["project_id"] == project.id

        db_session.refresh(project)
        merged = project.transcript_text or ""
        assert "solo.txt" in merged
        assert "Only transcript." in merged

    def test_response_transcript_ids_match_db_rows(
        self,
        test_client: TestClient,
        db_session: Session,
    ) -> None:
        """IDs in response must correspond to actual Transcript rows in the DB."""
        project = _create_project(db_session)

        response = test_client.post(
            f"/upload/transcripts/{project.id}",
            files=[
                _make_txt_file("Content X", "x.txt"),
                _make_txt_file("Content Y", "y.txt"),
            ],
        )

        body = response.json()
        returned_ids: list[str] = body["transcript_ids"]

        for tid in returned_ids:
            row: Transcript | None = db_session.get(Transcript, tid)
            assert row is not None, f"Transcript row {tid} not found in DB"
            assert row.project_id == project.id

    def test_unsupported_file_type_returns_422(
        self,
        test_client: TestClient,
        db_session: Session,
    ) -> None:
        """A .pdf or other unsupported extension must return 422."""
        project = _create_project(db_session)

        response = test_client.post(
            f"/upload/transcripts/{project.id}",
            files=[("files", ("bad.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf"))],
        )

        assert response.status_code == 422

    def test_project_id_in_response(
        self,
        test_client: TestClient,
        db_session: Session,
    ) -> None:
        """project_id in response body must match the path parameter."""
        project = _create_project(db_session)

        response = test_client.post(
            f"/upload/transcripts/{project.id}",
            files=[_make_txt_file("Content.", "f.txt")],
        )

        assert response.json()["project_id"] == project.id
