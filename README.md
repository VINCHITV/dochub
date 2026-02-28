# DocHub

Turn meeting transcripts into context-aware PRDs, then push them as Jira tickets.

**Pipeline:** `Transcript → PRD (RAG-informed) → User Stories → Jira tickets`

---

## Quick Start

### 1. Clone & configure

```bash
git clone <repo>
cd dochub

# Backend env
cp backend/.env.example backend/.env
# Fill in ANTHROPIC_API_KEY, OPENAI_API_KEY, JIRA_* values

# Frontend env
cp frontend/.env.local.example frontend/.env.local
```

### 2. Run with Docker Compose

```bash
docker compose up --build
```

- Backend: http://localhost:8000 (API docs: http://localhost:8000/docs)
- Frontend: http://localhost:3000

### 3. Run locally (dev)

**Backend:**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/seed_kb.py   # seed knowledge base
uvicorn app.main:app --reload   # single worker only
```

**Frontend:**
```bash
cd frontend
npm install
npm run dev
```

---

## Architecture

```
Transcript (.txt/.docx)
    │
    ▼
POST /upload  →  Project created (SQLite)
    │
    ▼
POST /generate/prd  →  SSE stream  →  7 PRD sections (RAG-informed)
    │
    ▼
GET /export/{id}/docx  →  Branded DOCX download
    │
    ▼
POST /projects/{id}/approve  →  PRD approved
    │
    ▼
POST /generate/stories  →  SSE stream  →  User stories (3-step pipeline)
    │
    ▼
POST /jira/tickets  →  Atomic Jira push (pre-validate + concurrent + rollback)
```

### Key design decisions

| Decision | Why |
|---|---|
| Single uvicorn worker | ChromaDB HNSW is process-exclusive — never `--workers N` |
| SSE via `fetch + ReadableStream` | Native EventSource can't POST |
| Heartbeat before every LLM call | Prevents Railway proxy idle timeout |
| Jira ADF format | REST API v3 requires it — plain text rejected |
| Store `issue_key` per ticket | Enables rollback without JQL indexing lag |
| Zustand persists only `projectId` | All other state derived from server on reload |

---

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + SQLModel + SQLite |
| AI | `claude-sonnet-4-6` via instructor + AsyncAnthropic |
| RAG | LlamaIndex + ChromaDB + rank-bm25 (HybridRetriever with RRF) |
| Embeddings | OpenAI `text-embedding-3-small` |
| Frontend | Next.js 14 App Router + shadcn/ui + Tailwind |
| State | Zustand (persists `projectId` only) |
| Jira | httpx async + REST API v3 + ADF |

---

## Environment Variables

```bash
# backend/.env
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
JIRA_BASE_URL=https://your-domain.atlassian.net
JIRA_EMAIL=
JIRA_API_TOKEN=
JIRA_PROJECT_KEY=

# frontend/.env.local
NEXT_PUBLIC_API_URL=http://localhost:8000
```

---

## Tests

```bash
cd backend
pytest tests/                      # unit + schema tests (no network)
pytest tests/test_jira_integration.py  # requires DOCHUB-TEST Jira project
```

---

## Pre-Demo Checklist

- [ ] `python scripts/seed_kb.py` — index seed PRDs
- [ ] `cp -r chroma_db chroma_db.demo_backup` — backup KB
- [ ] `pytest tests/test_jira_integration.py` — validate ADF
- [ ] Check `dochub.log` for startup errors
- [ ] Confirm single-worker uvicorn (NOT `--workers N`)
- [ ] Verify Jira `DELETE_ISSUES` permission configured
