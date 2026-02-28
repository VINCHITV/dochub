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


_SIZE_LABELS: dict[str, str] = {
    "XS": "XS — Extra Small (< 1 day)",
    "S": "S — Small (1-2 days)",
    "M": "M — Medium (3-5 days)",
    "L": "L — Large (6-10 days)",
    "XL": "XL — Extra Large (> 2 weeks, consider splitting)",
}


def _build_adf_description(
    description: str,
    acceptance_criteria: list[str],
    validations: list[dict],
    size: str = "M",
    transcript_references: Optional[list[dict]] = None,
) -> dict:
    """
    Build an ADF document for a Jira issue description.

    Structure:
    - Paragraph: user story description (As a... I want... So that...)
    - Heading 2: Acceptance Criteria
    - Bullet list: each AC item
    - Heading 2: Validations  (omitted if validations list is empty)
    - Bullet list: "field: rule — error_message" per validation row
    - Heading 2: Story Size
    - Paragraph: T-shirt size label
    - Heading 2: References  (omitted if no transcript_references)
    - Bullet list: "Speaker: excerpt [source]" per reference

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

    # Story Size section
    content.append(_adf_heading("Story Size", level=2))
    content.append(_adf_paragraph(_SIZE_LABELS.get(size, f"{size}")))

    # Transcript references section
    if transcript_references:
        content.append(_adf_heading("References", level=2))
        ref_items = []
        for ref in transcript_references:
            speaker = ref.get("speaker", "")
            excerpt = ref.get("excerpt", "")
            source = ref.get("source", "transcript")
            prefix = f"{speaker}: " if speaker else ""
            ref_items.append(f'{prefix}"{excerpt}" [{source}]')
        content.append(_adf_bullet_list(ref_items))

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


async def _update_jira_issue(
    client: httpx.AsyncClient,
    base_url: str,
    issue_key: str,
    summary: str,
    adf_description: dict,
) -> None:
    """
    Update an existing Jira issue's summary and description via PUT.

    Gated by _JIRA_CONCURRENCY semaphore. Raises httpx.HTTPStatusError on failure.
    """
    payload = {
        "fields": {
            "summary": summary,
            "description": adf_description,
        }
    }
    async with _JIRA_CONCURRENCY:
        resp = await client.put(
            f"{base_url}/rest/api/3/issue/{issue_key}",
            json=payload,
        )
    resp.raise_for_status()


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


async def _update_and_store(
    client: httpx.AsyncClient,
    base_url: str,
    story: UserStory,
    existing_ticket: JiraTicket,
    adf_desc: dict,
) -> tuple[str, str, str]:
    """
    Update an existing Jira issue and return (story_id, issue_key, issue_url).

    Does NOT write to the database — the caller writes sequentially after gather.
    Returns (story_id, issue_key, issue_url).
    Raises httpx.HTTPStatusError on Jira API failure.
    """
    await _update_jira_issue(
        client=client,
        base_url=base_url,
        issue_key=existing_ticket.issue_key,
        summary=story.title,
        adf_description=adf_desc,
    )

    logger.info(
        "jira.issue_updated",
        story_id=story.id,
        issue_key=existing_ticket.issue_key,
        issue_url=existing_ticket.issue_url,
    )

    return story.id, existing_ticket.issue_key, existing_ticket.issue_url


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


def _build_story_adf(story: UserStory) -> dict:
    """Parse story JSON fields and build its ADF description payload."""
    try:
        ac: list[str] = json.loads(story.acceptance_criteria)
    except (json.JSONDecodeError, TypeError):
        ac = [story.acceptance_criteria] if story.acceptance_criteria else []
    try:
        validations: list[dict] = json.loads(story.validations)
    except (json.JSONDecodeError, TypeError):
        validations = []
    try:
        transcript_refs: list[dict] = json.loads(story.transcript_references)
    except (json.JSONDecodeError, TypeError):
        transcript_refs = []

    return _build_adf_description(
        description=story.description,
        acceptance_criteria=ac,
        validations=validations,
        size=getattr(story, "size", "M") or "M",
        transcript_references=transcript_refs or None,
    )


@router.post("/tickets", status_code=status.HTTP_201_CREATED)
async def push_jira_tickets(
    body: JiraTicketsRequest,
    db: Annotated[Session, Depends(get_session)],
) -> dict:
    """
    Smart Jira push for all user stories in a project.

    Groups stories by action based on their status and existing JiraTicket rows:
    - open + no ticket  → CREATE new Jira issue
    - open + has ticket → UPDATE existing Jira issue
    - obsolete + ticket → DELETE from Jira (not in 'done')
    - done              → skip always

    Returns
    -------
    {
      "created": [{key, url, title, priority}, ...],
      "updated": [{key, url, title, priority}, ...],
      "deleted": [{key, title}, ...],
      "count": int  (total created + updated)
    }

    Raises
    ------
    404  — project not found
    409  — project not in STORIES_GENERATED (wrong state or double-push blocked)
    422  — no open/obsolete stories found, or story title exceeds 255 chars
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

    # --- Load all user stories ---
    stories_stmt = select(UserStory).where(UserStory.project_id == body.project_id)
    all_stories: list[UserStory] = list(db.exec(stories_stmt).all())

    if not all_stories:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No user stories found for this project. Generate stories first.",
        )

    # --- Load existing JiraTickets keyed by story_id ---
    tickets_stmt = select(JiraTicket).where(JiraTicket.project_id == body.project_id)
    existing_tickets: dict[str, JiraTicket] = {
        t.story_id: t
        for t in db.exec(tickets_stmt).all()
        if t.story_id and t.issue_key  # only rows with a real Jira key
    }

    # --- Categorise stories by action ---
    # Priority ordering for sorted response: high → medium → low
    _PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

    to_create: list[UserStory] = []   # open, no existing ticket
    to_update: list[UserStory] = []   # open, has existing ticket
    to_delete: list[UserStory] = []   # obsolete, has existing ticket
    # done stories are always skipped

    for story in all_stories:
        if story.story_status == "done":
            continue
        has_ticket = story.id in existing_tickets
        if story.story_status == "obsolete":
            if has_ticket:
                to_delete.append(story)
        else:  # open
            if has_ticket:
                to_update.append(story)
            else:
                to_create.append(story)

    # Sort each group by priority
    for group in (to_create, to_update, to_delete):
        group.sort(key=lambda s: _PRIORITY_ORDER.get(s.priority, 1))

    # --- Require Jira env vars (raises 503 if missing) ---
    jira_email, jira_token, base_url, project_key = _require_jira_env()

    # --- Pre-validation: story titles <= 255 chars for create/update ---
    for story in to_create + to_update:
        if len(story.title) > 255:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Story '{story.id}' title exceeds 255 characters "
                    f"({len(story.title)} chars). Jira summary field limit is 255."
                ),
            )

    auth = httpx.BasicAuth(username=jira_email, password=jira_token)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    async with httpx.AsyncClient(auth=auth, headers=headers, timeout=30.0) as client:
        # --- Pre-validation: project exists + CREATE_ISSUES permission ---
        await _validate_jira_project(client, base_url, project_key)
        await _validate_create_permission(client, base_url, project_key)

        # --- Advance to JIRA_PUSH_PENDING (double-push guard) ---
        advance_status(body.project_id, WorkflowStatus.STORIES_GENERATED, db)

        # --- Phase 1: DELETE obsolete stories from Jira (non-fatal on failure) ---
        deleted_results: list[dict] = []
        if to_delete:
            delete_keys = [existing_tickets[s.id].issue_key for s in to_delete]
            await asyncio.gather(
                *[_delete_jira_issue(client, base_url, key) for key in delete_keys],
                return_exceptions=True,
            )
            # Record and clean up deleted tickets from DB
            for story in to_delete:
                ticket = existing_tickets[story.id]
                deleted_results.append({"key": ticket.issue_key, "title": story.title})
                ticket.issue_key = None
                ticket.issue_url = None
                db.add(ticket)
            db.commit()

            logger.info(
                "jira.obsolete_deleted",
                project_id=body.project_id,
                deleted_keys=delete_keys,
                count=len(delete_keys),
            )

        # --- Phase 2: UPDATE modified stories concurrently ---
        update_gather_results: list = []
        if to_update:
            update_gather_results = await asyncio.gather(
                *[
                    _update_and_store(
                        client=client,
                        base_url=base_url,
                        story=story,
                        existing_ticket=existing_tickets[story.id],
                        adf_desc=_build_story_adf(story),
                    )
                    for story in to_update
                ],
                return_exceptions=True,
            )

        # --- Phase 3: CREATE new stories concurrently ---
        # Placeholder JiraTicket rows created before gather for rollback support.
        for story in to_create:
            placeholder = JiraTicket(
                project_id=body.project_id,
                story_id=story.id,
                issue_key=None,
                issue_url=None,
            )
            db.add(placeholder)
        db.commit()

        create_gather_results: list = []
        if to_create:
            create_gather_results = await asyncio.gather(
                *[
                    _create_and_store(
                        client=client,
                        base_url=base_url,
                        project_key=project_key,
                        story=story,
                        adf_desc=_build_story_adf(story),
                    )
                    for story in to_create
                ],
                return_exceptions=True,
            )

        # --- Collect errors from create + update ---
        create_errors = [str(r) for r in create_gather_results if isinstance(r, BaseException)]
        update_errors = [str(r) for r in update_gather_results if isinstance(r, BaseException)]
        all_errors = create_errors + update_errors

        create_successes: list[tuple[str, str, str]] = [
            r for r in create_gather_results if not isinstance(r, BaseException)
        ]
        update_successes: list[tuple[str, str, str]] = [
            r for r in update_gather_results if not isinstance(r, BaseException)
        ]

        # --- Sequential DB writes for successful creations ---
        for story_id, issue_key, issue_url in create_successes:
            ticket = db.exec(
                select(JiraTicket).where(JiraTicket.story_id == story_id)
            ).first()
            if ticket is not None:
                ticket.issue_key = issue_key
                ticket.issue_url = issue_url
                db.add(ticket)
        db.commit()

        # --- On create/update failure: rollback NEWLY CREATED issues, reset status ---
        if all_errors:
            # Only rollback newly created tickets (not pre-existing updated ones)
            new_keys_to_rollback = [s[1] for s in create_successes]
            if new_keys_to_rollback:
                await asyncio.gather(
                    *[_delete_jira_issue(client, base_url, key) for key in new_keys_to_rollback],
                    return_exceptions=True,
                )
                # Clear rolled-back keys from DB
                for _, issue_key, _ in create_successes:
                    ticket = db.exec(
                        select(JiraTicket).where(JiraTicket.issue_key == issue_key)
                    ).first()
                    if ticket:
                        ticket.issue_key = None
                        ticket.issue_url = None
                        db.add(ticket)
                db.commit()

            _reset_status_to_stories_generated(body.project_id, db)

            logger.error(
                "jira.push_failed",
                project_id=body.project_id,
                errors=all_errors,
                rolled_back=new_keys_to_rollback,
            )

            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "message": "Jira push failed — newly created issues have been rolled back.",
                    "errors": all_errors,
                    "rolled_back_keys": new_keys_to_rollback,
                },
            )

        # --- Build categorised response sorted by priority ---
        story_map = {s.id: s for s in all_stories}

        created_results: list[dict] = []
        for story_id, issue_key, issue_url in create_successes:
            s = story_map.get(story_id)
            created_results.append({
                "key": issue_key,
                "url": issue_url,
                "title": s.title if s else "",
                "priority": s.priority if s else "medium",
            })
        created_results.sort(key=lambda x: _PRIORITY_ORDER.get(x["priority"], 1))

        updated_results: list[dict] = []
        for story_id, issue_key, issue_url in update_successes:
            s = story_map.get(story_id)
            updated_results.append({
                "key": issue_key,
                "url": issue_url,
                "title": s.title if s else "",
                "priority": s.priority if s else "medium",
            })
        updated_results.sort(key=lambda x: _PRIORITY_ORDER.get(x["priority"], 1))

        # deleted_results already built above; sort by key for consistency
        deleted_results.sort(key=lambda x: x["key"])

        total_count = len(created_results) + len(updated_results)

        # --- Advance to JIRA_PUSH_SUCCESS → COMPLETED ---
        advance_status(body.project_id, WorkflowStatus.JIRA_PUSH_PENDING, db)
        advance_status(body.project_id, WorkflowStatus.JIRA_PUSH_SUCCESS, db)

        logger.info(
            "jira.push_success",
            project_id=body.project_id,
            created_count=len(created_results),
            updated_count=len(updated_results),
            deleted_count=len(deleted_results),
            total_count=total_count,
        )

        return {
            "created": created_results,
            "updated": updated_results,
            "deleted": deleted_results,
            "count": total_count,
            # Legacy field for backward compat
            "issue_keys": [r["key"] for r in created_results + updated_results],
        }
