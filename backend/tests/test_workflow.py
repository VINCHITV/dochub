"""
backend/tests/test_workflow.py

Tests for the workflow state machine in app/services/workflow.py.

Coverage:
- Every valid transition in VALID_TRANSITIONS advances the project status correctly.
- COMPLETED is a terminal state — advance_status raises 422 for it.
- advance_status raises 409 when expected_current does not match the actual status.
- advance_status raises 404 when the project does not exist.
- Idempotency: calling advance_status when the project is already at the next
  status (e.g. after a client retry) returns the project without error.
- VALID_TRANSITIONS dict has exactly 6 transitions (7 states, 1 terminal).

All tests use an in-memory SQLite DB via the `db_session` fixture from conftest.py.
No network calls, no file I/O, no mocks needed.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.models import Project
from app.services.workflow import VALID_TRANSITIONS, WorkflowStatus, advance_status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_project(db: Session, status: WorkflowStatus) -> Project:
    """Insert a minimal Project row with the given status and return it."""
    project = Project(name="Test Project", status=status)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


# ---------------------------------------------------------------------------
# VALID_TRANSITIONS structure
# ---------------------------------------------------------------------------

class TestValidTransitionsDict:
    def test_has_six_transitions(self):
        # 7 states, 1 terminal (COMPLETED) → 6 transitions
        assert len(VALID_TRANSITIONS) == 6

    def test_completed_is_not_a_key(self):
        assert WorkflowStatus.COMPLETED not in VALID_TRANSITIONS

    def test_all_keys_are_workflow_status(self):
        for key in VALID_TRANSITIONS:
            assert isinstance(key, WorkflowStatus)

    def test_all_values_are_workflow_status(self):
        for value in VALID_TRANSITIONS.values():
            assert isinstance(value, WorkflowStatus)

    def test_exact_transition_mapping(self):
        expected = {
            WorkflowStatus.TRANSCRIPT_UPLOADED: WorkflowStatus.PRD_GENERATED,
            WorkflowStatus.PRD_GENERATED: WorkflowStatus.PRD_APPROVED,
            WorkflowStatus.PRD_APPROVED: WorkflowStatus.STORIES_GENERATED,
            WorkflowStatus.STORIES_GENERATED: WorkflowStatus.JIRA_PUSH_PENDING,
            WorkflowStatus.JIRA_PUSH_PENDING: WorkflowStatus.JIRA_PUSH_SUCCESS,
            WorkflowStatus.JIRA_PUSH_SUCCESS: WorkflowStatus.COMPLETED,
        }
        assert VALID_TRANSITIONS == expected


# ---------------------------------------------------------------------------
# Valid transitions — parametrized over all 6 transitions
# ---------------------------------------------------------------------------

_TRANSITION_PAIRS = [
    (WorkflowStatus.TRANSCRIPT_UPLOADED, WorkflowStatus.PRD_GENERATED),
    (WorkflowStatus.PRD_GENERATED, WorkflowStatus.PRD_APPROVED),
    (WorkflowStatus.PRD_APPROVED, WorkflowStatus.STORIES_GENERATED),
    (WorkflowStatus.STORIES_GENERATED, WorkflowStatus.JIRA_PUSH_PENDING),
    (WorkflowStatus.JIRA_PUSH_PENDING, WorkflowStatus.JIRA_PUSH_SUCCESS),
    (WorkflowStatus.JIRA_PUSH_SUCCESS, WorkflowStatus.COMPLETED),
]


@pytest.mark.parametrize("from_status,to_status", _TRANSITION_PAIRS)
def test_valid_transition_advances_status(
    db_session: Session,
    from_status: WorkflowStatus,
    to_status: WorkflowStatus,
):
    """Every valid transition in VALID_TRANSITIONS must advance the project status."""
    # Arrange
    project = _create_project(db_session, from_status)
    project_id = project.id

    # Act
    updated = advance_status(project_id, from_status, db_session)

    # Assert
    assert updated.status == to_status
    assert updated.id == project_id


# ---------------------------------------------------------------------------
# Terminal state — COMPLETED has no successor
# ---------------------------------------------------------------------------

def test_completed_is_terminal_raises_422(db_session: Session):
    """Attempting to advance from COMPLETED raises 422 (no successor defined)."""
    # Arrange
    project = _create_project(db_session, WorkflowStatus.COMPLETED)

    # Act / Assert
    with pytest.raises(HTTPException) as exc_info:
        advance_status(project.id, WorkflowStatus.COMPLETED, db_session)

    assert exc_info.value.status_code == 422
    assert "terminal" in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# Conflict guard — wrong expected_current raises 409
# ---------------------------------------------------------------------------
#
# A 409 fires when project.status is NEITHER expected_current NOR next_status
# (the idempotency path). We must choose pairs carefully:
#
#   actual_status=TRANSCRIPT_UPLOADED, wrong_expected=PRD_GENERATED:
#     next_status = VALID_TRANSITIONS[PRD_GENERATED] = PRD_APPROVED
#     project.status (TRANSCRIPT_UPLOADED) != PRD_APPROVED AND != PRD_GENERATED → 409 ✓
#
#   actual_status=PRD_APPROVED, wrong_expected=TRANSCRIPT_UPLOADED:
#     next_status = VALID_TRANSITIONS[TRANSCRIPT_UPLOADED] = PRD_GENERATED
#     project.status (PRD_APPROVED) != PRD_GENERATED AND != TRANSCRIPT_UPLOADED → 409 ✓
#
#   actual_status=JIRA_PUSH_PENDING, wrong_expected=PRD_APPROVED:
#     next_status = VALID_TRANSITIONS[PRD_APPROVED] = STORIES_GENERATED
#     project.status (JIRA_PUSH_PENDING) != STORIES_GENERATED AND != PRD_APPROVED → 409 ✓
#
#   actual_status=COMPLETED, wrong_expected=PRD_GENERATED:
#     next_status = VALID_TRANSITIONS[PRD_GENERATED] = PRD_APPROVED
#     project.status (COMPLETED) != PRD_APPROVED AND != PRD_GENERATED → 409 ✓

@pytest.mark.parametrize("actual_status,wrong_expected", [
    # actual is before the expected_current in the pipeline — true conflict
    (WorkflowStatus.TRANSCRIPT_UPLOADED, WorkflowStatus.PRD_GENERATED),
    (WorkflowStatus.PRD_APPROVED, WorkflowStatus.TRANSCRIPT_UPLOADED),
    (WorkflowStatus.JIRA_PUSH_PENDING, WorkflowStatus.PRD_APPROVED),
    (WorkflowStatus.COMPLETED, WorkflowStatus.PRD_GENERATED),
])
def test_advance_status_raises_409_on_wrong_expected(
    db_session: Session,
    actual_status: WorkflowStatus,
    wrong_expected: WorkflowStatus,
):
    """advance_status raises 409 when expected_current != actual project status
    AND actual status is not the idempotency target (next_status)."""
    # Arrange
    project = _create_project(db_session, actual_status)

    # Act / Assert
    with pytest.raises(HTTPException) as exc_info:
        advance_status(project.id, wrong_expected, db_session)

    assert exc_info.value.status_code == 409
    assert project.id in exc_info.value.detail


# ---------------------------------------------------------------------------
# Not found — raises 404
# ---------------------------------------------------------------------------

def test_advance_status_raises_404_for_missing_project(db_session: Session):
    """advance_status raises 404 when the project_id does not exist."""
    with pytest.raises(HTTPException) as exc_info:
        advance_status(
            "non-existent-uuid-1234",
            WorkflowStatus.TRANSCRIPT_UPLOADED,
            db_session,
        )
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Idempotency — already at next state returns without error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expected_current,already_at_next", [
    (WorkflowStatus.TRANSCRIPT_UPLOADED, WorkflowStatus.PRD_GENERATED),
    (WorkflowStatus.PRD_GENERATED, WorkflowStatus.PRD_APPROVED),
    (WorkflowStatus.PRD_APPROVED, WorkflowStatus.STORIES_GENERATED),
    (WorkflowStatus.STORIES_GENERATED, WorkflowStatus.JIRA_PUSH_PENDING),
    (WorkflowStatus.JIRA_PUSH_PENDING, WorkflowStatus.JIRA_PUSH_SUCCESS),
    (WorkflowStatus.JIRA_PUSH_SUCCESS, WorkflowStatus.COMPLETED),
])
def test_advance_status_idempotent_when_already_at_next(
    db_session: Session,
    expected_current: WorkflowStatus,
    already_at_next: WorkflowStatus,
):
    """
    If the project is already at the desired next status (e.g. client retry
    after timeout), advance_status returns the project without raising or
    writing again.

    This mirrors the idempotency guarantee documented in workflow.py:
      'If the project is already at the desired next status ... the function
       returns the project without raising an error or writing again.'
    """
    # Arrange — project is already at the NEXT status (simulates a successful
    # first write where the client didn't receive the response)
    project = _create_project(db_session, already_at_next)

    # Act — caller believes the project is still at expected_current
    result = advance_status(project.id, expected_current, db_session)

    # Assert — no exception raised; project still at next status
    assert result.status == already_at_next


# ---------------------------------------------------------------------------
# Return value correctness
# ---------------------------------------------------------------------------

def test_advance_status_returns_updated_project_instance(db_session: Session):
    """advance_status returns a Project instance with the new status set."""
    project = _create_project(db_session, WorkflowStatus.TRANSCRIPT_UPLOADED)

    result = advance_status(
        project.id, WorkflowStatus.TRANSCRIPT_UPLOADED, db_session
    )

    assert isinstance(result, Project)
    assert result.id == project.id
    assert result.status == WorkflowStatus.PRD_GENERATED


def test_advance_status_persists_to_db(db_session: Session):
    """After advance_status, re-querying the DB reflects the new status."""
    from sqlmodel import select

    project = _create_project(db_session, WorkflowStatus.TRANSCRIPT_UPLOADED)
    advance_status(project.id, WorkflowStatus.TRANSCRIPT_UPLOADED, db_session)

    # Re-query the DB to confirm the write was committed
    refreshed = db_session.exec(
        select(Project).where(Project.id == project.id)
    ).first()
    assert refreshed is not None
    assert refreshed.status == WorkflowStatus.PRD_GENERATED


# ---------------------------------------------------------------------------
# WorkflowStatus enum completeness
# ---------------------------------------------------------------------------

class TestWorkflowStatusEnum:
    def test_has_seven_states(self):
        assert len(WorkflowStatus) == 7

    def test_all_expected_states_present(self):
        expected = {
            "TRANSCRIPT_UPLOADED",
            "PRD_GENERATED",
            "PRD_APPROVED",
            "STORIES_GENERATED",
            "JIRA_PUSH_PENDING",
            "JIRA_PUSH_SUCCESS",
            "COMPLETED",
        }
        actual = {status.name for status in WorkflowStatus}
        assert expected == actual

    def test_values_are_strings(self):
        for status in WorkflowStatus:
            assert isinstance(status.value, str)
            assert status.value == status.name  # str enum: value == name

    def test_workflow_status_is_str_subclass(self):
        assert issubclass(WorkflowStatus, str)
