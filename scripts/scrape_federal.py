#!/usr/bin/env python3
"""
Federal court scraper using job log scanning.

Usage:
    python scripts/scrape_federal.py --test              # Test: 10 files per court
    python scripts/scrape_federal.py                     # All courts, all years
    python scripts/scrape_federal.py --court CH_BGE      # Single court
    python scripts/scrape_federal.py --years 2023 2024   # Specific years
"""

import sys
import argparse
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scrapers.federal_scraper import FederalCourtScraper


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
            logging.FileHandler('logs/scrapers/federal.log'),
            logging.StreamHandler()
        ]
    )


def main():
    parser = argparse.ArgumentParser(description='Scrape Federal Court decisions via job logs')
    parser.add_argument('--test', action='store_true', help='Test mode: 10 files per court')
    parser.add_argument(
        '--court',
        choices=['CH_BGE', 'CH_BGer', 'CH_BVGer', 'CH_BStGer', 'CH_BPatG', 'CH_EDOEB'],
        help='Scrape specific court only'
    )
    parser.add_argument('--years', nargs='+', help='Filter for specific years (e.g., 2023 2024)')
    parser.add_argument('--workers', type=int, default=5, help='Number of parallel download threads')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging')

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)

    # Initialize scraper
    courts = [args.court] if args.court else None

    scraper = FederalCourtScraper(
        courts=courts,
        target_years=args.years,
        max_workers=args.workers,
        test_mode=args.test,
        test_limit=10
    )

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
