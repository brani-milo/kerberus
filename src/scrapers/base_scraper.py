"""
Base scraper class with state management and incremental updates.
"""

import os
import json
import logging
from datetime import datetime, date
from typing import Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class BaseScraper:
    """
    Base class for all KERBERUS scrapers.

    Provides:
    - State tracking (last run date, file counts)
    - Incremental update logic
    - Progress reporting
    - Error handling
    """

    def __init__(
        self,
        name: str,
        save_dir: str,
        state_file: str,
        start_year: int = 1990,
        end_year: Optional[int] = None,
        enable_incremental: bool = True
    ):
        """
        Initialize base scraper.

        Args:
            name: Scraper name (for logging)
            save_dir: Directory to save downloaded files
            state_file: Path to state JSON file
            start_year: Earliest year to scrape (default: 1990)
            end_year: Latest year to scrape (default: current year)
            enable_incremental: Enable incremental updates (default: True)
        """
        self.name = name
        self.save_dir = Path(save_dir)
        self.state_file = Path(state_file)
        self.start_year = start_year
        self.end_year = end_year if end_year else date.today().year
        self.enable_incremental = enable_incremental

        # Create directories
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        # Load state
        self.state = self.load_state()

        # Statistics
        self.stats = {
            'new_files': 0,
            'existing_files': 0,
            'errors': 0
        }

    def load_state(self) -> Dict:
        """Load last run state from JSON file."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load state file: {e}")

        return {
            "last_run": None,
            "last_file_count": 0,
            "scraper_name": self.name
        }

    def save_state(self):
        """Save current state to JSON file."""
        # Count files in directory
        total_files = len(list(self.save_dir.glob('*.html')))

        state = {
            "last_run": date.today().isoformat(),
            "last_file_count": total_files,
            "last_updated": datetime.now().isoformat(),
            "scraper_name": self.name,
            "stats": self.stats
        }

        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)

        logger.info(f"State saved: {total_files} files")

    def get_year_range(self) -> tuple:
        """
        Determine year range based on last run.

        Returns:
            (start_year, end_year) tuple
        """
        if not self.enable_incremental:
            # Incremental disabled (--full flag or manual override)
            logger.info(f"Incremental disabled - using full range: {self.start_year}-{self.end_year}")
            return (self.start_year, self.end_year)

        if self.state["last_run"] is None:
            # First run: download everything
            logger.info("First run - downloading all years")
            return (self.start_year, self.end_year)

        last_run = date.fromisoformat(self.state["last_run"])
        days_since = (date.today() - last_run).days

        if days_since <= 30:
            # Recent run: check last 2 years only
            start = max(self.start_year, self.end_year - 2)
            logger.info(f"Recent run ({days_since} days ago) - checking {start}-{self.end_year}")
            return (start, self.end_year)

        # Older run: check last 5 years
        start = max(self.start_year, self.end_year - 5)
        logger.info(f"Older run ({days_since} days ago) - checking {start}-{self.end_year}")
        return (start, self.end_year)

    def print_summary(self):
        """Print summary statistics."""
        total_files = len(list(self.save_dir.glob('*.html')))

        print(f"\n🎉 {self.name} Complete!")
        print(f"{'='*50}")
        print(f"📊 Summary:")
        print(f"   New files downloaded: {self.stats['new_files']}")
        print(f"   Existing files (skipped): {self.stats['existing_files']}")
        print(f"   Errors: {self.stats['errors']}")
        print(f"   Total files in collection: {total_files}")

        if self.state["last_run"]:
            growth = total_files - self.state["last_file_count"]
            print(f"   Previous total: {self.state['last_file_count']}")
            print(f"   Growth: +{growth}")

        print(f"{'='*50}\n")
