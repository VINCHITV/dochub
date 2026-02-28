# Backend Architect Memory — DocHub

## Phase 4 Complete (API Routes)
Files written: `routes/upload.py`, `routes/generate.py`, `routes/export.py`, `routes/jira.py`
Phase 2 files: `services/versions.py`, `services/workflow.py`, `models.py`, `database.py`, `main.py`

## Key Schema Decisions

### Project model
- `id`: `str` UUID (not `uuid.UUID`) — avoids SQLite/Postgres portability issues
- `prd_json`: `Optional[str]` — JSON-serialized dict of all 7 sections (no PRDSection table)
- `status`: `WorkflowStatus` enum — stored as VARCHAR string via `str` mixin on enum
- Version fields (`prompt_version`, `generator_model`, `embedding_model`, `extractor_model`) default to `versions.py` constants but are FROZEN at generation time

### UserStory model
- `acceptance_criteria`: `str` — JSON-serialized `list[str]`
- `validations`: `str` — JSON-serialized `list[dict]`
- Route/service layer owns json.dumps/json.loads boundary

### PRDMetadata model
- `status`: plain `str` ("active" | "superseded") — NOT `WorkflowStatus`
- `project_id`: `unique=True` — one metadata row per project

## Workflow State Machine
- `WorkflowStatus` extends `str, enum.Enum` — serializes as string in JSON/SQLite automatically
- `VALID_TRANSITIONS` dict is the single source of truth — 6 transitions, COMPLETED is terminal
- `advance_status()` is SYNCHRONOUS — no await between read and write (SQLite single-worker safety)
- Idempotent: if already at `next_status`, return project without error (handles client retries)
- Raises `HTTPException` 404/409/422 — NOT custom exceptions — so route layer needs no translation
- `from app.models import Project` is deferred inside `advance_status()` body to avoid circular import

## Route URL Map (with mount prefixes from main.py)
- `POST /upload` → upload.router `@router.post("")` (prefix="/upload")
- `GET /upload/projects/{id}` → upload.router `@router.get("/projects/{project_id}")`
- `POST /generate/prd` → generate.router `@router.post("/prd")`
- `POST /generate/stories` → generate.router `@router.post("/stories")`
- `GET /export/{id}/docx` → export.router `@router.get("/{project_id}/docx")`
- `POST /export/projects/{id}/approve` → export.router `@router.post("/projects/{project_id}/approve")`
- `POST /jira/tickets` → jira.router `@router.post("/tickets")`

## SSE Pattern (generate.py)
- Pre-validate project state BEFORE returning StreamingResponse (4xx can still be JSON)
- Once StreamingResponse starts, errors must be sent as SSE events `{"error": "...", "done": true}`
- Heartbeat BEFORE every blocking `generate_section()` call: `data: {"heartbeat": true}\n\n`
- Section event: `data: {"section": "title", "data": {...}, "done": false}\n\n`
- Terminal: `data: {"done": true, "hallucination_count": N}\n\n`
- `StreamingResponse` headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no` (Railway Nginx)
- `source_doc_ids` set from RAG node metadata on result objects AFTER generation, never by LLM

## PRD Generation Pipeline (_prd_stream)
- RAG retrieval: transcript[:500] as query, top_k=5
- 7 sequential sections: title → description → problem → why → success → audience → open_questions
- `_sections_so_far()` closure injects all previously generated sections into each subsequent prompt
- `generated_sections` dict uses flat text values for context injection and KB indexing
- `prd_sections_payload` stores full `model_dump()` dicts (not flat text) in prd_json DB column
- Hallucination detection: regex find numbers/% in PRD, count those absent from transcript
- KB indexing: product_area derived from title_result.title (lowercased, spaces→underscores, max 64)
- KB indexing failure is NON-FATAL (logged as warning, pipeline continues)
- advance_status called AFTER prd_json committed, BEFORE KB indexing

## User Story Pipeline (_stories_stream)
- Step 1: emit `{"step": "extracting_capabilities"}` BEFORE heartbeat, then generate `CapabilityList`
- Step 2: heartbeat, generate `SlicePlan`, emit `{"step": "slices_planned", "count": N}` AFTER
- Step 3: per slice — heartbeat → generate `UserStoryModel` → commit row → emit `{"step": "story_done"}`
- `story_model.validations` is `list[ValidationRow]` — call `.model_dump()` per item when saving
- `slices[:7]` enforced at service boundary even if LLM returns more
- UserStory rows committed one-by-one inside loop (durable partial results on disconnect)
- advance_status(PRD_APPROVED → STORIES_GENERATED) at end, after all stories saved

## Jira Route Patterns (jira.py)
- Pre-validation before ANY create: `_validate_jira_project()` + `_validate_create_permission()`
- `advance_status(STORIES_GENERATED → JIRA_PUSH_PENDING)` BEFORE `asyncio.gather` (double-push guard)
- JiraTicket placeholder rows (issue_key=None) written BEFORE gather so rollback can query by project_id
- `_create_and_store()`: create → set issue_key on ticket row → commit immediately (not batch-end)
- `asyncio.gather(..., return_exceptions=True)` — inspect all results, collect successes and errors
- Rollback: `_execute_rollback()` deletes by stored issue_key, clears key/url from JiraTicket rows
- Status reset: `_reset_status_to_stories_generated()` writes status directly (ONLY bypass of advance_status)
  - Safe: rollback only reachable from JIRA_PUSH_PENDING, single-worker SQLite, no race condition
- `_JIRA_CONCURRENCY = asyncio.Semaphore(5)` — module-level, caps concurrent Jira creates
- ADF description built by `_build_adf_description(description, ac_list, validations_list)`

## Database Session Pattern in Routes
- Routes use `db.add(obj)` + `db.commit()` + `db.refresh(obj)` directly on injected session
- `get_session()` dependency yields session inside `with Session(engine)` — no nested sessions
- Never create a second `Session(db.bind)` inside a route — use the injected `db` directly

## Circular Import Prevention
- `workflow.py` imports `from app.models import Project` INSIDE function body, not at module top
- `jira.py` `_reset_status_to_stories_generated()` imports `Project` with same deferred pattern
- Routes import from services only (never from other routes)

## Env Var Policy
- All 6 env vars log WARNING on missing at startup — do NOT crash startup
- Jira route raises HTTP 503 at request time if Jira vars missing (`_require_jira_env()`)
- AI vars allow running without Anthropic/OpenAI during CI import checks
