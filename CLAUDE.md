# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

DocHub is a hackathon prototype that turns meeting transcripts into context-aware PRDs, then turns those PRDs into Jira tickets. It uses RAG to build a living knowledge base across all processed PRDs.

Pipeline: `Transcript → PRD (RAG-informed, section-by-section) → User Stories (3-step) → Jira tickets`

Full requirements are in `DocHub Problem Statement.pdf`.

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python 3.12), SQLModel, SQLite |
| AI | Anthropic `claude-sonnet-4-6`, `instructor` + `AsyncAnthropic()` (must be async), Pydantic v2 |
| RAG | LlamaIndex + ChromaDB (local, persistent at `./chroma_db`) + `rank-bm25` |
| Embeddings | OpenAI `text-embedding-3-small` |
| Logging | `structlog` JSON to `dochub.log` |
| DOCX read | `python-docx` (heading-based section parse on re-upload) |
| DOCX write | `python-docx` (from branded template, `Heading 1` styles for sections) |
| Jira | Raw `httpx` async, REST API v3 (ADF format) |
| Frontend | Next.js 14 App Router, shadcn/ui, Tailwind CSS |
| State | Zustand (persists only `projectId` to localStorage; derives step from server status) |
| Streaming | SSE via `fetch` + `ReadableStream` — NOT `EventSource` (can't POST) |

## Commands

### Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload          # dev — single worker only (ChromaDB HNSW is process-exclusive)
```

Never run `uvicorn --workers N` with N > 1 — ChromaDB's HNSW binary is process-exclusive.

API docs at `http://localhost:8000/docs`.

### Frontend
```bash
cd frontend
npm install
npm run dev      # localhost:3000
npm run build && npm run lint
npx shadcn@latest add <component>
```

### Seed / backup knowledge base
```bash
cd backend
python scripts/seed_kb.py              # index pre-seeded PRDs into ChromaDB
cp -r chroma_db chroma_db.demo_backup  # backup before demo — recovery from HNSW corruption
```

### Run tests
```bash
cd backend && pytest tests/             # unit + schema tests (no network required)
pytest tests/test_jira_integration.py   # requires DOCHUB-TEST project + env vars
```

## Architecture

### Backend structure
```
backend/app/
├── main.py             # FastAPI app, CORS, router mounts, startup env-var check, structlog setup
├── models.py           # SQLModel: Project (WorkflowStatus + version tags), UserStory, JiraTicket, PRDMetadata
├── database.py         # engine, get_session dependency
├── routes/
│   ├── upload.py       # POST /upload — parse .txt/.docx to text
│   ├── generate.py     # POST /generate/prd, /generate/stories (SSE stream)
│   ├── export.py       # GET /export/{id}/docx — download branded .docx
│   └── jira.py         # POST /jira/tickets — pre-validate + concurrent create + stored-key rollback
└── services/
    ├── ai.py           # All Pydantic section/story models; instructor + AsyncAnthropic wrappers
    ├── rag.py          # HybridRetriever, PRDMetadata extraction on save, KB versioning
    ├── vector_store.py # ChromaDB client factory — PersistentClient wrapper used by rag.py
    ├── workflow.py     # WorkflowStatus enum, VALID_TRANSITIONS dict, advance_status()
    ├── docx_builder.py # python-docx export with Heading 1 styles + w:vanish metadata paragraph
    ├── docx_parser.py  # Heading-based re-upload parse; extract_embedded_metadata()
    ├── file_parser.py  # .txt/.docx bytes → plain text (mammoth for uploads)
    └── versions.py     # GENERATOR_MODEL, EMBEDDING_MODEL, EXTRACTOR_MODEL, PRD_PROMPT_VERSION constants
```

### Frontend structure
```
frontend/app/
├── page.tsx              # Name entry
└── workflow/page.tsx     # Wizard container
frontend/
├── components/
│   ├── PRDViewer.tsx     # Renders 7 sections as SSE section events arrive (skeleton per section)
│   ├── StoriesViewer.tsx # Renders stories as SSE story_done events arrive
│   └── WizardStepper.tsx
├── store/pipelineStore.ts  # Zustand: persists projectId only; rehydrateFromServer on load
└── hooks/
    ├── useSSEStream.ts   # fetch + ReadableStream; skips heartbeat events
    └── useFileUpload.ts
```

### PRD generation: section-by-section pattern
7 sequential `instructor` calls on `AsyncAnthropic()`. Each section is fully Pydantic-validated server-side before streaming. Retries (max 3) are invisible to the client — no race condition.

**Critical: heartbeat before every `instructor` call** prevents Railway proxy idle timeout:
```python
yield 'data: {"heartbeat": true}\n\n'   # before each blocking instructor call
result = await asyncio.to_thread(client.messages.create, ...)
yield f'data: {{"section": "{key}", "markdown": "...", "done": false}}\n\n'
```

Frontend `useSSEStream.ts` ignores events with `heartbeat: true`.

Each section call receives all previously generated sections as context for coherence. Sections carry `source_doc_ids: list[str]` populated from `NodeWithScore.node.metadata["doc_id"]` (not by LLM).

### User story generation: 3-step internal pipeline
All within `POST /generate/stories`. SSE step events drive frontend progress display.
```
Step 1 (non-streamed, ~4s): CapabilityList → SSE: {"step": "extracting_capabilities"}
Step 2 (non-streamed, ~5s): SlicePlan (max 7 slices) → SSE: {"step": "slices_planned", "count": 6}
Step 3 (per-story, streamed): → SSE: {"step": "story_done", "story": {...}}
Final: SSE: {"done": true}
```

### Workflow state machine
`WorkflowStatus` enum + `VALID_TRANSITIONS` dict in `workflow.py`. States:
`TRANSCRIPT_UPLOADED → PRD_GENERATED → PRD_APPROVED → STORIES_GENERATED → JIRA_PUSH_PENDING → JIRA_PUSH_SUCCESS → COMPLETED`

`advance_status(project_id, expected_current, db)` — idempotent, no `await` between read and write (SQLite serializes writes in single-worker; no optimistic locking needed for hackathon). Jira route checks status before push, advances to `JIRA_PUSH_PENDING` before `asyncio.gather` — prevents double-push on double-click.

### RAG knowledge base
- **ChromaDB** `PersistentClient` at `./chroma_db` — the committed vector store for this project
- SINGLE WORKER ONLY — ChromaDB's HNSW binary is process-exclusive (never `uvicorn --workers N`)
- Chunked with `HierarchicalNodeParser(chunk_sizes=[2048, 512])`
- Chunk metadata: `doc_id`, `section`, `date`, `product_area`, `status`, `embedding_model`
- Always filter `MetadataFilter(key="status", value="active")` — prevents retrieving superseded PRDs
- When saving new PRD: supersede old docs of same `product_area` before indexing new one
- `HybridRetriever`: ChromaDB semantic + in-memory `rank-bm25` + RRF (k=60)

### RAG conflict detection (prompt framing, not retrieval change)
Retrieved chunks passed with explicit conflict-check framing, not as generic context. `ConflictEntry` model: `source_prd_id`, `conflicting_statement`, `proposed_change`, `severity` (blocking/needs_discussion/minor). `instructor` enforces structured output.

### Observability (structlog JSON)
`structlog` configured in `main.py` writing JSON lines to `dochub.log`.

Token usage via `client.messages.create_with_completion()` — returns `(pydantic_model, completion)` tuple; `completion.usage.input_tokens/output_tokens` logged per section call.

Three log event types:

**`llm_call`** — emitted for every `instructor` call:
```json
{
  "event": "llm_call",
  "project_id": "...",
  "section": "description",
  "generator_model": "claude-sonnet-4-6",
  "prompt_version": "prd-v1.2",
  "input_tokens": 3412,
  "output_tokens": 487,
  "latency_ms": 2341,
  "retry_count": 0
}
```

**`rag_retrieval`** — emitted once per PRD generation, after `HybridRetriever.retrieve()`:
```json
{
  "event": "rag_retrieval",
  "project_id": "...",
  "embedding_model": "text-embedding-3-small",
  "top_k_results": [
    {
      "doc_id": "payments-v2",
      "section": "functional_requirements",
      "semantic_score": 0.87,
      "bm25_score": 14.3,
      "rrf_rank": 1
    }
  ],
  "latency_ms": 214
}
```
`HybridRetriever` must preserve individual `semantic_score` and `bm25_score` per candidate before RRF fusion, not only the fused score. `rrf_rank` is the 1-based position in the final merged list.

**`pipeline_complete`** — emitted at SSE `done: true`:
```json
{
  "event": "pipeline_complete",
  "project_id": "...",
  "generator_model": "claude-sonnet-4-6",
  "prompt_version": "prd-v1.2",
  "embedding_model": "text-embedding-3-small",
  "total_input_tokens": 24180,
  "total_output_tokens": 3412,
  "total_latency_ms": 18450,
  "sections_with_retries": ["open_questions"],
  "hallucination_warnings": 2
}
```

### Prompt & model version tagging
Every PRD generation is bound to a specific set of version constants defined in `services/versions.py`:

```python
# backend/app/services/versions.py
GENERATOR_MODEL    = "claude-sonnet-4-6"
EMBEDDING_MODEL    = "text-embedding-3-small"
EXTRACTOR_MODEL    = "claude-sonnet-4-6"   # PRDMetadata extraction call
PRD_PROMPT_VERSION = "prd-v1.2"            # increment when any PRD prompt changes
```

These constants are imported wherever prompts or models are called — never hardcode model strings elsewhere. `PRD_PROMPT_VERSION` follows `<scope>-v<major>.<minor>`: increment minor for wording tweaks, major for structural schema changes.

Stored on the `Project` SQLModel row:
```python
class Project(SQLModel, table=True):
    # ... existing fields ...
    prompt_version:     str = Field(default=PRD_PROMPT_VERSION)
    generator_model:    str = Field(default=GENERATOR_MODEL)
    embedding_model:    str = Field(default=EMBEDDING_MODEL)
    extractor_model:    str = Field(default=EXTRACTOR_MODEL)
```

This enables post-hoc filtering: "show all PRDs generated with prompt version prd-v1.1 that we need to regenerate after the prompt fix."

### Jira integration
- REST API v3, basic auth (email + API token)
- `description` must be **ADF (Atlassian Document Format)** JSON — never plain text or Markdown
- **Pre-validation pass before any create**: project exists, CREATE_ISSUES permission, valid issue type, well-formed ADF, summary ≤ 255 chars
- Store `issue_key` in `JiraTicket` table as EACH ticket is created (not batch-end)
- Rollback on `asyncio.gather` failure: delete by stored `issue_key` directly — no JQL, no indexing lag
- Requires `DELETE_ISSUES` permission — configure before demo day

### DOCX export + re-upload round-trip
**Export (`docx_builder.py`):**
- Open branded `assets/template.docx` (not `Document()`)
- Use `Heading 1` style for all 7 section titles (consistent, machine-parseable)
- Append one `w:vanish` hidden paragraph at document end: `[DOCHUB_METADATA]{"project_id": "...", "version": 1, "generated_at": "..."}`

**Re-upload parse (`docx_parser.py`):**
- Primary: `parse_docx_by_headings()` — finds `Heading 1` paragraphs, maps to `KNOWN_SECTIONS` dict
- Metadata: `extract_embedded_metadata()` — reads `[DOCHUB_METADATA]` prefix → auto-links re-upload to originating project in SQLite (no manual step)
- Hidden paragraph preserved by Word/LibreOffice/Google Docs on DOCX round-trip

### Hallucination detection
Regex-based numeric claim extractor: find numbers/percentages in PRD, check which appear in transcript. Surface as yellow warning badge in UI. Never auto-reject. LLM-as-judge deferred (15-30% false positive rate; doubles latency; fatal demo risk).

### Testing
`tests/test_schemas.py` — all Pydantic models (7 PRD sections, UserStory, ConflictEntry, ADF builder output). Zero deps, runs in ms.
One real Jira round-trip test against `DOCHUB-TEST` project (validates ADF format).
SSE termination test: `POST /generate/prd` stream contains 7 section events + `done: true` within 30s (use `respx` to mock Anthropic).
`startup_check()` in `main.py` lifespan: validate all env vars on boot.

## Key constraints from spec
- PRD: exactly 7 sections (Title, Description, Problem, Why, Success, Audience, Open Questions/Risks)
- Open Questions: Type 1 (conflicts with KB) + Type 2 (transcript gaps) — never omit
- User stories: vertically sliced, max 7, with AC (happy + alt + error paths) + Validations table
- Jira push: all-or-nothing — partial creation not acceptable
- Demo flow: under 10 minutes end-to-end
- KB: pre-seeded with 2-3 realistic PRDs before demo (`backend/data/seed_prds/`)

## Environment variables
```
# backend/.env
ANTHROPIC_API_KEY=
OPENAI_API_KEY=           # text-embedding-3-small
JIRA_BASE_URL=            # https://your-domain.atlassian.net
JIRA_EMAIL=
JIRA_API_TOKEN=
JIRA_PROJECT_KEY=

# backend/.env.test
JIRA_PROJECT_KEY=DOCHUB-TEST  # dedicated test project for integration tests

# frontend/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Pre-demo checklist
- [ ] `python scripts/seed_kb.py` — index pre-seeded PRDs
- [ ] `cp -r chroma_db chroma_db.demo_backup` — backup KB
- [ ] `pytest tests/test_jira_integration.py` — validate ADF against real Jira
- [ ] Check `dochub.log` for any startup errors
- [ ] Confirm `uvicorn app.main:app --reload` (NOT `--workers N`)
- [ ] Confirm `NEXT_PUBLIC_API_URL` points to deployed Railway URL (not localhost)
- [ ] Verify Jira `DELETE_ISSUES` permission is configured on demo project

## Post-hackathon migration path
1. Replace ChromaDB with pgvector (Supabase) — update `vector_store.py` client only; `HybridRetriever` interface unchanged
2. Migrate to Celery + Redis for background tasks and SSE reconnectability
3. Add OpenTelemetry spans once an observability backend (Grafana/Tempo) is provisioned
4. Replace `rank-bm25` with Postgres FTS for BM25 at scale

## Key dependencies
```
# Backend
fastapi>=0.115
anthropic           # use AsyncAnthropic() — never sync Anthropic() in async routes
instructor
pydantic>=2.0
llama-index-core
llama-index-vector-stores-chroma
chromadb
rank-bm25
python-docx==1.1.2
mammoth
httpx
sqlmodel
python-multipart
structlog

# Frontend
next@14
zustand
tailwindcss
react-markdown
```
