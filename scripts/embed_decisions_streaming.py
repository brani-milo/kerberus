#!/usr/bin/env python3
"""
Embed court decisions into the library collection - STREAMING VERSION.

Memory-efficient: Processes files in small batches instead of loading everything.
Suitable for large datasets (100k+ files).

Usage:
    python scripts/embed_decisions_streaming.py                    # All courts
    python scripts/embed_decisions_streaming.py --court CH_BGer    # Single court
    python scripts/embed_decisions_streaming.py --batch-files 500  # Custom file batch
    python scripts/embed_decisions_streaming.py --resume           # Resume from checkpoint
"""

import argparse
import json
import logging
import sys
import os
import re
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Generator, Optional
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

# Courts configuration
FEDERAL_COURTS = ["CH_BGer", "CH_BVGer", "CH_BGE", "CH_BStGer", "CH_EDOEB", "CH_BPatG"]
CANTONAL_COURTS = ["ticino"]
ALL_COURTS = FEDERAL_COURTS + CANTONAL_COURTS


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(Path(__file__).parent.parent / "logs" / "embed_streaming.log")
        ]
    )


def iter_json_files(court: str, project_root: Path) -> Generator[Path, None, None]:
    """
    Iterate over JSON files for a court without loading them.

    Yields file paths one at a time.
    """
    if court == "ticino":
        parsed_dir = project_root / "data" / "parsed" / "ticino"
    else:
        parsed_dir = project_root / "data" / "parsed" / "federal" / court

    if not parsed_dir.exists():
        logger.warning(f"Directory not found: {parsed_dir}")
        return

    for json_file in sorted(parsed_dir.glob("*.json")):
        yield json_file


def load_single_decision(json_file: Path) -> Optional[Dict]:
    """Load a single decision from JSON file."""
    try:
        # Skip empty files
        if json_file.stat().st_size == 0:
            logger.debug(f"Skipping empty file: {json_file.name}")
            return None

        with open(json_file, 'r', encoding='utf-8') as f:
            decision = json.load(f)
            decision['_source_file'] = json_file.name
            return decision
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in {json_file.name}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error loading {json_file.name}: {e}")
        return None


def chunk_decision(decision: dict, max_words: int = 1000) -> List[Dict]:
    """
    Chunk a court decision into semantic parts.
    """
    chunks = []
    decision_id = decision.get("id", "unknown")
    content = decision.get("content", {})

    sections = [
        ("regeste", content.get("regeste")),
        ("facts", content.get("facts")),
        ("reasoning", content.get("reasoning")),
        ("decision", content.get("decision"))
    ]

    chunk_index = 0

    for section_type, section_text in sections:
        if not section_text:
            continue

        section_chunks = split_text_into_chunks(section_text, max_words)

        for text in section_chunks:
            chunk = {
                "chunk_id": f"{decision_id}_chunk_{chunk_index}",
                "decision_id": decision_id,
                "chunk_type": section_type,
                "chunk_index": chunk_index,
                "text": text,
                "decision": decision
            }
            chunks.append(chunk)
            chunk_index += 1

    return chunks


def split_text_into_chunks(text: str, max_words: int = 1000, min_words: int = 200) -> List[str]:
    """Split text into chunks at paragraph boundaries."""
    if not text:
        return []

    words = text.split()
    if len(words) <= max_words:
        return [text]

    paragraphs = re.split(r'\n\n+|\n(?=\d+\.?\s)', text)

    chunks = []
    current_chunk = []
    current_word_count = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        para_words = len(para.split())

        if current_word_count + para_words > max_words and current_chunk:
            chunk_text = '\n\n'.join(current_chunk)
            chunks.append(chunk_text)
            current_chunk = [para]
            current_word_count = para_words
        else:
            current_chunk.append(para)
            current_word_count += para_words

    if current_chunk:
        chunk_text = '\n\n'.join(current_chunk)
        if len(chunks) > 0 and current_word_count < min_words:
            chunks[-1] = chunks[-1] + '\n\n' + chunk_text
        else:
            chunks.append(chunk_text)

    return chunks


def create_text_preview(text: str, max_chars: int = 200) -> str:
    """Create a text preview for display."""
    if not text:
        return ""
    text = ' '.join(text.split())[:max_chars]
    if len(text) >= max_chars:
        last_space = text.rfind(' ')
        if last_space > max_chars // 2:
            text = text[:last_space]
        text += "..."
    return text


def build_decision_payload(chunk: dict) -> dict:
    """Build payload for decision chunk."""
    decision = chunk.get("decision", {})
    metadata = decision.get("metadata", {})
    citations = metadata.get("citations", {})

    court = decision.get("court", "")
    source = "ticino" if court.startswith("CH_TI") or "TI_" in decision.get("id", "") else "federal"

    return {
        "doc_id": chunk.get("decision_id"),  # For incremental pipeline tracking
        "id": chunk.get("chunk_id"),
        "decision_id": chunk.get("decision_id"),
        "chunk_type": chunk.get("chunk_type"),
        "chunk_index": chunk.get("chunk_index"),
        "court": court,
        "date": decision.get("date"),
        "year": decision.get("year"),
        "language": decision.get("language"),
        "outcome": decision.get("outcome"),
        "judges": metadata.get("judges", []),
        "citations_laws": citations.get("laws", []) if isinstance(citations, dict) else [],
        "citations_cases": citations.get("cases", []) if isinstance(citations, dict) else [],
        "lower_court": metadata.get("lower_court"),
        "source": source,
        "text_preview": create_text_preview(chunk.get("text", ""))
    }


def get_checkpoint_path(court: str, project_root: Path) -> Path:
    """Get checkpoint file path for a court."""
    return project_root / "logs" / f"checkpoint_{court}.json"


def save_checkpoint(court: str, last_file: str, stats: dict, project_root: Path):
    """Save progress checkpoint."""
    checkpoint = {
        "court": court,
        "last_file": last_file,
        "timestamp": datetime.now().isoformat(),
        "stats": stats
    }
    checkpoint_path = get_checkpoint_path(court, project_root)
    with open(checkpoint_path, 'w') as f:
        json.dump(checkpoint, f, indent=2)


def load_checkpoint(court: str, project_root: Path) -> Optional[dict]:
    """Load progress checkpoint if exists."""
    checkpoint_path = get_checkpoint_path(court, project_root)
    if checkpoint_path.exists():
        with open(checkpoint_path, 'r') as f:
            return json.load(f)
    return None


def process_court_streaming(
    court: str,
    project_root: Path,
    embedder,
    qdrant,
    batch_files: int = 500,
    batch_embed: int = 4,
    resume: bool = False,
    dry_run: bool = False
) -> dict:
    """
    Process a single court with streaming/batching.

    Args:
        court: Court identifier
        project_root: Project root path
        embedder: BGE embedder instance
        qdrant: Qdrant manager instance
        batch_files: Number of files to load per batch
        batch_embed: Embedding batch size
        resume: Resume from checkpoint
        dry_run: Don't actually embed

    Returns:
        Stats dict
    """
    from src.embedder import BatchEmbeddingProcessor

    logger.info(f"\n{'='*60}")
    logger.info(f"Processing court: {court}")
    logger.info(f"{'='*60}")

    stats = {"files_processed": 0, "chunks_embedded": 0, "skipped": 0, "errors": 0}

    # Check for resume
    skip_until = None
    if resume:
        checkpoint = load_checkpoint(court, project_root)
        if checkpoint:
            skip_until = checkpoint["last_file"]
            stats = checkpoint.get("stats", stats)
            logger.info(f"Resuming from checkpoint: {skip_until}")
            logger.info(f"Previous stats: {stats}")

    # Ensure collection exists
    qdrant.create_collection("library", vector_size=1024)

    # Create processor
    processor = BatchEmbeddingProcessor(embedder, qdrant, "library")

    # Iterate over files in batches
    current_batch = []
    file_count = 0
    skipping = skip_until is not None
    last_file = None

    for json_file in iter_json_files(court, project_root):
        # Resume logic
        if skipping:
            if json_file.name == skip_until:
                skipping = False
                logger.info(f"Found checkpoint, resuming...")
            continue

        file_count += 1
        last_file = json_file.name

        # Load decision
        decision = load_single_decision(json_file)
        if decision is None:
            stats["errors"] += 1
            continue

        # Chunk decision
        chunks = chunk_decision(decision)
        current_batch.extend(chunks)

        # Process batch when full
        if file_count % batch_files == 0:
            if current_batch and not dry_run:
                logger.info(f"Embedding batch: {len(current_batch)} chunks from {batch_files} files...")

                batch_stats = processor.process_documents(
                    documents=current_batch,
                    text_field="text",
                    id_field="chunk_id",
                    payload_builder=build_decision_payload,
                    batch_size=batch_embed,
                    skip_existing=True,
                    show_progress=True
                )

                stats["chunks_embedded"] += batch_stats.get("embedded", 0)
                stats["skipped"] += batch_stats.get("skipped", 0)
                stats["errors"] += batch_stats.get("errors", 0)

            stats["files_processed"] = file_count

            # Save checkpoint
            save_checkpoint(court, last_file, stats, project_root)

            logger.info(f"Progress: {file_count} files, {stats['chunks_embedded']} chunks embedded")

            # Clear batch
            current_batch = []

    # Process remaining
    if current_batch and not dry_run:
        logger.info(f"Embedding final batch: {len(current_batch)} chunks...")

        batch_stats = processor.process_documents(
            documents=current_batch,
            text_field="text",
            id_field="chunk_id",
            payload_builder=build_decision_payload,
            batch_size=batch_embed,
            skip_existing=True,
            show_progress=True
        )

        stats["chunks_embedded"] += batch_stats.get("embedded", 0)
        stats["skipped"] += batch_stats.get("skipped", 0)
        stats["errors"] += batch_stats.get("errors", 0)

    stats["files_processed"] = file_count

    # Final checkpoint
    if last_file:
        save_checkpoint(court, last_file, stats, project_root)

    logger.info(f"\nCourt {court} complete:")
    logger.info(f"  Files processed: {stats['files_processed']}")
    logger.info(f"  Chunks embedded: {stats['chunks_embedded']}")
    logger.info(f"  Skipped: {stats['skipped']}")
    logger.info(f"  Errors: {stats['errors']}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Embed decisions (streaming/memory-efficient)")
    parser.add_argument("--court", type=str, help=f"Single court to process. Options: {', '.join(ALL_COURTS)}")
    parser.add_argument("--federal-only", action="store_true", help="Only federal courts")
    parser.add_argument("--ticino-only", action="store_true", help="Only Ticino")
    parser.add_argument("--batch-files", type=int, default=500, help="Files per batch (default: 500)")
    parser.add_argument("--batch-embed", type=int, default=4, help="Embedding batch size (default: 4)")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually embed")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    setup_logging(args.verbose)

    project_root = Path(__file__).parent.parent

    # Ensure logs directory
    (project_root / "logs").mkdir(exist_ok=True)

    # Determine courts to process
    if args.court:
        if args.court not in ALL_COURTS:
            print(f"Unknown court: {args.court}")
            print(f"Options: {', '.join(ALL_COURTS)}")
            sys.exit(1)
        courts = [args.court]
    elif args.ticino_only:
        courts = CANTONAL_COURTS
    elif args.federal_only:
        courts = FEDERAL_COURTS
    else:
        courts = ALL_COURTS

    logger.info("="*60)
    logger.info("STREAMING DECISION EMBEDDER")
    logger.info("="*60)
    logger.info(f"Courts: {', '.join(courts)}")
    logger.info(f"Batch files: {args.batch_files}")
    logger.info(f"Batch embed: {args.batch_embed}")
    logger.info(f"Resume: {args.resume}")
    logger.info(f"Dry run: {args.dry_run}")

    # Initialize embedder and Qdrant once
    logger.info("\nInitializing embedder...")
    from src.embedder import get_embedder
    from src.database.vector_db import QdrantManager

    embedder = get_embedder()
    qdrant = QdrantManager()

    # Process each court
    total_stats = {"files_processed": 0, "chunks_embedded": 0, "skipped": 0, "errors": 0}

    for court in courts:
        court_stats = process_court_streaming(
            court=court,
            project_root=project_root,
            embedder=embedder,
            qdrant=qdrant,
            batch_files=args.batch_files,
            batch_embed=args.batch_embed,
            resume=args.resume,
            dry_run=args.dry_run
        )

        for key in total_stats:
            total_stats[key] += court_stats.get(key, 0)

    # Final summary
    logger.info("\n" + "="*60)
    logger.info("FINAL SUMMARY")
    logger.info("="*60)
    logger.info(f"Total files processed: {total_stats['files_processed']}")
    logger.info(f"Total chunks embedded: {total_stats['chunks_embedded']}")
    logger.info(f"Total skipped: {total_stats['skipped']}")
    logger.info(f"Total errors: {total_stats['errors']}")
    logger.info("="*60)


if __name__ == "__main__":
    main()
