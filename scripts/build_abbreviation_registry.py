#!/usr/bin/env python3
"""
Build a complete registry of Swiss federal law abbreviations from Fedlex SPARQL.

Queries the Fedlex SPARQL endpoint for all active laws with abbreviations (titleShort)
in German, French, and Italian. Creates a mapping from abbreviations to SR numbers
and vice versa.

Output: data/fedlex/metadata/abbreviations.json
"""

import json
import logging
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, Set

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

SPARQL_ENDPOINT = "https://fedlex.data.admin.ch/sparqlendpoint"

# Query for all active laws with abbreviations in DE/FR/IT
ABBREVIATION_QUERY = """
PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT DISTINCT ?sr ?lang ?abbrev ?title WHERE {
  ?law a jolux:ConsolidationAbstract .
  ?law jolux:classifiedByTaxonomyEntry ?taxonomy .
  ?law jolux:inForceStatus ?status .
  ?taxonomy skos:notation ?sr .
  FILTER(DATATYPE(?sr) = <https://fedlex.data.admin.ch/vocabulary/notation-type/id-systematique>)
  FILTER(?status IN (
    <https://fedlex.data.admin.ch/vocabulary/enforcement-status/0>,
    <https://fedlex.data.admin.ch/vocabulary/enforcement-status/3>
  ))

  ?law jolux:isRealizedBy ?realization .
  ?realization jolux:language ?langUri .
  ?realization jolux:titleShort ?abbrev .
  OPTIONAL { ?realization jolux:title ?title }

  FILTER(?langUri IN (
    <http://publications.europa.eu/resource/authority/language/DEU>,
    <http://publications.europa.eu/resource/authority/language/FRA>,
    <http://publications.europa.eu/resource/authority/language/ITA>
  ))
  BIND(
    IF(?langUri = <http://publications.europa.eu/resource/authority/language/DEU>, "de",
    IF(?langUri = <http://publications.europa.eu/resource/authority/language/FRA>, "fr",
    IF(?langUri = <http://publications.europa.eu/resource/authority/language/ITA>, "it", "unknown")))
    AS ?lang
  )
}
ORDER BY ?sr ?lang
"""


def fetch_abbreviations() -> Dict:
    """
    Fetch all law abbreviations from Fedlex SPARQL endpoint.

    Returns:
        Dict with structure:
        {
            "by_sr": {
                "220": {"de": "OR", "fr": "CO", "it": "CO", "title_de": "...", ...},
                ...
            },
            "by_abbrev": {
                "OR": ["220"],
                "CO": ["220", "210"],  # Multiple laws can have same abbrev
                ...
            },
            "all_codes": ["OR", "CO", "ZGB", ...]  # Flat set for VALID_CODES
        }
    """
    print("=" * 70)
    print("📚 FEDLEX ABBREVIATION REGISTRY BUILDER")
    print("=" * 70)
    print(f"📅 Run date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("🔍 Querying Fedlex SPARQL for law abbreviations...")

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (KERBERUS Legal Research Bot)',
        'Accept': 'application/sparql-results+json'
    })

    try:
        resp = session.post(
            SPARQL_ENDPOINT,
            data={"query": ABBREVIATION_QUERY},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=180
        )

        if resp.status_code != 200:
            logger.error(f"SPARQL query failed: HTTP {resp.status_code}")
            print(f"❌ SPARQL query failed: HTTP {resp.status_code}")
            return None

        data = resp.json()
        bindings = data.get("results", {}).get("bindings", [])

        print(f"   Received {len(bindings)} records from SPARQL")

    except Exception as e:
        logger.error(f"SPARQL query error: {e}")
        print(f"❌ SPARQL error: {e}")
        return None

    # Process results
    by_sr: Dict[str, Dict] = {}
    by_abbrev: Dict[str, Set[str]] = {}
    all_codes: Set[str] = set()

    for binding in bindings:
        sr = binding.get("sr", {}).get("value", "")
        lang = binding.get("lang", {}).get("value", "")
        abbrev = binding.get("abbrev", {}).get("value", "").strip()
        title = binding.get("title", {}).get("value", "")

        if not sr or not lang or not abbrev:
            continue

        # Normalize abbreviation (uppercase, remove dots)
        abbrev_normalized = abbrev.upper().replace(".", "").strip()

        # Skip very short or very long abbreviations (likely errors)
        if len(abbrev_normalized) < 2 or len(abbrev_normalized) > 20:
            continue

        # Skip abbreviations that are just numbers
        if abbrev_normalized.isdigit():
            continue

        # Build by_sr structure
        if sr not in by_sr:
            by_sr[sr] = {}

        by_sr[sr][lang] = abbrev_normalized
        if title:
            by_sr[sr][f"title_{lang}"] = title

        # Build by_abbrev structure (reverse lookup)
        if abbrev_normalized not in by_abbrev:
            by_abbrev[abbrev_normalized] = set()
        by_abbrev[abbrev_normalized].add(sr)

        # Add to flat set
        all_codes.add(abbrev_normalized)

    # Convert sets to sorted lists for JSON serialization
    by_abbrev_list = {k: sorted(list(v)) for k, v in by_abbrev.items()}
    all_codes_list = sorted(list(all_codes))

    result = {
        "metadata": {
            "created": datetime.now().isoformat(),
            "source": "fedlex.data.admin.ch",
            "total_laws": len(by_sr),
            "total_abbreviations": len(all_codes_list),
            "languages": ["de", "fr", "it"]
        },
        "by_sr": by_sr,
        "by_abbrev": by_abbrev_list,
        "all_codes": all_codes_list
    }

    return result


def save_registry(data: Dict, output_path: Path):
    """Save the registry to JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"💾 Saved to: {output_path}")


def print_summary(data: Dict):
    """Print summary statistics."""
    print()
    print("=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)

    meta = data["metadata"]
    print(f"✅ Laws with abbreviations: {meta['total_laws']}")
    print(f"✅ Unique abbreviations: {meta['total_abbreviations']}")
    print()

    # Sample some well-known laws
    print("📋 Sample entries:")
    samples = ["220", "210", "311.0", "101", "142.20"]
    for sr in samples:
        if sr in data["by_sr"]:
            entry = data["by_sr"][sr]
            de = entry.get("de", "-")
            fr = entry.get("fr", "-")
            it = entry.get("it", "-")
            print(f"   SR {sr}: DE={de}, FR={fr}, IT={it}")

    print()

    # Show abbreviations with multiple laws
    multi_law_abbrevs = {k: v for k, v in data["by_abbrev"].items() if len(v) > 1}
    if multi_law_abbrevs:
        print(f"⚠️  Abbreviations shared by multiple laws: {len(multi_law_abbrevs)}")
        for abbrev, srs in list(multi_law_abbrevs.items())[:5]:
            print(f"   {abbrev} → {', '.join(srs[:3])}{'...' if len(srs) > 3 else ''}")

    print()
    print("=" * 70)


def main():
    # Output path
    output_path = Path(__file__).parent.parent / "data" / "fedlex" / "metadata" / "abbreviations.json"

    # Fetch abbreviations
    data = fetch_abbreviations()

    if not data:
        print("❌ Failed to fetch abbreviations")
        return 1

    # Save registry
    save_registry(data, output_path)

    # Print summary
    print_summary(data)

    print("✅ Registry build complete!")
    return 0


if __name__ == "__main__":
    exit(main())
