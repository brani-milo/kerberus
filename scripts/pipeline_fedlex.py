#!/usr/bin/env python3
"""
Pipeline: Fedlex Laws & Ordinances (Full Refresh)

Strategy: DELETE → SCRAPE → PARSE → EMBED → CLEANUP

This pipeline:
1. Deletes ALL existing Fedlex data (parsed + embedded)
2. Re-scrapes all active laws from fedlex.admin.ch via SPARQL
3. Parses PDFs to JSON
4. Embeds into Qdrant "codex" collection (overwrites)
5. Deletes raw PDFs (keeps parsed JSON)

Run weekly (cron: Sunday 2am) to capture law amendments.

Usage:
    python scripts/pipeline_fedlex.py              # Full refresh
    python scripts/pipeline_fedlex.py --dry-run   # Show plan only
    python scripts/pipeline_fedlex.py --keep-raw  # Keep PDFs after parsing
"""

import argparse
import logging
import shutil
import sys
import time
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Tuple

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
        logging.FileHandler(LOG_DIR / "pipeline_fedlex.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("FedlexPipeline")

# Paths
DATA_DIR = PROJECT_ROOT / "data"
FEDLEX_RAW_DIR = DATA_DIR / "fedlex"         # Raw PDFs (de/fr/it subdirs)
FEDLEX_PARSED_DIR = DATA_DIR / "parsed" / "fedlex"  # Parsed JSON

# Qdrant collection for laws
CODEX_COLLECTION = "codex"


def step_cleanup_existing(dry_run: bool = False) -> Dict:
    """
    Step 1: Delete existing Fedlex data.

    - Deletes parsed JSON files
    - Qdrant collection will be overwritten during embedding
    """
    print("\n" + "=" * 60)
    print("STEP 1: CLEANUP EXISTING DATA")
    print("=" * 60)

    stats = {"parsed_deleted": 0, "raw_deleted": 0}

    # Delete parsed JSON
    if FEDLEX_PARSED_DIR.exists():
        parsed_count = len(list(FEDLEX_PARSED_DIR.glob("**/*.json")))
        if dry_run:
            print(f"  [DRY RUN] Would delete {parsed_count} parsed JSON files")
        else:
            shutil.rmtree(FEDLEX_PARSED_DIR)
            print(f"  Deleted {parsed_count} parsed JSON files")
        stats["parsed_deleted"] = parsed_count
    else:
        print("  No existing parsed data")

    # Delete raw PDFs (from previous runs)
    if FEDLEX_RAW_DIR.exists():
        raw_count = len(list(FEDLEX_RAW_DIR.glob("**/*.pdf")))
        if raw_count > 0:
            if dry_run:
                print(f"  [DRY RUN] Would delete {raw_count} raw PDF files")
            else:
                for lang_dir in ["de", "fr", "it"]:
                    lang_path = FEDLEX_RAW_DIR / lang_dir
                    if lang_path.exists():
                        for pdf in lang_path.glob("*.pdf"):
                            pdf.unlink()
                print(f"  Deleted {raw_count} raw PDF files")
            stats["raw_deleted"] = raw_count
        else:
            print("  No existing raw PDFs")

    return stats


def step_scrape(dry_run: bool = False, skip_treaties: bool = True) -> Tuple[bool, Dict]:
    """
    Step 2: Scrape all active laws from Fedlex.

    Uses SPARQL API to discover active laws, then downloads PDFs.
    """
    print("\n" + "=" * 60)
    print("STEP 2: SCRAPE FEDLEX")
    print("=" * 60)

    if dry_run:
        print("  [DRY RUN] Would scrape ~4,500 active laws in 3 languages")
        return True, {"laws_found": 4500, "downloaded": 0}

    from src.scrapers.fedlex_scraper import FedlexScraper

    scraper = FedlexScraper(
        save_dir=str(FEDLEX_RAW_DIR),
        languages=["de", "fr", "it"]
    )

    try:
        scraper.run(skip_treaties=skip_treaties)

        # Count downloaded files
        downloaded = sum(
            len(list((FEDLEX_RAW_DIR / lang).glob("*.pdf")))
            for lang in ["de", "fr", "it"]
            if (FEDLEX_RAW_DIR / lang).exists()
        )

        return True, {"downloaded": downloaded}

    except Exception as e:
        logger.error(f"Scraping failed: {e}")
        return False, {"error": str(e)}


def step_parse(dry_run: bool = False) -> Tuple[bool, Dict]:
    """
    Step 3: Parse PDFs to JSON.

    Extracts text and metadata from each PDF.
    """
    print("\n" + "=" * 60)
    print("STEP 3: PARSE PDFs")
    print("=" * 60)

    if dry_run:
        print("  [DRY RUN] Would parse all downloaded PDFs to JSON")
        return True, {"parsed": 0}

    from src.parsers.fedlex_parser import FedlexParser

    FEDLEX_PARSED_DIR.mkdir(parents=True, exist_ok=True)

    parser = FedlexParser()
    stats = {"parsed": 0, "errors": 0}

    # Process each language
    for lang in ["de", "fr", "it"]:
        lang_dir = FEDLEX_RAW_DIR / lang
        if not lang_dir.exists():
            continue

        pdf_files = list(lang_dir.glob("*.pdf"))
        print(f"\n  Processing {len(pdf_files)} {lang.upper()} PDFs...")

        for pdf_path in pdf_files:
            try:
                output_file = FEDLEX_PARSED_DIR / lang / f"{pdf_path.stem}.json"
                output_file.parent.mkdir(parents=True, exist_ok=True)

                data = parser.parse(pdf_path)
                data["language"] = lang
                data["source"] = "fedlex"

                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

                stats["parsed"] += 1

            except Exception as e:
                stats["errors"] += 1
                if stats["errors"] <= 5:
                    logger.error(f"Parse error {pdf_path.name}: {e}")

    print(f"\n  Parsed: {stats['parsed']}, Errors: {stats['errors']}")
    return stats["errors"] < stats["parsed"], stats


def step_embed(dry_run: bool = False, batch_size: int = 4) -> Tuple[bool, Dict]:
    """
    Step 4: Embed parsed laws into Qdrant.

    Recreates the "codex" collection with fresh embeddings.
    """
    print("\n" + "=" * 60)
    print("STEP 4: EMBED INTO QDRANT")
    print("=" * 60)

    if dry_run:
        print("  [DRY RUN] Would embed all parsed laws into 'codex' collection")
        return True, {"embedded": 0}

    from src.embedder import get_embedder, BatchEmbeddingProcessor
    from src.database.vector_db import QdrantManager

    # Count parsed files
    json_files = list(FEDLEX_PARSED_DIR.glob("**/*.json"))
    print(f"\n  Loading {len(json_files)} parsed laws...")

    # Load all laws
    laws = []
    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                law = json.load(f)
                law['_source_file'] = json_file.name
                laws.append(law)
        except Exception as e:
            logger.error(f"Error loading {json_file}: {e}")

    if not laws:
        print("  No laws to embed!")
        return False, {"embedded": 0}

    # Chunk laws (similar to decisions but for legal articles)
    from scripts.embed_fedlex import chunk_law, build_law_payload

    all_chunks = []
    for law in laws:
        chunks = chunk_law(law)
        all_chunks.extend(chunks)

    print(f"  Created {len(all_chunks)} chunks from {len(laws)} laws")

    # Initialize embedder and Qdrant
    embedder = get_embedder()
    qdrant = QdrantManager()

    # Recreate collection (full refresh)
    qdrant.delete_collection(CODEX_COLLECTION)
    qdrant.create_collection(CODEX_COLLECTION, vector_size=1024)

    # Embed
    processor = BatchEmbeddingProcessor(embedder, qdrant, CODEX_COLLECTION)
    stats = processor.process_documents(
        documents=all_chunks,
        text_field="text",
        id_field="chunk_id",
        payload_builder=build_law_payload,
        batch_size=batch_size,
        skip_existing=False,  # Full refresh, don't skip
        show_progress=True
    )

    print(f"\n  Embedded {stats['embedded']} chunks")
    return True, stats


def step_cleanup_raw(dry_run: bool = False, keep_raw: bool = False) -> Dict:
    """
    Step 5: Delete raw PDFs (optional).

    Keeps parsed JSON, deletes raw PDFs to save space.
    """
    print("\n" + "=" * 60)
    print("STEP 5: CLEANUP RAW FILES")
    print("=" * 60)

    if keep_raw:
        print("  Keeping raw PDFs (--keep-raw flag)")
        return {"deleted": 0}

    if dry_run:
        raw_count = len(list(FEDLEX_RAW_DIR.glob("**/*.pdf"))) if FEDLEX_RAW_DIR.exists() else 0
        print(f"  [DRY RUN] Would delete {raw_count} raw PDF files")
        return {"deleted": raw_count}

    deleted = 0
    for lang in ["de", "fr", "it"]:
        lang_dir = FEDLEX_RAW_DIR / lang
        if lang_dir.exists():
            for pdf in lang_dir.glob("*.pdf"):
                pdf.unlink()
                deleted += 1

    print(f"  Deleted {deleted} raw PDF files")
    return {"deleted": deleted}


def run_pipeline(
    dry_run: bool = False,
    keep_raw: bool = False,
    skip_treaties: bool = True,
    batch_size: int = 4
) -> Dict:
    """
    Run the complete Fedlex pipeline.
    """
    start_time = time.time()

    print("\n" + "=" * 60)
    print("FEDLEX PIPELINE - FULL REFRESH")
    print("=" * 60)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"Keep raw PDFs: {keep_raw}")
    print(f"Skip treaties (SR 0.xxx): {skip_treaties}")

    results = {
        "start_time": datetime.now().isoformat(),
        "dry_run": dry_run,
        "steps": {}
    }

    # Step 1: Cleanup
    results["steps"]["cleanup"] = step_cleanup_existing(dry_run)

    # Step 2: Scrape
    success, stats = step_scrape(dry_run, skip_treaties)
    results["steps"]["scrape"] = stats
    if not success and not dry_run:
        print("\n❌ PIPELINE FAILED at scraping step")
        return results

    # Step 3: Parse
    success, stats = step_parse(dry_run)
    results["steps"]["parse"] = stats
    if not success and not dry_run:
        print("\n❌ PIPELINE FAILED at parsing step")
        return results

    # Step 4: Embed
    success, stats = step_embed(dry_run, batch_size)
    results["steps"]["embed"] = stats
    if not success and not dry_run:
        print("\n❌ PIPELINE FAILED at embedding step")
        return results

    # Step 5: Cleanup raw
    results["steps"]["cleanup_raw"] = step_cleanup_raw(dry_run, keep_raw)

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
        results_file = LOG_DIR / f"fedlex_pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"Results saved to: {results_file}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Fedlex Pipeline - Full Refresh (weekly)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show plan without executing"
    )
    parser.add_argument(
        "--keep-raw", action="store_true",
        help="Keep raw PDFs after parsing"
    )
    parser.add_argument(
        "--include-treaties", action="store_true",
        help="Include international treaties (SR 0.xxx)"
    )
    parser.add_argument(
        "--batch-size", type=int, default=4,
        help="Embedding batch size"
    )

    args = parser.parse_args()

    run_pipeline(
        dry_run=args.dry_run,
        keep_raw=args.keep_raw,
        skip_treaties=not args.include_treaties,
        batch_size=args.batch_size
    )


if __name__ == "__main__":
    main()
