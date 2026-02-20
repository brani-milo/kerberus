#!/usr/bin/env python3
"""
Upload clean Codex embeddings to Qdrant.

This script:
1. Connects to Qdrant server
2. Deletes existing 'codex' collection
3. Creates new 'codex' collection with proper schema
4. Uploads all embeddings from codex_clean/

Usage:
    # On server after copying codex_clean/ directory:
    python scripts/upload_codex_to_qdrant.py

Environment variables:
    QDRANT_HOST: Qdrant server host (default: localhost)
    QDRANT_PORT: Qdrant server port (default: 6333)
"""

import json
import os
import sys
from pathlib import Path
from typing import List, Dict
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    SparseVectorParams, SparseIndexParams, SparseVector
)

# Paths
EMBEDDINGS_DIR = Path(__file__).parent.parent / "data" / "embeddings" / "codex_clean"

# Qdrant config
QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
COLLECTION_NAME = "codex"
BATCH_SIZE = 100  # Points per batch


def main():
    print("=" * 70)
    print("UPLOAD CLEAN CODEX TO QDRANT")
    print("=" * 70)
    print(f"Qdrant: {QDRANT_HOST}:{QDRANT_PORT}")
    print(f"Source: {EMBEDDINGS_DIR}")
    print()

    # Check source exists
    if not EMBEDDINGS_DIR.exists():
        print(f"ERROR: Source directory not found: {EMBEDDINGS_DIR}")
        print("Run prepare_clean_codex.py first to generate clean embeddings.")
        sys.exit(1)

    # Connect to Qdrant
    print("1. Connecting to Qdrant...")
    client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=120)
    print("   Connected!")

    # Delete existing collection
    print("\n2. Deleting existing 'codex' collection...")
    try:
        client.delete_collection(collection_name=COLLECTION_NAME)
        print("   Deleted!")
    except Exception as e:
        print(f"   Collection doesn't exist or error: {e}")

    # Create new collection
    print("\n3. Creating new 'codex' collection...")
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config={
            "dense": VectorParams(
                size=1024,  # BGE-M3 dense vector size
                distance=Distance.COSINE
            )
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams(
                index=SparseIndexParams(on_disk=False)
            )
        }
    )
    print("   Created with dense (1024) + sparse vectors!")

    # Upload embeddings
    print("\n4. Uploading embeddings...")
    embedding_files = sorted(EMBEDDINGS_DIR.glob("embeddings_*.json"))
    print(f"   Found {len(embedding_files)} embedding files")

    total_uploaded = 0
    errors = 0

    for i, filepath in enumerate(embedding_files):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"   WARNING: Skipping corrupted file {filepath.name}")
            errors += 1
            continue

        # Convert to Qdrant points
        points = []
        for entry in data:
            try:
                vector_data = entry.get('vector', {})

                # Handle both old format (direct list) and new format (dict with dense/sparse)
                if isinstance(vector_data, list):
                    dense_vector = vector_data
                    sparse_indices = []
                    sparse_values = []
                else:
                    dense_vector = vector_data.get('dense', [])
                    sparse_data = vector_data.get('sparse', {})
                    sparse_indices = sparse_data.get('indices', [])
                    sparse_values = sparse_data.get('values', [])

                # Build vector dict
                vectors = {"dense": dense_vector}
                if sparse_indices and sparse_values:
                    vectors["sparse"] = SparseVector(
                        indices=sparse_indices,
                        values=sparse_values
                    )

                point = PointStruct(
                    id=entry.get('id', str(total_uploaded)),
                    vector=vectors,
                    payload=entry.get('payload', {})
                )
                points.append(point)
            except Exception as e:
                errors += 1
                continue

        # Upload in batches
        for batch_start in range(0, len(points), BATCH_SIZE):
            batch = points[batch_start:batch_start + BATCH_SIZE]
            try:
                client.upsert(
                    collection_name=COLLECTION_NAME,
                    points=batch
                )
                total_uploaded += len(batch)
            except Exception as e:
                print(f"   ERROR uploading batch: {e}")
                errors += len(batch)

        if (i + 1) % 25 == 0:
            print(f"   Processed {i + 1}/{len(embedding_files)} files, {total_uploaded} points uploaded...")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total points uploaded: {total_uploaded}")
    print(f"Errors: {errors}")

    # Verify
    print("\n5. Verifying collection...")
    info = client.get_collection(collection_name=COLLECTION_NAME)
    print(f"   Points in collection: {info.points_count}")
    print(f"   Status: {info.status}")

    print("\n" + "=" * 70)
    print("DONE!")
    print("=" * 70)


if __name__ == "__main__":
    main()
