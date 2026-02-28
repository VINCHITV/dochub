# DocHub

> Turn meeting transcripts into context-aware PRDs, then push them as Jira tickets — in under 10 minutes.

**Pipeline:** `Upload Transcript → Generate PRD (RAG-informed) → Review & Approve → Generate User Stories → Push to Jira`

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Getting API Keys](#getting-api-keys)
- [Option A: Docker Compose (recommended)](#option-a-docker-compose-recommended)
- [Option B: Local Development](#option-b-local-development)
- [Seeding the Knowledge Base](#seeding-the-knowledge-base)
- [Running Tests](#running-tests)
- [Project Structure](#project-structure)
- [API Reference](#api-reference)
- [How the Pipeline Works](#how-the-pipeline-works)
- [Troubleshooting](#troubleshooting)
- [Pre-Demo Checklist](#pre-demo-checklist)

---

## Prerequisites

| Tool | Minimum version | Check |
|---|---|---|
| Python | 3.12 | `python3 --version` |
| Node.js | 18 | `node --version` |
| npm | 9 | `npm --version` |
| Docker + Docker Compose | 24 | `docker --version` |
| Git | any | `git --version` |

You also need accounts for:
- [Anthropic](https://console.anthropic.com) — for `claude-sonnet-4-6` (PRD + story generation)
- [OpenAI](https://platform.openai.com) — for `text-embedding-3-small` (RAG embeddings)
- [Jira Cloud](https://www.atlassian.com/software/jira) — for ticket creation (optional for local testing)

---

## Getting API Keys

### Anthropic API Key
1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Navigate to **API Keys** → **Create Key**
3. Copy the key starting with `sk-ant-`

### OpenAI API Key
1. Go to [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
2. Click **Create new secret key**
3. Copy the key starting with `sk-`

### Jira API Token
1. Go to [id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens)
2. Click **Create API token** → give it a label (e.g. `dochub`)
3. Copy the token
4. Your `JIRA_BASE_URL` is `https://your-domain.atlassian.net`
5. Your `JIRA_EMAIL` is the email you log into Jira with
6. Your `JIRA_PROJECT_KEY` is the short prefix of your project (e.g. `PROJ` in `PROJ-123`)

> **Jira permissions required:** The API token user must have `CREATE_ISSUES` and `DELETE_ISSUES` permissions on the target project. `DELETE_ISSUES` is needed for the rollback-on-failure mechanism.

---

## Option A: Docker Compose (recommended)

The fastest way to get everything running.

### 1. Clone the repository

```bash
git clone <repo-url>
cd dochub
```

### 2. Configure environment variables

```bash
# Copy the example files
cp backend/.env.example backend/.env
cp frontend/.env.local.example frontend/.env.local
```

Edit `backend/.env` and fill in your keys:

```bash
# backend/.env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
JIRA_BASE_URL=https://your-domain.atlassian.net
JIRA_EMAIL=you@company.com
JIRA_API_TOKEN=your-jira-token
JIRA_PROJECT_KEY=PROJ
```

The `frontend/.env.local` is pre-configured for Docker — no changes needed:

```bash
# frontend/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 3. Build and start

```bash
docker compose up --build
```

First build takes ~3 minutes (downloading Python + Node dependencies). Subsequent starts take ~15 seconds.

### 4. Seed the knowledge base (first run only)

In a separate terminal, run the seeder inside the backend container:

```bash
docker compose exec backend python scripts/seed_kb.py
```

This indexes 3 pre-built PRD transcripts into ChromaDB so the RAG pipeline has prior context to retrieve from.

### 5. Open the app

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| API docs (Swagger) | http://localhost:8000/docs |

### 6. Stop

```bash
docker compose down
```

To also delete the ChromaDB vector store and SQLite database (full reset):

```bash
docker compose down -v
rm -rf backend/chroma_db backend/dochub.db
```

---

## Option B: Local Development

Run the backend and frontend in separate terminals without Docker. Recommended when actively developing.

### Backend

#### 1. Create a virtual environment

```bash
cd backend
python3 -m venv .venv
```

Activate it:

```bash
# macOS / Linux
source .venv/bin/activate

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Windows (CMD)
.venv\Scripts\activate.bat
```

You should see `(.venv)` in your prompt.

#### 2. Install dependencies

```bash
pip install -r requirements.txt
```

> This installs FastAPI, Anthropic SDK, LlamaIndex, ChromaDB, instructor, and all other backend dependencies. Takes ~2 minutes on first run.

#### 3. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` — same values as described in Option A above.

#### 4. Create the DOCX template (first run only)

```bash
python scripts/create_template.py
```

This creates `assets/template.docx` — the branded template used when exporting PRDs as DOCX files.

#### 5. Seed the knowledge base (first run only)

```bash
python scripts/seed_kb.py
```

You should see output like:

```
Seeding payments_v2.txt → product_area: payments ... done (42 chunks)
Seeding auth_sso.txt → product_area: auth ... done (38 chunks)
Seeding notifications_v3.txt → product_area: notifications ... done (35 chunks)
Knowledge base seeded successfully.
```

#### 6. Start the backend

```bash
# From the backend/ directory, with .venv activated
uvicorn app.main:app --reload
```

> **Important:** Always use a single worker. Never run `uvicorn app.main:app --workers 4` — ChromaDB's HNSW index is process-exclusive and will corrupt on multiple workers.

The backend starts at **http://localhost:8000**.

Verify it's running:

```bash
curl http://localhost:8000/health
# → {"status":"ok","version":"prd-v1.2"}
```

Logs are written to both stdout and `dochub.log` in JSON format.

---

### Frontend

Open a **new terminal** (keep the backend running in the first one).

#### 1. Install dependencies

```bash
cd frontend
npm install
```

#### 2. Configure environment variables

```bash
cp .env.local.example .env.local
```

The default value points to the local backend — no changes needed for local dev:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

#### 3. Start the frontend

```bash
npm run dev
```

The frontend starts at **http://localhost:3000**.

---

## Seeding the Knowledge Base

The RAG pipeline retrieves context from previously processed PRDs to inform new ones. Before the first run, seed it with sample PRDs:

```bash
cd backend
# with .venv activated (or inside Docker container)
python scripts/seed_kb.py
```

### What gets seeded

Three realistic transcripts in `backend/data/seed_prds/`:

| File | Product Area | Focus |
|---|---|---|
| `payments_v2.txt` | payments | 1-click checkout, saved cards, Apple Pay |
| `auth_sso.txt` | auth | Enterprise SAML 2.0 + OIDC SSO |
| `notifications_v3.txt` | notifications | Smart scheduling, preference center |

### Dry run (validate without API calls)

```bash
python scripts/seed_kb.py --dry-run
```

### Backup before a demo

```bash
cp -r backend/chroma_db backend/chroma_db.demo_backup
```

Restore if the vector store gets corrupted:

```bash
rm -rf backend/chroma_db
cp -r backend/chroma_db.demo_backup backend/chroma_db
```

---

## Running Tests

### Unit and schema tests (no network required)

```bash
cd backend
# with .venv activated
pytest tests/test_schemas.py tests/test_workflow.py -v
```

Expected output: **66 tests passed** in under 2 seconds.

### All tests except Jira integration

```bash
pytest tests/ --ignore=tests/test_jira_integration.py -v
```

### Jira integration test (requires real credentials)

This test creates and deletes a real ticket against a `DOCHUB-TEST` Jira project.

```bash
# Create backend/.env.test with test project key
cp .env.example .env.test
# Edit .env.test: set JIRA_PROJECT_KEY=DOCHUB-TEST

pytest tests/test_jira_integration.py -v
```

What it validates:
- Jira project is accessible with your credentials
- ADF-formatted ticket body is accepted by the API
- Ticket can be deleted (needed for rollback on push failures)

---

## Project Structure

```
dochub/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, CORS, structlog setup, lifespan startup check
│   │   ├── models.py            # SQLModel: Project, UserStory, JiraTicket, PRDMetadata
│   │   ├── database.py          # SQLite engine (WAL mode), get_session() dependency
│   │   ├── routes/
│   │   │   ├── upload.py        # POST /upload — parse transcript, create Project
│   │   │   ├── projects.py      # GET /projects/{id}, POST /projects/{id}/approve
│   │   │   ├── generate.py      # POST /generate/prd + /generate/stories (SSE)
│   │   │   ├── export.py        # GET /export/{id}/docx
│   │   │   └── jira.py          # POST /jira/tickets — atomic push with rollback
│   │   └── services/
│   │       ├── versions.py      # GENERATOR_MODEL, EMBEDDING_MODEL, PRD_PROMPT_VERSION
│   │       ├── workflow.py      # WorkflowStatus enum, VALID_TRANSITIONS, advance_status()
│   │       ├── ai.py            # Pydantic section/story models + instructor wrapper
│   │       ├── rag.py           # HybridRetriever (semantic + BM25 + RRF), save_prd_to_kb()
│   │       ├── vector_store.py  # ChromaDB PersistentClient singleton
│   │       ├── file_parser.py   # .txt/.docx → plain text (mammoth)
│   │       ├── docx_builder.py  # PRD → branded .docx with hidden metadata paragraph
│   │       └── docx_parser.py   # .docx → section dict, extract embedded metadata
│   ├── data/
│   │   └── seed_prds/           # 3 sample transcripts for KB seeding
│   ├── scripts/
│   │   ├── seed_kb.py           # Index seed PRDs into ChromaDB
│   │   └── create_template.py   # Generate assets/template.docx
│   ├── tests/
│   │   ├── conftest.py          # In-memory SQLite fixtures, TestClient
│   │   ├── test_schemas.py      # 53 Pydantic model tests (no network)
│   │   ├── test_workflow.py     # State machine transition tests
│   │   └── test_jira_integration.py  # Real Jira round-trip (skipped without credentials)
│   ├── assets/                  # template.docx (created by create_template.py)
│   ├── requirements.txt
│   ├── Dockerfile
│   └── pytest.ini
│
├── frontend/
│   ├── app/
│   │   ├── layout.tsx           # Root layout, global CSS
│   │   ├── page.tsx             # Home: project name entry
│   │   └── workflow/
│   │       └── page.tsx         # Wizard: upload → PRD → stories → Jira
│   ├── components/
│   │   ├── WizardStepper.tsx    # 5-step progress indicator
│   │   ├── PRDViewer.tsx        # Section-by-section PRD renderer with skeletons
│   │   └── StoriesViewer.tsx    # User story cards with AC table
│   ├── hooks/
│   │   ├── useSSEStream.ts      # fetch + ReadableStream SSE (skips heartbeats)
│   │   └── useFileUpload.ts     # Multipart upload hook
│   ├── store/
│   │   └── pipelineStore.ts     # Zustand store (persists only projectId)
│   ├── lib/
│   │   └── utils.ts             # cn() Tailwind utility
│   ├── package.json
│   ├── Dockerfile
│   └── next.config.js
│
├── docker-compose.yml
└── README.md
```

---

## API Reference

All endpoints return JSON. SSE endpoints return `text/event-stream`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check — returns `{"status":"ok","version":"prd-v1.2"}` |
| `POST` | `/upload` | Upload transcript (.txt or .docx), create Project |
| `GET` | `/projects/{id}` | Get project state for frontend rehydration |
| `POST` | `/projects/{id}/approve` | Approve PRD, advance to stories step |
| `POST` | `/generate/prd` | **SSE** — stream 7 PRD sections |
| `POST` | `/generate/stories` | **SSE** — stream user stories (3-step pipeline) |
| `GET` | `/export/{id}/docx` | Download PRD as branded .docx |
| `POST` | `/jira/tickets` | Push all stories to Jira (atomic) |

Interactive docs: **http://localhost:8000/docs**

### SSE event shapes

**PRD generation** (`POST /generate/prd`):
```jsonc
{"heartbeat": true}                          // keep-alive, ignore in client
{"section": "title",   "data": {...}, "done": false}
{"section": "description", "data": {...}, "done": false}
// ... 5 more sections ...
{"done": true, "hallucination_count": 2}     // stream complete
```

**Story generation** (`POST /generate/stories`):
```jsonc
{"step": "extracting_capabilities"}
{"step": "slices_planned", "count": 6}
{"step": "story_done", "story": {"id":"...","title":"...","acceptance_criteria":[...],"validations":[...]}}
// ... more story_done events ...
{"done": true}
```

---

## How the Pipeline Works

### 1. Upload
- Accepts `.txt` (plain text) or `.docx` (Word document)
- If uploading a DocHub-exported `.docx`, the hidden metadata paragraph auto-links it to the original project — no duplicate created
- Text is stored in SQLite; project status set to `TRANSCRIPT_UPLOADED`

### 2. PRD Generation (RAG-informed)
- Retrieves 5 relevant chunks from the knowledge base using **HybridRetriever**:
  - Semantic search via ChromaDB + OpenAI embeddings
  - BM25 lexical ranking over the candidate set
  - Reciprocal Rank Fusion (k=60) to merge scores
  - Always filters `status=active` — superseded PRDs never returned
- Generates 7 sections sequentially with `claude-sonnet-4-6` via `instructor`:
  `Title → Description → Problem → Why → Success → Audience → Open Questions/Risks`
- Each section receives all previously generated sections as context for coherence
- Open Questions has two required subsections:
  - **Type 1** — conflicts with existing KB PRDs (structured `ConflictEntry`)
  - **Type 2** — gaps/ambiguities in the transcript
- After generation: PRD indexed into ChromaDB; old PRDs for the same product area superseded
- Hallucination detection: numeric claims in PRD cross-checked against transcript

### 3. Review & Export
- PRD displayed section-by-section in the UI
- Yellow badge shows count of numeric claims not found in transcript
- Export to branded `.docx` at any time (includes hidden metadata for re-upload)
- Click **Approve** to advance to story generation

### 4. Story Generation (3-step pipeline)
- **Step 1** — Extract `CapabilityList` from PRD (~4s)
- **Step 2** — Plan up to 7 vertical `SlicePlan` entries (~5s)
- **Step 3** — Generate one `UserStoryModel` per slice, each with:
  - Title + "As a... I want... so that..." description
  - Acceptance criteria (happy path + alternate paths + error paths)
  - Field-level validations table

### 5. Jira Push (all-or-nothing)
- Pre-validation: project accessible, CREATE_ISSUES permission, summaries ≤ 255 chars
- Status advanced to `JIRA_PUSH_PENDING` before any creation — prevents double-push on double-click
- Tickets created concurrently (up to 5 at a time) using `asyncio.Semaphore`
- `issue_key` stored in SQLite immediately after each individual creation
- On any failure: rollback deletes all already-created tickets by stored key (no JQL, no indexing lag)
- On success: returns `{"issue_keys": ["PROJ-42", "PROJ-43", ...], "count": N}`

---

## Troubleshooting

### Backend won't start: "ANTHROPIC_API_KEY not set"
The startup check logs a warning but does not crash. Verify `backend/.env` exists and contains your key:
```bash
cat backend/.env | grep ANTHROPIC_API_KEY
```

### ChromaDB error on startup: "HNSW index is process-exclusive"
You're running more than one uvicorn worker. Always use:
```bash
uvicorn app.main:app --reload   # correct — single worker
# NOT: uvicorn app.main:app --workers 4
```

### "No module named 'app'" when running pytest
Run pytest from the `backend/` directory, not the repo root:
```bash
cd backend && pytest tests/
```

### PRD generation times out in the browser
The Railway/Nginx proxy has a 30-second idle timeout. The backend sends a heartbeat event before every LLM call to keep the connection alive. If you see timeouts locally, check that the backend is running and `ANTHROPIC_API_KEY` is valid.

### Jira push fails: "issuetype Story not found"
Your Jira project may use a different issue type name (e.g. "User Story" instead of "Story"). Check your project's issue type scheme:
1. Go to your Jira project → **Project settings** → **Issue types**
2. Confirm "Story" exists. If not, add it or update `JIRA_ISSUE_TYPE` in `.env` once that env var is wired in.

### Jira push fails: "403 Forbidden"
The API token user lacks `CREATE_ISSUES` permission on the target project. Ask your Jira admin to grant it.

### Jira push fails: "401 Unauthorized"
Check `JIRA_EMAIL` and `JIRA_API_TOKEN`. The token must be an **API token** (from id.atlassian.com), not your Jira password.

### "prd_json is null" on story generation
The PRD was not generated or was not saved. Check `dochub.log` for errors during PRD generation:
```bash
tail -f backend/dochub.log | python3 -m json.tool
```

### Frontend shows blank page after reload
Zustand only persists `projectId` in localStorage. On reload it calls `GET /projects/{id}` to rehydrate state. If the backend isn't running, this silently fails and redirects to the home page — expected behavior.

### npm install fails on Apple Silicon (M1/M2/M3)
Some packages need Rosetta. If you see architecture errors:
```bash
softwareupdate --install-rosetta
arch -x86_64 npm install
```

---

## Pre-Demo Checklist

Run these steps in order before a demo session:

```bash
cd backend
source .venv/bin/activate

# 1. Create DOCX template (first time only)
python scripts/create_template.py

# 2. Seed the knowledge base
python scripts/seed_kb.py

# 3. Backup the vector store
cp -r chroma_db chroma_db.demo_backup

# 4. Validate Jira integration
pytest tests/test_jira_integration.py -v

# 5. Check logs for startup errors
uvicorn app.main:app --reload &
sleep 3 && tail -20 dochub.log

# 6. Start frontend
cd ../frontend && npm run dev
```

Checklist:
- [ ] `backend/.env` has all 6 env vars set
- [ ] `assets/template.docx` exists (`python scripts/create_template.py`)
- [ ] `chroma_db/` exists and is non-empty (`python scripts/seed_kb.py`)
- [ ] `chroma_db.demo_backup/` exists
- [ ] `pytest tests/test_jira_integration.py` passes
- [ ] `dochub.log` has no ERROR lines on startup
- [ ] Backend health check returns 200: `curl http://localhost:8000/health`
- [ ] Frontend loads at http://localhost:3000
- [ ] Jira project has `CREATE_ISSUES` + `DELETE_ISSUES` permissions configured
- [ ] `NEXT_PUBLIC_API_URL` points to the deployed backend URL (not localhost) if using Railway

---

## Environment Variables Reference

### `backend/.env`

| Variable | Required | Example | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | `sk-ant-...` | Claude API key for PRD + story generation |
| `OPENAI_API_KEY` | Yes | `sk-...` | OpenAI key for text-embedding-3-small |
| `JIRA_BASE_URL` | Yes* | `https://acme.atlassian.net` | Your Jira Cloud base URL |
| `JIRA_EMAIL` | Yes* | `you@company.com` | Email associated with the API token |
| `JIRA_API_TOKEN` | Yes* | `ATATT3x...` | Jira API token (not your password) |
| `JIRA_PROJECT_KEY` | Yes* | `PROJ` | Short project key (prefix before ticket numbers) |

*Required for Jira push. The app runs without Jira vars — PRD and story generation work fine.

### `frontend/.env.local`

| Variable | Required | Default | Description |
|---|---|---|---|
| `NEXT_PUBLIC_API_URL` | Yes | `http://localhost:8000` | Backend API base URL |

---

## Stack

| Layer | Technology | Version |
|---|---|---|
| Backend runtime | Python | 3.12 |
| Web framework | FastAPI | ≥ 0.115 |
| Database | SQLModel + SQLite | ≥ 0.0.21 |
| AI models | claude-sonnet-4-6 | latest |
| Structured output | instructor + Pydantic | ≥ 1.6 / ≥ 2.0 |
| RAG indexing | LlamaIndex | ≥ 0.11 |
| Vector store | ChromaDB | ≥ 0.5 |
| Hybrid ranking | rank-bm25 + RRF | ≥ 0.2 |
| Embeddings | OpenAI text-embedding-3-small | via openai ≥ 1.40 |
| Jira client | httpx (async) | ≥ 0.27 |
| DOCX | python-docx | 1.1.2 |
| Logging | structlog | ≥ 24.0 |
| Frontend framework | Next.js | 14.2.29 |
| Styling | Tailwind CSS | 3.x |
| State management | Zustand | 5.x |
| Markdown rendering | react-markdown | 9.x |
