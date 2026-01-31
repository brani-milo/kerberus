#!/usr/bin/env python3
"""
Show embedding statistics for KERBERUS collections.

Usage:
    python scripts/embed_status.py
"""

import sys
from pathlib import Path
from collections import defaultdict
from typing import Dict, Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.vector_db import QdrantManager


def get_collection_stats(qdrant: QdrantManager, collection_name: str) -> Dict[str, Any]:
    """
    Get statistics for a collection.

    Args:
        qdrant: QdrantManager instance
        collection_name: Name of collection

    Returns:
        Statistics dict
    """
    stats = {
        "total": 0,
        "by_language": defaultdict(int),
        "by_domain": defaultdict(int),
        "by_court": defaultdict(int),
        "by_year": defaultdict(int),
        "by_law_type": defaultdict(int),
        "by_source": defaultdict(int),
        "by_chunk_type": defaultdict(int)
    }

    try:
        # Check if collection exists
        collections = qdrant.client.get_collections().collections
        if not any(col.name == collection_name for col in collections):
            return stats

        # Get collection info
        info = qdrant.client.get_collection(collection_name)
        stats["total"] = info.points_count

        # Scroll through all points to get payload stats
        offset = None
        batch_size = 100

        while True:
            results = qdrant.client.scroll(
                collection_name=collection_name,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )

            points, next_offset = results

            if not points:
                break

            for point in points:
                payload = point.payload or {}

                # Count by language
                if "language" in payload:
                    stats["by_language"][payload["language"]] += 1

                # Count by domain (for codex)
                if "domain" in payload:
                    stats["by_domain"][payload["domain"]] += 1

                # Count by law_type (for codex)
                if "law_type" in payload:
                    stats["by_law_type"][payload["law_type"]] += 1

                # Count by court (for library)
                if "court" in payload:
                    stats["by_court"][payload["court"]] += 1

                # Count by year (for library)
                if "year" in payload:
                    stats["by_year"][payload["year"]] += 1

                # Count by source
                if "source" in payload:
                    stats["by_source"][payload["source"]] += 1

                # Count by chunk_type (for library)
                if "chunk_type" in payload:
                    stats["by_chunk_type"][payload["chunk_type"]] += 1

            if next_offset is None:
                break

            offset = next_offset

    except Exception as e:
        print(f"Error getting stats for {collection_name}: {e}")

    return stats


def format_count_dict(counts: Dict, max_items: int = 10) -> str:
    """Format a count dictionary for display."""
    if not counts:
        return "  (none)"

    items = sorted(counts.items(), key=lambda x: (-x[1], str(x[0])))
    lines = []

    for key, count in items[:max_items]:
        lines.append(f"  {key}: {count:,}")

    if len(items) > max_items:
        lines.append(f"  ... and {len(items) - max_items} more")

    return "\n".join(lines)


def print_codex_stats(stats: Dict):
    """Print statistics for codex collection."""
    print("\nCollection: codex (Swiss Laws)")
    print("-" * 40)
    print(f"Total vectors: {stats['total']:,}")

    if stats["by_language"]:
        lang_str = " | ".join(
            f"{lang.upper()}={count:,}"
            for lang, count in sorted(stats["by_language"].items())
        )
        print(f"By language: {lang_str}")

    if stats["by_law_type"]:
        print("\nBy law type:")
        print(format_count_dict(stats["by_law_type"]))

    if stats["by_domain"]:
        print("\nBy domain:")
        print(format_count_dict(stats["by_domain"]))


def print_library_stats(stats: Dict):
    """Print statistics for library collection."""
    print("\nCollection: library (Case Law)")
    print("-" * 40)
    print(f"Total vectors: {stats['total']:,}")

    if stats["by_language"]:
        lang_str = " | ".join(
            f"{lang.upper()}={count:,}"
            for lang, count in sorted(stats["by_language"].items())
        )
        print(f"By language: {lang_str}")

    if stats["by_court"]:
        print("\nBy court:")
        print(format_count_dict(stats["by_court"]))

    if stats["by_source"]:
        print("\nBy source:")
        print(format_count_dict(stats["by_source"]))

    if stats["by_chunk_type"]:
        print("\nBy chunk type:")
        print(format_count_dict(stats["by_chunk_type"]))

    if stats["by_year"]:
        # Group by decade
        years = sorted(stats["by_year"].keys(), reverse=True)
        if years:
            recent = sum(stats["by_year"][y] for y in years if y and y >= 2020)
            mid = sum(stats["by_year"][y] for y in years if y and 2015 <= y < 2020)
            older = sum(stats["by_year"][y] for y in years if y and 2010 <= y < 2015)
            old = sum(stats["by_year"][y] for y in years if y and y < 2010)

            print("\nBy period:")
            if recent:
                print(f"  2020-present: {recent:,}")
            if mid:
                print(f"  2015-2019: {mid:,}")
            if older:
                print(f"  2010-2014: {older:,}")
            if old:
                print(f"  Before 2010: {old:,}")


def main():
    print("=" * 55)
    print("KERBERUS Embedding Status")
    print("=" * 55)

    # Connect to Qdrant
    try:
        qdrant = QdrantManager()
    except Exception as e:
        print(f"\nError connecting to Qdrant: {e}")
        print("Make sure Qdrant is running (docker compose up -d)")
        sys.exit(1)

    # Get and print stats for each collection
    codex_stats = get_collection_stats(qdrant, "codex")
    print_codex_stats(codex_stats)

    library_stats = get_collection_stats(qdrant, "library")
    print_library_stats(library_stats)

    # Summary
    total = codex_stats["total"] + library_stats["total"]
    print("\n" + "=" * 55)
    print(f"Total vectors across all collections: {total:,}")
    print("=" * 55)


if __name__ == "__main__":
    main()
