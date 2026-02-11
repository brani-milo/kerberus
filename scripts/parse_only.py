#!/usr/bin/env python3
"""
Parse-only script - no embedding.

Parses all raw HTML/PDF files to JSON.
Run this before embedding to ensure all documents are parsed.

Usage:
    python scripts/parse_only.py                    # All courts
    python scripts/parse_only.py --court CH_BVGer   # Single court
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from datetime import datetime
from tqdm import tqdm

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
        logging.FileHandler(LOG_DIR / "parse_only.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("ParseOnly")

# Paths
DATA_DIR = PROJECT_ROOT / "data"
FEDERAL_RAW_DIR = DATA_DIR / "federal_archive_full"
TICINO_RAW_DIR = DATA_DIR / "ticino"
PARSED_DIR = DATA_DIR / "parsed"

# Courts in order (smallest first for quick wins)
FEDERAL_COURTS = ["CH_BPatG", "CH_EDOEB", "CH_BStGer", "CH_BGE", "CH_BVGer", "CH_BGer"]
CANTONAL_COURTS = ["ticino"]
ALL_COURTS = FEDERAL_COURTS + CANTONAL_COURTS


def parse_federal_court(court: str) -> dict:
    """Parse all files for a federal court."""
    from src.parsers.federal_parser import FederalParser

    raw_dir = FEDERAL_RAW_DIR / court
    output_dir = PARSED_DIR / "federal" / court
    output_dir.mkdir(parents=True, exist_ok=True)

    if not raw_dir.exists():
        logger.warning(f"Raw directory not found: {raw_dir}")
        return {"parsed": 0, "skipped": 0, "errors": 0}

    # Get all files
    files = list(raw_dir.glob("*.html")) + list(raw_dir.glob("*.pdf"))

    if not files:
        logger.info(f"{court}: No files to parse")
        return {"parsed": 0, "skipped": 0, "errors": 0}

    parser = FederalParser()
    stats = {"parsed": 0, "skipped": 0, "errors": 0}

    logger.info(f"Parsing {court}: {len(files)} files")

    for file_path in tqdm(files, desc=f"Parsing {court}"):
        output_file = output_dir / f"{file_path.stem}.json"

        # Skip if already parsed
        if output_file.exists() and output_file.stat().st_size > 0:
            stats["skipped"] += 1
            continue

        try:
            data = parser.parse(file_path)
            parser.save_json(data, output_file)
            stats["parsed"] += 1
        except Exception as e:
            stats["errors"] += 1
            if stats["errors"] <= 10:
                logger.error(f"Error parsing {file_path.name}: {e}")

    logger.info(f"{court}: parsed={stats['parsed']}, skipped={stats['skipped']}, errors={stats['errors']}")
    return stats


def parse_ticino() -> dict:
    """Parse all Ticino files."""
    from src.parsers.ticino_parser import TicinoParser

    output_dir = PARSED_DIR / "ticino"
    output_dir.mkdir(parents=True, exist_ok=True)

    if not TICINO_RAW_DIR.exists():
        logger.warning(f"Ticino raw directory not found: {TICINO_RAW_DIR}")
        return {"parsed": 0, "skipped": 0, "errors": 0}

    files = list(TICINO_RAW_DIR.glob("*.html"))

    if not files:
        logger.info("Ticino: No files to parse")
        return {"parsed": 0, "skipped": 0, "errors": 0}

    parser = TicinoParser()
    stats = {"parsed": 0, "skipped": 0, "errors": 0}

    logger.info(f"Parsing Ticino: {len(files)} files")

    for file_path in tqdm(files, desc="Parsing Ticino"):
        output_file = output_dir / f"{file_path.stem}.json"

        # Skip if already parsed
        if output_file.exists() and output_file.stat().st_size > 0:
            stats["skipped"] += 1
            continue

        try:
            data = parser.parse(file_path)
            parser.save_json(data, output_file)
            stats["parsed"] += 1
        except Exception as e:
            stats["errors"] += 1
            if stats["errors"] <= 10:
                logger.error(f"Error parsing {file_path.name}: {e}")

    logger.info(f"Ticino: parsed={stats['parsed']}, skipped={stats['skipped']}, errors={stats['errors']}")
    return stats


def main():
    parser = argparse.ArgumentParser(description="Parse-only (no embedding)")
    parser.add_argument("--court", type=str, help=f"Single court: {', '.join(ALL_COURTS)}")
    parser.add_argument("--federal-only", action="store_true", help="Only federal courts")
    parser.add_argument("--ticino-only", action="store_true", help="Only Ticino")
    args = parser.parse_args()

    start_time = time.time()

    logger.info("=" * 60)
    logger.info("PARSE-ONLY SCRIPT")
    logger.info("=" * 60)
    logger.info(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Determine courts
    if args.court:
        if args.court == "ticino":
            courts = ["ticino"]
        elif args.court in FEDERAL_COURTS:
            courts = [args.court]
        else:
            print(f"Unknown court: {args.court}")
            sys.exit(1)
    elif args.ticino_only:
        courts = CANTONAL_COURTS
    elif args.federal_only:
        courts = FEDERAL_COURTS
    else:
        courts = ALL_COURTS

    logger.info(f"Courts to parse: {', '.join(courts)}")

    # Parse each court
    total_stats = {"parsed": 0, "skipped": 0, "errors": 0}

    for court in courts:
        logger.info(f"\n{'='*40}")
        logger.info(f"Processing: {court}")
        logger.info(f"{'='*40}")

        if court == "ticino":
            stats = parse_ticino()
        else:
            stats = parse_federal_court(court)

        for key in total_stats:
            total_stats[key] += stats.get(key, 0)

    # Summary
    duration = time.time() - start_time

    logger.info("\n" + "=" * 60)
    logger.info("PARSING COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Duration: {duration / 60:.1f} minutes")
    logger.info(f"Total parsed: {total_stats['parsed']}")
    logger.info(f"Total skipped: {total_stats['skipped']}")
    logger.info(f"Total errors: {total_stats['errors']}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
