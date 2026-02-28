# DocHub Technical Description

## 1) Project Purpose

DocHub is an AI-assisted product workflow system that converts meeting transcripts into implementation-ready artifacts:

1. Upload transcript (`.txt` or `.docx`)
2. Generate a structured PRD (7 fixed sections, streamed over SSE)
3. Approve PRD
4. Generate vertically sliced user stories (streamed over SSE)
5. Push stories to Jira with all-or-nothing guarantees

The product is optimized for demo speed (end-to-end under ~10 minutes) while preserving architecture choices that support later scaling and migration.

---

## 2) High-Level Architecture

### Backend
- Framework: FastAPI (Python 3.12)
- Persistence: SQLModel + SQLite
- AI orchestration: `instructor` + async OpenAI client
- RAG: LlamaIndex node parsing + ChromaDB persistent vector store + BM25 reranking
- Observability: `structlog` (JSON logs + console logs)
- Integrations: Jira REST API v3 via `httpx` async

### Frontend
- Framework: Next.js 14 (App Router)
- Styling: Tailwind CSS + shadcn-style component patterns
- State: Zustand (persisting only `projectId`)
- Streaming: `fetch` + `ReadableStream` (SSE via POST, not `EventSource`)

### Storage and Runtime Artifacts
- SQLite DB for projects/stories/tickets/metadata
- ChromaDB persisted in `backend/chroma_db`
- Structured logs in `backend/dochub.log`

---

## 3) Repository Structure (Technical View)

```text
dochub/
  backend/
    app/
      main.py
      models.py
      database.py
      routes/
        upload.py
        projects.py
        generate.py
        export.py
        jira.py
      services/
        ai.py
        rag.py
        vector_store.py
        workflow.py
        file_parser.py
        docx_builder.py
        docx_parser.py
        versions.py
    scripts/
      seed_kb.py
      create_template.py
    tests/
  frontend/
    app/
      page.tsx
      workflow/page.tsx
    components/
      WizardStepper.tsx
      PRDViewer.tsx
      StoriesViewer.tsx
    hooks/
      useFileUpload.ts
      useSSEStream.ts
    store/
      pipelineStore.ts
```

---

## 4) Core Domain Model

Defined in `backend/app/models.py`.

### `Project`
Primary pipeline entity.

- `id`: UUID string
- `name`: user-provided project name
- `transcript_text`: extracted transcript
- `prd_json`: serialized 7-section PRD
- `status`: workflow enum state
- version tags captured at generation time:
  - `prompt_version`
  - `generator_model`
  - `embedding_model`
  - `extractor_model`

### `UserStory`
Generated story tied to a project.

- `project_id` FK
- `title`, `description`
- `acceptance_criteria` (JSON string)
- `validations` (JSON string)

### `JiraTicket`
Mapping of story to created Jira issue.

- `project_id`, `story_id` FKs
- `issue_key`, `issue_url`
- written progressively to support rollback safety

### `PRDMetadata`
Knowledge base lifecycle metadata.

- one row per project (`project_id` unique)
- `product_area`, `doc_id`, `date`, `status`
- `status` controls retrievability (`active` vs `superseded`)

---

## 5) Workflow State Machine

Implemented in `backend/app/services/workflow.py`.

### States
- `TRANSCRIPT_UPLOADED`
- `PRD_GENERATED`
- `PRD_APPROVED`
- `STORIES_GENERATED`
- `JIRA_PUSH_PENDING`
- `JIRA_PUSH_SUCCESS`
- `COMPLETED`

### Transition Policy
- Transition rules are centralized in `VALID_TRANSITIONS`
- Normal status advancement must use `advance_status(...)`
- Built-in idempotency: if already at target next status, returns success
- Conflict protection: raises `409` on out-of-order transitions

Special exception:
- Jira rollback path may directly set `JIRA_PUSH_PENDING -> STORIES_GENERATED` as a controlled recovery write.

---

## 6) Backend Application Lifecycle

Defined in `backend/app/main.py`.

### Startup Behavior
- Loads `.env`
- Configures structlog:
  - JSON renderer to file (`dochub.log`)
  - human-readable console renderer to stdout
- Runs startup env checks (warning-only)
- Ensures DB tables exist

### Router Mounts
- `/upload`
- `/projects`
- `/generate`
- `/export`
- `/jira`

### Health Endpoint
- `GET /health`
- Returns status + prompt version for deploy verification

---

## 7) API and Streaming Contracts

## Upload and Project
- `POST /upload`
  - multipart upload of file + `project_name`
  - accepts `.txt` and `.docx`
  - `.docx` may auto-link to existing project via hidden metadata
- `GET /projects/{id}`
  - returns project data for frontend rehydration
- `POST /projects/{id}/approve`
  - advances `PRD_GENERATED -> PRD_APPROVED`

## PRD Generation
- `POST /generate/prd` (SSE)
- Emits:
  - heartbeat events: `{"heartbeat": true}`
  - section events: `{"section": "<name>", "data": {...}, "done": false}`
  - terminal event: `{"done": true, "hallucination_count": <int>}`

## Story Generation
- `POST /generate/stories` (SSE)
- Emits:
  - `{"step": "extracting_capabilities"}`
  - `{"step": "slices_planned", "count": N}`
  - repeated `{"step": "story_done", "story": {...}}`
  - terminal `{"done": true}`

## Export
- `GET /export/{id}/docx`
- Builds and returns branded DOCX from stored PRD

## Jira
- `POST /jira/tickets`
- Creates one Jira issue per story with rollback on any failure

---

## 8) PRD Generation Internals

Implemented mainly in `backend/app/routes/generate.py` and `backend/app/services/ai.py`.

### Section Pipeline
PRD generation is sequential and structured:

1. `title`
2. `description`
3. `problem`
4. `why`
5. `success`
6. `audience`
7. `open_questions`

Each section:
- has dedicated prompt instructions
- validates against a Pydantic model
- receives transcript + RAG context + previously generated sections
- is streamed as soon as ready

### Reliability and UX Details
- Heartbeat event before every blocking model call to avoid proxy idle timeouts
- Token and latency logs per section
- Soft-fail behavior for KB indexing errors (warn, do not hard-crash stream)

### Hallucination Heuristic
- Numeric claims are regex-extracted from PRD output
- Claims missing from transcript are counted as warnings
- Returned as `hallucination_count` at stream completion

---

## 9) AI Layer and Schema Contracts

Defined in `backend/app/services/ai.py`.

### Model Strategy
- Async `instructor` client over OpenAI chat completions
- Strong schema-first extraction with Pydantic models
- Retry policy (`max_retries=3`) enforced by instructor

### Key Pydantic Models
- PRD section models (`TitleSection`, `DescriptionSection`, etc.)
- `ConflictEntry` for knowledge-base conflict tracking
- Story pipeline models:
  - `CapabilityList`
  - `SlicePlan`
  - `UserStoryModel`
  - `ValidationRow`

### Versioning
Configured in `backend/app/services/versions.py`:
- `GENERATOR_MODEL = "gpt-4o"`
- `EXTRACTOR_MODEL = "gpt-4o"`
- `EMBEDDING_MODEL = "text-embedding-3-small"`
- `PRD_PROMPT_VERSION = "prd-v1.2"`

These constants are imported across services (no hardcoded model names elsewhere).

---

## 10) RAG and Knowledge Base Design

Implemented in `backend/app/services/rag.py`.

### Retrieval Strategy (Hybrid)
1. Semantic retrieval from ChromaDB
2. BM25 reranking over semantic candidate set
3. Reciprocal Rank Fusion (RRF, `k=60`)

### Important Retrieval Guarantees
- Always filters to `status=active`
- Preserves and logs:
  - `semantic_score`
  - `bm25_score`
  - `rrf_rank`
- Deterministic tie-breaking (lexicographic on `doc_id`)

### Indexing Strategy
When storing a newly generated PRD:
- supersede prior `active` docs in same `product_area`
- chunk with hierarchical parser (`2048`, `512`)
- embed chunks with OpenAI embeddings
- upsert to ChromaDB with metadata:
  - `doc_id`, `section`, `date`, `product_area`, `status`, `embedding_model`, `project_id`

---

## 11) Jira Integration and Transaction Semantics

Implemented in `backend/app/routes/jira.py`.

### Pre-validation
Before issue creation:
- Jira env vars present
- project exists and is reachable
- user has `CREATE_ISSUES`
- story titles satisfy Jira summary limits

### Push Execution
- move workflow to `JIRA_PUSH_PENDING` before create fan-out
- create placeholder `JiraTicket` rows
- create issues concurrently (bounded semaphore)
- write `issue_key`/`issue_url` back per success

### Rollback Logic (All-or-Nothing)
If any issue creation fails:
- delete every created issue by stored `issue_key`
- clear stored keys/urls
- reset project status back to `STORIES_GENERATED`
- return failure payload with rollback details

If all succeed:
- advance `JIRA_PUSH_PENDING -> JIRA_PUSH_SUCCESS -> COMPLETED`

---

## 12) DOCX Round-Trip Support

### Export (`docx_builder.py`)
- Builds from branded template
- Writes standardized section headings
- Appends hidden metadata paragraph:
  - `[DOCHUB_METADATA]{...}`

### Re-upload (`docx_parser.py`)
- Reads hidden metadata from uploaded DOCX
- If referenced project exists, upload endpoint returns existing project instead of creating duplicates

This enables document round-tripping without manual project re-linking.

---

## 13) Frontend Technical Flow

Main UI lives in `frontend/app/workflow/page.tsx`.

### State Management
In `frontend/store/pipelineStore.ts`:
- only `projectId` is persisted to localStorage
- status and payloads are rehydrated from backend (`GET /projects/{id}`)
- step rendering is derived from server status, reducing client drift

### Streaming
In `frontend/hooks/useSSEStream.ts`:
- sends POST request
- reads `ReadableStream` chunks
- parses `data: ...` lines
- ignores heartbeat events
- emits parsed events to handlers

### Step Mapping
Client wizard progression is based on workflow status:
- upload -> review -> stories -> jira

---

## 14) Observability and Diagnostics

`structlog` events provide operational traceability.

### Key Event Types
- `llm_call`
  - per model call token/latency metrics
- `rag_retrieval`
  - retrieval candidate scores and rank
- `pipeline_complete`
  - aggregate PRD pipeline metrics and hallucination warning count
- Jira lifecycle events
  - creation success, rollback start/complete, push success/failure

Logs are emitted in JSON format to `backend/dochub.log`.

---

## 15) Operational Constraints and Deployment Notes

- Backend should run as a single worker when ChromaDB is active (process-exclusive index behavior)
- Environment variable correctness is critical for AI/Jira features
- Jira rollback depends on delete permission in target project
- SSE heartbeat behavior is required in environments with idle timeout proxies

---

## 16) Testing Strategy

Backend tests cover:
- schema validation (Pydantic contracts)
- workflow transitions
- route behavior and integration paths
- Jira integration round-trip (when credentials/project are available)

Recommended regular checks:
- unit/schema tests for fast validation
- targeted Jira integration before demos
- startup and pipeline log inspection for runtime issues

---

## 17) Current System Characteristics

### Strengths
- strong typed contracts across AI outputs
- explicit workflow guardrails
- streaming UX for long-running AI tasks
- rollback-safe Jira integration
- version-tagged generation metadata

### Known Trade-offs
- SQLite + local ChromaDB suit prototype/demo scale, not distributed high throughput
- BM25 leg reranks semantic candidates (not full independent corpus retrieval)
- numeric hallucination detection is heuristic (fast but not semantically complete)

### Migration Readiness
Architecture already separates concerns in a way that supports migration to:
- managed vector DB / pgvector
- Postgres transactional locking
- background workers for long-running pipelines
- richer tracing/telemetry stacks

