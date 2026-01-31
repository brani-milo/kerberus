#!/usr/bin/env python3
"""
Parse Ticino cantonal court decisions (HTML -> JSON).

Usage:
    python scripts/parse_ticino.py              # Parse all decisions
    python scripts/parse_ticino.py --test       # Test mode (5 files only)
    python scripts/parse_ticino.py --verbose    # Verbose logging
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from multiprocessing import Pool, cpu_count
from collections import defaultdict
from tqdm import tqdm

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.parsers.ticino_parser import TicinoParser

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False, log_file: bool = True):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    handlers = [logging.StreamHandler(sys.stdout)]

    if log_file:
        log_dir = Path(__file__).parent.parent / "logs" / "parsers"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / "parse_ticino.log"))

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers
    )


def process_file(file_info):
    """Process a single file (for multiprocessing)."""
    input_path, output_dir = file_info
    parser = TicinoParser()

    try:
        data = parser.parse(input_path)

        # Create output filename from input filename but json
        output_file = output_dir / f"{input_path.stem}.json"

        parser.save_json(data, output_file)
        return {"success": True, "file": input_path.name, "data": data}

    except Exception as e:
        logger.error(f"Error processing {input_path}: {e}")
        return {"success": False, "file": input_path.name, "error": str(e)}


def print_stats(results: list):
    """Print parsing statistics."""
    success_count = sum(1 for r in results if r.get("success"))
    fail_count = len(results) - success_count

    print("\n" + "=" * 50)
    print("TICINO PARSING COMPLETE")
    print("=" * 50)
    print(f"Total files:   {len(results)}")
    print(f"Successful:    {success_count}")
    print(f"Failed:        {fail_count}")

    # Collect stats from successful parses
    courts = defaultdict(int)
    years = defaultdict(int)
    with_judges = 0
    with_regeste = 0
    with_facts = 0

    for r in results:
        if r.get("success") and r.get("data"):
            data = r["data"]
            court = data.get("court", "unknown")
            courts[court] += 1

            year = data.get("year")
            if year:
                years[year] += 1

            if data.get("metadata", {}).get("judges"):
                with_judges += 1

            content = data.get("content", {})
            if content.get("regeste"):
                with_regeste += 1
            if content.get("facts"):
                with_facts += 1

    if courts:
        print("\nBy court:")
        for court, count in sorted(courts.items()):
            print(f"  {court}: {count}")

    if years:
        print("\nBy year (top 10):")
        for year, count in sorted(years.items(), reverse=True)[:10]:
            print(f"  {year}: {count}")

    print("\nMetadata quality:")
    print(f"  With judges:  {with_judges}/{success_count} ({100*with_judges//max(1,success_count)}%)")
    print(f"  With regeste: {with_regeste}/{success_count} ({100*with_regeste//max(1,success_count)}%)")
    print(f"  With facts:   {with_facts}/{success_count} ({100*with_facts//max(1,success_count)}%)")

    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Parse Ticino court decisions")
    parser.add_argument("--test", action="store_true", help="Test mode (5 files only)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--single-process", action="store_true", help="Disable multiprocessing")
    parser.add_argument("--input-dir", type=str, help="Input directory (default: data/ticino)")
    parser.add_argument("--output-dir", type=str, help="Output directory (default: data/parsed/ticino)")
    args = parser.parse_args()

    setup_logging(args.verbose)

    # Paths
    base_dir = Path(__file__).parent.parent
    input_dir = Path(args.input_dir) if args.input_dir else base_dir / "data" / "ticino"
    output_dir = Path(args.output_dir) if args.output_dir else base_dir / "data" / "parsed" / "ticino"

    if not input_dir.exists():
        logger.error(f"Input directory does not exist: {input_dir}")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Collect HTML files
    files = list(input_dir.glob("*.html"))
    logger.info(f"Found {len(files)} HTML files to parse")

    if not files:
        logger.warning("No HTML files found")
        sys.exit(0)

    # Apply test limit
    if args.test:
        files = files[:5]
        logger.info(f"Test mode: processing only {len(files)} files")

    # Prepare tasks
    tasks = [(f, output_dir) for f in files]

    # Process files
    if args.single_process or args.test:
        # Single process mode (easier debugging)
        results = []
        for task in tqdm(tasks, desc="Parsing Ticino decisions"):
            result = process_file(task)
            results.append(result)
    else:
        # Multiprocessing mode
        num_processes = max(1, int(cpu_count() * 0.75))
        logger.info(f"Starting parsing with {num_processes} processes...")

        with Pool(processes=num_processes) as pool:
            results = list(tqdm(
                pool.imap(process_file, tasks),
                total=len(files),
                desc="Parsing Ticino decisions"
            ))

    # Print results
    print_stats(results)


if __name__ == "__main__":
    main()
