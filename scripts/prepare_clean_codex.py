#!/usr/bin/env python3
"""
Prepare clean Codex embeddings for upload to Qdrant.

This script:
1. Loads all local embeddings from data/embeddings/codex/
2. Filters to only include entries where SR number is in active laws
3. Corrects abbreviations using authoritative abbreviations.json
4. Saves cleaned embeddings ready for upload

Usage:
    python scripts/prepare_clean_codex.py
"""

import json
import os
from pathlib import Path
from typing import Dict, Set

# Paths
EMBEDDINGS_DIR = Path(__file__).parent.parent / "data" / "embeddings" / "codex"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "embeddings" / "codex_clean"
DISCOVERED_LAWS_PATH = Path(__file__).parent.parent / "data" / "fedlex" / "metadata" / "discovered_laws.json"
ABBREVIATIONS_PATH = Path(__file__).parent.parent / "data" / "fedlex" / "metadata" / "abbreviations.json"


def load_active_laws() -> Set[str]:
    """Load set of active SR numbers from discovered_laws.json."""
    with open(DISCOVERED_LAWS_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return set(data.get('laws', {}).keys())


def load_abbreviations() -> Dict:
    """Load correct abbreviations from abbreviations.json."""
    with open(ABBREVIATIONS_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('by_sr', {})


def correct_entry_abbreviation(entry: Dict, abbrevs: Dict) -> Dict:
    """Correct abbreviation in a single entry, respecting document language."""
    payload = entry.get('payload', {})
    sr_number = payload.get('sr_number', '')
    doc_language = payload.get('language', 'de')  # Document's language

    if sr_number in abbrevs:
        correct_data = abbrevs[sr_number]
        old_abbrev = payload.get('abbreviation')

        # Get correct abbreviation for THIS document's language
        new_abbrev = correct_data.get(doc_language) or correct_data.get('de')

        if new_abbrev and old_abbrev and old_abbrev != new_abbrev:
            payload['abbreviation'] = new_abbrev
            payload['abbreviations_all'] = {
                'de': correct_data.get('de', ''),
                'fr': correct_data.get('fr', ''),
                'it': correct_data.get('it', '')
            }
            # Update sr_name with correct title for document's language
            title_key = f'title_{doc_language}'
            if correct_data.get(title_key):
                payload['sr_name'] = correct_data.get(title_key)
            elif correct_data.get('title_de'):
                payload['sr_name'] = correct_data.get('title_de')

            return entry, old_abbrev, new_abbrev

    return entry, None, None


def main():
    print("=" * 70)
    print("PREPARE CLEAN CODEX EMBEDDINGS")
    print("=" * 70)

    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load active laws and abbreviations
    print("\n1. Loading active laws and abbreviations...")
    active_laws = load_active_laws()
    abbrevs = load_abbreviations()
    print(f"   Active laws: {len(active_laws)}")
    print(f"   Abbreviations: {len(abbrevs)}")

    # Process all embedding files
    print("\n2. Processing embedding files...")

    total_entries = 0
    kept_entries = 0
    filtered_entries = 0
    corrected_abbrevs = 0
    corrections_made = {}  # Track which abbreviations were corrected

    embedding_files = sorted(EMBEDDINGS_DIR.glob("embeddings_*.json"))
    print(f"   Found {len(embedding_files)} embedding files")

    for i, filepath in enumerate(embedding_files):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"   WARNING: Skipping corrupted file {filepath.name}: {e}")
            continue

        cleaned_data = []

        for entry in data:
            total_entries += 1
            payload = entry.get('payload', {})
            sr_number = payload.get('sr_number', '')

            # Filter: only keep active laws
            if sr_number not in active_laws:
                filtered_entries += 1
                continue

            # Correct abbreviation
            entry, old_abbrev, new_abbrev = correct_entry_abbreviation(entry, abbrevs)
            if old_abbrev and new_abbrev:
                corrected_abbrevs += 1
                key = f"{old_abbrev} -> {new_abbrev}"
                corrections_made[key] = corrections_made.get(key, 0) + 1

            cleaned_data.append(entry)
            kept_entries += 1

        # Save cleaned file
        output_path = OUTPUT_DIR / filepath.name
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(cleaned_data, f, ensure_ascii=False)

        if (i + 1) % 50 == 0:
            print(f"   Processed {i + 1}/{len(embedding_files)} files...")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total entries processed: {total_entries}")
    print(f"Entries kept (active laws): {kept_entries}")
    print(f"Entries filtered (inactive): {filtered_entries}")
    print(f"Abbreviations corrected: {corrected_abbrevs}")
    print(f"\nOutput saved to: {OUTPUT_DIR}")

    if corrections_made:
        print("\nAbbreviation corrections made:")
        for correction, count in sorted(corrections_made.items(), key=lambda x: -x[1])[:20]:
            print(f"   {correction}: {count} entries")

    print("\n" + "=" * 70)
    print("Next steps:")
    print("1. Copy codex_clean/ to server")
    print("2. Delete existing Codex collection from Qdrant")
    print("3. Upload clean embeddings to Qdrant")
    print("=" * 70)


if __name__ == "__main__":
    main()
