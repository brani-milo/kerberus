#!/usr/bin/env python3
"""
Embed court decisions into the library collection.

Handles:
- Federal decisions (BGE, BGer, BVGE, BStGer, etc.)
- Ticino cantonal decisions

Usage:
    python scripts/embed_decisions.py              # Embed all decisions
    python scripts/embed_decisions.py --test       # Test mode (5 decisions)
    python scripts/embed_decisions.py --verbose    # Verbose logging
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from collections import defaultdict
from tqdm import tqdm

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.embedder import get_embedder, BatchEmbeddingProcessor
from src.embedder.chunking import chunk_decision, create_text_preview  # shared with backfill/Modal
from src.database.vector_db import QdrantManager

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )


def load_decisions(data_dir: Path, limit: int = None) -> list:
    """
    Load all parsed decisions from a directory.

    Args:
        data_dir: Path to data/parsed/{federal,ticino}
        limit: Optional limit on number of decisions

    Returns:
        List of decision dicts
    """
    decisions = []

    # Find all JSON files
    json_files = sorted(data_dir.glob("*.json"))

    logger.info(f"Found {len(json_files)} JSON files in {data_dir}")

    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                decision = json.load(f)
                decision['_source_file'] = json_file.name
                decisions.append(decision)

            if limit and len(decisions) >= limit:
                break

        except Exception as e:
            logger.error(f"Error loading {json_file}: {e}")

    return decisions


def determine_source(decision: dict) -> str:
    """Determine if decision is federal or cantonal."""
    court = decision.get("court", "")
    if court.startswith("CH_TI") or "TI_" in decision.get("id", ""):
        return "ticino"
    return "federal"


def build_decision_payload(chunk: dict) -> dict:
    """
    Build payload for decision chunk.

    Args:
        chunk: Chunk dict with text and metadata

    Returns:
        Payload dict for Qdrant
    """
    decision = chunk.get("decision", {})
    metadata = decision.get("metadata", {})
    citations = metadata.get("citations", {})

    return {
        "id": chunk.get("chunk_id"),
        "decision_id": chunk.get("decision_id"),
        "chunk_type": chunk.get("chunk_type"),
        "chunk_index": chunk.get("chunk_index"),
        "court": decision.get("court"),
        "date": decision.get("date"),
        "year": decision.get("year"),
        "language": decision.get("language"),
        "outcome": decision.get("outcome"),
        "judges": metadata.get("judges", []),
        "citations_laws": citations.get("laws", []),
        "citations_cases": citations.get("cases", []),
        "lower_court": metadata.get("lower_court"),
        "source": determine_source(decision),
        "text_preview": create_text_preview(chunk.get("text", "")),
        # Full chunk text: the reranker scores THIS, not the 200-char preview
        "text": chunk.get("text", ""),
    }


def print_stats(stats: dict, decisions: list, total_chunks: int):
    """Print embedding statistics."""
    print("\n" + "=" * 50)
    print("DECISIONS EMBEDDING COMPLETE")
    print("=" * 50)
    print(f"Decisions processed: {len(decisions)}")
    print(f"Total chunks: {total_chunks}")
    print(f"Embedded:  {stats['embedded']}")
    print(f"Skipped:   {stats['skipped']}")
    print(f"Errors:    {stats['errors']}")

    # Count by court
    court_counts = defaultdict(int)
    for decision in decisions:
        court = decision.get("court", "unknown")
        court_counts[court] += 1

    print("\nDecisions by court:")
    for court, count in sorted(court_counts.items()):
        print(f"  {court}: {count}")

    # Count by language
    lang_counts = defaultdict(int)
    for decision in decisions:
        lang = decision.get("language", "unknown")
        lang_counts[lang] += 1

    print("\nDecisions by language:")
    for lang, count in sorted(lang_counts.items()):
        print(f"  {lang.upper()}: {count}")

    # Count by year
    year_counts = defaultdict(int)
    for decision in decisions:
        year = decision.get("year")
        if year:
            year_counts[year] += 1

    if year_counts:
        print("\nDecisions by year (top 10):")
        for year, count in sorted(year_counts.items(), reverse=True)[:10]:
            print(f"  {year}: {count}")

    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Embed court decisions into library collection")
    parser.add_argument("--test", action="store_true", help="Test mode (5 decisions only)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--batch-size", type=int, default=4, help="Batch size for embedding")
    parser.add_argument("--no-skip", action="store_true", help="Don't skip existing documents")
    parser.add_argument("--federal-only", action="store_true", help="Only process federal decisions")
    parser.add_argument("--ticino-only", action="store_true", help="Only process Ticino decisions")
    args = parser.parse_args()

    setup_logging(args.verbose)

    # Determine limit
    limit = 5 if args.test else None

    # Load decisions
    all_decisions = []
    project_root = Path(__file__).parent.parent

    if not args.ticino_only:
        federal_dir = project_root / "data" / "parsed" / "federal"
        if federal_dir.exists():
            federal_decisions = load_decisions(federal_dir, limit=limit)
            logger.info(f"Loaded {len(federal_decisions)} federal decisions")
            all_decisions.extend(federal_decisions)

    if not args.federal_only:
        ticino_dir = project_root / "data" / "parsed" / "ticino"
        if ticino_dir.exists():
            ticino_limit = limit - len(all_decisions) if limit else None
            ticino_decisions = load_decisions(ticino_dir, limit=ticino_limit)
            logger.info(f"Loaded {len(ticino_decisions)} Ticino decisions")
            all_decisions.extend(ticino_decisions)

    if not all_decisions:
        logger.warning("No decisions found to embed")
        sys.exit(0)

    # Chunk all decisions
    logger.info("Chunking decisions...")
    all_chunks = []
    for decision in tqdm(all_decisions, desc="Chunking"):
        chunks = chunk_decision(decision)
        all_chunks.extend(chunks)

    logger.info(f"Created {len(all_chunks)} chunks from {len(all_decisions)} decisions")

    # Initialize embedder and Qdrant
    logger.info("Initializing embedder...")
    embedder = get_embedder()

    logger.info("Connecting to Qdrant...")
    qdrant = QdrantManager()

    # Ensure collection exists
    qdrant.create_collection("library", vector_size=1024)

    # Create processor
    processor = BatchEmbeddingProcessor(embedder, qdrant, "library")

    # Process chunks
    logger.info(f"Embedding {len(all_chunks)} chunks...")
    stats = processor.process_documents(
        documents=all_chunks,
        text_field="text",
        id_field="chunk_id",
        payload_builder=build_decision_payload,
        batch_size=args.batch_size,
        skip_existing=not args.no_skip,
        show_progress=True
    )

    # Print results
    print_stats(stats, all_decisions, len(all_chunks))


if __name__ == "__main__":
    main()
