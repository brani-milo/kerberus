"""
Triad Search: 3-lane parallel search with MMR and reranking.

Searches:
- Lane 1 (Codex): Swiss laws and statutes
- Lane 2 (Library): Case law and jurisprudence
- Lane 3 (Dossier): User's personal documents and firm shared docs
"""

import logging
import asyncio
from typing import List, Dict, Optional
from src.embedder.bge_embedder import get_embedder
from src.reranker.bge_reranker import get_reranker
from src.search.mmr import apply_mmr
from src.database.vector_db import QdrantManager

logger = logging.getLogger(__name__)


class TriadSearch:
    """
    Three-lane parallel search engine.

    Pipeline per lane:
    1. Vector search (top 50)
    2. MMR diversification (top 20)
    3. Reranking (top 10)
    4. Confidence scoring
    """

    def __init__(
        self,
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333
    ):
        """Initialize Triad Search."""
        self.embedder = get_embedder(device="mps")
        self.reranker = get_reranker(device="cpu")
        self.vector_db = QdrantManager(host=qdrant_host, port=qdrant_port)

        logger.info("✅ Triad Search initialized")

    async def search(
        self,
        query: str,
        user_id: Optional[str] = None,
        firm_id: Optional[str] = None,
        filters: Optional[Dict] = None,
        top_k: int = 10
    ) -> Dict:
        """
        Execute triad search across all lanes using hybrid search.

        Args:
            query: User query
            user_id: User ID for dossier search
            firm_id: Firm ID for shared dossier search
            filters: Optional metadata filters
            top_k: Results per lane

        Returns:
            {
                'codex': {'results': [...], 'confidence': 'HIGH', 'message': '...'},
                'library': {'results': [...], 'confidence': 'MEDIUM', 'message': '...'},
                'dossier': {'results': [...], 'confidence': 'LOW', 'message': '...'},
                'overall_confidence': 'MEDIUM',
                'query_embedding': {...}
            }
        """
        try:
            # Step 1: Generate query embedding (dense + sparse)
            query_vectors = await self.embedder.encode_async(query)

            # Step 2: Search all lanes in parallel using hybrid search
            lane_tasks = [
                self._search_lane("codex", query, query_vectors, filters, top_k),
                self._search_lane("library", query, query_vectors, filters, top_k),
                self._search_dossier(user_id, firm_id, query, query_vectors, filters, top_k)
            ]

            codex_result, library_result, dossier_result = await asyncio.gather(*lane_tasks)

            # Step 3: Determine overall confidence (minimum of all lanes)
            confidences = [codex_result['confidence'], library_result['confidence'], dossier_result['confidence']]
            confidence_order = ['NONE', 'LOW', 'MEDIUM', 'HIGH']
            overall_confidence = min(confidences, key=lambda c: confidence_order.index(c) if c in confidence_order else 0)

            return {
                'codex': codex_result,
                'library': library_result,
                'dossier': dossier_result,
                'overall_confidence': overall_confidence,
                'query_embedding': query_vectors
            }

        except Exception as e:
            logger.error(f"Triad search failed: {e}", exc_info=True)
            raise

    async def _search_lane(
        self,
        collection_name: str,
        query: str,
        query_vectors: Dict[str, any],
        filters: Optional[Dict],
        top_k: int
    ) -> Dict:
        """
        Search single lane with full hybrid pipeline.

        Pipeline:
        Pipeline:
        1. Hybrid search - RRF fusion of dense + sparse (top 250)
        2. MMR diversification (top 20)
        3. Rerank with cross-encoder (top 10)
        4. Confidence scoring
        """
        try:
            # Step 1: Hybrid search (dense + sparse with RRF fusion)
            # IMPORTANT: Remove year filter for Codex (laws don't have 'year' like decisions)
            lane_filters = filters.copy() if filters else None
            if collection_name == 'codex' and lane_filters and 'year_range' in lane_filters:
                del lane_filters['year_range']

            logger.info(f"Searching {collection_name} with query: {query[:50]}...")
            candidates = self.vector_db.search_hybrid(
                collection_name=collection_name,
                dense_vector=query_vectors['dense'],
                sparse_vector=query_vectors['sparse'],
                sparse_vector=query_vectors['sparse'],
                limit=250,
                filters=lane_filters
            )
            
            logger.info(f"{collection_name}: Got {len(candidates)} candidates from hybrid search")

            if not candidates:
                logger.warning(f"{collection_name}: No candidates found")
                return {
                    'results': [],
                    'confidence': 'NONE',
                    'message': f'No results found in {collection_name}'
                }

            # Step 2: MMR diversification
            # Note: Increased lambda to 0.85 to reduce penalty on cantonal decisions
            # which might share metadata (source) with federal ones but are distinct
            diverse_results = apply_mmr(
                candidates=candidates,
                query_embedding=query_vectors['dense'],
                lambda_param=0.85,
                top_k=20
            )

            # Step 3: Rerank with confidence
            # Extract text with fallback chain for different document types
            rerank_docs = []
            for doc in diverse_results:
                payload = doc.get('payload', {})
                # Fallback chain: text_preview (primary) -> article_text -> text -> content.reasoning -> empty
                text = (
                    payload.get('text_preview') or
                    payload.get('article_text') or
                    payload.get('text') or
                    (payload.get('content', {}).get('reasoning') if isinstance(payload.get('content'), dict) else None) or
                    (payload.get('content', {}).get('regeste') if isinstance(payload.get('content'), dict) else None) or
                    ''
                )
                rerank_docs.append({'text': text, **doc})

            reranked = self.reranker.rerank_with_confidence(
                query=query,
                documents=rerank_docs,
                top_k=top_k
            )

            return reranked

        except Exception as e:
            logger.error(f"Lane search failed for {collection_name}: {e}", exc_info=True)
            return {
                'results': [],
                'confidence': 'NONE',
                'message': f'Error searching {collection_name}'
            }

    async def _search_dossier(
        self,
        user_id: Optional[str],
        firm_id: Optional[str],
        query: str,
        query_vectors: Dict[str, any],
        filters: Optional[Dict],
        top_k: int
    ) -> Dict:
        """
        Search user's personal and firm shared dossiers using hybrid search.

        Combines:
        - Personal dossier (dossier_user_{uuid})
        - Firm dossier (dossier_firm_{uuid}) if applicable
        """
        try:
            collections_to_search = []

            # Add user collection if exists
            if user_id:
                collections_to_search.append(f"dossier_user_{user_id}")

            # Add firm collection if exists
            if firm_id:
                collections_to_search.append(f"dossier_firm_{firm_id}")

            if not collections_to_search:
                return {
                    'results': [],
                    'confidence': 'NONE',
                    'message': 'No dossier available'
                }

            # Search all dossier collections in parallel using hybrid search
            all_results = []
            for col in collections_to_search:
                try:
                    results = self.vector_db.search_hybrid(
                        collection_name=col,
                        dense_vector=query_vectors['dense'],
                        sparse_vector=query_vectors['sparse'],
                        dense_vector=query_vectors['dense'],
                        sparse_vector=query_vectors['sparse'],
                        limit=125,  # 125 per collection = 250 total (if 2 cols)
                        filters=filters
                    )
                    if results:
                        all_results.extend(results)
                except Exception as col_error:
                    # Collection might not exist yet, skip silently
                    logger.debug(f"Dossier collection {col} not found or error: {col_error}")
                    continue

            if not all_results:
                return {
                    'results': [],
                    'confidence': 'NONE',
                    'message': 'No relevant documents in dossier'
                }

            # Sort by score and take top 250
            all_results = sorted(all_results, key=lambda x: x['score'], reverse=True)[:250]

            # Apply MMR and rerank
            diverse_results = apply_mmr(
                candidates=all_results,
                query_embedding=query_vectors['dense'],
                lambda_param=0.85,
                top_k=20
            )

            # Extract text with fallback chain for dossier documents
            rerank_docs = []
            for doc in diverse_results:
                payload = doc.get('payload', {})
                text = (
                    payload.get('text_preview') or
                    payload.get('text') or
                    payload.get('content') or
                    payload.get('article_text') or
                    ''
                )
                rerank_docs.append({'text': text, **doc})

            reranked = self.reranker.rerank_with_confidence(
                query=query,
                documents=rerank_docs,
                top_k=top_k
            )

            return reranked

        except Exception as e:
            logger.error(f"Dossier search failed: {e}", exc_info=True)
            return {
                'results': [],
                'confidence': 'NONE',
                'message': 'Error searching dossier'
            }
