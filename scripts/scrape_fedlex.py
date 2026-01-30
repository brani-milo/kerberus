#!/usr/bin/env python3
"""
Fedlex scraper - downloads latest Swiss federal laws and ordinances.

Designed for weekly full refresh:
- Discovers all active (in force) laws via SPARQL
- Downloads latest PDF version of each law
- Removes files for abrogated laws

Usage:
    python scripts/scrape_fedlex.py --test        # Test mode: first 5 laws only
    python scripts/scrape_fedlex.py               # Full download (all active laws)
    python scripts/scrape_fedlex.py --skip-treaties  # Skip international treaties

Schedule weekly (e.g., Sundays at 3am):
    0 3 * * 0 cd /path/to/kerberus && python scripts/scrape_fedlex.py
"""

import sys
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scrapers.fedlex_scraper import FedlexScraper


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    log_dir = Path('logs/scrapers')
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/scrapers/fedlex.log'),
            logging.StreamHandler() if verbose else logging.NullHandler()
        ]
    )


def main():
    parser = argparse.ArgumentParser(
        description='Download latest Swiss federal laws from Fedlex',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/scrape_fedlex.py --test           # Test with 5 laws
  python scripts/scrape_fedlex.py --skip-treaties  # Skip international treaties
  python scripts/scrape_fedlex.py --languages de   # German only
        """
    )
    parser.add_argument('--test', action='store_true',
                        help='Test mode: download only first 5 laws')
    parser.add_argument('--skip-treaties', action='store_true',
                        help='Skip international treaties (SR 0.xxx)')
    parser.add_argument('--languages', nargs='+', default=['de', 'fr', 'it'],
                        choices=['de', 'fr', 'it'],
                        help='Languages to download (default: de fr it)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Verbose logging')

    args = parser.parse_args()

    setup_logging(args.verbose)

    scraper = FedlexScraper(
        save_dir="data/fedlex",
        languages=args.languages
    )

    try:
        scraper.run(test_mode=args.test, skip_treaties=args.skip_treaties)
    except KeyboardInterrupt:
        print("\n⚠️ Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        logging.exception("Scraper failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
