#!/usr/bin/env python3
"""
Fedlex batch parser - parses all downloaded PDFs into structured JSON.

Usage:
    python scripts/parse_fedlex.py --test           # Test mode: parse first 3 PDFs only
    python scripts/parse_fedlex.py                  # Parse all PDFs
    python scripts/parse_fedlex.py --language de   # Parse only German PDFs
    python scripts/parse_fedlex.py --sr 220        # Parse specific SR number only

Output:
    data/parsed/fedlex/SR_220_de.json    # All articles from OR in German
    data/parsed/fedlex/SR_311_0_de.json  # All articles from StGB in German
    ...
"""

import sys
import argparse
import logging
import json
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.parsers.fedlex_parser import FedlexParser


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    log_dir = Path('logs/parsers')
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_dir / 'fedlex_parser.log'),
            logging.StreamHandler()
        ]
    )


def extract_sr_from_filename(filename: str) -> str:
    """
    Extract SR number from filename.

    Examples:
        SR_220_de.pdf -> 220
        SR_311_0_de.pdf -> 311.0
        SR_172_021_de.pdf -> 172.021
    """
    # Remove extension and language suffix
    stem = filename.replace('.pdf', '')
    parts = stem.split('_')

    # Remove 'SR' prefix and language suffix
    if parts[0] == 'SR':
        parts = parts[1:]
    if parts[-1] in ('de', 'fr', 'it'):
        parts = parts[:-1]

    # Rejoin with dots
    return '.'.join(parts)


def main():
    parser_cli = argparse.ArgumentParser(
        description='Parse Fedlex PDFs into structured JSON',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/parse_fedlex.py --test          # Test with 3 PDFs
    python scripts/parse_fedlex.py --language de  # German only
    python scripts/parse_fedlex.py --sr 220       # Parse OR only
        """
    )
    parser_cli.add_argument(
        '--test', action='store_true',
        help='Test mode: parse first 3 PDFs only'
    )
    parser_cli.add_argument(
        '--language', '-l', choices=['de', 'fr', 'it'],
        help='Parse only specific language'
    )
    parser_cli.add_argument(
        '--sr',
        help='Parse only specific SR number (e.g., 220, 311.0)'
    )
    parser_cli.add_argument(
        '--verbose', '-v', action='store_true',
        help='Verbose logging'
    )

    args = parser_cli.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    # Initialize parser
    fedlex_parser = FedlexParser()

    # Setup directories
    fedlex_dir = Path('data/fedlex')
    output_dir = Path('data/parsed/fedlex')
    output_dir.mkdir(parents=True, exist_ok=True)

    # Determine languages to process
    languages = [args.language] if args.language else ['de', 'fr', 'it']

    # Collect PDF files to process
    pdf_files = []
    for lang in languages:
        lang_dir = fedlex_dir / lang
        if lang_dir.exists():
            for pdf in sorted(lang_dir.glob('SR_*.pdf')):
                # Filter by SR number if specified
                if args.sr:
                    sr_from_file = extract_sr_from_filename(pdf.name)
                    if sr_from_file != args.sr:
                        continue

                pdf_files.append((pdf, lang))

    if not pdf_files:
        print("No PDF files found to parse.")
        print(f"Looked in: {fedlex_dir}")
        return 1

    if args.test:
        pdf_files = pdf_files[:3]
        print(f"  TEST MODE: Parsing only {len(pdf_files)} PDFs")
        print()

    # Print header
    print("=" * 70)
    print(" FEDLEX PDF PARSER")
    print("=" * 70)
    print(f" Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" PDFs to parse: {len(pdf_files)}")
    print(f" Languages: {', '.join(languages)}")
    print(f" Output: {output_dir}")
    print("=" * 70)
    print()

    # Statistics
    stats = {
        "success": 0,
        "error": 0,
        "total_articles": 0,
        "total_chunks": 0,
        "by_language": {lang: {"files": 0, "articles": 0} for lang in languages}
    }

    # Process each PDF
    for i, (pdf_path, lang) in enumerate(pdf_files, 1):
        # Extract SR number
        sr_number = extract_sr_from_filename(pdf_path.name)

        print(f"[{i}/{len(pdf_files)}] {pdf_path.name}", end=" ", flush=True)

        try:
            # Parse PDF
            articles = fedlex_parser.parse_pdf(pdf_path, sr_number, lang)

            if articles:
                # Save to JSON
                output_file = output_dir / f"{pdf_path.stem}.json"

                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(articles, f, indent=2, ensure_ascii=False)

                # Update statistics
                stats["success"] += 1
                stats["total_articles"] += len(articles)

                # Count partial articles (chunks)
                chunks = sum(1 for a in articles if a.get("is_partial"))
                full_articles = len(articles) - chunks
                stats["total_chunks"] += chunks

                stats["by_language"][lang]["files"] += 1
                stats["by_language"][lang]["articles"] += len(articles)

                print(f"-> {len(articles)} articles ({chunks} chunks)")
            else:
                stats["error"] += 1
                print("-> No articles found")
                logger.warning(f"No articles found in {pdf_path.name}")

        except Exception as e:
            stats["error"] += 1
            print(f"-> ERROR: {e}")
            logger.exception(f"Failed to parse {pdf_path.name}")

    # Print summary
    print()
    print("=" * 70)
    print(" PARSING COMPLETE")
    print("=" * 70)
    print(f" Success: {stats['success']}")
    print(f" Errors:  {stats['error']}")
    print(f" Total articles: {stats['total_articles']}")
    print(f" Total chunks (long articles split): {stats['total_chunks']}")
    print()

    # Per-language stats
    print(" By language:")
    for lang in languages:
        lang_stats = stats["by_language"][lang]
        if lang_stats["files"] > 0:
            flag = {"de": "DE", "fr": "FR", "it": "IT"}.get(lang, lang)
            print(f"   {flag}: {lang_stats['files']} files, {lang_stats['articles']} articles")

    print()

    # Show sample output files
    output_files = sorted(output_dir.glob('*.json'))
    if output_files:
        print(f" Output files saved to: {output_dir}/")
        for f in output_files[:5]:
            # Get file size
            size_kb = f.stat().st_size // 1024
            print(f"   {f.name} ({size_kb} KB)")
        if len(output_files) > 5:
            print(f"   ... and {len(output_files) - 5} more files")

    print("=" * 70)

    return 0 if stats["error"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
