# Architecture Review Agent Memory

## Last Full Review: 2026-02-28
- Reviewed all files in backend/app/, backend/tests/, backend/scripts/
- Branch: develop (commit c040063)

## Confirmed Correct Patterns
- advance_status() idempotent, tested exhaustively in test_workflow.py
- Jira double-push guard: advance to JIRA_PUSH_PENDING before gather
- Jira rollback by stored issue_key (not JQL), keys cleared for retry
- RAG status=active filter always applied in _semantic_retrieve()
- RRF fusion preserves semantic_score + bm25_score per node, rrf_rank 1-based
- All model strings from services/versions.py, no hardcoded strings found
- ChromaDB single-worker enforced: Dockerfile CMD has no --workers flag
- SSE heartbeat before every instructor call, X-Accel-Buffering: no
- ADF builder produces valid type:doc version:1 format
- HierarchicalNodeParser chunk_sizes=[2048, 512] matches spec
- source_doc_ids from RAG node metadata, not LLM-generated
- create_with_completion() used for token extraction

## Known Issues (from 2026-02-28 review)
See [review-findings.md](review-findings.md) for full details.

### High Risk
1. Sync OpenAI calls in rag.py _embed_query() and save_prd_to_kb() block event loop
2. SSE generators receive db Session from Depends -- lifetime ambiguity with StreamingResponse
3. sections_with_retries always empty -- instructor retries are transparent to caller

### Medium Risk
4. Concurrent _create_and_store share single Session in asyncio.gather
5. Missing issue type validation before Jira ticket creation
6. PRDMetadata row never created in SQLite (model exists but is unused)
7. assets/template.docx missing -- create_template.py never run
8. JIRA_PUSH_SUCCESS -> COMPLETED transition never exercised, projects stuck at JIRA_PUSH_SUCCESS

### Low Risk
9. save_prd_to_kb() sync blocking at end of PRD stream
10. CORS allow_origins=* with credentials
11. Full transcript in all 7 section prompts (token cost)
12. Version tags set at Project creation, not PRD generation time

## Architecture Notes
- workflow.py has deferred import of Project to avoid circular import -- correct pattern
- jira.py _reset_status_to_stories_generated is the ONLY direct status write (documented exception)
- No SSE stream test exists despite CLAUDE.md mentioning one (respx in requirements but unused)
- PRDMetadata model fully defined but never instantiated anywhere in codebase
- get_session() is sync generator (not async) -- correct for SQLModel but needs care with SSE
