#!/usr/bin/env python3
"""
Local GPU Embedding for KERBERUS.

Generates BGE-M3 embeddings on a local machine with NVIDIA GPU.
Alternative to Modal for users with their own GPU infrastructure.

Requirements:
    - NVIDIA GPU with 8GB+ VRAM (16GB+ recommended)
    - CUDA toolkit installed
    - pip install torch transformers FlagEmbedding

Usage:
    python scripts/embed_local.py --collection codex
    python scripts/embed_local.py --collection library --court CH_BGer
    python scripts/embed_local.py --collection codex --device cpu  # CPU fallback (slow)
    python scripts/embed_local.py --collection codex --batch-size 8  # Reduce for low VRAM

Output:
    Embeddings are saved to data/embeddings/{collection}/*.json
    Then import to Qdrant with: python scripts/import_embeddings_local.py
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional
import time

# Ensure we can import from src/
sys.path.insert(0, str(Path(__file__).parent.parent))


def load_bge_model(device: str = "cuda"):
    """Load BGE-M3 model."""
    try:
        from FlagEmbedding import BGEM3FlagModel
        import torch
    except ImportError:
        print("Missing dependencies. Install with:")
        print("  pip install torch transformers FlagEmbedding peft sentence-transformers")
        sys.exit(1)

    if device == "cuda" and not torch.cuda.is_available():
        print("CUDA not available. Falling back to CPU (will be slow).")
        device = "cpu"

    print(f"Loading BGE-M3 on {device}...")
    if device == "cuda":
        print(f"  GPU: {torch.cuda.get_device_name(0)}")
        print(f"  VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB")

    model = BGEM3FlagModel(
        "BAAI/bge-m3",
        use_fp16=(device == "cuda"),
        device=device
    )

    # Warmup
    _ = model.encode("Warmup", max_length=512)
    print("BGE-M3 loaded and ready!")

    return model


def embed_texts(model, texts: List[str], batch_size: int = 32) -> List[Dict]:
    """Embed a list of texts and return dense + sparse vectors."""
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        max_length=2048,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False
    )

    results = []
    for i in range(len(texts)):
        dense = embeddings["dense_vecs"][i].tolist()

        # Sparse: convert to {token_id: weight} format
        sparse = {}
        if "lexical_weights" in embeddings:
            sparse_data = embeddings["lexical_weights"][i]
            if hasattr(sparse_data, "items"):
                for token_id, weight in sparse_data.items():
                    if weight > 0:
                        sparse[str(token_id)] = float(weight)

        results.append({
            "dense": dense,
            "sparse": sparse
        })

    return results


def chunk_text(text: str, max_length: int = 1500, overlap: int = 200) -> List[str]:
    """Split text into overlapping chunks."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + max_length
        chunk = text[start:end]

        # Try to break at sentence boundary
        if end < len(text):
            for sep in [". ", ".\n", "\n\n", "\n", " "]:
                last_sep = chunk.rfind(sep)
                if last_sep > max_length * 0.5:
                    chunk = chunk[:last_sep + len(sep)]
                    break

        chunks.append(chunk.strip())
        start += len(chunk) - overlap

    return [c for c in chunks if c]


def process_fedlex(model, input_dir: Path, output_dir: Path, batch_size: int = 32):
    """Process Fedlex (codex) articles."""
    import orjson

    output_dir.mkdir(parents=True, exist_ok=True)

    json_files = sorted(list(input_dir.glob("*.json")))
    print(f"Found {len(json_files)} Fedlex files in {input_dir}")

    if not json_files:
        print("No files found!")
        return

    total_articles = 0
    total_chunks = 0
    start_time = time.time()

    # Process in file batches
    file_batch_size = 100
    for file_idx in range(0, len(json_files), file_batch_size):
        batch_files = json_files[file_idx:file_idx + file_batch_size]
        batch_num = file_idx // file_batch_size

        # Collect all articles from this batch
        all_chunks = []
        chunk_metadata = []

        for json_file in batch_files:
            try:
                with open(json_file, "rb") as f:
                    articles = orjson.loads(f.read())

                if not isinstance(articles, list):
                    articles = [articles]

                for article in articles:
                    total_articles += 1
                    text = article.get("text", "")
                    if not text or len(text) < 10:
                        continue

                    chunks = chunk_text(text)
                    for chunk_idx, chunk in enumerate(chunks):
                        all_chunks.append(chunk)
                        chunk_metadata.append({
                            "article": article,
                            "chunk_idx": chunk_idx,
                            "total_chunks": len(chunks)
                        })

            except Exception as e:
                print(f"Error loading {json_file.name}: {e}")

        if not all_chunks:
            continue

        # Embed all chunks
        print(f"Embedding batch {batch_num}: {len(all_chunks)} chunks from {len(batch_files)} files...")
        embeddings = embed_texts(model, all_chunks, batch_size)

        # Build output points
        points = []
        for i, (emb, meta) in enumerate(zip(embeddings, chunk_metadata)):
            article = meta["article"]
            chunk_idx = meta["chunk_idx"]

            doc_id = article.get("id", f"article_{total_chunks + i}")
            point_id = f"{doc_id}_chunk_{chunk_idx}"

            points.append({
                "id": point_id,
                "vector": emb,
                "payload": {
                    "doc_id": doc_id,
                    "chunk_index": chunk_idx,
                    "total_chunks": meta["total_chunks"],
                    "language": article.get("language", "de"),
                    "sr_number": article.get("sr_number", ""),
                    "law_title": article.get("law_title", article.get("title", "")),
                    "article_number": article.get("article_number", ""),
                    "enacted_date": article.get("enacted_date", ""),
                    "source": "fedlex",
                    "text_preview": all_chunks[i][:500]
                }
            })

        total_chunks += len(points)

        # Save batch
        output_file = output_dir / f"embeddings_{batch_num:05d}.json"
        with open(output_file, "wb") as f:
            f.write(orjson.dumps(points))

        elapsed = time.time() - start_time
        rate = total_chunks / elapsed if elapsed > 0 else 0
        print(f"  Saved {len(points)} points to {output_file.name} "
              f"(total: {total_chunks}, {rate:.0f} pts/sec)")

    print(f"\nDone! Processed {total_articles} articles -> {total_chunks} chunks")


def process_library(model, input_dir: Path, output_dir: Path,
                    court: Optional[str] = None, batch_size: int = 32,
                    courts_filter: Optional[List[str]] = None):
    """Process court decisions (library).

    Args:
        model: BGE-M3 model
        input_dir: Directory containing court subdirectories
        output_dir: Output directory for embeddings
        court: Single specific court to process (overrides courts_filter)
        batch_size: Embedding batch size
        courts_filter: List of courts to process (if court is not specified)
    """
    import orjson

    output_dir.mkdir(parents=True, exist_ok=True)

    # Find court directories
    if court:
        court_dirs = [input_dir / court] if (input_dir / court).exists() else []
    elif courts_filter:
        court_dirs = [input_dir / c for c in courts_filter if (input_dir / c).exists()]
    else:
        court_dirs = [d for d in input_dir.iterdir() if d.is_dir()]

    if not court_dirs:
        print(f"No court directories found in {input_dir}")
        return

    for court_dir in court_dirs:
        court_name = court_dir.name
        court_output = output_dir / court_name
        court_output.mkdir(parents=True, exist_ok=True)

        json_files = sorted(list(court_dir.rglob("*.json")))
        print(f"\nProcessing {court_name}: {len(json_files)} files")

        if not json_files:
            continue

        total_chunks = 0
        start_time = time.time()

        # Process in file batches
        file_batch_size = 50
        for file_idx in range(0, len(json_files), file_batch_size):
            batch_files = json_files[file_idx:file_idx + file_batch_size]
            batch_num = file_idx // file_batch_size

            all_chunks = []
            chunk_metadata = []

            for json_file in batch_files:
                try:
                    with open(json_file, "rb") as f:
                        decision = orjson.loads(f.read())

                    # Get text from various possible fields
                    text = ""
                    for field in ["text", "regeste", "content", "sachverhalt", "erwägungen"]:
                        if field in decision and decision[field]:
                            text += str(decision[field]) + "\n\n"

                    if not text or len(text) < 50:
                        continue

                    chunks = chunk_text(text)
                    for chunk_idx, chunk in enumerate(chunks):
                        all_chunks.append(chunk)
                        chunk_metadata.append({
                            "decision": decision,
                            "chunk_idx": chunk_idx,
                            "total_chunks": len(chunks),
                            "file_name": json_file.stem
                        })

                except Exception as e:
                    print(f"Error loading {json_file.name}: {e}")

            if not all_chunks:
                continue

            # Embed
            print(f"  Batch {batch_num}: {len(all_chunks)} chunks...")
            embeddings = embed_texts(model, all_chunks, batch_size)

            # Build points
            points = []
            for i, (emb, meta) in enumerate(zip(embeddings, chunk_metadata)):
                decision = meta["decision"]
                chunk_idx = meta["chunk_idx"]
                file_name = meta["file_name"]

                doc_id = decision.get("id", file_name)
                point_id = f"{doc_id}_chunk_{chunk_idx}"

                points.append({
                    "id": point_id,
                    "vector": emb,
                    "payload": {
                        "doc_id": doc_id,
                        "chunk_index": chunk_idx,
                        "total_chunks": meta["total_chunks"],
                        "court": court_name,
                        "date": decision.get("date", ""),
                        "year": decision.get("year"),
                        "language": decision.get("language", ""),
                        "outcome": decision.get("outcome", ""),
                        "source": "federal",
                        "text_preview": all_chunks[i][:500]
                    }
                })

            total_chunks += len(points)

            # Save
            output_file = court_output / f"embeddings_{batch_num:05d}.json"
            with open(output_file, "wb") as f:
                f.write(orjson.dumps(points))

            elapsed = time.time() - start_time
            rate = total_chunks / elapsed if elapsed > 0 else 0
            print(f"    Saved {len(points)} points ({total_chunks} total, {rate:.0f} pts/sec)")

        print(f"  {court_name}: {total_chunks} chunks embedded")


FEDERAL_COURTS = [
    "CH_BGer",    # Federal Supreme Court
    "CH_BVGer",   # Federal Administrative Court
    "CH_BGE",     # Leading cases (BGE)
    "CH_BStGer",  # Federal Criminal Court
    "CH_BPatG",   # Federal Patent Court
    "CH_EDOEB",   # Data Protection Authority
]

CANTONAL_COURTS = [
    "ticino",     # Ticino cantonal courts
    # Add more cantonal courts here as they become available
]

ALL_COURTS = FEDERAL_COURTS + CANTONAL_COURTS


def main():
    parser = argparse.ArgumentParser(
        description="Local GPU embedding for KERBERUS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Embed all federal laws (codex/Fedlex)
  python scripts/embed_local.py --collection codex

  # Embed all federal court decisions
  python scripts/embed_local.py --collection federal

  # Embed a specific court
  python scripts/embed_local.py --collection library --court CH_BGer

  # List available courts
  python scripts/embed_local.py --list-courts
        """
    )
    parser.add_argument("--collection", choices=["codex", "federal", "library", "all"],
                        help="Collection to embed: codex (laws), federal (all federal courts), library (all courts), all")
    parser.add_argument("--court", help="Specific court (for library collection). Use --list-courts to see options.")
    parser.add_argument("--device", default="cuda", choices=["cuda", "cpu", "mps"],
                        help="Device to use (default: cuda)")
    parser.add_argument("--batch-size", type=int, default=32,
                        help="Batch size for embedding (reduce if OOM)")
    parser.add_argument("--input-dir", help="Input directory (overrides default)")
    parser.add_argument("--output-dir", help="Output directory (overrides default)")
    parser.add_argument("--list-courts", action="store_true",
                        help="List available courts and exit")
    args = parser.parse_args()

    # List courts mode
    if args.list_courts:
        print("Available courts for embedding:\n")
        print("Federal courts:")
        for court in FEDERAL_COURTS:
            print(f"  - {court}")
        print("\nCantonal courts:")
        for court in CANTONAL_COURTS:
            print(f"  - {court}")
        print("\nCollections:")
        print("  - codex    : Federal laws from Fedlex")
        print("  - federal  : All federal courts (CH_BGer, CH_BVGer, etc.)")
        print("  - library  : All courts including cantonal")
        print("  - all      : Both codex and library")
        return

    if not args.collection:
        parser.error("--collection is required (or use --list-courts)")

    # Default paths
    project_root = Path(__file__).parent.parent
    data_dir = project_root / "data"

    # Determine what to process based on collection type
    collections_to_process = []

    if args.collection == "all":
        collections_to_process = ["codex", "library"]
    elif args.collection in ["codex", "library"]:
        collections_to_process = [args.collection]
    elif args.collection == "federal":
        # Federal is a subset of library - only federal courts
        collections_to_process = ["federal"]

    # Load model
    model = load_bge_model(args.device)

    # Process each collection
    for collection in collections_to_process:
        print(f"\n{'='*60}")
        print(f"Processing: {collection}")
        print(f"{'='*60}")

        if collection == "codex":
            input_dir = Path(args.input_dir) if args.input_dir else data_dir / "parsed" / "fedlex"
            output_dir = Path(args.output_dir) if args.output_dir else data_dir / "embeddings" / "codex"

            if not input_dir.exists():
                print(f"Input directory not found: {input_dir}")
                print("Run the Fedlex scraper first: python scripts/scrapers/fedlex_scraper.py")
                continue

            process_fedlex(model, input_dir, output_dir, args.batch_size)

        elif collection in ["library", "federal"]:
            input_dir = Path(args.input_dir) if args.input_dir else data_dir / "parsed"
            output_dir = Path(args.output_dir) if args.output_dir else data_dir / "embeddings" / "library"

            if not input_dir.exists():
                print(f"Input directory not found: {input_dir}")
                print("Run the scrapers first.")
                continue

            # Determine which courts to process
            if args.court:
                courts_to_process = [args.court]
            elif collection == "federal":
                courts_to_process = FEDERAL_COURTS
            else:
                courts_to_process = None  # All courts

            process_library(model, input_dir, output_dir,
                          court=args.court if args.court else None,
                          batch_size=args.batch_size,
                          courts_filter=courts_to_process if not args.court else None)

    # Done - print summary
    print(f"\n{'='*60}")
    print("Embedding complete!")
    if "codex" in collections_to_process or args.collection == "codex":
        print(f"  Codex embeddings: {data_dir / 'embeddings' / 'codex'}")
    if any(c in ["library", "federal"] for c in collections_to_process):
        print(f"  Library embeddings: {data_dir / 'embeddings' / 'library'}")
    print(f"\nTo import to Qdrant:")
    if "codex" in collections_to_process:
        print(f"  QDRANT_HOST=localhost python scripts/import_embeddings_local.py --collection codex")
    if any(c in ["library", "federal"] for c in collections_to_process):
        print(f"  QDRANT_HOST=localhost python scripts/import_embeddings_local.py --collection library")


if __name__ == "__main__":
    main()
