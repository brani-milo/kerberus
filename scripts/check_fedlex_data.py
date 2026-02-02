
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.database.vector_db import QdrantManager
from qdrant_client.http import models

def check_fedlex():
    db = QdrantManager(collection_name="library")
    
    # Count fedlex items
    count_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="source",
                match=models.MatchValue(value="fedlex")
            )
        ]
    )
    
    count = db.client.count(
        collection_name="library",
        count_filter=count_filter
    )
    
    print(f"Total 'fedlex' documents in 'library': {count.count}")
    
    if count.count > 0:
        # Fetch one to see what it looks like
        results = db.search_hybrid(
            text="switzerland", # dummy query
            limit=1,
            filters={"sources": ["fedlex"]}
        )
        if results:
            print("\nSample Document:")
            print(results[0].payload)
            
if __name__ == "__main__":
    check_fedlex()
