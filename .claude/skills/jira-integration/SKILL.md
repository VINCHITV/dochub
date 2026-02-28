# Jira Integration Skill

## Purpose
Create Jira tickets transactionally using REST API v3.

## Requirements
- Convert story content into valid Atlassian Document Format (ADF).
- Validate project existence and permissions.
- Enforce summary <= 255 chars.
- Use batch label: dochub_batch:<uuid>.

## Atomicity Rule
If any ticket creation fails:
- Delete all previously created tickets in that batch.
- Return failure.

## Guardrails
- Never allow partial success.
- Always log API responses.
- Use concurrency limit (Semaphore 5)
