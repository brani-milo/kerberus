#!/usr/bin/env python3
"""
Import embeddings from JSON files into local Qdrant.
Run this on the server after downloading embeddings from Modal.

Usage:
    QDRANT_HOST=127.0.0.1 python scripts/import_embeddings_local.py --collection library
"""

import os
import sys
import argparse
import time
from pathlib import Path

# Ensure we can import from src/
sys.path.insert(0, str(Path(__file__).parent.parent))

import orjson
from src.database.vector_db import QdrantManager


def import_embeddings(collection: str, embeddings_dir: Path, fast_mode: bool = True):
    """Import all embedding files from a directory into Qdrant."""
    import uuid
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct, SparseVector

    qdrant_host = os.environ.get("QDRANT_HOST", "localhost")
    qdrant_port = int(os.environ.get("QDRANT_PORT", 6333))

    print(f"Connecting to Qdrant at {qdrant_host}:{qdrant_port}...", flush=True)

    if fast_mode:
        # Direct client connection for faster imports (wait=False)
        client = QdrantClient(host=qdrant_host, port=qdrant_port, timeout=300)
        print(f"Connected! (fast mode - async writes)", flush=True)
    else:
        # Use QdrantManager for safer imports (wait=True)
        qdrant = QdrantManager()
        qdrant.create_collection(collection, vector_size=1024)
        print(f"Connected! (safe mode - sync writes)", flush=True)

    # Find all embedding files
    json_files = sorted(list(embeddings_dir.rglob("embeddings_*.json")))
    print(f"Found {len(json_files)} embedding files in {embeddings_dir}", flush=True)

    if not json_files:
        print("No embedding files found!", flush=True)
        return

    total_imported = 0
    total_errors = 0
    start_time = time.time()

    for i, json_file in enumerate(json_files):
        try:
            with open(json_file, "rb") as f:
                points = orjson.loads(f.read())

            if fast_mode:
                # Convert to PointStruct with UUID conversion
                qdrant_points = []
                for p in points:
                    raw_id = p.get("id", "")
                    try:
                        struct_id = str(uuid.UUID(str(raw_id)))
                    except (ValueError, TypeError):
                        struct_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(raw_id)))

                    vector_data = p["vector"]
                    if isinstance(vector_data, dict) and "sparse" in vector_data:
                        sparse_raw = vector_data["sparse"]
                        if isinstance(sparse_raw, dict):
                            indices = [int(k) for k in sparse_raw.keys()]
                            values = list(sparse_raw.values())
                            vector_data["sparse"] = SparseVector(indices=indices, values=values)

                    payload = p.get("payload", {})
                    payload["_original_id"] = raw_id

                    qdrant_points.append(PointStruct(
                        id=struct_id, vector=vector_data, payload=payload
                    ))

                # Use wait=True in safe mode to avoid overwhelming Qdrant
                client.upsert(collection_name=collection, points=qdrant_points, wait=not fast_mode)

                # In safe mode, add a small delay every 10 files
                if not fast_mode and (i + 1) % 10 == 0:
                    time.sleep(1)
            else:
                qdrant.upsert_points(collection, points)

            total_imported += len(points)

            if (i + 1) % 25 == 0:
                elapsed = time.time() - start_time
                rate = total_imported / elapsed if elapsed > 0 else 0
                remaining = len(json_files) - (i + 1)
                eta = remaining * (elapsed / (i + 1)) if i > 0 else 0
                print(f"Progress: {i+1}/{len(json_files)} files, {total_imported:,} pts, "
                      f"{rate:.0f} pts/sec, ETA: {eta/60:.1f}min", flush=True)

        except Exception as e:
            total_errors += 1
            print(f"Error {json_file.name}: {e}", flush=True)

    # Final sync to ensure all writes complete
    if fast_mode:
        print("Waiting for writes to complete...", flush=True)
        time.sleep(5)  # Give Qdrant time to process

    elapsed = time.time() - start_time
    print(f"\nDone! Imported {total_imported:,} points in {elapsed:.0f}s "
          f"({total_errors} errors)", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Import embeddings to Qdrant")
    parser.add_argument("--collection", required=True,
                        help="Collection to import into (codex or library)")
    parser.add_argument("--embeddings-dir",
                        help="Directory containing embedding files")
    parser.add_argument("--safe", action="store_true",
                        help="Use safe mode (wait=True, slower but won't crash Qdrant)")
    args = parser.parse_args()

    # Default embeddings directory based on collection
    if args.embeddings_dir:
        embeddings_dir = Path(args.embeddings_dir)
    else:
        # Try common locations
        possible_paths = [
            Path(f"/data/embeddings/json_embeddings/{args.collection}"),
            Path(__file__).parent.parent / "data" / "embeddings" / args.collection,
        ]
        embeddings_dir = None
        for p in possible_paths:
            if p.exists():
                embeddings_dir = p
                break

        if not embeddings_dir:
            print(f"Could not find embeddings directory. Tried: {possible_paths}")
            sys.exit(1)

    # In safe mode, use wait=True (fast_mode=False)
    import_embeddings(args.collection, embeddings_dir, fast_mode=not args.safe)


if __name__ == "__main__":
    main()
