from typing import List, Dict, Optional, Union
import logging

from ..embedder.bge_embedder import get_embedder
from ..database.vector_db import QdrantManager

logger = logging.getLogger(__name__)

class HybridSearchEngine:
    """
    Search engine combining Dense (Vector) and Sparse (Lexical) retrieval.
    """

    def __init__(self, collection_name: str = "library"):
        self.embedder = get_embedder()
        self.qdrant = QdrantManager()
        self.collection_name = collection_name

    def search(
        self,
        query: str,
        limit: int = 10,
        filters: Optional[Dict] = None,
        alpha: float = 0.5  # 0.0 = Pure Lexical, 1.0 = Pure Dense
    ) -> List[Dict]:
        """
        Perform hybrid search.

        Args:
            query: User search query
            limit: Number of results
            filters: Metadata filters
            alpha: Weighting between Dense (Semantic) and Sparse (Keyword) search.
                   Currently Qdrant uses score fusion or individual query components.
                   For true hybrid RRF (Reciprocal Rank Fusion), we would query both and merge.
                   
                   Here we use Qdrant's 'prefetch' system or simple hybrid if supported.
                   
                   Since we are using named vectors: 'dense' and 'sparse'.
                   We can execute a compound query.
        """
        # 1. Embed query (returns {'dense': [...], 'sparse': {...}})
        # Use sync method directly since this search method is synchronous
        query_vectors = self.embedder._encode_single(query)

        dense_vector = query_vectors['dense']
        sparse_vector = query_vectors['sparse']

        return self.qdrant.search_hybrid(
            collection_name=self.collection_name,
            dense_vector=dense_vector,
            sparse_vector=sparse_vector,
            limit=limit,
            filters=filters
        )
