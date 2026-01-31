"""
Hybrid Search Engine for KERBERUS.

Combines Dense (semantic) and Sparse (lexical/BM25-like) retrieval
using Reciprocal Rank Fusion (RRF) for optimal multilingual legal search.
"""

from typing import List, Dict, Optional
import logging

from ..embedder.bge_embedder import get_embedder
from ..database.vector_db import QdrantManager

logger = logging.getLogger(__name__)


class HybridSearchEngine:
    """
    Search engine combining Dense (Vector) and Sparse (Lexical) retrieval.

    Uses BGE-M3 for both dense (1024D) and sparse (lexical weights) embeddings,
    then fuses results using Qdrant's RRF (Reciprocal Rank Fusion).

    RRF Formula: score = Σ 1/(k + rank_i) for each retrieval method
    This is more robust than linear interpolation (alpha weighting) because
    it handles different score scales naturally.
    """

    def __init__(self, collection_name: str = "library"):
        """
        Initialize hybrid search engine.

        Args:
            collection_name: Default collection to search (codex, library, or dossier_*)
        """
        self.embedder = get_embedder()
        self.qdrant = QdrantManager()
        self.collection_name = collection_name

    def search(
        self,
        query: str,
        limit: int = 10,
        filters: Optional[Dict] = None,
        collection_name: Optional[str] = None,
        multilingual: bool = False
    ) -> List[Dict]:
        """
        Perform hybrid search combining semantic and lexical retrieval.

        Args:
            query: User search query (supports DE/FR/IT)
            limit: Number of results to return
            filters: Metadata filters (e.g., {'year_range': {'min': 2020, 'max': 2024}})
            collection_name: Override default collection
            multilingual: If True, use dense-only search for better cross-language results.
                         Hybrid search (default) favors same-language matches due to lexical component.

        Returns:
            List of search results with scores and payloads

        Example:
            >>> engine = HybridSearchEngine(collection_name="codex")
            >>> # Hybrid search (default) - good for same-language queries
            >>> results = engine.search("licenziamento immediato", limit=5)
            >>> # Multilingual search - better for cross-language queries
            >>> results = engine.search("quali sono le lingue nazionali", limit=5, multilingual=True)
        """
        target_collection = collection_name or self.collection_name

        # Generate dense + sparse embeddings in single call
        query_vectors = self.embedder._encode_single(query)

        if multilingual:
            # Dense-only search for better cross-language results
            # Semantic similarity works across languages, lexical doesn't
            return self.qdrant.search_dense(
                collection_name=target_collection,
                dense_vector=query_vectors['dense'],
                limit=limit,
                filters=filters
            )
        else:
            # Hybrid search with RRF fusion (default)
            # Better recall but favors same-language matches
            return self.qdrant.search_hybrid(
                collection_name=target_collection,
                dense_vector=query_vectors['dense'],
                sparse_vector=query_vectors['sparse'],
                limit=limit,
                filters=filters
            )

    def search_with_vectors(
        self,
        dense_vector: List[float],
        sparse_vector: Dict[str, float],
        limit: int = 10,
        filters: Optional[Dict] = None,
        collection_name: Optional[str] = None
    ) -> List[Dict]:
        """
        Search with pre-computed vectors (useful when reusing embeddings).

        Args:
            dense_vector: 1024-dimensional dense embedding
            sparse_vector: Lexical weights dict {token_id: weight}
            limit: Number of results
            filters: Metadata filters
            collection_name: Override default collection

        Returns:
            List of search results
        """
        target_collection = collection_name or self.collection_name

        return self.qdrant.search_hybrid(
            collection_name=target_collection,
            dense_vector=dense_vector,
            sparse_vector=sparse_vector,
            limit=limit,
            filters=filters
        )
