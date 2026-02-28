# backend/app/services/versions.py
"""
Version constants for all AI models and prompt schemas used in DocHub.

Rules:
- Import these constants wherever a model string or prompt version is needed.
- NEVER hardcode model strings elsewhere in the codebase.
- PRD_PROMPT_VERSION follows <scope>-v<major>.<minor>:
    - Increment minor for wording tweaks.
    - Increment major for structural schema changes (field additions/removals).
- Stored on the Project row at generation time to enable post-hoc filtering
  (e.g. "show all PRDs generated with prd-v1.1 that need regeneration").
"""

GENERATOR_MODEL: str = "gpt-4o"
"""Primary model for PRD section generation and user story generation."""

EMBEDDING_MODEL: str = "text-embedding-3-small"
"""OpenAI embedding model used for ChromaDB vector indexing and retrieval."""

EXTRACTOR_MODEL: str = "gpt-4o"
"""Model used for PRDMetadata extraction (product_area, date, doc_id) after PRD save."""

PRD_PROMPT_VERSION: str = "prd-v1.4"
"""
Prompt schema version tag. Increment when any PRD generation prompt changes.
All Projects store this at generation time so old PRDs can be identified
and optionally regenerated after a prompt version bump.

prd-v1.3: Added GapEntry model (question + transcript_excerpt) for type2_gaps,
           and kb_excerpt field on ConflictEntry for source evidence display.
prd-v1.4: Added source_doc_name, doc_date, and transcript_excerpt fields to
           ConflictEntry. RAG context format enriched with human-readable project
           name and explicit date tag for LLM extraction.
"""
