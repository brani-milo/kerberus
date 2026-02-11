#!/usr/bin/env python3
"""
Pipeline: Court Decisions (Incremental)

Strategy: SCRAPE NEW → PARSE NEW → EMBED NEW → DELETE RAW (keep parsed)

This pipeline:
1. Checks Qdrant for already-embedded document IDs
2. Scrapes ONLY new decisions (not already downloaded)
3. Parses new raw files to JSON (keeps parsed forever)
4. Embeds only new documents (appends to existing)
5. Deletes raw HTML/PDF files (parsed JSON retained)

Court decisions are immutable once published, so:
- Never re-download existing decisions
- Never re-embed existing decisions
- Keep parsed JSON forever (full text for UI display)

Run daily/weekly to capture new decisions.

Usage:
    python scripts/pipeline_decisions.py                    # All courts
    python scripts/pipeline_decisions.py --federal-only     # Federal courts only
    python scripts/pipeline_decisions.py --ticino-only      # Ticino only
    python scripts/pipeline_decisions.py --court CH_BGer    # Single court
    python scripts/pipeline_decisions.py --dry-run          # Show plan only
    python scripts/pipeline_decisions.py --keep-raw         # Keep raw files
"""

import argparse
import logging
import sys
import time
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Set, Tuple

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Setup logging
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / "pipeline_decisions.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("DecisionsPipeline")

# Paths
DATA_DIR = PROJECT_ROOT / "data"
FEDERAL_RAW_DIR = DATA_DIR / "federal_archive_full"
TICINO_RAW_DIR = DATA_DIR / "ticino"
PARSED_DIR = DATA_DIR / "parsed"

# Court configuration
FEDERAL_COURTS = ["CH_BGer", "CH_BVGer", "CH_BGE", "CH_BStGer", "CH_EDOEB", "CH_BPatG"]
CANTONAL_COURTS = ["ticino"]
ALL_COURTS = FEDERAL_COURTS + CANTONAL_COURTS

# Qdrant collection for decisions
LIBRARY_COLLECTION = "library"


def get_embedded_doc_ids(collection: str = LIBRARY_COLLECTION) -> Set[str]:
    """
    Get set of document IDs already in Qdrant.

    Returns set of doc_id values from payload.
    """
    try:
        from src.database.vector_db import QdrantManager

        qdrant = QdrantManager()

        # Scroll through all points to get doc_ids
        # Note: For large collections, this should be optimized with scroll pagination
        embedded_ids = set()

        offset = None
        batch_size = 1000

        while True:
            results = qdrant.client.scroll(
                collection_name=collection,
                scroll_filter=None,
                limit=batch_size,
                offset=offset,
                with_payload=["doc_id"],
                with_vectors=False
            )

            points, next_offset = results

            for point in points:
                if point.payload and "doc_id" in point.payload:
                    embedded_ids.add(point.payload["doc_id"])

            if next_offset is None:
                break
            offset = next_offset

        return embedded_ids

    except Exception as e:
        logger.warning(f"Could not get embedded IDs (collection may not exist): {e}")
        return set()


def get_parsed_doc_ids(court: str) -> Set[str]:
    """
    Get set of document IDs already parsed (JSON files exist).

    Returns set of document IDs based on JSON filenames.
    """
    if court == "ticino":
        parsed_dir = PARSED_DIR / "ticino"
    else:
        parsed_dir = PARSED_DIR / "federal" / court

    if not parsed_dir.exists():
        return set()

    # Get doc_ids from JSON filenames (e.g., "CH_BGer_001_2024.json" -> "CH_BGer_001_2024")
    return {f.stem for f in parsed_dir.glob("*.json")}


def get_raw_file_ids(court: str) -> Set[str]:
    """
    Get set of document IDs from raw files (HTML/PDF).

    Returns set of document IDs based on raw filenames.
    """
    if court == "ticino":
        raw_dir = TICINO_RAW_DIR
        extensions = ["*.html"]
    else:
        raw_dir = FEDERAL_RAW_DIR / court
        extensions = ["*.html", "*.pdf"]

    if not raw_dir.exists():
        return set()

    raw_ids = set()
    for ext in extensions:
        for f in raw_dir.glob(ext):
            raw_ids.add(f.stem)

    return raw_ids


def step_analyze(courts: List[str]) -> Dict:
    """
    Step 1: Analyze what needs to be processed.

    Compares:
    - Raw files (downloaded but not parsed)
    - Parsed files (parsed but not embedded)
    - Embedded (already in Qdrant)
    """
    print("\n" + "=" * 60)
    print("STEP 1: ANALYZE")
    print("=" * 60)

    analysis = {}

    # Get all embedded doc_ids once
    print("  Checking Qdrant for embedded documents...")
    embedded_ids = get_embedded_doc_ids()
    print(f"  Found {len(embedded_ids)} documents in Qdrant")

    for court in courts:
        raw_ids = get_raw_file_ids(court)
        parsed_ids = get_parsed_doc_ids(court)

        # What needs parsing: raw files not yet parsed
        needs_parsing = raw_ids - parsed_ids

        # What needs embedding: parsed files not yet embedded
        needs_embedding = parsed_ids - embedded_ids

        analysis[court] = {
            "raw_files": len(raw_ids),
            "parsed_files": len(parsed_ids),
            "embedded": len(parsed_ids & embedded_ids),
            "needs_parsing": len(needs_parsing),
            "needs_embedding": len(needs_embedding),
            "needs_parsing_ids": needs_parsing,
            "needs_embedding_ids": needs_embedding,
        }

        print(f"\n  {court}:")
        print(f"    Raw: {len(raw_ids)}, Parsed: {len(parsed_ids)}, Embedded: {len(parsed_ids & embedded_ids)}")
        print(f"    → Needs parsing: {len(needs_parsing)}")
        print(f"    → Needs embedding: {len(needs_embedding)}")

    return analysis


def step_scrape(
    courts: List[str],
    analysis: Dict,
    dry_run: bool = False
) -> Dict:
    """
    Step 2: Scrape new decisions (incremental).

    Only downloads decisions not already in raw directory.
    """
    print("\n" + "=" * 60)
    print("STEP 2: SCRAPE NEW DECISIONS")
    print("=" * 60)

    results = {}

    for court in courts:
        if court == "ticino":
            # Use Ticino scraper (already incremental by design)
            if dry_run:
                print(f"\n  [DRY RUN] {court}: Would scrape new decisions")
                results[court] = {"new_files": 0}
                continue

            from src.scrapers.ticino_scraper import TicinoScraper

            scraper = TicinoScraper(
                save_dir=str(TICINO_RAW_DIR),
                enable_incremental=True  # Skip existing files
            )

            print(f"\n  Scraping {court}...")
            scraper.scrape()

            results[court] = {
                "new_files": scraper.stats.get("new_files", 0),
                "existing_files": scraper.stats.get("existing_files", 0),
            }

        else:
            # Federal courts - check if scraper exists
            # For now, assume raw files are already downloaded
            print(f"\n  {court}: Using existing raw files (federal scraper not implemented)")
            results[court] = {"new_files": 0, "note": "using_existing"}

    return results


def step_parse(
    courts: List[str],
    analysis: Dict,
    dry_run: bool = False
) -> Dict:
    """
    Step 3: Parse new raw files to JSON.

    Only parses files that don't have corresponding JSON.
    """
    print("\n" + "=" * 60)
    print("STEP 3: PARSE NEW DECISIONS")
    print("=" * 60)

    results = {}

    for court in courts:
        needs_parsing = analysis[court]["needs_parsing_ids"]

        if not needs_parsing:
            print(f"\n  {court}: No new files to parse")
            results[court] = {"parsed": 0, "skipped": 0}
            continue

        if dry_run:
            print(f"\n  [DRY RUN] {court}: Would parse {len(needs_parsing)} files")
            results[court] = {"parsed": 0, "would_parse": len(needs_parsing)}
            continue

        # Determine directories and parser
        if court == "ticino":
            from src.parsers.ticino_parser import TicinoParser
            raw_dir = TICINO_RAW_DIR
            output_dir = PARSED_DIR / "ticino"
            parser = TicinoParser()
            extensions = [".html"]
        else:
            from src.parsers.federal_parser import FederalParser
            raw_dir = FEDERAL_RAW_DIR / court
            output_dir = PARSED_DIR / "federal" / court
            parser = FederalParser()
            extensions = [".html", ".pdf"]

        output_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n  Parsing {len(needs_parsing)} new files for {court}...")

        parsed_count = 0
        error_count = 0

        for doc_id in needs_parsing:
            # Find the raw file
            raw_file = None
            for ext in extensions:
                candidate = raw_dir / f"{doc_id}{ext}"
                if candidate.exists():
                    raw_file = candidate
                    break

            if not raw_file:
                error_count += 1
                continue

            try:
                output_file = output_dir / f"{doc_id}.json"
                data = parser.parse(raw_file)
                parser.save_json(data, output_file)
                parsed_count += 1

            except Exception as e:
                error_count += 1
                if error_count <= 5:
                    logger.error(f"Parse error {doc_id}: {e}")

        print(f"    Parsed: {parsed_count}, Errors: {error_count}")
        results[court] = {"parsed": parsed_count, "errors": error_count}

    return results


def step_embed(
    courts: List[str],
    analysis: Dict,
    dry_run: bool = False,
    batch_size: int = 4
) -> Dict:
    """
    Step 4: Embed new parsed decisions.

    Only embeds documents not already in Qdrant.
    """
    print("\n" + "=" * 60)
    print("STEP 4: EMBED NEW DECISIONS")
    print("=" * 60)

    results = {}

    for court in courts:
        # Refresh analysis after parsing (new files may now be available)
        parsed_ids = get_parsed_doc_ids(court)
        embedded_ids = get_embedded_doc_ids()
        needs_embedding = parsed_ids - embedded_ids

        if not needs_embedding:
            print(f"\n  {court}: No new documents to embed")
            results[court] = {"embedded": 0}
            continue

        if dry_run:
            print(f"\n  [DRY RUN] {court}: Would embed {len(needs_embedding)} documents")
            results[court] = {"embedded": 0, "would_embed": len(needs_embedding)}
            continue

        # Load documents that need embedding
        if court == "ticino":
            parsed_dir = PARSED_DIR / "ticino"
        else:
            parsed_dir = PARSED_DIR / "federal" / court

        print(f"\n  Loading {len(needs_embedding)} documents for {court}...")

        documents = []
        for doc_id in needs_embedding:
            json_file = parsed_dir / f"{doc_id}.json"
            if json_file.exists():
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        doc = json.load(f)
                        doc['_source_file'] = json_file.name
                        documents.append(doc)
                except Exception as e:
                    logger.error(f"Error loading {json_file}: {e}")

        if not documents:
            print(f"    No documents loaded")
            results[court] = {"embedded": 0}
            continue

        # Chunk and embed
        from src.embedder import get_embedder, BatchEmbeddingProcessor
        from src.database.vector_db import QdrantManager
        from scripts.embed_decisions import chunk_decision, build_decision_payload

        all_chunks = []
        for doc in documents:
            chunks = chunk_decision(doc)
            all_chunks.extend(chunks)

        print(f"    Created {len(all_chunks)} chunks from {len(documents)} documents")

        embedder = get_embedder()
        qdrant = QdrantManager()
        qdrant.create_collection(LIBRARY_COLLECTION, vector_size=1024)

        processor = BatchEmbeddingProcessor(embedder, qdrant, LIBRARY_COLLECTION)
        stats = processor.process_documents(
            documents=all_chunks,
            text_field="text",
            id_field="chunk_id",
            payload_builder=build_decision_payload,
            batch_size=batch_size,
            skip_existing=True,  # Safety: skip if somehow already exists
            show_progress=True
        )

        print(f"    Embedded {stats['embedded']} chunks")
        results[court] = stats

    return results


def step_cleanup_raw(
    courts: List[str],
    dry_run: bool = False,
    keep_raw: bool = False
) -> Dict:
    """
    Step 5: Delete raw files for successfully parsed documents.

    Keeps parsed JSON, deletes raw HTML/PDF.
    """
    print("\n" + "=" * 60)
    print("STEP 5: CLEANUP RAW FILES")
    print("=" * 60)

    if keep_raw:
        print("  Keeping raw files (--keep-raw flag)")
        return {"deleted": 0}

    results = {}
    total_deleted = 0

    for court in courts:
        # Only delete raw files that have been successfully parsed
        parsed_ids = get_parsed_doc_ids(court)

        if court == "ticino":
            raw_dir = TICINO_RAW_DIR
            extensions = [".html"]
        else:
            raw_dir = FEDERAL_RAW_DIR / court
            extensions = [".html", ".pdf"]

        if not raw_dir.exists():
            results[court] = {"deleted": 0}
            continue

        deleted = 0
        for doc_id in parsed_ids:
            for ext in extensions:
                raw_file = raw_dir / f"{doc_id}{ext}"
                if raw_file.exists():
                    if dry_run:
                        deleted += 1
                    else:
                        raw_file.unlink()
                        deleted += 1

        if dry_run:
            print(f"  [DRY RUN] {court}: Would delete {deleted} raw files")
        else:
            print(f"  {court}: Deleted {deleted} raw files")

        results[court] = {"deleted": deleted}
        total_deleted += deleted

    results["total"] = total_deleted
    return results


def run_pipeline(
    courts: List[str],
    dry_run: bool = False,
    keep_raw: bool = False,
    batch_size: int = 4
) -> Dict:
    """
    Run the complete decisions pipeline.
    """
    start_time = time.time()

    print("\n" + "=" * 60)
    print("DECISIONS PIPELINE - INCREMENTAL")
    print("=" * 60)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"Courts: {', '.join(courts)}")
    print(f"Keep raw files: {keep_raw}")

    results = {
        "start_time": datetime.now().isoformat(),
        "dry_run": dry_run,
        "courts": courts,
        "steps": {}
    }

    # Step 1: Analyze
    analysis = step_analyze(courts)
    results["steps"]["analyze"] = {
        court: {k: v for k, v in stats.items() if not k.endswith("_ids")}
        for court, stats in analysis.items()
    }

    # Step 2: Scrape (if scrapers exist)
    results["steps"]["scrape"] = step_scrape(courts, analysis, dry_run)

    # Step 3: Parse
    results["steps"]["parse"] = step_parse(courts, analysis, dry_run)

    # Step 4: Embed
    results["steps"]["embed"] = step_embed(courts, analysis, dry_run, batch_size)

    # Step 5: Cleanup
    results["steps"]["cleanup"] = step_cleanup_raw(courts, dry_run, keep_raw)

    # Summary
    duration = time.time() - start_time
    results["duration_seconds"] = duration
    results["end_time"] = datetime.now().isoformat()

    print("\n" + "=" * 60)
    print("PIPELINE COMPLETE")
    print("=" * 60)
    print(f"Duration: {duration / 60:.1f} minutes")

    if not dry_run:
        # Save results
        results_file = LOG_DIR / f"decisions_pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"Results saved to: {results_file}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Decisions Pipeline - Incremental (daily/weekly)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show plan without executing"
    )
    parser.add_argument(
        "--keep-raw", action="store_true",
        help="Keep raw HTML/PDF files after parsing"
    )
    parser.add_argument(
        "--federal-only", action="store_true",
        help="Only process federal courts"
    )
    parser.add_argument(
        "--ticino-only", action="store_true",
        help="Only process Ticino"
    )
    parser.add_argument(
        "--court", type=str,
        help="Process single court (e.g., CH_BGer, ticino)"
    )
    parser.add_argument(
        "--batch-size", type=int, default=4,
        help="Embedding batch size"
    )

    args = parser.parse_args()

    # Determine courts
    if args.ticino_only:
        courts = CANTONAL_COURTS
    elif args.federal_only:
        courts = FEDERAL_COURTS
    elif args.court:
        if args.court not in ALL_COURTS:
            print(f"Unknown court: {args.court}")
            print(f"Available: {', '.join(ALL_COURTS)}")
            sys.exit(1)
        courts = [args.court]
    else:
        courts = ALL_COURTS

    run_pipeline(
        courts=courts,
        dry_run=args.dry_run,
        keep_raw=args.keep_raw,
        batch_size=args.batch_size
    )


if __name__ == "__main__":
    main()
