#!/usr/bin/env python3
"""
Reset Qdrant database by deleting and recreating collections.
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.vector_db import QdrantManager
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    qdrant = QdrantManager()
    
    collections = ["codex", "library"]
    
    for col in collections:
        logger.info(f"Deleting collection: {col}")
        try:
            qdrant.client.delete_collection(col)
            logger.info(f"✅ Deleted {col}")
        except Exception as e:
            logger.warning(f"Could not delete {col} (might not exist): {e}")

    logger.info("Qdrant database emptied.")

if __name__ == "__main__":
    main()
