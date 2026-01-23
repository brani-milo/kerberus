"""
Ticino cantonal court scraper using entscheidsuche.ch API.
"""

import logging
import time
import requests
from pathlib import Path
from typing import List, Dict, Optional
from .base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class TicinoScraper(BaseScraper):
    """
    Scraper for Ticino cantonal court decisions.

    Uses entscheidsuche.ch API to discover files, then downloads
    from TI_Gerichte directory.
    """

    # API endpoints
    API_URL = "http://v2202109132150164038.luckysrv.de:8080/"
    BASE_URL = "https://entscheidsuche.ch/docs/TI_Gerichte/"
    FALLBACK_URL = "https://entscheidsuche.ch/docs/"

    def __init__(
        self,
        save_dir: str = "data/ticino",
        state_file: str = "data/state/ticino_scraper.json",
        start_year: int = 1990,
        end_year: Optional[int] = None,
        enable_incremental: bool = True
    ):
        super().__init__(
            name="Ticino Court Scraper",
            save_dir=save_dir,
            state_file=state_file,
            start_year=start_year,
            end_year=end_year,
            enable_incremental=enable_incremental
        )

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'KERBERUS Legal Research Bot/1.0'
        })

    def scrape(self):
        """Main scraping method."""
        start_year, end_year = self.get_year_range()

        logger.info(f"Starting Ticino scrape: {start_year}-{end_year}")
        print(f"\n🚀 {self.name}")
        print(f"📅 Year range: {start_year}-{end_year}")
        if not self.enable_incremental:
            print(f"⚠️  Incremental updates DISABLED")
        if self.state["last_run"]:
            print(f"📌 Last run: {self.state['last_run']} ({self.state['last_file_count']} files)")
        print()

        for year in range(start_year, end_year + 1):
            self._scrape_year(year)

        # Save state and print summary
        self.save_state()
        self.print_summary()

    def _scrape_year(self, year: int):
        """Scrape all cases for a given year."""
        print(f"📅 Processing {year}...", end=" ", flush=True)

        start_index = 0
        year_new = 0
        year_existing = 0
        max_iterations = 20  # Safety limit (20 * 500 = 10,000 results max)
        iteration = 0

        while iteration < max_iterations:
            iteration += 1

            # Query API
            hits = self._query_api(year, start_index)

            if not hits:
                break

            # Process each hit
            for hit in hits:
                filename = self._extract_filename(hit, year)

                if not filename:
                    continue

                # Check if already downloaded
                save_path = self.save_dir / filename

                if save_path.exists():
                    year_existing += 1
                    self.stats['existing_files'] += 1
                    continue

                # Download new file
                if self._download_file(filename, save_path):
                    year_new += 1
                    self.stats['new_files'] += 1

                    # Progress indicator (every 10 new files)
                    if year_new % 10 == 0:
                        print(f"✨{year_new}", end=" ", flush=True)

            # Move to next batch
            start_index += len(hits)

            # Stop if we've processed enough (likely hit all valid files)
            if start_index >= 9500:
                break

            # Rate limiting
            time.sleep(0.5)

        # Print year summary
        if year_new > 0:
            print(f"→ ✅ {year_new} NEW (total: {year_new + year_existing})")
        else:
            print(f"→ ✅ No new files (total: {year_existing})")

    def _query_api(self, year: int, start: int) -> List[Dict]:
        """
        Query entscheidsuche.ch API for cases.

        Args:
            year: Year to search
            start: Pagination offset

        Returns:
            List of hit dictionaries
        """
        payload = {
            "type": "hitlist",
            "engine": "entscheidsuche",
            "term": f"canton:TI {year}",
            "start": start,
            "count": 500
        }

        try:
            resp = self.session.post(self.API_URL, json=payload, timeout=30)

            if resp.status_code != 200:
                logger.warning(f"API returned {resp.status_code}")
                return []

            data = resp.json()
            return data.get("hitlist", [])

        except Exception as e:
            logger.error(f"API query failed: {e}")
            self.stats['errors'] += 1
            return []

    def _extract_filename(self, hit: Dict, year: int) -> Optional[str]:
        """
        Extract filename from API hit.

        Args:
            hit: Hit dictionary from API
            year: Year being processed

        Returns:
            Filename (e.g., "TI_1993_001.html") or None
        """
        url = hit.get("url", "")

        if "/view/" not in url:
            return None

        # Extract filename from URL
        filename = url.split("/")[-1]

        # Ensure .html extension
        if not filename.endswith(".html"):
            filename += ".html"

        # STRICT FILTER: Year must be in filename
        # This prevents downloading cases from other years that mention this year
        if str(year) not in filename:
            return None

        return filename

    def _download_file(self, filename: str, save_path: Path) -> bool:
        """
        Download HTML file.

        Args:
            filename: Filename to download
            save_path: Path to save file

        Returns:
            True if successful, False otherwise
        """
        try:
            # Try primary URL
            url = self.BASE_URL + filename
            resp = self.session.get(url, timeout=10)

            if resp.status_code == 200:
                save_path.write_bytes(resp.content)
                logger.debug(f"Downloaded: {filename}")
                return True

            # Try fallback URL
            if resp.status_code == 404:
                fallback_url = self.FALLBACK_URL + filename
                resp2 = self.session.get(fallback_url, timeout=10)

                if resp2.status_code == 200:
                    save_path.write_bytes(resp2.content)
                    logger.debug(f"Downloaded (fallback): {filename}")
                    return True

            logger.warning(f"Download failed ({resp.status_code}): {filename}")
            self.stats['errors'] += 1
            return False

        except Exception as e:
            logger.error(f"Download error for {filename}: {e}")
            self.stats['errors'] += 1
            return False
