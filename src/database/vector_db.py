"""
Qdrant vector database manager for KERBERUS.
"""

import logging
from typing import List, Dict, Optional
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
import uuid

logger = logging.getLogger(__name__)


class QdrantManager:
    """
    Qdrant vector database manager.

    Manages collections:
    - codex: Swiss laws and statutes
    - library: Case law and jurisprudence
    - dossier_user_{uuid}: Per-user document collections
    - dossier_firm_{uuid}: Per-firm shared collections
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6333,
        api_key: Optional[str] = None
    ):
        """
        Initialize Qdrant client.

        Args:
            host: Qdrant server host
            port: Qdrant server port
            api_key: Optional API key for authentication
        """
        self.host = host
        self.port = port
        self.client = QdrantClient(host=host, port=port, api_key=api_key)

        logger.info(f"Connected to Qdrant at {host}:{port}")

    def create_collection(
        self,
        collection_name: str,
        vector_size: int = 1024,
        distance: Distance = Distance.COSINE
    ):
        """
        Create a new collection.

        Args:
            collection_name: Name of collection
            vector_size: Embedding dimension (1024 for BGE-M3)
            distance: Distance metric (Cosine recommended)
        """
        try:
            # Check if collection exists
            collections = self.client.get_collections().collections
            if any(col.name == collection_name for col in collections):
                logger.info(f"Collection '{collection_name}' already exists")
                return

            # Create collection
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=vector_size,
                    distance=distance
                )
            )

            logger.info(f"✅ Created collection: {collection_name}")

        except Exception as e:
            logger.error(f"Failed to create collection: {e}", exc_info=True)
            raise

    def upsert_points(
        self,
        collection_name: str,
        points: List[Dict]
    ):
        """
        Insert or update points in collection.

        Args:
            collection_name: Target collection
            points: List of dicts with 'id', 'vector', 'payload' keys
        """
        try:
            # Convert to PointStruct format
            point_structs = [
                PointStruct(
                    id=point.get('id', str(uuid.uuid4())),
                    vector=point['vector'],
                    payload=point.get('payload', {})
                )
                for point in points
            ]

            # Upsert
            self.client.upsert(
                collection_name=collection_name,
                points=point_structs
            )

            logger.info(f"Upserted {len(points)} points to {collection_name}")

        except Exception as e:
            logger.error(f"Upsert failed: {e}", exc_info=True)
            raise

    async def search_async(
        self,
        collection_name: str,
        query_vector: List[float],
        limit: int = 50,
        score_threshold: Optional[float] = None,
        filters: Optional[Dict] = None
    ) -> List[Dict]:
        """
        Search collection asynchronously.

        Args:
            collection_name: Collection to search
            query_vector: Query embedding
            limit: Number of results
            score_threshold: Minimum similarity score
            filters: Metadata filters (e.g., {'jurisdiction': 'federal'})

        Returns:
            List of search results with scores
        """
        try:
            # Build filter if provided
            query_filter = None
            if filters:
                conditions = [
                    FieldCondition(
                        key=key,
                        match=MatchValue(value=value)
                    )
                    for key, value in filters.items()
                ]
                query_filter = Filter(must=conditions)

            # Search
            results = self.client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=limit,
                score_threshold=score_threshold,
                query_filter=query_filter
            )

            # Convert to dict format
            formatted_results = [
                {
                    'id': result.id,
                    'score': result.score,
                    'payload': result.payload,
                    'embedding': query_vector  # Include for MMR
                }
                for result in results
            ]

            return formatted_results

        except Exception as e:
            logger.error(f"Search failed for {collection_name}: {e}", exc_info=True)
            return []


def init_qdrant_collections():
    """
    Initialize default Qdrant collections.
    Called by scripts/init_databases.py
    """
    try:
        manager = QdrantManager()

        # Create public collections
        manager.create_collection("codex", vector_size=1024)
        manager.create_collection("library", vector_size=1024)

        logger.info("✅ Qdrant collections initialized")

    except Exception as e:
        logger.error(f"Failed to initialize Qdrant: {e}")
        raise
