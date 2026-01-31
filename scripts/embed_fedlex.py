#!/usr/bin/env python3
"""
Embed Fedlex law articles into the codex collection.

Usage:
    python scripts/embed_fedlex.py              # Embed all articles
    python scripts/embed_fedlex.py --test       # Test mode (10 articles)
    python scripts/embed_fedlex.py --verbose    # Verbose logging
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from collections import defaultdict

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.embedder import get_embedder, BatchEmbeddingProcessor
from src.database.vector_db import QdrantManager

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )


def load_fedlex_articles(data_dir: Path, limit: int = None) -> list:
    """
    Load all parsed Fedlex articles.

    Args:
        data_dir: Path to data/parsed/fedlex
        limit: Optional limit on number of articles

    Returns:
        List of article dicts
    """
    articles = []

    # Find all SR_*.json files
    json_files = sorted(data_dir.glob("SR_*.json"))

    logger.info(f"Found {len(json_files)} Fedlex JSON files")

    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                file_articles = json.load(f)

            # Each file contains a list of articles
            if isinstance(file_articles, list):
                articles.extend(file_articles)
            else:
                articles.append(file_articles)

            if limit and len(articles) >= limit:
                articles = articles[:limit]
                break

        except Exception as e:
            logger.error(f"Error loading {json_file}: {e}")

    logger.info(f"Loaded {len(articles)} articles from Fedlex")
    return articles


def build_fedlex_payload(article: dict) -> dict:
    """
    Build payload for Fedlex article.

    Args:
        article: Article dict from parsed JSON

    Returns:
        Payload dict for Qdrant
    """
    # Create text preview
    text = article.get("article_text", "")
    text_preview = ' '.join(text.split())[:200]
    if len(text) > 200:
        text_preview += "..."

    return {
        "id": article.get("id"),
        "base_id": article.get("base_id"),
        "sr_number": article.get("sr_number"),
        "sr_name": article.get("sr_name"),
        "abbreviation": article.get("abbreviation"),
        "abbreviations_all": article.get("abbreviations_all", {}),
        "article_number": article.get("article_number"),
        "article_title": article.get("article_title"),
        "hierarchy_path": article.get("hierarchy_path"),
        "part": article.get("part"),
        "title": article.get("title"),
        "chapter": article.get("chapter"),
        "section": article.get("section"),
        "law_type": article.get("law_type"),
        "domain": article.get("domain"),
        "subdomain": article.get("subdomain"),
        "language": article.get("language"),
        "source": article.get("source", "fedlex"),
        "is_partial": article.get("is_partial", False),
        "paragraph_number": article.get("paragraph_number"),
        "text_preview": text_preview
    }


def print_stats(stats: dict, articles: list):
    """Print embedding statistics."""
    print("\n" + "=" * 50)
    print("FEDLEX EMBEDDING COMPLETE")
    print("=" * 50)
    print(f"Embedded:  {stats['embedded']}")
    print(f"Skipped:   {stats['skipped']}")
    print(f"Errors:    {stats['errors']}")

    # Count by language
    lang_counts = defaultdict(int)
    for article in articles:
        lang = article.get("language", "unknown")
        lang_counts[lang] += 1

    print("\nArticles by language:")
    for lang, count in sorted(lang_counts.items()):
        print(f"  {lang.upper()}: {count}")

    # Count by law type
    type_counts = defaultdict(int)
    for article in articles:
        law_type = article.get("law_type", "unknown")
        type_counts[law_type] += 1

    print("\nArticles by law type:")
    for law_type, count in sorted(type_counts.items()):
        print(f"  {law_type}: {count}")

    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Embed Fedlex articles into codex collection")
    parser.add_argument("--test", action="store_true", help="Test mode (10 articles only)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument("--batch-size", type=int, default=32, help="Batch size for embedding")
    parser.add_argument("--no-skip", action="store_true", help="Don't skip existing documents")
    args = parser.parse_args()

    setup_logging(args.verbose)

    # Determine limit
    limit = 10 if args.test else None

    # Load articles
    data_dir = Path(__file__).parent.parent / "data" / "parsed" / "fedlex"
    if not data_dir.exists():
        logger.error(f"Data directory not found: {data_dir}")
        sys.exit(1)

    articles = load_fedlex_articles(data_dir, limit=limit)

    if not articles:
        logger.warning("No articles found to embed")
        sys.exit(0)

    # Initialize embedder and Qdrant
    logger.info("Initializing embedder...")
    embedder = get_embedder()

    logger.info("Connecting to Qdrant...")
    qdrant = QdrantManager()

    # Ensure collection exists
    qdrant.create_collection("codex", vector_size=1024)

    # Create processor
    processor = BatchEmbeddingProcessor(embedder, qdrant, "codex")

    # Process articles
    logger.info(f"Embedding {len(articles)} articles...")
    stats = processor.process_documents(
        documents=articles,
        text_field="article_text",
        id_field="id",
        payload_builder=build_fedlex_payload,
        batch_size=args.batch_size,
        skip_existing=not args.no_skip,
        show_progress=True
    )

    # Print results
    print_stats(stats, articles)


if __name__ == "__main__":
    main()
