#!/usr/bin/env python3
"""
backend/scripts/seed_kb.py

Seed the ChromaDB knowledge base with pre-built PRD transcripts.

This script indexes all .txt files in data/seed_prds/ into the ChromaDB
persistent collection at ./chroma_db. It must be run from the backend/
directory with the virtual environment activated and all environment
variables set (OPENAI_API_KEY is required for embeddings).

Usage:
    cd backend
    source .venv/bin/activate
    python scripts/seed_kb.py

Each transcript is treated as a "raw" PRD document — it is indexed directly
without going through the LLM PRD generation pipeline. This gives the RAG
system context about prior products from the start of the demo.

The product_area for each file is derived from the filename stem:
    payments_v2.txt  -> "payments"
    auth_sso.txt     -> "auth"
    notifications_v3.txt -> "notifications"

Design note:
    This script uses save_prd_to_kb() which will supersede any existing
    active documents for the same product_area before indexing. Running
    the script multiple times is idempotent in terms of final KB state.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — ensure `app` package is importable from `backend/scripts/`.
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _SCRIPTS_DIR.parent
sys.path.insert(0, str(_BACKEND_DIR))

# ---------------------------------------------------------------------------
# Load environment variables before importing app modules.
# ---------------------------------------------------------------------------
from dotenv import load_dotenv  # noqa: E402

load_dotenv(_BACKEND_DIR / ".env")

# ---------------------------------------------------------------------------
# App imports (after sys.path and dotenv setup).
# ---------------------------------------------------------------------------
from app.services.rag import save_prd_to_kb  # noqa: E402
from app.services.vector_store import get_chroma_client, get_or_create_collection  # noqa: E402


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEED_DIR = _BACKEND_DIR / "data" / "seed_prds"

# Map from filename stem prefix to product_area label.
# The first matching prefix in this dict is used; order matters.
_PRODUCT_AREA_MAP: dict[str, str] = {
    "payments": "payments",
    "auth": "auth",
    "notifications": "notifications",
}


def _infer_product_area(stem: str) -> str:
    """
    Derive a product_area label from a filename stem.

    Examples:
        "payments_v2"     -> "payments"
        "auth_sso"        -> "auth"
        "notifications_v3" -> "notifications"

    Falls back to the raw stem if no prefix match is found.
    """
    lower_stem = stem.lower()
    for prefix, area in _PRODUCT_AREA_MAP.items():
        if lower_stem.startswith(prefix):
            return area
    # Fallback: use stem as-is (replace underscores with hyphens for readability)
    return lower_stem.replace("_", "-")


def seed_file(
    txt_path: Path,
    collection,
    *,
    dry_run: bool = False,
) -> None:
    """
    Read a transcript .txt file and index it into ChromaDB.

    Parameters
    ----------
    txt_path : Path
        Absolute path to the transcript file.
    collection : chromadb.Collection
        Target ChromaDB collection.
    dry_run : bool
        If True, print what would happen but skip the actual embedding/upsert.
    """
    stem = txt_path.stem
    product_area = _infer_product_area(stem)
    project_id = f"seed-{stem}"

    print(f"  Reading: {txt_path.name}")
    transcript_text = txt_path.read_text(encoding="utf-8")

    # For seeding, we treat the full transcript as a single "description" section.
    # This gives the retriever broad context about the product without requiring
    # an LLM call to generate formal PRD sections.
    prd_sections = {
        "description": transcript_text,
        "product_area": product_area,
    }

    if dry_run:
        word_count = len(transcript_text.split())
        print(
            f"  [DRY RUN] Would index: product_area={product_area!r}, "
            f"project_id={project_id!r}, word_count={word_count}"
        )
        return

    print(f"  Indexing: product_area={product_area!r}, project_id={project_id!r}")
    try:
        doc_id = save_prd_to_kb(
            prd_sections=prd_sections,
            project_id=project_id,
            product_area=product_area,
            collection=collection,
        )
        print(f"  Indexed as doc_id={doc_id!r}")
    except Exception as exc:
        print(f"  ERROR indexing {txt_path.name}: {exc}", file=sys.stderr)
        raise


def main(dry_run: bool = False) -> None:
    """
    Find all .txt files in data/seed_prds/ and index them into ChromaDB.
    """
    print("DocHub Knowledge Base Seeder")
    print("=" * 40)

    # Validate that OPENAI_API_KEY is set (required for embeddings)
    if not os.environ.get("OPENAI_API_KEY") and not dry_run:
        print(
            "ERROR: OPENAI_API_KEY is not set. "
            "Set it in backend/.env or export it before running this script.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not _SEED_DIR.is_dir():
        print(f"ERROR: Seed directory not found: {_SEED_DIR}", file=sys.stderr)
        sys.exit(1)

    txt_files = sorted(_SEED_DIR.glob("*.txt"))
    if not txt_files:
        print(f"WARNING: No .txt files found in {_SEED_DIR}")
        return

    print(f"Found {len(txt_files)} transcript(s) to index:")
    for f in txt_files:
        print(f"  - {f.name}")
    print()

    if dry_run:
        print("[DRY RUN MODE — no embeddings or ChromaDB writes will occur]")
        print()

    # Initialise ChromaDB client and collection
    if not dry_run:
        print("Connecting to ChromaDB...")
        client = get_chroma_client()
        collection = get_or_create_collection(client)
        print(f"Collection ready: '{collection.name}'")
        print()
    else:
        collection = None

    # Index each file
    success_count = 0
    error_count = 0

    for txt_path in txt_files:
        print(f"Processing {txt_path.name}...")
        try:
            seed_file(txt_path, collection, dry_run=dry_run)
            success_count += 1
            print(f"  Done.\n")
        except Exception:
            error_count += 1
            print(f"  Failed (see error above).\n")

    # Summary
    print("=" * 40)
    print(f"Seeding complete: {success_count} succeeded, {error_count} failed.")

    if error_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Seed the DocHub ChromaDB knowledge base with pre-built PRD transcripts."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without making any API calls or DB writes.",
    )
    args = parser.parse_args()

    main(dry_run=args.dry_run)
