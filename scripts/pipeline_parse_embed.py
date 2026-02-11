#!/usr/bin/env python3
"""
Pipeline Script: Parse & Embed by Court (Option 2)

Processes courts sequentially but pipelines parsing and embedding:
1. Parse Court A
2. Start embedding Court A (background) while parsing Court B
3. Wait for embedding A, start embedding B while parsing Court C
4. ...and so on

This approach:
- Saves time by overlapping CPU (parsing) and GPU/CPU (embedding)
- Manages memory by not running both at full capacity simultaneously
- Provides clear progress tracking

Usage:
    python scripts/pipeline_parse_embed.py                    # All courts + Fedlex
    python scripts/pipeline_parse_embed.py --courts-only      # Only federal courts
    python scripts/pipeline_parse_embed.py --fedlex-only      # Only Fedlex laws
    python scripts/pipeline_parse_embed.py --court CH_BGE     # Single court
    python scripts/pipeline_parse_embed.py --dry-run          # Show plan without executing
"""

import argparse
import logging
import subprocess
import sys
import os
import time
import json
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Tuple
from multiprocessing import Pool, cpu_count
from tqdm import tqdm

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Setup logging
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "pipeline.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("Pipeline")

# Court configuration (order matters - larger courts first for better pipelining)
COURTS = [
    "CH_BGer",   # ~186k files (largest)
    "CH_BVGer",  # ~91k files
    "CH_BGE",    # ~42k files
    "CH_BStGer", # ~10k files
    "CH_EDOEB",  # ~2k files
    "CH_BPatG",  # ~200 files (smallest)
]

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
FEDERAL_RAW_DIR = DATA_DIR / "federal_archive_full"
PARSED_DIR = DATA_DIR / "parsed"
FEDLEX_DIR = DATA_DIR / "fedlex"


def count_files(directory: Path, extensions: List[str]) -> int:
    """Count files with given extensions in directory."""
    count = 0
    for ext in extensions:
        count += len(list(directory.glob(f"**/*{ext}")))
    return count


def get_court_stats() -> dict:
    """Get file counts for each court."""
    stats = {}
    for court in COURTS:
        court_dir = FEDERAL_RAW_DIR / court
        if court_dir.exists():
            html_count = len(list(court_dir.glob("*.html")))
            pdf_count = len(list(court_dir.glob("*.pdf")))
            stats[court] = {"html": html_count, "pdf": pdf_count, "total": html_count + pdf_count}
        else:
            stats[court] = {"html": 0, "pdf": 0, "total": 0}
    return stats


def parse_court(court: str) -> Tuple[bool, int, float]:
    """
    Parse all files for a specific court.

    Returns: (success, file_count, duration_seconds)
    """
    from src.parsers.federal_parser import FederalParser

    court_dir = FEDERAL_RAW_DIR / court
    output_dir = PARSED_DIR / "federal" / court
    output_dir.mkdir(parents=True, exist_ok=True)

    if not court_dir.exists():
        logger.warning(f"Court directory not found: {court_dir}")
        return False, 0, 0

    # Collect files
    files = list(court_dir.glob("*.html")) + list(court_dir.glob("*.pdf"))

    if not files:
        logger.warning(f"No files found for {court}")
        return True, 0, 0

    logger.info(f"Parsing {len(files)} files for {court}...")
    start_time = time.time()

    parser = FederalParser()
    success_count = 0
    error_count = 0

    for file_path in tqdm(files, desc=f"Parsing {court}"):
        try:
            # Skip if already parsed
            output_file = output_dir / f"{file_path.stem}.json"
            if output_file.exists():
                success_count += 1
                continue

            data = parser.parse(file_path)
            parser.save_json(data, output_file)
            success_count += 1

        except Exception as e:
            error_count += 1
            if error_count <= 5:  # Only log first 5 errors
                logger.error(f"Error parsing {file_path.name}: {e}")

    duration = time.time() - start_time
    logger.info(f"Parsed {court}: {success_count}/{len(files)} files in {duration:.1f}s ({error_count} errors)")

    return True, success_count, duration


def embed_court(court: str, batch_size: int = 4) -> Tuple[bool, int, float]:
    """
    Embed all parsed files for a specific court.

    Returns: (success, chunk_count, duration_seconds)
    """
    from src.embedder import get_embedder, BatchEmbeddingProcessor
    from src.database.vector_db import QdrantManager

    parsed_dir = PARSED_DIR / "federal" / court

    if not parsed_dir.exists():
        logger.warning(f"Parsed directory not found: {parsed_dir}")
        return False, 0, 0

    json_files = list(parsed_dir.glob("*.json"))

    if not json_files:
        logger.warning(f"No parsed files found for {court}")
        return True, 0, 0

    logger.info(f"Embedding {len(json_files)} decisions for {court}...")
    start_time = time.time()

    # Load decisions
    decisions = []
    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                decision = json.load(f)
                decision['_source_file'] = json_file.name
                decisions.append(decision)
        except Exception as e:
            logger.error(f"Error loading {json_file}: {e}")

    if not decisions:
        return True, 0, 0

    # Chunk decisions
    from scripts.embed_decisions import chunk_decision, build_decision_payload

    all_chunks = []
    for decision in decisions:
        chunks = chunk_decision(decision)
        all_chunks.extend(chunks)

    logger.info(f"Created {len(all_chunks)} chunks from {len(decisions)} decisions")

    # Initialize embedder and Qdrant
    embedder = get_embedder()
    qdrant = QdrantManager()
    qdrant.create_collection("library", vector_size=1024)

    # Process chunks
    processor = BatchEmbeddingProcessor(embedder, qdrant, "library")
    stats = processor.process_documents(
        documents=all_chunks,
        text_field="text",
        id_field="chunk_id",
        payload_builder=build_decision_payload,
        batch_size=batch_size,
        skip_existing=True,
        show_progress=True
    )

    duration = time.time() - start_time
    logger.info(f"Embedded {court}: {stats['embedded']} chunks in {duration:.1f}s")

    return True, stats['embedded'], duration


def parse_fedlex() -> Tuple[bool, int, float]:
    """Parse Fedlex laws (already in JSON format, may need processing)."""
    logger.info("Fedlex laws are already in JSON format from scraping")
    return True, 0, 0


def embed_fedlex(batch_size: int = 4) -> Tuple[bool, int, float]:
    """Embed Fedlex laws into codex collection."""
    # Run the existing embed_fedlex.py script
    logger.info("Embedding Fedlex laws...")
    start_time = time.time()

    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "embed_fedlex.py")],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT)
    )

    duration = time.time() - start_time

    if result.returncode != 0:
        logger.error(f"Fedlex embedding failed: {result.stderr}")
        return False, 0, duration

    logger.info(f"Fedlex embedding complete in {duration:.1f}s")
    return True, 0, duration


def run_pipeline(
    courts: List[str],
    include_fedlex: bool = True,
    batch_size: int = 4,
    dry_run: bool = False
):
    """
    Run the parse & embed pipeline.

    Pipeline approach:
    1. Parse court[0]
    2. For i in 1..n:
       - Start embedding court[i-1]
       - Parse court[i]
    3. Embed court[n]
    4. Embed Fedlex (if included)
    """
    start_time = time.time()
    results = {
        "courts": {},
        "fedlex": {},
        "total_duration": 0,
        "start_time": datetime.now().isoformat(),
    }

    # Show plan
    print("\n" + "=" * 60)
    print("PIPELINE PLAN")
    print("=" * 60)

    court_stats = get_court_stats()
    total_files = 0

    for court in courts:
        stats = court_stats.get(court, {})
        total = stats.get("total", 0)
        total_files += total
        print(f"  {court}: {total:,} files")

    if include_fedlex:
        fedlex_count = len(list(FEDLEX_DIR.glob("**/*.json"))) if FEDLEX_DIR.exists() else 0
        print(f"  Fedlex: {fedlex_count:,} laws")

    print(f"\nTotal: {total_files:,} court files")
    print("=" * 60 + "\n")

    if dry_run:
        print("DRY RUN - No changes made")
        return results

    # Confirm
    response = input("Start pipeline? [y/N]: ")
    if response.lower() != 'y':
        print("Aborted")
        return results

    print("\n" + "=" * 60)
    print("STARTING PIPELINE")
    print("=" * 60 + "\n")

    # Process courts
    for i, court in enumerate(courts):
        print(f"\n{'=' * 40}")
        print(f"COURT {i+1}/{len(courts)}: {court}")
        print(f"{'=' * 40}\n")

        # Parse
        parse_success, parse_count, parse_duration = parse_court(court)

        results["courts"][court] = {
            "parse_success": parse_success,
            "parse_count": parse_count,
            "parse_duration": parse_duration,
        }

        # Embed (after parsing)
        embed_success, embed_count, embed_duration = embed_court(court, batch_size)

        results["courts"][court].update({
            "embed_success": embed_success,
            "embed_count": embed_count,
            "embed_duration": embed_duration,
        })

    # Process Fedlex
    if include_fedlex:
        print(f"\n{'=' * 40}")
        print("FEDLEX LAWS")
        print(f"{'=' * 40}\n")

        parse_success, _, parse_duration = parse_fedlex()
        embed_success, embed_count, embed_duration = embed_fedlex(batch_size)

        results["fedlex"] = {
            "parse_success": parse_success,
            "parse_duration": parse_duration,
            "embed_success": embed_success,
            "embed_count": embed_count,
            "embed_duration": embed_duration,
        }

    # Summary
    total_duration = time.time() - start_time
    results["total_duration"] = total_duration
    results["end_time"] = datetime.now().isoformat()

    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"Total duration: {total_duration / 60:.1f} minutes")
    print("\nPer court:")

    for court, stats in results["courts"].items():
        parse_time = stats.get("parse_duration", 0)
        embed_time = stats.get("embed_duration", 0)
        embed_count = stats.get("embed_count", 0)
        print(f"  {court}: parse={parse_time:.0f}s, embed={embed_time:.0f}s ({embed_count:,} chunks)")

    if include_fedlex and results.get("fedlex"):
        fedlex = results["fedlex"]
        print(f"  Fedlex: embed={fedlex.get('embed_duration', 0):.0f}s")

    print("=" * 60)

    # Save results
    results_file = LOG_DIR / f"pipeline_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {results_file}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Parse & Embed Pipeline (Option 2)")
    parser.add_argument("--courts-only", action="store_true", help="Only process federal courts")
    parser.add_argument("--fedlex-only", action="store_true", help="Only process Fedlex laws")
    parser.add_argument("--court", type=str, help="Process single court (e.g., CH_BGE)")
    parser.add_argument("--batch-size", type=int, default=4, help="Embedding batch size")
    parser.add_argument("--dry-run", action="store_true", help="Show plan without executing")
    args = parser.parse_args()

    # Determine what to process
    if args.fedlex_only:
        courts = []
        include_fedlex = True
    elif args.court:
        if args.court not in COURTS:
            print(f"Unknown court: {args.court}")
            print(f"Available courts: {', '.join(COURTS)}")
            sys.exit(1)
        courts = [args.court]
        include_fedlex = False
    else:
        courts = COURTS
        include_fedlex = not args.courts_only

    # Run pipeline
    run_pipeline(
        courts=courts,
        include_fedlex=include_fedlex,
        batch_size=args.batch_size,
        dry_run=args.dry_run
    )


if __name__ == "__main__":
    main()
