"""
Qdrant vector database manager for KERBERUS.
"""

import logging
import os
from typing import List, Dict, Optional
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, SparseVectorParams, SparseIndexParams
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
        host: str = None,
        port: int = None,
        api_key: Optional[str] = None
    ):
        # Read from environment variables with fallback to defaults
        if host is None:
            host = os.environ.get("QDRANT_HOST", "localhost")
        if port is None:
            port = int(os.environ.get("QDRANT_PORT", "6333"))
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
        distance: Distance = Distance.COSINE,
        enable_sparse: bool = True
    ):
        """
        Create a new collection.

        Args:
            collection_name: Name of collection
            vector_size: Embedding dimension of dense vector
            distance: Distance metric (Cosine recommended)
            enable_sparse: Whether to enable sparse vectors for hybrid search
        """
        try:
            # Check if collection exists
            collections = self.client.get_collections().collections
            if any(col.name == collection_name for col in collections):
                logger.info(f"Collection '{collection_name}' already exists")
                # Warning: We don't partial update schema here. If schema changed, need to recreate.
                return

            # Configure vectors
            vectors_config = {
                "dense": VectorParams(
                    size=vector_size,
                    distance=distance
                )
            }
            
            sparse_vector_config = None
            if enable_sparse:
                sparse_vector_config = {
                    "sparse": SparseVectorParams(
                        index=SparseIndexParams(
                            on_disk=False,
                        )
                    )
                }

            # Create collection
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=vectors_config,
                sparse_vectors_config=sparse_vector_config
            )

            logger.info(f"✅ Created collection: {collection_name} (sparse={enable_sparse})")

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
            points: List of dicts with 'id', 'vector', 'payload' keys.
                    'vector' can be a list (single dense) or dict (named vectors).
        """
        try:
            # Convert to PointStruct format
            point_structs = []
            for point in points:
                raw_id = point.get('id')
                
                # Check for UUID validity, fallback to generating one if needed
                try:
                    if raw_id:
                        # Try to parse as UUID
                        struct_id = str(uuid.UUID(str(raw_id)))
                    else:
                        raise ValueError("Empty ID")
                except (ValueError, TypeError, AttributeError):
                    # If invalid UUID, generate deterministic UUID from string ID if possible
                    if raw_id:
                         # Use UUID5 (SHA-1 hash) with namespace URL for consistency
                         struct_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(raw_id)))
                    else:
                         struct_id = str(uuid.uuid4())

                # Inject original ID into payload so we don't lose it
                payload = point.get('payload', {})
                payload['_original_id'] = raw_id
                
                # Handle vector format (could be plain list or dictionary of named vectors)
                vector_data = point['vector']
                
                # Pre-processing for Sparse Vectors:
                # BGE returns sparse weights as dict {token_id: weight}.
                # Qdrant expects SparseVector(indices=[...], values=[...]).
                if isinstance(vector_data, dict) and "sparse" in vector_data:
                    sparse_raw = vector_data["sparse"]
                    if isinstance(sparse_raw, dict):
                         # Convert {id: weight} to {indices: [], values: []}
                         indices = []
                         values = []
                         for k, v in sparse_raw.items():
                             try:
                                 indices.append(int(k))
                                 values.append(float(v))
                             except ValueError:
                                 continue
                         
                         from qdrant_client.models import SparseVector
                         vector_data["sparse"] = SparseVector(indices=indices, values=values)

                # If we get a dictionary with 'dense'/'sparse', we pass it directly
                # as Qdrant client handles named vectors mapping automatically
                
                point_structs.append(
                    PointStruct(
                        id=struct_id,
                        vector=vector_data,
                        payload=payload
                    )
                )

            # Upsert
            self.client.upsert(
                collection_name=collection_name,
                points=point_structs
            )

            logger.info(f"Upserted {len(points)} points to {collection_name}")

        except Exception as e:
            logger.error(f"Upsert failed: {e}", exc_info=True)
            raise

    def search_dense(
        self,
        collection_name: str,
        dense_vector: List[float],
        limit: int = 50,
        filters: Optional[Dict] = None
    ) -> List[Dict]:
        """
        Search using only dense vectors (semantic/multilingual search).

        This is better for cross-lingual queries because it doesn't
        include lexical matching which favors same-language results.

        Args:
            collection_name: Collection to search
            dense_vector: 1024D dense embedding
            limit: Number of results
            filters: Metadata filters

        Returns:
            List of results with cosine similarity scores (0-1)
        """
        query_filter = self._build_filter(filters)

        # Use query_points with named vector
        results = self.client.query_points(
            collection_name=collection_name,
            query=dense_vector,
            using="dense",  # Named vector
            limit=limit,
            query_filter=query_filter
        ).points

        return [
            {
                'id': point.id,
                'score': point.score,
                'payload': point.payload,
                'embedding': None
            }
            for point in results
        ]

    async def search_async(
        self,
        collection_name: str,
        query_vector: List[float],
        limit: int = 50,
        score_threshold: Optional[float] = None,
        filters: Optional[Dict] = None
    ) -> List[Dict]:
        """
        Search collection asynchronously (dense-only, legacy method).

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

            # Search using named dense vector
            results = self.client.query_points(
                collection_name=collection_name,
                query=query_vector,
                using="dense",
                limit=limit,
                score_threshold=score_threshold,
                query_filter=query_filter
            ).points

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

    def search_hybrid(
        self,
        collection_name: str,
        dense_vector: List[float],
        sparse_vector: Dict[str, float],
        limit: int = 50,
        filters: Optional[Dict] = None
    ) -> List[Dict]:
        """
        Perform hybrid search using dense and sparse vectors with RRF fusion.

        RRF (Reciprocal Rank Fusion) combines results from both dense (semantic)
        and sparse (lexical/BM25-like) retrieval for better recall.

        Args:
            collection_name: Target collection
            dense_vector: 1024D dense embedding from BGE-M3
            sparse_vector: Lexical weights dict {token_id: weight}
            limit: Number of results
            filters: Optional metadata filters

        Returns:
            List of results with 'id', 'score', 'payload' keys
        """
        from qdrant_client import models

        # Build advanced filter
        query_filter = self._build_filter(filters)

        # Convert sparse dict to indices/values arrays
        sparse_indices = []
        sparse_values = []
        for k, v in sparse_vector.items():
            try:
                sparse_indices.append(int(k))
                sparse_values.append(float(v))
            except ValueError:
                pass

        # Prefetch from both dense and sparse indexes
        prefetch = [
            models.Prefetch(
                query=dense_vector,
                using="dense",
                limit=limit * 2,
                filter=query_filter
            ),
            models.Prefetch(
                query=models.SparseVector(indices=sparse_indices, values=sparse_values),
                using="sparse",
                limit=limit * 2,
                filter=query_filter
            )
        ]

        # Execute with RRF fusion
        results = self.client.query_points(
            collection_name=collection_name,
            prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
        ).points

        return [
            {
                'id': point.id,
                'score': point.score,
                'payload': point.payload,
                'embedding': None  # RRF doesn't return stored vectors
            }
            for point in results
        ]

    def _build_filter(self, filters: Optional[Dict]) -> Optional[Filter]:
        """
        Build Qdrant filter from dictionary specification.

        Supports:
        - year_range: {'min': 2020, 'max': 2024}
        - sources: ['federal', 'ticino']
        - cantons: ['ZH', 'TI']
        - law_types: ['civil', 'penal']
        - Single value matches: {'language': 'de'}
        """
        if not filters:
            return None

        from qdrant_client import models

        must_conditions = []

        for key, value in filters.items():
            if key == 'year_range':
                # Range filter for year field
                rng = models.Range(
                    gte=value.get('min'),
                    lte=value.get('max')
                )
                must_conditions.append(
                    FieldCondition(key='year', range=rng)
                )

            elif key == 'sources':
                if isinstance(value, list) and value:
                    must_conditions.append(
                        FieldCondition(key='source', match=models.MatchAny(any=value))
                    )

            elif key == 'cantons':
                if isinstance(value, list) and value:
                    must_conditions.append(
                        FieldCondition(key='canton', match=models.MatchAny(any=value))
                    )

            elif key == 'law_types':
                if isinstance(value, list) and value:
                    must_conditions.append(
                        FieldCondition(key='law_type', match=models.MatchAny(any=value))
                    )

            elif isinstance(value, list):
                # Generic list handling
                must_conditions.append(
                    FieldCondition(key=key, match=models.MatchAny(any=value))
                )
            else:
                # Simple single value match
                must_conditions.append(
                    FieldCondition(key=key, match=MatchValue(value=value))
                )

        return Filter(must=must_conditions) if must_conditions else None

    async def search_hybrid_async(
        self,
        collection_name: str,
        dense_vector: List[float],
        sparse_vector: Dict[str, float],
        limit: int = 50,
        filters: Optional[Dict] = None
    ) -> List[Dict]:
        """
        Async wrapper for hybrid search.

        Note: Qdrant client operations are synchronous, so this uses
        asyncio.to_thread for non-blocking execution.
        """
        import asyncio
        return await asyncio.to_thread(
            self.search_hybrid,
            collection_name,
            dense_vector,
            sparse_vector,
            limit,
            filters
        )


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
