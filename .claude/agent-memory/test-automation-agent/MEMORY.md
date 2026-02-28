# Test Automation Agent Memory — DocHub

## Key Architecture Facts

- **backend/** is the root for pytest; run `pytest tests/` from there.
- `pytest.ini` lives at `backend/pytest.ini` with `asyncio_mode = auto`.
- `conftest.py` lives at `backend/tests/conftest.py`.
- All app imports use `app.*` package paths (not relative). `conftest.py` inserts `backend/` into `sys.path`.
- **Routes are stubs** (Phase 1): `upload.py`, `generate.py`, `jira.py` are empty routers. Tests for these routes cannot run yet.

## Workflow State Machine Gotchas

**Critical**: `advance_status()` has idempotency BEFORE the 409 conflict check:
```python
if project.status == next_status:    # idempotency — returns silently
    return project
if project.status != expected_current:  # only then: 409
    raise HTTPException(409, ...)
```
When writing 409 tests, the parametrize pairs must ensure `project.status` is NEITHER `expected_current` NOR `next_status = VALID_TRANSITIONS[expected_current]`. Otherwise the test hits the idempotency path instead. See `test_workflow.py` for correct pairs.

## Pydantic v2 Behavior

- `str` fields reject `int` inputs (no auto-coercion in strict context by default). Confirmed with pydantic 2.12.4.
- `list[str]` fields reject plain string inputs.
- Missing required fields → `ValidationError` with `loc == ("field_name",)`.
- `Literal["a","b","c"]` fields reject anything not in the literal. Error loc contains field name.

## Fake Env Vars in conftest.py

Set these BEFORE importing `app.main` (which triggers `load_dotenv`):
```python
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key-not-real")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key-not-real")
# etc.
```
`startup_check()` is WARNING-only — app doesn't crash with missing vars.

## Jira Integration Tests Skip Condition

The skip guard checks for test-stub values (`"test-" not in _JIRA_API_TOKEN`). This prevents the integration tests from attempting to run with conftest.py's fake values.

## In-Memory SQLite Setup

Use `StaticPool` from `sqlmodel.pool` for in-memory SQLite so the same connection is reused across multiple `Session` instances within one test:
```python
from sqlmodel.pool import StaticPool
engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, ...)
```

## File Locations

- Tests: `backend/tests/` (conftest, test_schemas, test_workflow, test_jira_integration)
- Seed data: `backend/data/seed_prds/` (payments_v2.txt, auth_sso.txt, notifications_v3.txt)
- Scripts: `backend/scripts/seed_kb.py`
- Config: `backend/pytest.ini`

## See Also

- `patterns.md` — detailed mock strategies (to be populated as more test phases are implemented)
