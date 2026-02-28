# RAG Retrieval & Conflict Detection Skill

## Purpose
Perform hybrid retrieval and detect conflicts with past PRDs.

## Retrieval Rules
- Always filter status="active".
- Run vector search and BM25.
- Merge using Reciprocal Rank Fusion (RRF).

## Output
1. Retrieval snapshot:
    - doc_id
    - section
    - snippet
    - semantic_score
    - bm25_score
    - merged_rank
2. ConflictEntry list:
    - source_prd_id
    - conflicting_statement
    - proposed_change
    - severity (blocking / needs_discussion / minor)

## Guardrails
- Always return retrieval snapshot for logging.
- Do not fabricate conflicts.
- Only flag conflict if explicit contradiction exists.