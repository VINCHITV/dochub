# backend/app/routes/jira.py
"""
Jira ticket push route for DocHub.

POST /jira/tickets
    Pre-validates Jira project access and story data, then concurrently creates
    one Jira issue per UserStory.  Implements all-or-nothing semantics:
    - advance_status to JIRA_PUSH_PENDING before asyncio.gather (double-push guard)
    - JiraTicket row written after EACH successful individual creation (not batch-end)
    - On asyncio.gather failure: DELETE by stored issue_key, reset status to STORIES_GENERATED
    - On success: advance_status to JIRA_PUSH_SUCCESS

Jira REST API v3 specifics:
    - Basic auth: base64(email:api_token) — httpx handles encoding
    - Description field: ADF (Atlassian Document Format) JSON, never plain text
    - Concurrent creation: asyncio.Semaphore(5) to avoid rate-limit 429

Environment variables required:
    JIRA_BASE_URL      e.g. https://your-domain.atlassian.net
    JIRA_EMAIL         Atlassian account email
    JIRA_API_TOKEN     Atlassian API token
    JIRA_PROJECT_KEY   e.g. PROJ

All business logic is isolated in helpers.  The route handler contains zero
business logic beyond loading the project and stories.

Rollback note:
    Status reset on failure uses a direct DB write (not advance_status) because
    advance_status only moves forward.  This is the ONLY place in the codebase
    where status is written directly.  It is safe here because JIRA_PUSH_PENDING
    is only reachable through a valid advance from STORIES_GENERATED, and the
    direct write is an exceptional recovery to the last known-good state.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Annotated, Optional

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from app.database import get_session
from app.models import JiraTicket, Project, UserStory
from app.services.workflow import WorkflowStatus, advance_status

logger = structlog.get_logger()

router = APIRouter()

# Maximum concurrent Jira create calls (avoids 429 rate-limit on Atlassian cloud)
_JIRA_CONCURRENCY = asyncio.Semaphore(5)


# ---------------------------------------------------------------------------
# Request body
# ---------------------------------------------------------------------------


class JiraTicketsRequest(BaseModel):
    project_id: str


# ---------------------------------------------------------------------------
# ADF builder helpers
# ---------------------------------------------------------------------------


def _adf_text(text: str) -> dict:
    return {"type": "text", "text": text}


def _adf_paragraph(text: str) -> dict:
    return {
        "type": "paragraph",
        "content": [_adf_text(text)],
    }


def _adf_heading(text: str, level: int = 2) -> dict:
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [_adf_text(text)],
    }


def _adf_bullet_list(items: list[str]) -> dict:
    return {
        "type": "bulletList",
        "content": [
            {
                "type": "listItem",
                "content": [_adf_paragraph(item)],
            }
            for item in items
        ],
    }


def _build_adf_description(
    description: str,
    acceptance_criteria: list[str],
    validations: list[dict],
) -> dict:
    """
    Build an ADF document for a Jira issue description.

    Structure:
    - Paragraph: user story description (As a... I want... So that...)
    - Heading 2: Acceptance Criteria
    - Bullet list: each AC item
    - Heading 2: Validations  (omitted if validations list is empty)
    - Bullet list: "field: rule — error_message" per validation row

    Returns a complete ADF doc object (type=doc, version=1).
    """
    content: list[dict] = []

    # Description paragraph
    if description:
        content.append(_adf_paragraph(description))

    # Acceptance Criteria section
    if acceptance_criteria:
        content.append(_adf_heading("Acceptance Criteria", level=2))
        content.append(_adf_bullet_list(acceptance_criteria))

    # Validations section (rendered as bullet list — ADF tables require complex markup)
    if validations:
        content.append(_adf_heading("Validations", level=2))
        validation_items = [
            f"{v.get('field', '')}: {v.get('rule', '')} — {v.get('error_message', '')}"
            for v in validations
        ]
        content.append(_adf_bullet_list(validation_items))

    # ADF requires at least one content node — add an empty paragraph as safety
    if not content:
        content.append(_adf_paragraph("(No description provided.)"))

    return {
        "type": "doc",
        "version": 1,
        "content": content,
    }


# ---------------------------------------------------------------------------
# Jira HTTP helpers
# ---------------------------------------------------------------------------


def _require_jira_env() -> tuple[str, str, str, str]:
    """
    Read and validate required Jira env vars.

    Returns (email, api_token, base_url, project_key).

    Raises
    ------
    HTTPException 503
        If any Jira env var is missing — caller should abort before any API call.
    """
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    base_url = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    project_key = os.environ.get("JIRA_PROJECT_KEY", "")

    missing = [name for name, val in {
        "JIRA_EMAIL": email,
        "JIRA_API_TOKEN": token,
        "JIRA_BASE_URL": base_url,
        "JIRA_PROJECT_KEY": project_key,
    }.items() if not val]

    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Jira integration not configured. Missing env vars: {missing}",
        )

    return email, token, base_url, project_key


async def _validate_jira_project(
    client: httpx.AsyncClient,
    base_url: str,
    project_key: str,
) -> None:
    """
    Verify the Jira project exists and credentials are accepted.

    Raises HTTPException 502 on auth failure, 403, 404, or other HTTP errors.
    """
    url = f"{base_url}/rest/api/3/project/{project_key}"
    try:
        resp = await client.get(url)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Jira connection error during project validation: {exc}",
        ) from exc

    if resp.status_code == 401:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Jira authentication failed. Check JIRA_EMAIL and JIRA_API_TOKEN.",
        )
    if resp.status_code == 403:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Jira access denied to project '{project_key}'. Check project permissions.",
        )
    if resp.status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Jira project '{project_key}' not found. Check JIRA_PROJECT_KEY.",
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Jira project validation failed: HTTP {resp.status_code} — {resp.text[:200]}",
        )


async def _validate_create_permission(
    client: httpx.AsyncClient,
    base_url: str,
    project_key: str,
) -> None:
    """
    Check that the authenticated user has CREATE_ISSUES permission on the project.

    Uses GET /rest/api/3/mypermissions with projectKey + permissions query params.
    Raises HTTPException 502 if the permission check fails or is denied.
    """
    url = f"{base_url}/rest/api/3/mypermissions"
    params = {"projectKey": project_key, "permissions": "CREATE_ISSUES"}
    try:
        resp = await client.get(url, params=params)
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Jira connection error during permission check: {exc}",
        ) from exc

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Jira permission check failed: HTTP {resp.status_code}",
        )

    permissions = resp.json().get("permissions", {})
    create_perm = permissions.get("CREATE_ISSUES", {})
    if not create_perm.get("havePermission", False):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Authenticated user does not have CREATE_ISSUES permission "
                f"on project '{project_key}'."
            ),
        )


async def _create_jira_issue(
    client: httpx.AsyncClient,
    base_url: str,
    project_key: str,
    summary: str,
    adf_description: dict,
) -> tuple[str, str]:
    """
    Create a single Jira issue and return (issue_key, issue_url).

    Gated by _JIRA_CONCURRENCY semaphore to cap concurrent requests.

    Raises
    ------
    httpx.HTTPStatusError
        On any 4xx/5xx response from Jira — propagated to trigger rollback in caller.
    """
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary,
            "description": adf_description,
            "issuetype": {"name": "Story"},
        }
    }

    async with _JIRA_CONCURRENCY:
        resp = await client.post(
            f"{base_url}/rest/api/3/issue",
            json=payload,
        )

    resp.raise_for_status()  # raises httpx.HTTPStatusError on 4xx/5xx
    data = resp.json()
    issue_key: str = data["key"]
    issue_url: str = f"{base_url}/browse/{issue_key}"
    return issue_key, issue_url


async def _delete_jira_issue(
    client: httpx.AsyncClient,
    base_url: str,
    issue_key: str,
) -> None:
    """
    Delete a single Jira issue by key.

    Used during rollback.  Errors are logged and NOT re-raised so that other
    rollback deletes continue even when one fails.
    """
    try:
        resp = await client.delete(f"{base_url}/rest/api/3/issue/{issue_key}")
        if resp.status_code in (200, 204, 404):
            logger.info("jira.rollback_deleted", issue_key=issue_key, http_status=resp.status_code)
        else:
            logger.warning(
                "jira.rollback_delete_unexpected_status",
                issue_key=issue_key,
                http_status=resp.status_code,
                response=resp.text[:200],
            )
    except httpx.RequestError as exc:
        logger.warning(
            "jira.rollback_delete_request_error",
            issue_key=issue_key,
            error=str(exc),
        )


async def _create_and_store(
    client: httpx.AsyncClient,
    base_url: str,
    project_key: str,
    story: UserStory,
    adf_desc: dict,
) -> tuple[str, str, str]:
    """
    Create one Jira issue and return (story_id, issue_key, issue_url).

    Does NOT write to the database — the caller collects all results from
    asyncio.gather and writes them sequentially after gather completes.
    This avoids concurrent Session access from multiple coroutines sharing a
    single Session instance.

    Returns (story_id, issue_key, issue_url).
    Raises httpx.HTTPStatusError on Jira API failure — propagated to gather caller.
    """
    issue_key, issue_url = await _create_jira_issue(
        client=client,
        base_url=base_url,
        project_key=project_key,
        summary=story.title,
        adf_description=adf_desc,
    )

    logger.info(
        "jira.issue_created",
        story_id=story.id,
        issue_key=issue_key,
        issue_url=issue_url,
    )

    return story.id, issue_key, issue_url


def _reset_status_to_stories_generated(project_id: str, db: Session) -> None:
    """
    Directly reset a project's status from JIRA_PUSH_PENDING back to STORIES_GENERATED.

    This is the ONLY place in the codebase where status is written directly
    (bypassing advance_status) because advance_status only moves forward.
    This recovery write is only reachable when:
      1. The project was validly advanced to JIRA_PUSH_PENDING, AND
      2. The asyncio.gather for Jira creation failed after that advance.
    Under SQLite single-worker, there is no race condition risk.
    """
    from app.models import Project as _Project  # deferred to match workflow.py pattern

    statement = select(_Project).where(_Project.id == project_id)
    project: Optional[_Project] = db.exec(statement).first()

    if project is not None and project.status == WorkflowStatus.JIRA_PUSH_PENDING:
        project.status = WorkflowStatus.STORIES_GENERATED
        db.add(project)
        db.commit()
        logger.warning(
            "jira.status_reset_to_stories_generated",
            project_id=project_id,
        )


async def _execute_rollback(
    client: httpx.AsyncClient,
    base_url: str,
    project_id: str,
    db: Session,
) -> list[str]:
    """
    Delete all Jira issues whose keys are stored in JiraTicket rows for the project.

    Clears issue_key/issue_url from JiraTicket rows after deletion so the
    rows are clean if the user retries the push.

    Returns the list of issue_keys that deletion was attempted for.
    """
    tickets_stmt = select(JiraTicket).where(JiraTicket.project_id == project_id)
    tickets: list[JiraTicket] = list(db.exec(tickets_stmt).all())
    keys_to_delete = [t.issue_key for t in tickets if t.issue_key]

    if not keys_to_delete:
        logger.warning("jira.rollback_no_keys_to_delete", project_id=project_id)
        return []

    logger.warning(
        "jira.rollback_start",
        project_id=project_id,
        keys_to_delete=keys_to_delete,
    )

    # Attempt all deletes concurrently; errors are logged inside _delete_jira_issue
    await asyncio.gather(
        *[_delete_jira_issue(client, base_url, key) for key in keys_to_delete],
        return_exceptions=True,
    )

    # Clear issue_key/issue_url from JiraTicket rows
    for ticket in tickets:
        if ticket.issue_key:
            ticket.issue_key = None
            ticket.issue_url = None
            db.add(ticket)
    db.commit()

    logger.warning(
        "jira.rollback_complete",
        project_id=project_id,
        attempted_count=len(keys_to_delete),
    )

    return keys_to_delete


# ---------------------------------------------------------------------------
# POST /jira/tickets
# ---------------------------------------------------------------------------


@router.post("/tickets", status_code=status.HTTP_201_CREATED)
async def push_jira_tickets(
    body: JiraTicketsRequest,
    db: Annotated[Session, Depends(get_session)],
) -> dict:
    """
    Create Jira issues for all user stories in a project.

    All-or-nothing: if any single creation fails, ALL created issues are deleted
    and status is reset to STORIES_GENERATED.

    Returns
    -------
    {"issue_keys": [str, ...], "count": int}

    Raises
    ------
    404  — project not found
    409  — project not in STORIES_GENERATED (wrong state or double-push blocked)
    422  — no user stories found, or story title exceeds 255 chars
    502  — Jira API error (project not found, auth failure, creation failure)
    503  — Jira credentials not configured in env vars
    """
    # --- Load and validate project ---
    statement = select(Project).where(Project.id == body.project_id)
    project: Optional[Project] = db.exec(statement).first()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{body.project_id}' not found.",
        )

    if project.status != WorkflowStatus.STORIES_GENERATED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Jira push requires status STORIES_GENERATED; "
                f"current status is '{project.status.value}'."
            ),
        )

    # --- Load user stories ---
    stories_stmt = select(UserStory).where(UserStory.project_id == body.project_id)
    stories: list[UserStory] = list(db.exec(stories_stmt).all())

    if not stories:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No user stories found for this project. Generate stories first.",
        )

    # --- Require Jira env vars (raises 503 if missing) ---
    jira_email, jira_token, base_url, project_key = _require_jira_env()

    # --- Pre-validation: story titles <= 255 chars ---
    for story in stories:
        if len(story.title) > 255:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Story '{story.id}' title exceeds 255 characters "
                    f"({len(story.title)} chars). Jira summary field limit is 255."
                ),
            )

    # --- Build ADF payloads for all stories ---
    story_payloads: list[tuple[UserStory, dict]] = []
    for story in stories:
        try:
            ac: list[str] = json.loads(story.acceptance_criteria)
        except (json.JSONDecodeError, TypeError):
            ac = [story.acceptance_criteria]
        try:
            validations: list[dict] = json.loads(story.validations)
        except (json.JSONDecodeError, TypeError):
            validations = []

        adf_desc = _build_adf_description(
            description=story.description,
            acceptance_criteria=ac,
            validations=validations,
        )
        story_payloads.append((story, adf_desc))

    auth = httpx.BasicAuth(username=jira_email, password=jira_token)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    async with httpx.AsyncClient(auth=auth, headers=headers, timeout=30.0) as client:
        # --- Pre-validation: project exists + CREATE_ISSUES permission ---
        # These raise HTTPException on failure; status is still STORIES_GENERATED here
        # so a failure before the advance leaves the project in a valid retryable state.
        await _validate_jira_project(client, base_url, project_key)
        await _validate_create_permission(client, base_url, project_key)

        # --- Advance status to JIRA_PUSH_PENDING (prevents double-push on double-click) ---
        # Any concurrent request that reaches this point after the advance will see
        # JIRA_PUSH_PENDING and receive a 409 from advance_status's conflict guard.
        advance_status(body.project_id, WorkflowStatus.STORIES_GENERATED, db)

        # --- Create placeholder JiraTicket rows (issue_key=None) ---
        # Written before asyncio.gather so that _execute_rollback can query them
        # by project_id even if issue_key is set only for a subset.
        for story, _ in story_payloads:
            ticket = JiraTicket(
                project_id=body.project_id,
                story_id=story.id,
                issue_key=None,
                issue_url=None,
            )
            db.add(ticket)
        db.commit()

        # --- Concurrent Jira issue creation ---
        # _create_and_store returns (story_id, issue_key, issue_url) without touching
        # the DB — all DB writes happen sequentially after gather completes, so there
        # is no concurrent Session access from multiple coroutines.
        # return_exceptions=True delivers exceptions as values so we can inspect all
        # results and roll back only those that succeeded when others failed.
        gather_results = await asyncio.gather(
            *[
                _create_and_store(
                    client=client,
                    base_url=base_url,
                    project_key=project_key,
                    story=story,
                    adf_desc=adf_desc,
                )
                for story, adf_desc in story_payloads
            ],
            return_exceptions=True,
        )

        # --- Inspect results: separate successes from failures ---
        errors: list[str] = []
        successes: list[tuple[str, str, str]] = []  # (story_id, issue_key, issue_url)

        for result in gather_results:
            if isinstance(result, BaseException):
                errors.append(str(result))
            else:
                successes.append(result)  # (story_id, issue_key, issue_url)

        # --- Sequential DB writes for successful creations ---
        # Write issue_key/issue_url to each placeholder JiraTicket row.
        # Done before the error check so that _execute_rollback can find the
        # keys by querying JiraTicket rows even when only some creations succeeded.
        for story_id, issue_key, issue_url in successes:
            ticket = db.exec(
                select(JiraTicket).where(JiraTicket.story_id == story_id)
            ).first()
            if ticket is not None:
                ticket.issue_key = issue_key
                ticket.issue_url = issue_url
                db.add(ticket)
        db.commit()

        # --- On ANY failure: rollback all created issues, reset status ---
        if errors:
            rolled_back = await _execute_rollback(client, base_url, body.project_id, db)
            _reset_status_to_stories_generated(body.project_id, db)

            logger.error(
                "jira.push_failed",
                project_id=body.project_id,
                errors=errors,
                rolled_back=rolled_back,
            )

            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "message": "Jira push failed — all created issues have been rolled back.",
                    "errors": errors,
                    "rolled_back_keys": rolled_back,
                },
            )

        # --- All creations succeeded: advance to JIRA_PUSH_SUCCESS, then COMPLETED ---
        issue_keys = [s[1] for s in successes]

        advance_status(body.project_id, WorkflowStatus.JIRA_PUSH_PENDING, db)
        advance_status(body.project_id, WorkflowStatus.JIRA_PUSH_SUCCESS, db)

        logger.info(
            "jira.push_success",
            project_id=body.project_id,
            issue_keys=issue_keys,
            count=len(issue_keys),
        )

        return {"issue_keys": issue_keys, "count": len(issue_keys)}
