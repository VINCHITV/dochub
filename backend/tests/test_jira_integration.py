"""
backend/tests/test_jira_integration.py

Real Jira integration tests — skipped unless all Jira env vars are set.

These tests hit the actual Jira REST API v3 against the DOCHUB-TEST project.
They validate:
1. Project accessibility: GET /rest/api/3/project/{key} returns 200
2. Issue creation: POST /rest/api/3/issue with a minimal ADF body returns 201
   and includes an `issue_key` in the response.
3. Cleanup: DELETE /rest/api/3/issue/{key} for the created issue returns 204.

Requirements:
- JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_PROJECT_KEY must all be set.
- The Jira project must have CREATE_ISSUES and DELETE_ISSUES permissions configured
  for the JIRA_EMAIL account.
- Run with: pytest tests/test_jira_integration.py -v

ADF (Atlassian Document Format) structure tested:
{
    "type": "doc",
    "version": 1,
    "content": [
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": "..."}]
        }
    ]
}

This is the exact format required by Jira REST API v3. Plain text or Markdown
will be rejected with a 400.
"""

from __future__ import annotations

import base64
import os

import httpx
import pytest

# ---------------------------------------------------------------------------
# Skip condition — all 4 Jira env vars must be present for these tests to run.
# ---------------------------------------------------------------------------

_JIRA_BASE_URL = os.environ.get("JIRA_BASE_URL", "")
_JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")
_JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN", "")
_JIRA_PROJECT_KEY = os.environ.get("JIRA_PROJECT_KEY", "")

_JIRA_CONFIGURED = all([
    _JIRA_BASE_URL,
    _JIRA_EMAIL,
    _JIRA_API_TOKEN,
    _JIRA_PROJECT_KEY,
    # Guard against the test-stub values set in conftest.py
    "test-" not in _JIRA_API_TOKEN,
    "test-" not in _JIRA_EMAIL,
])

pytestmark = pytest.mark.skipif(
    not _JIRA_CONFIGURED,
    reason=(
        "Jira integration tests require real JIRA_BASE_URL, JIRA_EMAIL, "
        "JIRA_API_TOKEN, and JIRA_PROJECT_KEY environment variables. "
        "Set these in backend/.env.test and export them before running."
    ),
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _auth_headers() -> dict[str, str]:
    """Build Basic Auth headers for Jira REST API v3."""
    credentials = f"{_JIRA_EMAIL}:{_JIRA_API_TOKEN}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _adf_description(text: str) -> dict:
    """Construct a minimal valid ADF document for a Jira issue description."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": text}
                ],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Test 1: Project accessibility
# ---------------------------------------------------------------------------

def test_jira_project_accessible():
    """
    GET /rest/api/3/project/{key} returns 200 for the configured project.

    Validates:
    - Auth credentials are accepted by Jira.
    - The project key resolves to a real project.
    - Response contains the project key in the body.
    """
    # Arrange
    url = f"{_JIRA_BASE_URL}/rest/api/3/project/{_JIRA_PROJECT_KEY}"
    headers = _auth_headers()

    # Act
    with httpx.Client() as client:
        response = client.get(url, headers=headers, timeout=30.0)

    # Assert
    assert response.status_code == 200, (
        f"Expected 200 from Jira project lookup, got {response.status_code}: "
        f"{response.text[:300]}"
    )
    body = response.json()
    assert body.get("key") == _JIRA_PROJECT_KEY


# ---------------------------------------------------------------------------
# Test 2: Issue creation with ADF body
# ---------------------------------------------------------------------------

def test_jira_create_issue_with_adf_returns_issue_key():
    """
    POST /rest/api/3/issue with a valid ADF description creates an issue.

    Validates:
    - ADF format is accepted by Jira (not rejected with 400).
    - Response body contains 'key' and 'id' fields.
    - The created issue key follows the project key prefix pattern.

    The created issue is stored in `_created_keys` module-level list so
    test_jira_cleanup can delete it.
    """
    # Arrange
    url = f"{_JIRA_BASE_URL}/rest/api/3/issue"
    headers = _auth_headers()
    payload = {
        "fields": {
            "project": {"key": _JIRA_PROJECT_KEY},
            "summary": "[DocHub test] ADF format validation — safe to delete",
            "issuetype": {"name": "Task"},
            "description": _adf_description(
                "This issue was created by DocHub's automated test suite. "
                "It validates that ADF (Atlassian Document Format) is accepted "
                "by the Jira REST API v3. Safe to delete."
            ),
        }
    }

    # Act
    with httpx.Client() as client:
        response = client.post(
            url, headers=headers, json=payload, timeout=30.0
        )

    # Assert
    assert response.status_code == 201, (
        f"Expected 201 from Jira issue creation, got {response.status_code}: "
        f"{response.text[:500]}"
    )
    body = response.json()
    assert "key" in body, f"Response missing 'key' field: {body}"
    assert "id" in body, f"Response missing 'id' field: {body}"
    issue_key = body["key"]
    assert issue_key.startswith(_JIRA_PROJECT_KEY), (
        f"Issue key '{issue_key}' does not start with project key '{_JIRA_PROJECT_KEY}'"
    )

    # Store for cleanup test (module-level so cleanup test can access it)
    _created_keys.append(issue_key)


# Module-level list: populated by test_jira_create_issue, consumed by test_jira_cleanup.
# Using a list (not a fixture) because pytest marks the tests with skipif at module level
# and we need the key to persist between test functions.
_created_keys: list[str] = []


# ---------------------------------------------------------------------------
# Test 3: Cleanup — delete the created issue
# ---------------------------------------------------------------------------

def test_jira_delete_created_issue():
    """
    DELETE /rest/api/3/issue/{key} for a previously created issue returns 204.

    Validates:
    - The delete endpoint works with Basic Auth.
    - No JQL or batch delete is used — deletion is by direct issue_key.
    - This mirrors the rollback logic in routes/jira.py.

    This test is intentionally ordered AFTER test_jira_create_issue_with_adf_returns_issue_key
    so that _created_keys is populated. If the create test was skipped or failed,
    this test skips gracefully.
    """
    if not _created_keys:
        pytest.skip(
            "No issue key to clean up — create test was skipped or failed."
        )

    issue_key = _created_keys[0]
    url = f"{_JIRA_BASE_URL}/rest/api/3/issue/{issue_key}"
    headers = _auth_headers()

    # Act
    with httpx.Client() as client:
        response = client.delete(url, headers=headers, timeout=30.0)

    # Assert
    assert response.status_code == 204, (
        f"Expected 204 from Jira issue delete for '{issue_key}', "
        f"got {response.status_code}: {response.text[:300]}"
    )

    # Verify the issue is gone
    with httpx.Client() as client:
        check_response = client.get(url, headers=headers, timeout=30.0)
    assert check_response.status_code == 404, (
        f"Issue '{issue_key}' should be deleted but GET returned "
        f"{check_response.status_code}"
    )
