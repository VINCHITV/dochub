# RAG Engineer Agent Memory — DocHub

## Key File Paths
- `/Users/manishsingh/workspace/dochub/backend/app/services/rag.py` — HybridRetriever + save_prd_to_kb
- `/Users/manishsingh/workspace/dochub/backend/app/services/vector_store.py` — ChromaDB singleton factory
- `/Users/manishsingh/workspace/dochub/backend/app/services/versions.py` — all model/version constants (source of truth)
- `/Users/manishsingh/workspace/dochub/backend/app/services/ai.py` — instructor client + Pydantic section models
- `/Users/manishsingh/workspace/dochub/backend/app/services/docx_builder.py` — DOCX export
- `/Users/manishsingh/workspace/dochub/backend/app/services/docx_parser.py` — DOCX re-upload parse
- ChromaDB persistent path: `./chroma_db` (relative to `backend/`)
- ChromaDB collection name: `dochub_prd_kb`

## Tuning Decisions Made
- RRF k=60 (Cormack 2009 standard); suitable for top_k <= 20 in PRD-sized KBs
- Overfetch factor = 3 (fetch top_k*3 from each leg before fusion)
- BM25 runs over semantic leg candidates only (not full corpus) — avoids O(N) scan for KB < 10k chunks
- HierarchicalNodeParser chunk_sizes=[2048, 512]: 2048 for broad semantic, 512 for BM25+tight embedding
- cosine distance on ChromaDB collection (matches text-embedding-3-small convention)

## Invariants / Hard Rules
- EVERY ChromaDB query must include `{"status": {"$eq": "active"}}` in the where clause
- semantic_score and bm25_score MUST be preserved in node.metadata before RRF — never overwrite with fused score
- embedding_model metadata field always set from `versions.EMBEDDING_MODEL` (never hardcoded)
- ChromaDB exceptions must raise RuntimeError with context — never silently return empty
- Only `rag.py` and `ai.py` need to import from `versions.py` (other services don't use AI models)
- Single worker only — never uvicorn --workers N > 1 (ChromaDB HNSW is process-exclusive)

## Known Edge Cases
- Empty BM25 corpus on first PRD: handled — semantic_retrieve returns [] early, logs empty retrieval
- product_area collision: supersede step runs before indexing new doc (ordering matters)
- ChromaDB does not support in-place metadata updates via where-clause — must get+upsert pattern
- instructor mode for Anthropic: use `instructor.Mode.ANTHROPIC_TOOLS` (not JSON mode)
- AsyncAnthropic() required in async routes — sync Anthropic() will deadlock

## Collection Metadata Schema (every chunk)
doc_id, section, date (ISO), product_area, status ("active"|"superseded"), embedding_model, project_id

## Supersede Pattern (ordering critical)
1. _supersede_product_area() — get+upsert with status="superseded"
2. Then save_prd_to_kb() indexes new doc with status="active"
Never reverse this order.

## Reindex Pattern
Use reindex_product_area() when EMBEDDING_MODEL changes.
Logs warning on mismatch before superseding — detects mixed embedding spaces.

## See Also
- `patterns.md` — detailed implementation notes (if created)
