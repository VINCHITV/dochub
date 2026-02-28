# Full Review Findings -- 2026-02-28

## Files Reviewed
All files in:
- backend/app/main.py, models.py, database.py
- backend/app/routes/upload.py, generate.py, jira.py, export.py, projects.py
- backend/app/services/ai.py, rag.py, vector_store.py, workflow.py, docx_builder.py, docx_parser.py, file_parser.py, versions.py
- backend/tests/conftest.py, test_schemas.py, test_workflow.py, test_jira_integration.py
- backend/scripts/seed_kb.py, create_template.py
- backend/Dockerfile, requirements.txt, pytest.ini

## Issue #1: Sync OpenAI in rag.py
- File: backend/app/services/rag.py lines 65-75, 525-536
- _embed_query() uses sync openai.OpenAI() inside async call chain
- save_prd_to_kb() also uses sync openai.OpenAI()
- Fix: asyncio.to_thread() at call sites, or convert to async openai.AsyncOpenAI()

## Issue #2: Session lifetime in SSE generators
- File: backend/app/routes/generate.py lines 128-172, 611-664
- db: Session from Depends(get_session) passed to async generator
- get_session() context manager may close before generator completes
- Fix: Create new Session(engine) inside the generator body

## Issue #3: sections_with_retries always empty
- File: backend/app/routes/generate.py line 196
- Initialized as empty list, never appended to
- instructor max_retries=3 happens internally, not visible to caller
- Fix: Use instructor on_retry callback or manual retry loop in generate_section()

## Issue #4: Shared Session in asyncio.gather
- File: backend/app/routes/jira.py lines 588-602
- Multiple _create_and_store coroutines commit to same Session concurrently
- Fix: Collect gather results, persist all ticket keys sequentially after gather

## Issue #5: Missing issue type validation
- File: backend/app/routes/jira.py
- Hard-codes issuetype "Story" at line 293
- No pre-check that "Story" exists in target project's scheme
- Fix: Add _validate_issue_type() using createmeta endpoint

## Issue #6: PRDMetadata row never created
- File: backend/app/routes/generate.py lines 570-583
- save_prd_to_kb() called but no PRDMetadata row written to SQLite
- Model exists in models.py but is never instantiated
- Fix: Create PRDMetadata row after save_prd_to_kb() succeeds

## Issue #7: Missing template.docx
- File: backend/assets/ (empty directory)
- create_template.py exists but was never run
- docx_builder.py falls back gracefully to Document()
- Fix: Run create_template.py and commit the result

## Issue #8: JIRA_PUSH_SUCCESS -> COMPLETED never triggered
- File: backend/app/routes/jira.py line 636
- Only advances to JIRA_PUSH_SUCCESS, never to COMPLETED
- Projects stuck at JIRA_PUSH_SUCCESS
- Fix: Add second advance_status call or collapse states
