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
from src.search.mmr import apply_mmr, deduplicate_by_document
from src.search.document_fetcher import enrich_results_with_full_content
from src.database.vector_db import QdrantManager

logger = logging.getLogger(__name__)

# Abrogated laws that should be filtered out from Codex results
# These laws have been replaced but old versions are still in the database
# Maps SR number -> list of abrogated abbreviations
ABROGATED_LAWS = {
    "142.20": ["ANAG", "LSEE", "LDDS"],  # Replaced by AIG/LEI/LStrI in 2008
    "142.201": ["ANAV", "RSEE", "ODDS"],  # Replaced by VZAE/OASA in 2008
}


def filter_abrogated_laws(results: List[Dict]) -> List[Dict]:
    """
    Remove results from abrogated laws.

    These are laws that have been replaced but old versions
    are still in the database with incorrect abbreviations.
    """
    filtered = []
    removed_count = 0

    for result in results:
        payload = result.get('payload', {})
        sr_number = payload.get('sr_number', '')
        abbreviation = payload.get('abbreviation', '')

        # Check if this is an abrogated law
        if sr_number in ABROGATED_LAWS:
            abrogated_abbrevs = ABROGATED_LAWS[sr_number]
            if abbreviation in abrogated_abbrevs:
                removed_count += 1
                logger.debug(f"Filtered abrogated law: {abbreviation} (SR {sr_number})")
                continue

        filtered.append(result)

    if removed_count > 0:
        logger.info(f"Filtered {removed_count} results from abrogated laws")

    return filtered


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
        qdrant_host: str = None,
        qdrant_port: int = None
    ):
        """Initialize Triad Search."""
        self.embedder = get_embedder()  # Auto-detect: CUDA → MPS → CPU
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
                limit=500,  # Increased from 250 to capture more candidates
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
            # Pipeline: 500 → 100 → 100 → 10 (hybrid → MMR → rerank → dedupe)
            diverse_results = apply_mmr(
                candidates=candidates,
                query_embedding=query_vectors['dense'],
                lambda_param=0.85,
                top_k=100  # Increased from 50 to capture more diverse candidates
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

            # Rerank all 100 candidates - deduplication will pick top 10 unique
            reranked = self.reranker.rerank_with_confidence(
                query=query,
                documents=rerank_docs,
                top_k=100  # Score all 100, dedupe picks 10 unique
            )

            # Step 4: Deduplicate - keep only best chunk per unique document
            # This ensures we feed Qwen with 10 unique cases/laws, not 10 chunks from 3 docs
            if reranked.get('results'):
                reranked['results'] = deduplicate_by_document(
                    reranked['results'],
                    top_k=top_k
                )
                logger.info(f"{collection_name}: {len(reranked['results'])} unique documents after deduplication")

                # Step 4.5: Filter out abrogated laws from Codex
                # These are old law versions that should not be cited
                if collection_name == 'codex':
                    reranked['results'] = filter_abrogated_laws(reranked['results'])

                # Step 5: Fetch full document content for Qwen
                # Chunks are for retrieval; Qwen needs full documents for accurate analysis
                reranked['results'] = enrich_results_with_full_content(
                    reranked['results'],
                    collection=collection_name
                )
                logger.info(f"{collection_name}: Enriched with full document content")

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
                        limit=250,  # 250 per collection = 500 total (if 2 cols)
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

            # Sort by score and take top 500
            all_results = sorted(all_results, key=lambda x: x['score'], reverse=True)[:500]

            # Apply MMR and rerank
            # Pipeline: 500 → 100 → 100 → 10 (hybrid → MMR → rerank → dedupe)
            diverse_results = apply_mmr(
                candidates=all_results,
                query_embedding=query_vectors['dense'],
                lambda_param=0.85,
                top_k=100  # Keep 100 for reranker to score
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

            # Rerank all 100 candidates - deduplication will pick top 10 unique
            reranked = self.reranker.rerank_with_confidence(
                query=query,
                documents=rerank_docs,
                top_k=100  # Score all 100, dedupe picks 10 unique
            )

            # Deduplicate - keep only best chunk per unique document
            if reranked.get('results'):
                reranked['results'] = deduplicate_by_document(
                    reranked['results'],
                    top_k=top_k
                )
                logger.info(f"Dossier: {len(reranked['results'])} unique documents after deduplication")

                # Note: Full content enrichment for dossier happens at the API layer
                # where the decrypted DossierSearchService is available.
                # Mark results as needing enrichment.
                reranked['needs_enrichment'] = True

            return reranked

        except Exception as e:
            logger.error(f"Dossier search failed: {e}", exc_info=True)
            return {
                'results': [],
                'confidence': 'NONE',
                'message': 'Error searching dossier'
            }
