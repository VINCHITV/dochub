# Creating Agent “Skills” for DocHub (Iteration 1)

For DocHub’s Iteration 1, we should implement dedicated Claude subagents (skills) for each major task (PRD generation, story planning, RAG retrieval, Jira integration, etc.). Each subagent has a narrow role, specific inputs/outputs, and limited “tools,” following best practices for focused AI assistants【1†L92-L100】【13†L1-L4】. In particular, Claude Code docs emphasize writing clear role descriptions so Claude delegates appropriately【1†L92-L100】【13†L1-L4】. Below is a plan to define those agents, set up a local dev environment, and ensure robust testing and documentation.

## 1. Inventory Iteration 1 Components & Agent Mapping 

First, list the key components and map them to agent responsibilities:

- **PRD Generation**: Uses RAG + LLM to produce 7 PRD sections from the transcript and context.  
- **User-Story Pipeline**: Includes (a) capability extraction, (b) slice planning, (c) story expansion.  
- **RAG Engine**: Semantic retrieval (Chroma) + lexical ranking (BM25/RRF) + metadata. Also conflict detection.  
- **Jira Integration**: Formats stories into ADF and creates tickets via REST API. Ensures atomic batch.  
- **Backend Core**: Handles file parsing, state machine, DB models (Project, PRDMetadata, UserStory, JiraTicket).  
- **Export Service**: Generates branded DOCX (python-docx).  

Map to subagents (skills):

- **`prd-agent`** – Handles PRD sections. Input: transcript text + retrieved context. Output: validated JSON of 7 PRD sections (with labeled inferences).  
- **`story-agent`** – Handles stories in 3 steps. Input: PRD content (and transcript). Output: list of user-story JSONs (Title, Description, Acceptance criteria, Validations).  
- **`rag-engineer-agent`** – Handles retrieval and metadata. Input: transcript or PRD context + product area. Output: retrieved chunks and ConflictEntries.  
- **`jira-agent`** – Handles Jira ADF formatting and API calls. Input: story objects. Output: ticket creation (ID responses) or cleanup.  
- **`backend-architect-agent`** – Oversees core services (routes, models, workflow logic). Not an LLM agent but an internal “agent” for design.  
- **`test-automation-agent`** – Defines tests and mocks for all the above.  

Each agent’s scope is tight. For example, the RAG agent does **only** retrieval and conflict analysis, not PRD phrasing; the PRD agent focuses on content generation, not on pushing tickets. This aligns with Claude’s recommendation: use subagents to “preserve context by keeping exploration and implementation out of your main conversation” and to specialize behavior with focused prompts【1†L92-L100】. In practice, we’ll put each subagent’s prompt in its own file (Claude YAML or `.claude/agents/`), ensuring clear separation【13†L1-L4】【1†L92-L100】.

## 2. Defining Agent Skills & I/O Contracts 

For each subagent, specify its *role*, *inputs*, *outputs*, and *behavior*. We should document these precisely (like a mini-API) and encode them in Pydantic models or similar.

- **`rag-engineer-agent`**  
  - **Role:** “A search engineer retrieving relevant past PRDs and identifying conflicts.”  
  - **Input:** Transcript text or PRD draft; `product_area` metadata.  
  - **Output:** A list of retrieved document chunks (with ids, scores) *and* a list of `ConflictEntry` objects (source_prd_id, conflicting_statement, proposed_change, severity) for Type 1 open questions.  
  - **Behavior:** Run vector+BM25 retrieval (filter `status="active"`), format results with source metadata, and use the LLM to label conflicts【10†L221-L230】.  
  - **Schema:** E.g. `ConflictEntry(BaseModel)`; retrieval output is for logging.

- **`prd-agent`**  
  - **Role:** “A product architect writing each PRD section clearly from transcript and context.”  
  - **Input:** Transcript text and prioritized context bullet points (from RAG).  
  - **Output:** JSON with keys `title, description, problem, why, success, audience, open_questions` (each string or list).  
  - **Behavior:** Generate sections **sequentially**, each validated by Pydantic. If transcript lacks detail, mark “(inferred)” in the text.  
  - **Schema:** `PRDDocument` Pydantic model matching the 7-section format. No partial JSON is streamed to UI (only final markdown per section).

- **`story-agent`**  
  - **Role:** “An engineering lead splitting PRD into sprint-ready user stories.”  
  - **Step 1 (CapabilityExtraction):** Input PRD text; output list of “capabilities” or user actions (strings).  
  - **Step 2 (SlicePlanning):** Input capabilities; output 3–7 slice names (features or epics).  
  - **Step 3 (StoryExpansion):** For each slice name: generate Title, Description (As a... So that), Acceptance Criteria list, Validation table.  
  - **Schema:** `SlicePlan(BaseModel)` and `UserStory(BaseModel)`.  
  - **Behavior:** Ensure vertical slices (full-stack), full AC (happy + error paths), limited story count and criteria (guardrails).

- **`jira-agent`**  
  - **Role:** “A Jira API specialist creating tickets transactionally.”  
  - **Input:** List of stories (with fields Title, Description, AC, Validations).  
  - **Output:** IDs of created Jira issues or an error to trigger cleanup.  
  - **Behavior:** Pre-validate (project exists, permission, summary length, ADF JSON valid). Then create all issues with a unique batch tag (e.g. `dochub_batch:<uuid>`). If any fail, delete the already-created ones (rollback).  
  - **Schema:** Uses internal request/response structures (not an LLM output).

- **`backend-architect-agent`** and **`test-automation-agent`**  
  - These are more human-driven personas for design and testing tasks rather than runtime agents. They define how we structure code and tests (using Makefile, Docker, CI).

Each agent’s prompt is its “runbook” of instructions. According to best practices, the *agent’s role and runbook* should be static guidance, separate from the per-request prompt【10†L210-L219】【10†L221-L230】. For example, the PRD agent’s runbook says “Generate section with heading, avoid hallucinated facts,” etc. These instructions, along with example input/output, should be documented in the code repository (as Markdown or docs) to ensure repeatability. For complex flows, multiple agents reduce token overload and simplify debugging【10†L233-L242】.

## 3. Dockerized Local Environment & Claude CLI Integration 

To develop and test locally, containerize every component via Docker Compose:

```yaml
version: '3'
services:
  backend:
    build: ./backend
    ports: ['8000:8000']
    env_file: backend/.env
    depends_on: [db, vector]
  db:
    image: postgres:16
    environment: [POSTGRES_USER=dochub, POSTGRES_PASSWORD=..., POSTGRES_DB=dochub]
    ports: ['5432:5432']
  vector:
    image: chromadb/chroma
    ports: ['8001:8000']
  redis:
    image: redis:7
    ports: ['6379:6379']
```

Use **Postgres+pgvector** instead of SQLite for durability, but Iteration 1 can run on SQLite if easier. Likewise, run Chroma locally (as above) or any managed vector DB. This “container per service” approach aligns with treating agents as portable artifacts【3†L129-L137】. Each service is isolated (DB, Chroma, Redis, etc.), making CI and teamwork easier.

Since we’re using Claude CLI, configure it to use our local endpoints. For example, if we run a local Anthropic-compatible model (or just want to ensure requests route through our proxy), we can set:
```
export ANTHROPIC_BASE_URL=http://localhost:12434
```
pointing to a local **Docker Model Runner** or other mock LLM server【4†L131-L139】. Claude Code supports `ANTHROPIC_BASE_URL` for this exact purpose【4†L131-L139】. This lets all `claude run` commands talk to local containers or the real API as needed. 

In practice, we would use the Claude CLI (`claude run subagent-name`) as our “orchestration” in development. The Docker environment ensures each service is reachable. We can even run separate dev containers per subagent if desired (see Reddit workflows of running multiple containers for multi-agent dev), but a single Compose stack plus subagent CLI should suffice.

## 4. Mock Modes & Automated Testing 

For a reliable pipeline, implement *mock modes* for external calls so tests don’t hit real APIs:

- **LLM Mock Mode:** When `LLM_MODE=mock`, intercept calls to Anthropic/OpenAI. As shown in recent guides, you can use a library like **respx** or httpx’s built-in mocking with pytest to stub responses【6†L45-L53】. For example, in pytest mark a respx mock at `base_url` and return canned JSON for each LLM response. This lets us write deterministic tests for prompt logic (similar to Tony Aldon’s example of mocking OpenAI calls【6†L45-L53】). We can even simulate retries or errors by controlling the mock’s responses.

- **Jira Mock Mode:** When `JIRA_MODE=mock`, have a fake Jira server or stub HTTP client. For instance, monkey-patch or mock httpx’s AsyncClient so that calls to Jira endpoints return pre-defined success/failure payloads. The Atlasian developer forums suggest mocking the Jira library similarly. The key is to test the *rollback logic*: write a test where the second ticket creation returns 5xx, then assert that your code deletes the first (using the `dochub_batch` tag).

- **Database & Chroma:** Use test fixtures to seed an in-memory SQLite or a test Postgres instance. For Chroma, you can run it in Docker (as above) and clean state between tests.

- **Workflow Tests:** Write end-to-end integration tests. E.g., “given a sample transcript, run the full `/generate/prd` then `/generate/stories`, then attempt `/jira/tickets` (mocked) and verify all status transitions.” Use pytest and HTTPX’s AsyncClient to call your FastAPI routes. Ensure idempotency: double-calling an already-pushed project should not create duplicates.

Automated tests should cover each agent’s behavior. For LLM-based steps (PRD, stories), rather than checking exact text, verify schema conformity (Pydantic validation) and presence of required sections/bullets. For deterministic steps, assert exact outputs. By mocking external calls, tests remain fast and repeatable.

## 5. Documentation: Agent Usage, Prompts, and Runbook 

Finally, **document everything** so that the subagents and their workflows are clear:

- **Agent Descriptions:** Create a README or wiki section listing each agent, its **role statement**, and its I/O contract. For example, describe `prd-agent` as “Generates a PRD JSON given a transcript text. It outputs keys Title, Description, ...” including the Pydantic model fields.

- **Prompts:** Save each agent’s system prompt (runbook) in version control. For instance, in a “prompts/” directory, store the exact prompt template that the PRD agent uses (with placeholders). This ensures reproducibility. You can even include a brief example of input transcript and expected snippet of output to illustrate usage.

- **Runbooks:** As recommended for enterprise agents【10†L221-L230】【10†L233-L242】, write a runbook for each agent. This is a natural-language doc (or commented YAML) describing the agent’s steps. E.g. for `story-agent`: 
  1. “Extract unique user actions from PRD sections.”
  2. “Group related actions into ≤7 feature slices.”
  3. “For each slice, generate a user story with Title, Description (As a ...), 3–5 acceptance criteria, and a validation table.”
  Listing these ensures the LLM’s temperature can stay low and still know exactly what to do【10†L221-L230】. Include tips like “Always produce error-case acceptance criteria,” “Use bullet lists,” etc.

- **Runbook Versioning:** Treat prompts and runbooks like code. Include version annotations (prompt_v1, model=Claude-sonnet-4.6, etc.) to track changes. This aligns with best practices of role/runbook stability【10†L210-L219】.

- **Example Workflows:** Document a “runbook” for the overall flow. For instance, a section in docs: “To generate a PRD and push to Jira: 1) Upload transcript via CLI, 2) Run `/generate/prd`, 3) Review output, 4) Run `/generate/stories`, 5) Review output, 6) Run `/jira/tickets`.” Outline expected screen output and where to look if something goes wrong.

By organizing documentation this way, any new developer (or auditor) can see exactly what each agent does and how to invoke it. This mirrors the advice that “including examples of the expected input and output formats is highly effective” for consistent agent performance【10†L221-L230】.

---

**In summary:** Yes, creating specialized “skills” (subagents) for each Iteration 1 task is the right approach. We should define clear agent roles, design precise I/O contracts, containerize the setup, mock external systems in tests, and document every prompt and step. This aligns with Claude’s guidance on subagents【1†L92-L100】 and with modern multi-agent practices【3†L129-L137】【10†L221-L230】, and will yield a clean, maintainable, production-ready codebase.

