"""
Federal court scraper using job logs from entscheidsuche.ch.

Based on existing implementation that reads job logs and extracts file references.
"""

import logging
import re
import json
import requests
from bs4 import BeautifulSoup
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from .base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class FederalCourtScraper(BaseScraper):
    """
    Scraper for Swiss Federal Courts using job log scanning approach.

    This implementation preserves the proven methodology:
    1. Scan job logs from Jobs/{court}/ directory
    2. Extract file references using regex patterns
    3. Download files in parallel using ThreadPoolExecutor
    """

    # Court configuration
    COURT_CONFIG = {
        "CH_BGE":    {"folder": "CH_BGE",    "prefix": "CH_BGE",   "mode": "strict"},
        "CH_BGer":   {"folder": "CH_BGer",   "prefix": "CH_BGer",  "mode": "strict"},
        "CH_BVGer":  {"folder": "CH_BVGer",  "prefix": "CH_BVGE",  "mode": "strict"},
        "CH_BStGer": {"folder": "CH_BSTG",   "prefix": "CH_BSTG",  "mode": "strict"},
        "CH_BPatG":  {"folder": "CH_BPatG",  "prefix": "",         "mode": "loose"},
        "CH_EDOEB":  {"folder": "CH_EDOEB",  "prefix": "CH_ED",    "mode": "loose"}
    }

    # URLs
    DOMAIN = "https://entscheidsuche.ch"
    DOCS_BASE = "https://entscheidsuche.ch/docs/"

    def __init__(
        self,
        base_dir: str = "data/federal_archive_full",
        state_file: str = "data/state/federal_scraper.json",
        courts: Optional[List[str]] = None,
        target_years: Optional[List[str]] = None,
        max_workers: int = 5,
        test_mode: bool = False,
        test_limit: int = 10
    ):
        """
        Initialize Federal Court scraper.

        Args:
            base_dir: Base directory for federal data (default: data/federal_archive_full)
            state_file: Path to state JSON file
            courts: List of court IDs to scrape (default: all)
            target_years: List of years to filter (default: all years)
            max_workers: Thread pool size for parallel downloads
            test_mode: Enable test mode (limits downloads)
            test_limit: Max files per court in test mode
        """
        # Note: BaseScraper requires save_dir, but we manage subdirs per court
        super().__init__(
            name="Federal Court Scraper",
            save_dir=base_dir,
            state_file=state_file,
            start_year=1990,  # Not used in this scraper
            end_year=None,
            enable_incremental=True
        )

        self.courts_to_scrape = courts if courts else list(self.COURT_CONFIG.keys())
        self.target_years = target_years if target_years else []
        self.max_workers = max_workers
        self.test_mode = test_mode
        self.test_limit = test_limit

        # Create court subdirectories
        for court_key in self.courts_to_scrape:
            court_dir = self.save_dir / court_key
            court_dir.mkdir(parents=True, exist_ok=True)

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (KERBERUS Legal Research Bot)"
        })

    def save_state(self):
        """Save current state to JSON file (override to count all subdirectories)."""
        from datetime import datetime, date

        # Count ALL files (html and pdf) in all court subdirectories
        total_files = 0
        for court_key in self.COURT_CONFIG.keys():
            court_dir = self.save_dir / court_key
            if court_dir.exists():
                total_files += len(list(court_dir.glob('*.html')))
                total_files += len(list(court_dir.glob('*.pdf')))

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

    def print_summary(self):
        """Print summary statistics (override to count all subdirectories)."""
        # Count ALL files (html and pdf) in all court subdirectories
        total_files = 0
        for court_key in self.COURT_CONFIG.keys():
            court_dir = self.save_dir / court_key
            if court_dir.exists():
                total_files += len(list(court_dir.glob('*.html')))
                total_files += len(list(court_dir.glob('*.pdf')))

        print(f"\n🎉 {self.name} Complete!")
        print(f"{'='*60}")
        print(f"📊 Summary:")
        print(f"   New files downloaded: {self.stats['new_files']}")
        print(f"   Existing files (skipped): {self.stats['existing_files']}")
        print(f"   Errors: {self.stats['errors']}")
        print(f"   Total files in collection: {total_files}")

        if self.state["last_run"]:
            growth = total_files - self.state["last_file_count"]
            print(f"   Previous total: {self.state['last_file_count']}")
            print(f"   Growth: +{growth}")

        print(f"{'='*60}\n")

    def scrape(self):
        """Main scraping method - processes all configured courts."""
        logger.info("Starting Federal Court scrape")
        print(f"\n🚀 {self.name}")

        if self.test_mode:
            print(f"⚠️  TEST MODE: Limited to {self.test_limit} files per court")

        if self.target_years:
            print(f"🔍 Filtering for years: {', '.join(self.target_years)}")

        print(f"🏛️  Courts: {', '.join(self.courts_to_scrape)}")
        print()

        for court_key in self.courts_to_scrape:
            if court_key not in self.COURT_CONFIG:
                logger.warning(f"Unknown court: {court_key}")
                continue

            self._scrape_court(court_key, self.COURT_CONFIG[court_key])

        self.save_state()
        self.print_summary()

    def _scrape_court(self, court_key: str, config: Dict):
        """Scrape a single court using job log scanning."""
        print(f"\n{'='*60}")
        print(f"🏛️  PROCESSING: {court_key}")
        print(f"{'='*60}")

        court_dir = self.save_dir / court_key

        # Step 1: Get job log URLs
        log_urls = self._get_job_log_urls(config["folder"])
        if not log_urls:
            print(f"   ❌ No job logs found for {court_key}")
            return

        print(f"   📂 Found {len(log_urls)} job logs")

        # TEST MODE: Limit log scanning to speed up testing
        if self.test_mode:
            # In test mode, scan only first 50 logs (should be enough to find 10+ files)
            original_count = len(log_urls)
            log_urls = log_urls[:50]
            print(f"   ⚠️  TEST MODE: Scanning only first {len(log_urls)} of {original_count} logs")

        # Step 2: Scan logs for file references
        master_list = self._scan_logs_for_files(log_urls, config)
        print(f"   ✨ Total Historical Files: {len(master_list)}")

        if not master_list:
            print(f"   ❌ No files found in logs")
            return

        # Step 3: Filter by year (if specified)
        final_list = self._filter_by_years(master_list)
        print(f"   📥 Queueing {len(final_list)} downloads...")

        # Step 4: Download files
        self._download_files(final_list, config["folder"], court_dir, court_key)

    def _get_job_log_urls(self, folder_name: str) -> List[str]:
        """Get URLs of all job logs for a court."""
        jobs_dir_url = f"{self.DOMAIN}/docs/Jobs/{folder_name}/"

        try:
            r = self.session.get(jobs_dir_url, timeout=30)
            if r.status_code != 200:
                logger.warning(f"Failed to list jobs for {folder_name}: {r.status_code}")
                return []

            soup = BeautifulSoup(r.text, "html.parser")
            urls = []

            for link in soup.find_all("a"):
                href = link.get("href")
                if href and "Job_" in href and href.endswith(".json"):
                    if href.startswith("http"):
                        full_url = href
                    elif href.startswith("/"):
                        full_url = self.DOMAIN + href
                    else:
                        full_url = jobs_dir_url + href
                    urls.append(full_url)

            return sorted(urls)

        except Exception as e:
            logger.error(f"Error getting job logs for {folder_name}: {e}")
            return []

    def _scan_logs_for_files(self, log_urls: List[str], config: Dict) -> Set[str]:
        """Scan job logs to extract file references."""
        folder = config["folder"]
        mode = config["mode"]
        prefix = config["prefix"]

        # Build regex pattern based on mode
        if mode == "strict":
            pattern = re.compile(rf'({re.escape(prefix)}[^/"]+\.(?:html|pdf))')
        else:
            pattern = re.compile(r'([a-zA-Z0-9_\\%\-\.]+\.(?:html|pdf))')

        found_files = set()

        for i, url in enumerate(log_urls):
            try:
                r = self.session.get(url, timeout=10)
                if r.status_code == 200:
                    matches = pattern.findall(r.text)
                    for m in matches:
                        clean = m.strip()

                        # Unicode fix
                        if "\\u" in clean:
                            try:
                                clean = json.loads(f'"{clean}"')
                            except:
                                pass

                        # Path normalization
                        if "/" not in clean:
                            clean = f"{folder}/{clean}"

                        found_files.add(clean)

            except Exception as e:
                logger.debug(f"Error scanning log {url}: {e}")

            if i % 200 == 0 and i > 0:
                print(f"      ... scanned {i} logs (Found {len(found_files)} files)")

        return found_files

    def _filter_by_years(self, file_list: Set[str]) -> List[str]:
        """Filter files by target years if specified."""
        if not self.target_years:
            return list(file_list)

        filtered = []
        for fp in file_list:
            for year in self.target_years:
                if year in fp:
                    filtered.append(fp)
                    break

        return filtered

    def _download_files(self, file_list: List[str], folder_name: str, save_dir: Path, court_key: str):
        """Download files using ThreadPoolExecutor."""
        # Test mode: limit files
        if self.test_mode:
            file_list = file_list[:self.test_limit]
            print(f"   ⚠️  TEST MODE: Limited to {len(file_list)} files")

        stats = {"success": 0, "skipped": 0, "missing": 0, "error": 0}
        tasks = [(fp, folder_name, save_dir, self.DOCS_BASE) for fp in file_list]

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_file = {executor.submit(self._download_worker, t): t for t in tasks}

            completed = 0
            total = len(tasks)

            for future in as_completed(future_to_file):
                status, _ = future.result()
                stats[status] += 1
                completed += 1

                # Update statistics
                if status == "success":
                    self.stats['new_files'] += 1
                elif status == "skipped":
                    self.stats['existing_files'] += 1
                elif status in ["missing", "error"]:
                    self.stats['errors'] += 1

                if completed % 50 == 0 or completed == total:
                    print(f"      [Progress] {completed}/{total} | ✅ {stats['success']} | ⏭️ {stats['skipped']} | ❌ {stats['missing']} | ⚠️ {stats['error']}", end="\r")

        print()  # New line after progress
        print(f"   🎉 {court_key} Complete")
        print(f"      Stats: ✅ {stats['success']} new | ⏭️ {stats['skipped']} existing | ❌ {stats['missing']} missing | ⚠️ {stats['error']} errors")

    def _download_worker(self, task_tuple: Tuple) -> Tuple[str, str]:
        """Worker function for downloading a single file."""
        filename, folder_name, save_dir, dl_base = task_tuple

        # Extract local filename
        if "/" in filename:
            local_fname = filename.split("/")[-1]
        else:
            local_fname = filename

        local_path = save_dir / local_fname

        # Skip if already exists (smart resume)
        if local_path.exists():
            return ("skipped", local_fname)

        target_url = dl_base + filename

        try:
            r = requests.get(target_url, timeout=20)
            if r.status_code == 200:
                local_path.write_bytes(r.content)
                logger.debug(f"Downloaded: {local_fname}")
                return ("success", local_fname)
            elif r.status_code == 404:
                logger.warning(f"File not found: {filename}")
                return ("missing", local_fname)
            else:
                logger.warning(f"Download failed ({r.status_code}): {filename}")
                return ("error", str(r.status_code))

        except Exception as e:
            logger.error(f"Download error for {filename}: {e}")
            return ("error", "conn_err")
