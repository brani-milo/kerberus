#!/usr/bin/env python3
"""
Ticino cantonal court scraper.

Usage:
    python scripts/scrape_ticino.py              # Incremental update
    python scripts/scrape_ticino.py --full       # Full re-scrape
    python scripts/scrape_ticino.py --year 1993  # Specific year (for testing)
"""

import sys
import argparse
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scrapers.ticino_scraper import TicinoScraper


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    # Ensure log directory exists
    log_dir = Path('logs/scrapers')
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/scrapers/ticino.log'),
            logging.StreamHandler()
        ]
    )


def main():
    parser = argparse.ArgumentParser(description='Scrape Ticino cantonal court decisions')
    parser.add_argument('--full', action='store_true', help='Full re-scrape (ignore state)')
    parser.add_argument('--year', type=int, help='Scrape specific year only')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging')

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)

    # Initialize scraper
    scraper = TicinoScraper()

    # Handle arguments
    if args.full:
        # Reset state for full re-scrape
        scraper.state = {"last_run": None, "last_file_count": 0}
        scraper.enable_incremental = False
        print("🔄 Full re-scrape mode")

    if args.year:
        # Override year range
        scraper.start_year = args.year
        scraper.end_year = args.year
        scraper.enable_incremental = False
        print(f"🎯 Single year mode: {args.year}")

    # Run scraper
    try:
        scraper.scrape()
    except KeyboardInterrupt:
        print("\n⚠️ Interrupted by user")
        scraper.save_state()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        logging.exception("Scraper failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
