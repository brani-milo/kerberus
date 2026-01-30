"""
Fedlex scraper for Swiss federal laws and ordinances.

Downloads the latest version of all active/valid laws from fedlex.admin.ch.
Designed for weekly full refresh - always downloads latest versions and
removes abrogated laws from the data directory.
"""

import logging
import time
import json
import requests
from pathlib import Path
from typing import List, Dict, Set, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class FedlexScraper:
    """
    Scraper for Swiss federal laws and ordinances from Fedlex.

    Discovers active laws via SPARQL API and downloads latest PDF versions.
    Removes files for abrogated laws to keep data current.
    """

    SPARQL_ENDPOINT = "https://fedlex.data.admin.ch/sparqlendpoint"
    FILESTORE_BASE = "https://fedlex.data.admin.ch/filestore/fedlex.data.admin.ch"

    # SPARQL query to discover all active consolidated laws with SR numbers
    # Filters for laws that are currently in force (status 0) or partially in force (status 3)
    # Status 0 = in force (preferred), Status 3 = partially in force
    # Returns status to allow preferring fully in-force laws
    DISCOVERY_QUERY = """
    PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

    SELECT DISTINCT ?law ?sr ?status WHERE {
        ?law a jolux:ConsolidationAbstract .
        ?law jolux:classifiedByTaxonomyEntry ?taxonomy .
        ?law jolux:inForceStatus ?status .
        ?taxonomy skos:notation ?sr .
        FILTER(DATATYPE(?sr) = <https://fedlex.data.admin.ch/vocabulary/notation-type/id-systematique>)
        FILTER(?status IN (
            <https://fedlex.data.admin.ch/vocabulary/enforcement-status/0>,
            <https://fedlex.data.admin.ch/vocabulary/enforcement-status/3>
        ))
    }
    ORDER BY ?sr ?status
    """

    def __init__(
        self,
        save_dir: str = "data/fedlex",
        languages: List[str] = ["de", "fr", "it"]
    ):
        """
        Initialize Fedlex scraper.

        Args:
            save_dir: Directory to save PDFs
            languages: Languages to download (de, fr, it)
        """
        self.save_dir = Path(save_dir)
        self.languages = languages

        # Create directories
        for lang in languages:
            (self.save_dir / lang).mkdir(parents=True, exist_ok=True)

        (self.save_dir / "metadata").mkdir(parents=True, exist_ok=True)

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (KERBERUS Legal Research Bot)',
            'Accept': 'application/sparql-results+json'
        })

    def discover_active_laws(self) -> Dict[str, str]:
        """
        Discover all active (in force) laws via SPARQL API.

        When multiple laws have the same SR number, prefers:
        - Status 0 (in force) over status 3 (partially in force)

        Returns:
            Dict mapping SR numbers to ELI paths
        """
        print("🔍 Discovering active laws via SPARQL API...")

        laws = {}
        law_status = {}  # Track status for each SR to prefer status 0

        try:
            resp = self.session.post(
                self.SPARQL_ENDPOINT,
                data={"query": self.DISCOVERY_QUERY},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=120
            )

            if resp.status_code == 200:
                data = resp.json()
                bindings = data.get("results", {}).get("bindings", [])

                for binding in bindings:
                    sr = binding.get("sr", {}).get("value", "")
                    law_uri = binding.get("law", {}).get("value", "")
                    status = binding.get("status", {}).get("value", "")

                    if sr and law_uri:
                        eli_path = law_uri.replace("https://fedlex.data.admin.ch/", "")

                        # Check if we already have this SR number
                        if sr in laws:
                            # Prefer status 0 (in force) over status 3 (partially in force)
                            current_status = law_status.get(sr, "")
                            if "status/0" in status and "status/0" not in current_status:
                                # New entry has status 0, replace
                                laws[sr] = eli_path
                                law_status[sr] = status
                        else:
                            laws[sr] = eli_path
                            law_status[sr] = status

                print(f"   Found {len(laws)} active laws")
                logger.info(f"SPARQL discovery found {len(laws)} active laws")
            else:
                print(f"   SPARQL query failed: HTTP {resp.status_code}")
                logger.error(f"SPARQL query failed: {resp.status_code}")

        except Exception as e:
            print(f"   SPARQL error: {e}")
            logger.error(f"SPARQL discovery error: {e}")

        return laws

    def _get_latest_consolidation(self, eli_path: str, language: str) -> Optional[str]:
        """
        Get the latest consolidation URL for a law in a specific language.

        Args:
            eli_path: ELI path (e.g., "eli/cc/1999/404")
            language: Language code (de, fr, it)

        Returns:
            Direct PDF URL or None if not available
        """
        # Query for the latest consolidation with PDF manifestation
        query = f"""
        PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>

        SELECT ?date ?pdf WHERE {{
            <https://fedlex.data.admin.ch/{eli_path}> jolux:isRealizedBy ?consolidation .
            ?consolidation jolux:dateApplicability ?date .
            ?consolidation jolux:isRealizedBy ?expression .
            ?expression jolux:language <http://publications.europa.eu/resource/authority/language/{language.upper()}> .
            ?expression jolux:isEmbodiedBy ?manifestation .
            ?manifestation a jolux:Manifestation .
            ?manifestation jolux:format <https://fedlex.data.admin.ch/vocabulary/file-format/pdf-a> .
            ?manifestation jolux:isExemplifiedBy ?pdf .
            FILTER(?date <= NOW())
        }}
        ORDER BY DESC(?date)
        LIMIT 1
        """

        # Map language codes to EU authority codes
        lang_map = {"de": "DEU", "fr": "FRA", "it": "ITA"}
        query = query.replace(f"/language/{language.upper()}", f"/language/{lang_map.get(language, language.upper())}")

        try:
            resp = self.session.post(
                self.SPARQL_ENDPOINT,
                data={"query": query},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30
            )

            if resp.status_code == 200:
                data = resp.json()
                bindings = data.get("results", {}).get("bindings", [])
                if bindings:
                    pdf_url = bindings[0].get("pdf", {}).get("value", "")
                    if pdf_url:
                        return pdf_url
        except Exception as e:
            logger.debug(f"Failed to get consolidation for {eli_path}/{language}: {e}")

        return None

    def _build_pdf_urls(self, eli_path: str, language: str) -> List[str]:
        """
        Build list of possible PDF URLs to try for a law.

        Args:
            eli_path: ELI path (e.g., "eli/cc/1999/404")
            language: Language code (de, fr, it)

        Returns:
            List of URLs to try (most recent dates first)
        """
        eli_suffix = eli_path.replace("eli/cc/", "")
        eli_dashes = eli_suffix.replace("/", "-")  # Only replace slashes, keep underscores

        # Try recent dates (most recent first)
        current_year = datetime.now().year
        dates = []
        for year in range(current_year, current_year - 10, -1):
            dates.append(f"{year}0101")

        urls = []
        for date in dates:
            urls.append(
                f"{self.FILESTORE_BASE}/{eli_path}/{date}/{language}/pdf-a/"
                f"fedlex-data-admin-ch-eli-cc-{eli_dashes}-{date}-{language}-pdf-a.pdf"
            )

        return urls

    def download_law_pdf(self, sr_number: str, eli_path: str, language: str) -> Dict:
        """
        Download the latest version of a law PDF.

        Always downloads fresh copy (overwrites existing files).

        Args:
            sr_number: SR number (e.g., "220", "311.0")
            eli_path: ELI path (e.g., "eli/cc/24/233_245_233")
            language: Language code (de, fr, it)

        Returns:
            Download result dict
        """
        sr_clean = sr_number.replace(".", "_")
        filename = f"SR_{sr_clean}_{language}.pdf"
        save_path = self.save_dir / language / filename

        # First try to get exact URL via SPARQL
        pdf_url = self._get_latest_consolidation(eli_path, language)

        if pdf_url:
            try:
                resp = self.session.get(pdf_url, timeout=30, allow_redirects=True)
                if resp.status_code == 200 and resp.content[:4] == b'%PDF':
                    save_path.write_bytes(resp.content)
                    size_kb = len(resp.content) // 1024
                    logger.debug(f"Downloaded SR {sr_number} ({language}) via SPARQL: {size_kb}KB")
                    return {"status": "success", "sr": sr_number, "lang": language, "size": size_kb}
            except Exception as e:
                logger.debug(f"SPARQL URL failed for {sr_number}/{language}: {e}")

        # Fallback: try constructed URLs with recent dates
        urls = self._build_pdf_urls(eli_path, language)

        for url in urls:
            try:
                resp = self.session.get(url, timeout=30, allow_redirects=True)

                # Check if we got a valid PDF response
                if resp.status_code == 200 and len(resp.content) > 10000 and resp.content[:4] == b'%PDF':
                    save_path.write_bytes(resp.content)
                    size_kb = len(resp.content) // 1024
                    logger.debug(f"Downloaded SR {sr_number} ({language}): {size_kb}KB")
                    return {"status": "success", "sr": sr_number, "lang": language, "size": size_kb}
                # Continue to next URL if this one didn't have a valid PDF
            except Exception as e:
                logger.debug(f"Failed URL {url}: {e}")
            # Always continue to next URL if current one didn't work

        logger.warning(f"No PDF available for SR {sr_number} ({language})")
        return {"status": "no_pdf", "sr": sr_number, "lang": language}

    def cleanup_abrogated_laws(self, active_sr_numbers: Set[str]):
        """
        Remove PDFs for laws that are no longer active (abrogated).

        Args:
            active_sr_numbers: Set of currently active SR numbers
        """
        print("🧹 Cleaning up abrogated laws...")

        removed_count = 0

        for lang in self.languages:
            lang_dir = self.save_dir / lang

            for pdf_file in lang_dir.glob("SR_*.pdf"):
                # Extract SR number from filename: SR_220_de.pdf -> 220
                sr_from_file = pdf_file.stem.replace(f"_{lang}", "").replace("SR_", "").replace("_", ".")

                if sr_from_file not in active_sr_numbers:
                    pdf_file.unlink()
                    removed_count += 1
                    logger.info(f"Removed abrogated law: {pdf_file.name}")

        if removed_count > 0:
            print(f"   Removed {removed_count} files for abrogated laws")
        else:
            print("   No abrogated laws to remove")

    def save_metadata(self, laws: Dict[str, str], stats: Dict):
        """Save run metadata."""
        metadata_file = self.save_dir / "metadata" / "last_run.json"

        metadata = {
            "run_date": datetime.now().isoformat(),
            "total_active_laws": len(laws),
            "download_stats": stats,
            "laws": laws
        }

        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        logger.info(f"Saved metadata to {metadata_file}")

    def run(self, test_mode: bool = False, skip_treaties: bool = False):
        """
        Run the full scraper workflow.

        1. Discover all active laws via SPARQL
        2. Clean up files for abrogated laws
        3. Download latest version of all active laws

        Args:
            test_mode: If True, only process first 5 laws
            skip_treaties: If True, skip international treaties (SR 0.xxx)
        """
        print("=" * 70)
        print("🚀 FEDLEX SCRAPER - Swiss Federal Laws")
        print("=" * 70)
        print(f"📅 Run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"🌍 Languages: {', '.join(self.languages)}")
        print()

        # Step 1: Discover active laws
        laws = self.discover_active_laws()

        if not laws:
            print("❌ No laws discovered! Aborting.")
            return

        # Filter treaties if requested
        if skip_treaties:
            original_count = len(laws)
            laws = {sr: eli for sr, eli in laws.items() if not sr.startswith("0.")}
            excluded = original_count - len(laws)
            print(f"   Excluding {excluded} treaties (SR 0.xxx)")

        print()

        # Step 2: Clean up abrogated laws
        self.cleanup_abrogated_laws(set(laws.keys()))
        print()

        # Step 3: Download all active laws
        sr_list = sorted(laws.keys())

        if test_mode:
            sr_list = sr_list[:5]
            print(f"⚠️  TEST MODE: Processing only first 5 laws")
            print()

        print(f"📥 Downloading {len(sr_list)} laws in {len(self.languages)} languages...")
        print(f"   Total files: {len(sr_list) * len(self.languages)}")
        print()

        stats = {"success": 0, "no_pdf": 0, "error": 0}

        for i, sr_number in enumerate(sr_list, 1):
            eli_path = laws[sr_number]
            print(f"[{i}/{len(sr_list)}] SR {sr_number}")

            for lang in self.languages:
                lang_icon = {"de": "🇩🇪", "fr": "🇫🇷", "it": "🇮🇹"}.get(lang, "🌐")
                print(f"   {lang_icon} {lang.upper()}...", end=" ", flush=True)

                result = self.download_law_pdf(sr_number, eli_path, lang)
                stats[result["status"]] += 1

                if result["status"] == "success":
                    print(f"✅ {result['size']}KB")
                elif result["status"] == "no_pdf":
                    print("⚠️  no PDF available")
                else:
                    print("❌ error")

                time.sleep(0.2)  # Rate limiting

        # Save metadata
        self.save_metadata(laws, stats)

        # Summary
        print()
        print("=" * 70)
        print("📊 SUMMARY")
        print("=" * 70)
        print(f"✅ Downloaded: {stats['success']}")
        print(f"⚠️  No PDF available: {stats['no_pdf']}")
        print(f"❌ Errors: {stats.get('error', 0)}")
        print()

        # Per-language stats
        for lang in self.languages:
            lang_icon = {"de": "🇩🇪", "fr": "🇫🇷", "it": "🇮🇹"}.get(lang, "🌐")
            files = list((self.save_dir / lang).glob("*.pdf"))
            total_size = sum(f.stat().st_size for f in files) if files else 0
            print(f"{lang_icon} {lang.upper()}: {len(files)} files ({total_size // 1024 // 1024} MB)")

        print()
        print(f"💾 Data saved to: {self.save_dir}")
        print("=" * 70)
