#!/usr/bin/env python3
"""
Reset Qdrant Vector Collections.

This script DELETES existing collections to allow recreation with the new
Hybrid Search schema (Dense + Sparse vectors).

Usage:
    python scripts/reset_vectors.py
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.vector_db import QdrantManager

def main():
    print("WARNING: This will DELETE all vector data.")
    confirm = input("Are you sure? (y/N): ")
    if confirm.lower() != 'y':
        print("Aborted.")
        return

    manager = QdrantManager()
    
    collections = ["codex", "library"]
    
    for name in collections:
        try:
            print(f"Deleting collection: {name}...")
            manager.client.delete_collection(name)
            print(f"✅ Deleted {name}")
        except Exception as e:
            print(f"⚠️ Could not delete {name}: {e}")

    print("\nReset complete. Now run:")
    print("  make embed-fedlex")
    print("  make embed-decisions")

if __name__ == "__main__":
    main()
