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
- `VALID_TRANSITIONS` dict is the single source of truth; COMPLETED is terminal
- `advance_status()` is SYNCHRONOUS — no await between read and write (SQLite single-worker safety)
- Idempotent: if already at `next_status`, return project without error (handles client retries)
- Raises `HTTPException` 404/409/422 — NOT custom exceptions
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

## SSE Generator Session Ownership (CRITICAL)
- `_prd_stream` and `_stories_stream` must NOT accept `db: Session` from route handler.
  FastAPI's Depends cleanup closes the session when the route handler returns, before the
  async generator finishes streaming.
- Pattern: open `with Session(engine) as db:` INSIDE the generator at the point of DB use.
  Import `engine` from `app.database`, not via `get_session`.
- For `_prd_stream`: single `with Session(engine) as db:` block at the END (after 7 AI calls),
  wrapping persist prd_json + advance_status + PRDMetadata commit.
- For `_stories_stream`: each story commit uses its own `with Session(engine) as db:` block
  (one per loop iteration); advance_status uses a separate block after the loop.
- NEVER `yield` inside a `with Session(engine) as db:` block. Pattern:
  `_err: Optional[str] = None` → set inside `with` → `if _err: yield ...; return` outside.

## Sync Calls in Async Context (CRITICAL)
- `HybridRetriever.retrieve()` calls `openai.OpenAI()` synchronously — blocks event loop.
- `save_prd_to_kb()` calls `openai.OpenAI()` synchronously — blocks event loop.
- Fix: `await asyncio.to_thread(retriever.retrieve, query, top_k, project_id)` and
  `await asyncio.to_thread(save_prd_to_kb, sections, project_id, product_area, collection)`.
- `asyncio.to_thread` takes positional args after the fn; use `functools.partial` for kwargs.

## PRD Generation Pipeline (_prd_stream)
- RAG retrieval: transcript[:500] as query, top_k=5, wrapped in asyncio.to_thread
- 7 sequential sections: title → description → problem → why → success → audience → open_questions
- `_sections_so_far()` closure injects all previously generated sections into each subsequent prompt
- `generated_sections` dict uses flat text values for context injection and KB indexing
- `prd_sections_payload` stores full `model_dump()` dicts (not flat text) in prd_json DB column
- Hallucination detection: regex find numbers/% in PRD, count those absent from transcript
- KB indexing: product_area derived from title_result.title (lowercased, spaces→underscores, max 64)
- KB indexing wrapped in asyncio.to_thread; failure is NON-FATAL (logged as warning)
- PRDMetadata row written ONLY after successful KB indexing (skipped if KB indexing fails)
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
- `_create_and_store()` returns `(story_id, issue_key, issue_url)` — does NOT write to DB.
  DB writes happen SEQUENTIALLY after gather completes to avoid concurrent Session access.
- After gather: write successes to DB first (so rollback can find keys on partial failure), then check errors.
- `asyncio.gather(..., return_exceptions=True)` — inspect all results, collect successes and errors
- Rollback: `_execute_rollback()` deletes by stored issue_key, clears key/url from JiraTicket rows
- Status reset: `_reset_status_to_stories_generated()` writes status directly (ONLY bypass of advance_status)
  - Safe: rollback only reachable from JIRA_PUSH_PENDING, single-worker SQLite, no race condition
- `_JIRA_CONCURRENCY = asyncio.Semaphore(5)` — module-level, caps concurrent Jira creates
- ADF description built by `_build_adf_description(description, ac_list, validations_list)`
- On success: advance JIRA_PUSH_PENDING → JIRA_PUSH_SUCCESS, then JIRA_PUSH_SUCCESS → COMPLETED
  (two consecutive advance_status calls; state machine has COMPLETED as terminal)

## PRDMetadata Write Pattern
- Written in `_prd_stream` after `save_prd_to_kb()` succeeds (gated by `kb_index_succeeded` flag).
- Fields: project_id, product_area, doc_id=f"{product_area}-{date.today().isoformat()}", date, status="active", embedding_model=EMBEDDING_MODEL
- Uses same `with Session(engine) as db:` block as the prd_json persist + advance_status.
- Imports needed: `from app.models import PRDMetadata`; `from datetime import date`

## Database Session Pattern in Routes
- Routes use `db.add(obj)` + `db.commit()` + `db.refresh(obj)` directly on injected session
- `get_session()` dependency yields session inside `with Session(engine)` — no nested sessions
- Never create a second `Session(db.bind)` inside a route — use the injected `db` directly
- SSE generators: use `with Session(engine) as db:` directly, import `engine` from `app.database`

## Circular Import Prevention
- `workflow.py` imports `from app.models import Project` INSIDE function body, not at module top
- `jira.py` `_reset_status_to_stories_generated()` imports `Project` with same deferred pattern
- Routes import from services only (never from other routes)

## Env Var Policy
- All 6 env vars log WARNING on missing at startup — do NOT crash startup
- Jira route raises HTTP 503 at request time if Jira vars missing (`_require_jira_env()`)
- AI vars allow running without Anthropic/OpenAI during CI import checks

## A1: Multi-Transcript Upload (implemented 2026-02-28)
- New `Transcript` SQLModel table in `models.py` (between UserStory and JiraTicket)
  - `doc_id` format: `<project_id>-t<sort_order>` (0-based, continuous across calls)
  - `sort_order` = count of existing rows at insert time; never reset to 0
- New route: `POST /upload/transcripts/{project_id}` in `routes/upload.py`
  - `files: List[UploadFile]` — use `List` from `typing`, not `list` bare (FastAPI 0.115 multipart)
  - Status guard: 409 if `project.status != TRANSCRIPT_UPLOADED`
  - Merge: `"\n\n".join(f"--- [Source: {t.filename}] ---\n\n{t.raw_text}" for t in all_transcripts)`
  - `db.flush()` + `db.refresh(t)` to get generated IDs before final `db.commit()`
- `save_prd_to_kb` gained optional `transcript_doc_ids: Optional[list[str]] = None`
  - Stored as `json.dumps(list)` in chunk metadata (ChromaDB requires scalar values)
- Tests: `backend/tests/test_multi_transcript.py` — 12 tests

## Test Environment Note
- `.venv` (Python 3.13) — run `.venv/bin/pip install -r requirements.txt` before tests
- Static syntax check: `python3 -c "import ast; ast.parse(open(f).read())"` works without venv
- `dochub.log` is a directory in dev environment — conftest.py sets `LOG_FILE` to a temp file:
  `os.environ.setdefault("LOG_FILE", os.path.join(tempfile.gettempdir(), "dochub_test.log"))`
  Must be set BEFORE `from app.main import app` (main.py opens the log file at import time)

## B1: Upload Refined PRD (implemented 2026-02-28)
- Route: `POST /projects/{project_id}/upload-refined-prd` in `routes/projects.py`
- Request: `multipart/form-data` with `file: UploadFile` (.docx only, 422 for anything else)
- Allowed statuses: `{PRD_GENERATED, PRD_APPROVED}` — stored in `_REFINED_PRD_ALLOWED_STATUSES` frozenset
- If `PRD_APPROVED`: resets to `PRD_GENERATED` via direct write (intentional backward transition — only valid use)
- open_questions is ALWAYS regenerated from KB; never taken from the DOCX file
- RAG retrieval failure is non-fatal (logs warning, proceeds with empty rag_context)
- `_build_section_dict(key, text) -> dict` converts flat text to prd_json nested format per section model
- Mocking pattern for tests: patch at `app.routes.projects.generate_section`,
  `app.routes.projects.get_chroma_client`, `app.routes.projects.get_or_create_collection`,
  and monkeypatch `app.routes.projects.HybridRetriever.retrieve` to return `[]`

## Route URL Map — Updated (B1)
- `GET /projects/{id}` → projects.router
- `POST /projects/{id}/approve` → projects.router
- `POST /projects/{id}/upload-refined-prd` → projects.router (NEW — B1)
- `POST /upload` → upload.router
- `POST /upload/transcripts/{id}` → upload.router (A1)
