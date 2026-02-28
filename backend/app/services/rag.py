"""
backend/app/services/rag.py

Hybrid retrieval layer for DocHub's RAG knowledge base.

Architecture:
  Semantic leg  — ChromaDB cosine-similarity query (OpenAI text-embedding-3-small)
  BM25 leg      — in-memory BM25Okapi over the same candidate set (rank-bm25)
  Fusion        — Reciprocal Rank Fusion (k=60) over both ranked lists

Invariants enforced here:
  - Every ChromaDB query includes MetadataFilter(key="status", value="active").
  - semantic_score and bm25_score are captured before fusion and logged separately.
  - RRF ordering is deterministic: ties broken by doc_id lexicographic order.
  - No model strings are hardcoded — all come from services.versions.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import date
from typing import Any, Optional

import openai
import structlog
from chromadb import Collection
from llama_index.core import Document
from llama_index.core.node_parser import HierarchicalNodeParser
from llama_index.core.schema import NodeWithScore, TextNode
from rank_bm25 import BM25Okapi

from app.services.versions import EMBEDDING_MODEL

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# RRF constant — k=60 is the well-established default from the original paper
# (Cormack et al. 2009). Increasing k dampens the influence of top-ranked
# candidates; decreasing k amplifies it. 60 works well for top_k <= 20.
# ---------------------------------------------------------------------------
_RRF_K: int = 60

# How many candidates to fetch from each leg before fusion.
# We over-fetch (top_k * 3) so that BM25 has enough material to rerank even
# when the semantic leg returns documents that BM25 would never surface alone.
_OVERFETCH_FACTOR: int = 3


# ---------------------------------------------------------------------------
# Embedding helper
# ---------------------------------------------------------------------------

def _embed_query(query: str) -> list[float]:
    """
    Embed a query string using OpenAI text-embedding-3-small.

    Uses the module-level EMBEDDING_MODEL constant — never hardcoded.

    Raises
    ------
    RuntimeError
        On any OpenAI API error, wrapping the original exception with context.
    """
    try:
        client = openai.OpenAI()  # reads OPENAI_API_KEY from env
        response = client.embeddings.create(
            input=query,
            model=EMBEDDING_MODEL,
        )
        return response.data[0].embedding
    except Exception as exc:
        raise RuntimeError(
            f"OpenAI embedding call failed (model={EMBEDDING_MODEL}): {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# HybridRetriever
# ---------------------------------------------------------------------------

class HybridRetriever:
    """
    Two-leg hybrid retriever: ChromaDB semantic + BM25, fused via RRF.

    Parameters
    ----------
    collection : chromadb.Collection
        The ChromaDB collection to query. Must already exist and be populated.
    top_k : int
        Number of results to return from retrieve(). Defaults to 5.
    """

    def __init__(self, collection: Collection, top_k: int = 5) -> None:
        self._collection = collection
        self._top_k = top_k

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        project_id: str = "",
        product_area: Optional[str] = None,
    ) -> list[NodeWithScore]:
        """
        Run hybrid retrieval and return top_k NodeWithScore objects.

        Each returned node's metadata includes:
          - doc_id, section: from the original chunk metadata
          - semantic_score, bm25_score: preserved from each leg before RRF
          - rrf_rank: 1-based position in the final fused list

        Parameters
        ----------
        query : str
            The user query or PRD section prompt.
        top_k : int
            Number of results to return. Overrides the instance default.
        project_id : str
            Project identifier — used only for structured logging.
        product_area : Optional[str]
            If provided, adds a product_area pre-filter to the ChromaDB query
            in addition to the mandatory status=active filter.

        Returns
        -------
        list[NodeWithScore]
            Fused results, length <= top_k.

        Raises
        ------
        RuntimeError
            On ChromaDB query failure (never silently returns empty on error).
        """
        start_ts = time.monotonic()

        fetch_k = top_k * _OVERFETCH_FACTOR

        # Leg 1: semantic retrieval
        semantic_results = self._semantic_retrieve(query, fetch_k, product_area)

        # Early exit when the collection is empty (e.g. first PRD before any KB seeding)
        if not semantic_results:
            latency_ms = (time.monotonic() - start_ts) * 1000
            self._log_retrieval(project_id, [], latency_ms)
            return []

        # Leg 2: BM25 over the same candidate set
        bm25_results = self._bm25_retrieve(query, semantic_results, fetch_k)

        # Fusion
        fused = self._rrf_fuse(semantic_results, bm25_results, top_k, k=_RRF_K)

        latency_ms = (time.monotonic() - start_ts) * 1000
        self._log_retrieval(project_id, fused, latency_ms)

        return fused

    # ------------------------------------------------------------------
    # Private: semantic leg
    # ------------------------------------------------------------------

    def _semantic_retrieve(
        self,
        query: str,
        k: int,
        product_area: Optional[str] = None,
    ) -> list[NodeWithScore]:
        """
        Query ChromaDB with the embedded query vector.

        Always applies MetadataFilter(key="status", value="active").
        Optionally adds a product_area equality filter.

        Returns list of NodeWithScore; semantic_score is stored in
        node.metadata["semantic_score"] for later logging.
        """
        query_embedding = _embed_query(query)

        # Build the ChromaDB where-clause filter dict.
        # The status=active filter is ALWAYS required — no unfiltered queries allowed.
        where: dict[str, Any]
        if product_area:
            where = {
                "$and": [
                    {"status": {"$eq": "active"}},
                    {"product_area": {"$eq": product_area}},
                ]
            }
        else:
            where = {"status": {"$eq": "active"}}

        try:
            results = self._collection.query(
                query_embeddings=[query_embedding],
                n_results=k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            raise RuntimeError(
                f"ChromaDB semantic query failed: {exc}"
            ) from exc

        nodes: list[NodeWithScore] = []

        if not results["ids"] or not results["ids"][0]:
            return nodes

        ids = results["ids"][0]
        documents = results["documents"][0]
        metadatas = results["metadatas"][0]
        distances = results["distances"][0]

        for chroma_id, doc_text, meta, dist in zip(ids, documents, metadatas, distances):
            # ChromaDB cosine distance: similarity = 1 - distance
            similarity = float(1.0 - dist)

            node_meta = dict(meta)
            node_meta["semantic_score"] = similarity
            # bm25_score initialised to 0.0 here; overwritten after BM25 leg
            node_meta.setdefault("bm25_score", 0.0)
            node_meta["_chroma_id"] = chroma_id  # internal — used for dedup in fusion

            text_node = TextNode(text=doc_text, metadata=node_meta)
            nodes.append(NodeWithScore(node=text_node, score=similarity))

        return nodes

    # ------------------------------------------------------------------
    # Private: BM25 leg
    # ------------------------------------------------------------------

    def _bm25_retrieve(
        self,
        query: str,
        candidates: list[NodeWithScore],
        k: int,
    ) -> list[NodeWithScore]:
        """
        Run BM25Okapi over the candidate corpus returned by the semantic leg.

        BM25 is built in-memory from the semantic candidates only (not the full
        ChromaDB corpus). This is intentional: we use BM25 as a reranker over
        the semantic shortlist rather than a full independent retrieval leg.
        The trade-off: BM25 cannot surface docs that the semantic leg missed, but
        it avoids the O(N) full-corpus scan that would be required for a true
        dual-retrieval setup. For PRD-sized KBs (<10k chunks) this is acceptable.

        BM25 scores are stored in node.metadata["bm25_score"] on the original
        candidate nodes so that _rrf_fuse can read them for logging.
        """
        corpus_texts = [nws.node.get_content() for nws in candidates]

        # Tokenise by whitespace — sufficient for English PRD text
        tokenised_corpus = [text.lower().split() for text in corpus_texts]
        tokenised_query = query.lower().split()

        bm25 = BM25Okapi(tokenised_corpus)
        scores: list[float] = bm25.get_scores(tokenised_query).tolist()

        # Write bm25_score back into the candidate metadata so it survives into fusion
        for nws, score in zip(candidates, scores):
            nws.node.metadata["bm25_score"] = float(score)

        # Build ranked BM25 list (same objects, different ordering)
        indexed = list(enumerate(candidates))
        # Sort descending by BM25 score, then by doc_id for determinism on ties
        indexed.sort(
            key=lambda iv: (-scores[iv[0]], iv[1].node.metadata.get("doc_id", "")),
        )

        return [nws for _, nws in indexed[:k]]

    # ------------------------------------------------------------------
    # Private: RRF fusion
    # ------------------------------------------------------------------

    def _rrf_fuse(
        self,
        semantic: list[NodeWithScore],
        bm25: list[NodeWithScore],
        top_k: int,
        k: int = _RRF_K,
    ) -> list[NodeWithScore]:
        """
        Reciprocal Rank Fusion over the two ranked lists.

        Formula: rrf_score(d) = sum_i( 1 / (k + rank_i(d)) )
        rank_i is 1-based within each list. Documents absent from a list are
        not penalised — they simply do not receive a contribution from that leg.

        Tiebreaker: lexicographic order on doc_id ensures deterministic output
        for identical RRF scores (important for reproducibility of log events).

        Parameters
        ----------
        semantic : list[NodeWithScore]
            Ranked semantic candidates (position 0 = best).
        bm25 : list[NodeWithScore]
            Ranked BM25 candidates (position 0 = best).
        top_k : int
            Number of results to return.
        k : int
            RRF smoothing constant (default 60).

        Returns
        -------
        list[NodeWithScore]
            Up to top_k results with rrf_rank set in metadata.
        """
        # Index candidates by chroma_id for dedup
        all_candidates: dict[str, NodeWithScore] = {}
        rrf_scores: dict[str, float] = {}

        def _chroma_id(nws: NodeWithScore) -> str:
            return str(nws.node.metadata.get("_chroma_id", id(nws)))

        for rank_idx, nws in enumerate(semantic):
            cid = _chroma_id(nws)
            all_candidates[cid] = nws
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k + rank_idx + 1)

        for rank_idx, nws in enumerate(bm25):
            cid = _chroma_id(nws)
            # Only add to candidates if not already present (prefer the semantic copy
            # since it holds the canonical metadata including semantic_score)
            all_candidates.setdefault(cid, nws)
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (k + rank_idx + 1)

        # Sort by RRF score descending; break ties on doc_id lexicographic order
        sorted_cids = sorted(
            all_candidates.keys(),
            key=lambda cid: (
                -rrf_scores[cid],
                all_candidates[cid].node.metadata.get("doc_id", ""),
            ),
        )

        fused: list[NodeWithScore] = []
        for rrf_rank, cid in enumerate(sorted_cids[:top_k], start=1):
            nws = all_candidates[cid]
            nws.node.metadata["rrf_rank"] = rrf_rank
            # Update the NodeWithScore.score to the RRF score for downstream consumers
            # (individual leg scores are preserved in metadata)
            fused.append(NodeWithScore(node=nws.node, score=rrf_scores[cid]))

        return fused

    # ------------------------------------------------------------------
    # Private: logging
    # ------------------------------------------------------------------

    def _log_retrieval(
        self,
        project_id: str,
        results: list[NodeWithScore],
        latency_ms: float,
    ) -> None:
        """
        Emit a structured rag_retrieval log event via structlog.

        Preserves individual semantic_score and bm25_score per result — these
        must NOT be overwritten by the fused RRF score in the log output.
        """
        top_k_results = [
            {
                "doc_id": nws.node.metadata.get("doc_id", ""),
                "section": nws.node.metadata.get("section", ""),
                "semantic_score": nws.node.metadata.get("semantic_score", 0.0),
                "bm25_score": nws.node.metadata.get("bm25_score", 0.0),
                "rrf_rank": nws.node.metadata.get("rrf_rank", 0),
            }
            for nws in results
        ]

        logger.info(
            "rag_retrieval",
            project_id=project_id,
            embedding_model=EMBEDDING_MODEL,
            top_k_results=top_k_results,
            latency_ms=round(latency_ms, 2),
        )


# ---------------------------------------------------------------------------
# PRD indexing — save a generated PRD into the knowledge base
# ---------------------------------------------------------------------------

def _supersede_product_area(collection: Collection, product_area: str) -> None:
    """
    Mark all existing active documents for a product_area as superseded.

    Called before indexing a new PRD so that stale chunks are excluded from
    future retrieval (MetadataFilter status=active will skip them).

    ChromaDB does not support in-place metadata updates via a where-clause,
    so we:
      1. Query for all active IDs in the product_area.
      2. Fetch their existing metadata.
      3. Upsert with status="superseded".

    Raises
    ------
    RuntimeError
        On any ChromaDB failure — never silently swallows errors.
    """
    try:
        results = collection.get(
            where={
                "$and": [
                    {"status": {"$eq": "active"}},
                    {"product_area": {"$eq": product_area}},
                ]
            },
            include=["metadatas", "documents", "embeddings"],
        )
    except Exception as exc:
        raise RuntimeError(
            f"ChromaDB get() failed while superseding product_area='{product_area}': {exc}"
        ) from exc

    if not results["ids"]:
        return  # Nothing to supersede — normal on first PRD for this area

    updated_metadatas = []
    for meta in results["metadatas"]:
        updated = dict(meta)
        updated["status"] = "superseded"
        updated_metadatas.append(updated)

    try:
        collection.upsert(
            ids=results["ids"],
            documents=results["documents"],
            embeddings=results["embeddings"],
            metadatas=updated_metadatas,
        )
    except Exception as exc:
        raise RuntimeError(
            f"ChromaDB upsert() failed while superseding product_area='{product_area}': {exc}"
        ) from exc


def save_prd_to_kb(
    prd_sections: dict[str, str],
    project_id: str,
    product_area: str,
    collection: Collection,
    transcript_doc_ids: Optional[list[str]] = None,
) -> str:
    """
    Index a generated PRD into the ChromaDB knowledge base.

    Steps:
      1. Supersede all existing active docs with the same product_area.
      2. Build a LlamaIndex Document from the PRD sections.
      3. Chunk with HierarchicalNodeParser(chunk_sizes=[2048, 512]).
      4. Embed each chunk with OpenAI text-embedding-3-small.
      5. Upsert into ChromaDB with full metadata schema.

    Parameters
    ----------
    prd_sections : dict[str, str]
        Mapping of section_key -> markdown/text content (e.g. "problem" -> "...").
    project_id : str
        Project identifier — stored in chunk metadata as doc_id source component.
    product_area : str
        Product area name (e.g. "payments"). Used for deduplication and filtering.
    collection : chromadb.Collection
        The ChromaDB collection to index into.
    transcript_doc_ids : list[str] | None
        Optional list of transcript doc_ids (e.g. ["<project_id>-t0", "<project_id>-t1"])
        to record in each chunk's metadata for source provenance tracking.
        When provided, stored as a JSON-serialised string under the key
        "transcript_doc_ids" in chunk metadata so ChromaDB (which requires
        scalar metadata values) can store and filter on it.
        Existing callers that omit this parameter are unaffected.

    Returns
    -------
    str
        The doc_id assigned to the newly indexed PRD (format: "{product_area}-{date}").

    Raises
    ------
    RuntimeError
        On embedding or ChromaDB failures.
    """
    today = date.today().isoformat()
    doc_id = f"{product_area}-{today}"

    # Step 1 — supersede old chunks for this product_area BEFORE indexing new ones
    _supersede_product_area(collection, product_area)

    # Step 2 — build a single LlamaIndex Document with section metadata
    full_text = "\n\n".join(
        f"## {section_key.upper()}\n{content}"
        for section_key, content in prd_sections.items()
        if content
    )

    document = Document(
        text=full_text,
        metadata={
            "doc_id": doc_id,
            "product_area": product_area,
            "date": today,
            "project_id": project_id,
        },
    )

    # Step 3 — chunk with HierarchicalNodeParser at two granularities
    # Parent nodes (2048 tokens): broad semantic context
    # Child nodes (512 tokens): precise snippets for BM25 + tight embedding
    parser = HierarchicalNodeParser.from_defaults(chunk_sizes=[2048, 512])
    nodes = parser.get_nodes_from_documents([document])

    if not nodes:
        logger.warning(
            "save_prd_to_kb_empty_nodes",
            project_id=project_id,
            doc_id=doc_id,
            product_area=product_area,
        )
        return doc_id

    # Step 4 — embed all nodes via OpenAI
    oai_client = openai.OpenAI()
    texts = [node.get_content() for node in nodes]

    try:
        embed_response = oai_client.embeddings.create(
            input=texts,
            model=EMBEDDING_MODEL,
        )
    except Exception as exc:
        raise RuntimeError(
            f"OpenAI embedding batch failed for doc_id='{doc_id}': {exc}"
        ) from exc

    embeddings = [item.embedding for item in embed_response.data]

    # Step 5 — upsert into ChromaDB with full metadata schema
    ids: list[str] = []
    documents_list: list[str] = []
    metadatas: list[dict] = []
    embeddings_list: list[list[float]] = []

    for node, embedding in zip(nodes, embeddings):
        chunk_id = str(uuid.uuid4())

        # Infer section from the first heading in the chunk text (best-effort)
        section = _infer_section(node.get_content(), list(prd_sections.keys()))

        chunk_meta: dict[str, Any] = {
            "doc_id": doc_id,
            "section": section,
            "date": today,
            "product_area": product_area,
            "status": "active",
            "embedding_model": EMBEDDING_MODEL,
            "project_id": project_id,
        }

        # Store transcript source provenance when A1 multi-transcript upload is used.
        # ChromaDB metadata values must be scalars, so we serialise the list to JSON.
        if transcript_doc_ids is not None:
            chunk_meta["transcript_doc_ids"] = json.dumps(transcript_doc_ids)

        ids.append(chunk_id)
        documents_list.append(node.get_content())
        metadatas.append(chunk_meta)
        embeddings_list.append(embedding)

    try:
        collection.upsert(
            ids=ids,
            documents=documents_list,
            embeddings=embeddings_list,
            metadatas=metadatas,
        )
    except Exception as exc:
        raise RuntimeError(
            f"ChromaDB upsert failed for doc_id='{doc_id}': {exc}"
        ) from exc

    logger.info(
        "prd_indexed_to_kb",
        project_id=project_id,
        doc_id=doc_id,
        product_area=product_area,
        chunk_count=len(ids),
        embedding_model=EMBEDDING_MODEL,
    )

    return doc_id


def _infer_section(text: str, known_sections: list[str]) -> str:
    """
    Best-effort section inference from chunk text.

    Looks for a '## SECTION_KEY' heading prefix inserted by save_prd_to_kb.
    Falls back to "general" if no known section header is found.
    """
    text_upper = text.upper()
    for section_key in known_sections:
        if f"## {section_key.upper()}" in text_upper:
            return section_key
    return "general"


# ---------------------------------------------------------------------------
# Reindex utility — migrate a product_area to a new embedding model
# ---------------------------------------------------------------------------

def reindex_product_area(
    product_area: str,
    prd_sections: dict[str, str],
    project_id: str,
    collection: Collection,
) -> str:
    """
    Re-embed and re-index all chunks for a product_area.

    Use this when EMBEDDING_MODEL changes to avoid mixing embedding spaces.
    Steps:
      1. Check existing chunks for embedding_model mismatch — log a warning if found.
      2. Supersede all existing active chunks.
      3. Re-index with save_prd_to_kb (uses current EMBEDDING_MODEL).

    Parameters
    ----------
    product_area : str
        The product area to reindex.
    prd_sections : dict[str, str]
        Current PRD content for this area.
    project_id : str
        Project identifier for logging.
    collection : chromadb.Collection
        Target ChromaDB collection.

    Returns
    -------
    str
        New doc_id after reindexing.
    """
    # Check for embedding model mismatch before superseding
    try:
        existing = collection.get(
            where={
                "$and": [
                    {"status": {"$eq": "active"}},
                    {"product_area": {"$eq": product_area}},
                ]
            },
            include=["metadatas"],
        )
    except Exception as exc:
        raise RuntimeError(
            f"ChromaDB get() failed during reindex check for product_area='{product_area}': {exc}"
        ) from exc

    for meta in (existing.get("metadatas") or []):
        stored_model = meta.get("embedding_model", "")
        if stored_model and stored_model != EMBEDDING_MODEL:
            logger.warning(
                "embedding_model_mismatch",
                product_area=product_area,
                stored_model=stored_model,
                current_model=EMBEDDING_MODEL,
                action="reindexing",
            )
            break  # one warning per reindex is sufficient

    return save_prd_to_kb(prd_sections, project_id, product_area, collection)
