"""
backend/app/services/vector_store.py

ChromaDB client factory for DocHub's RAG knowledge base.

Design decisions:
- Singleton PersistentClient — ChromaDB's HNSW binary is process-exclusive; a single
  client instance is safe in the single-worker uvicorn deployment required by this stack.
- Path "./chroma_db" is relative to the uvicorn working directory (backend/), which is
  the project-level convention; override via CHROMA_PATH env var if needed.
- Collection name "dochub_prd_kb" is the canonical collection; all RAG operations use
  get_or_create_collection() so the collection is created on first run.

Usage:
    from app.services.vector_store import get_chroma_client, get_or_create_collection

    client = get_chroma_client()
    collection = get_or_create_collection(client)
"""

import os
from typing import Optional

import chromadb
from chromadb import Collection

# Module-level singleton — avoids re-opening the HNSW segment on every request.
_client: Optional[chromadb.PersistentClient] = None

# Default path is relative to the uvicorn working directory (backend/).
_CHROMA_PATH: str = os.environ.get("CHROMA_PATH", "./chroma_db")

# Canonical collection name — all RAG code references this via get_or_create_collection().
_COLLECTION_NAME: str = "dochub_prd_kb"


def get_chroma_client() -> chromadb.PersistentClient:
    """
    Return the module-level ChromaDB PersistentClient singleton.

    Creates the client (and the ./chroma_db directory) on first call.
    Subsequent calls return the cached instance without re-opening HNSW.

    Raises
    ------
    RuntimeError
        If the ChromaDB client cannot be initialised (e.g. corrupted segment files).
    """
    global _client
    if _client is None:
        try:
            _client = chromadb.PersistentClient(path=_CHROMA_PATH)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to initialise ChromaDB PersistentClient at '{_CHROMA_PATH}': {exc}"
            ) from exc
    return _client


def get_or_create_collection(
    client: chromadb.PersistentClient,
    name: str = _COLLECTION_NAME,
) -> Collection:
    """
    Return an existing ChromaDB collection or create it if it does not exist.

    Parameters
    ----------
    client : chromadb.PersistentClient
        The ChromaDB client returned by get_chroma_client().
    name : str
        Collection name. Defaults to "dochub_prd_kb".

    Returns
    -------
    chromadb.Collection
        The (possibly newly created) collection.

    Raises
    ------
    RuntimeError
        If the collection cannot be retrieved or created.
    """
    try:
        return client.get_or_create_collection(
            name=name,
            # cosine distance matches OpenAI text-embedding-3-small similarity convention.
            metadata={"hnsw:space": "cosine"},
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to get or create ChromaDB collection '{name}': {exc}"
        ) from exc
