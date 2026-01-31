#!/usr/bin/env python3
"""
Test Hybrid Search.

This script tests the HybridSearchEngine by running a few sample queries.
"""

import sys
from pathlib import Path
import logging

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.search.hybrid_search import HybridSearchEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    engine = HybridSearchEngine(collection_name="library")
    
    queries = [
        "Art. 337 OR",
        "unfair dismissal",
        "Kündigung zur Unzeit"
    ]
    
    print("\n" + "="*50)
    print("HYBRID SEARCH TEST")
    print("="*50)
    
    for query in queries:
        print(f"\n🔍 Query: '{query}'")
        try:
            results = engine.search(query, limit=3)
            
            if not results:
                print("  No results found.")
            
            for i, res in enumerate(results):
                payload = res.get('payload', {})
                preview = payload.get('text_preview', 'No preview')
                score = res.get('score', 0.0)
                art_id = payload.get('id', 'Unknown ID')
                
                print(f"  {i+1}. [{score:.4f}] {art_id}")
                print(f"     {preview[:100]}...")
                
        except Exception as e:
            print(f"  ❌ Error: {e}")

if __name__ == "__main__":
    main()
