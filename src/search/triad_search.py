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


def detect_query_context(query: str) -> Dict:
    """
    Detect language and canton mentions in query.

    Language is used for query enhancement (generating legal terms in same language).
    Canton is used for boosting cantonal law results.

    Returns:
        {
            'language': 'de'|'fr'|'it'|None,
            'canton': 'TI'|'ZH'|...|None,
            'canton_name': 'Ticino'|'Zürich'|...|None
        }
    """
    query_lower = query.lower()

    # Language detection based on common words
    italian_markers = ['quale', 'quali', 'come', 'cosa', 'dove', 'quando', 'perché', 'chi',
                       'sono', 'è', 'della', 'delle', 'nel', 'nella', 'per', 'con', 'licenza',
                       'edilizia', 'costruzione', 'contratto', 'lavoro', 'legge']
    french_markers = ['quel', 'quelle', 'comment', 'quoi', 'où', 'quand', 'pourquoi', 'qui',
                      'sont', 'est', 'des', 'dans', 'pour', 'avec', 'permis', 'construction',
                      'contrat', 'travail', 'loi']
    german_markers = ['welche', 'welcher', 'wie', 'was', 'wo', 'wann', 'warum', 'wer',
                      'sind', 'ist', 'der', 'die', 'das', 'für', 'mit', 'bewilligung',
                      'bau', 'vertrag', 'arbeit', 'gesetz']

    it_count = sum(1 for m in italian_markers if m in query_lower)
    fr_count = sum(1 for m in french_markers if m in query_lower)
    de_count = sum(1 for m in german_markers if m in query_lower)

    language = None
    if it_count > fr_count and it_count > de_count and it_count >= 2:
        language = 'it'
    elif fr_count > it_count and fr_count > de_count and fr_count >= 2:
        language = 'fr'
    elif de_count > 0:
        language = 'de'

    # Canton detection
    canton_map = {
        'ticino': ('TI', 'Ticino'), 'tessin': ('TI', 'Ticino'), 'canton ticino': ('TI', 'Ticino'),
        'zürich': ('ZH', 'Zürich'), 'zurich': ('ZH', 'Zürich'), 'kanton zürich': ('ZH', 'Zürich'),
        'bern': ('BE', 'Bern'), 'berne': ('BE', 'Bern'),
        'genève': ('GE', 'Genève'), 'genf': ('GE', 'Genève'), 'geneva': ('GE', 'Genève'),
        'vaud': ('VD', 'Vaud'), 'waadt': ('VD', 'Vaud'),
        'valais': ('VS', 'Valais'), 'wallis': ('VS', 'Valais'),
        'graubünden': ('GR', 'Graubünden'), 'grisons': ('GR', 'Graubünden'),
        'fribourg': ('FR', 'Fribourg'), 'freiburg': ('FR', 'Fribourg'),
    }

    canton = None
    canton_name = None
    for keyword, (code, name) in canton_map.items():
        if keyword in query_lower:
            canton = code
            canton_name = name
            break

    return {'language': language, 'canton': canton, 'canton_name': canton_name}


def boost_by_context(results: List[Dict], query_context: Dict) -> List[Dict]:
    """
    Boost results based on canton mentions in query.

    - Matching canton: 3.5x score boost

    Note: Language boost removed intentionally. A user asking in Italian
    may need German/French laws that are more pertinent to their case.
    The law's relevance matters more than its language.
    """
    canton = query_context.get('canton')

    if not canton:
        return results

    boosted = []
    for result in results:
        payload = result.get('payload', {})
        score = result.get('score', 0)
        boost = 1.0

        # Canton boost (strong for explicit canton mentions)
        doc_canton = payload.get('canton')
        doc_source = payload.get('source', 'fedlex')
        if doc_canton == canton:
            boost *= 3.5  # Strong boost for exact canton match
        elif canton == 'TI' and doc_source == 'ticino':
            boost *= 3.5  # Ticino source match

        result['score'] = score * boost
        result['boost_applied'] = boost
        boosted.append(result)

    # Re-sort by boosted score
    boosted.sort(key=lambda x: x.get('score', 0), reverse=True)

    boosted_count = sum(1 for r in boosted if r.get('boost_applied', 1.0) > 1.0)
    if boosted_count > 0:
        logger.info(f"Boosted {boosted_count} results (canton={canton})")

    return boosted


# Active laws whitelist - loaded from discovered_laws.json
# Only laws in this set are considered current/valid
_ACTIVE_LAWS_CACHE = None


def _load_active_laws() -> set:
    """Load the set of active SR numbers from discovered_laws.json."""
    global _ACTIVE_LAWS_CACHE

    if _ACTIVE_LAWS_CACHE is not None:
        return _ACTIVE_LAWS_CACHE

    import json
    from pathlib import Path

    # Try multiple possible paths
    possible_paths = [
        Path("/app/data/fedlex/metadata/discovered_laws.json"),  # Docker
        Path(__file__).parent.parent.parent / "data" / "fedlex" / "metadata" / "discovered_laws.json",  # Local
    ]

    for path in possible_paths:
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    laws = data.get('laws', {})
                    _ACTIVE_LAWS_CACHE = set(laws.keys())
                    logger.info(f"Loaded {len(_ACTIVE_LAWS_CACHE)} active laws from {path}")
                    return _ACTIVE_LAWS_CACHE
            except Exception as e:
                logger.error(f"Failed to load active laws from {path}: {e}")

    logger.warning("Could not load active laws whitelist - no filtering will be applied")
    _ACTIVE_LAWS_CACHE = set()
    return _ACTIVE_LAWS_CACHE


def filter_to_active_laws(results: List[Dict]) -> List[Dict]:
    """
    Filter results to only include currently active laws.

    Uses discovered_laws.json as the authoritative source for
    which SR numbers are currently in force.

    Note: Ticino cantonal laws (source="ticino") are always kept
    since they were scraped from the active laws list.
    """
    active_laws = _load_active_laws()

    if not active_laws:
        # Whitelist not available, return all results
        return results

    filtered = []
    removed_count = 0

    for result in results:
        payload = result.get('payload', {})
        sr_number = payload.get('sr_number', '')
        source = payload.get('source', 'fedlex')

        # Always keep Ticino cantonal laws - they're already filtered at scrape time
        if source == 'ticino':
            filtered.append(result)
            continue

        # For federal laws, check against whitelist
        if sr_number in active_laws:
            filtered.append(result)
        else:
            removed_count += 1
            logger.debug(f"Filtered inactive law: SR {sr_number}")

    if removed_count > 0:
        logger.info(f"Filtered {removed_count} results from inactive/abrogated laws")

    return filtered


# Abbreviation correction cache - loaded from abbreviations.json
_ABBREVIATIONS_CACHE = None


def _load_abbreviations() -> Dict:
    """Load the correct abbreviations from abbreviations.json."""
    global _ABBREVIATIONS_CACHE

    if _ABBREVIATIONS_CACHE is not None:
        return _ABBREVIATIONS_CACHE

    import json
    from pathlib import Path

    # Try multiple possible paths
    possible_paths = [
        Path("/app/data/fedlex/metadata/abbreviations.json"),  # Docker
        Path(__file__).parent.parent.parent / "data" / "fedlex" / "metadata" / "abbreviations.json",  # Local
    ]

    for path in possible_paths:
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    _ABBREVIATIONS_CACHE = data.get('by_sr', {})
                    logger.info(f"Loaded {len(_ABBREVIATIONS_CACHE)} law abbreviations from {path}")
                    return _ABBREVIATIONS_CACHE
            except Exception as e:
                logger.error(f"Failed to load abbreviations from {path}: {e}")

    logger.warning("Could not load abbreviations - no corrections will be applied")
    _ABBREVIATIONS_CACHE = {}
    return _ABBREVIATIONS_CACHE


def correct_abbreviations(results: List[Dict], language: str = 'de') -> List[Dict]:
    """
    Correct abbreviations in results using authoritative Fedlex data.

    The Qdrant database may have outdated abbreviations (e.g., ANAG instead of AIG
    for SR 142.20). This function corrects them using the abbreviations.json
    which is generated from the official Fedlex SPARQL API.

    Args:
        results: Search results with payload containing sr_number and abbreviation
        language: Language code ('de', 'fr', 'it') for selecting correct abbreviation

    Returns:
        Results with corrected abbreviations
    """
    abbrevs = _load_abbreviations()

    if not abbrevs:
        return results

    corrected_count = 0
    lang_map = {'de': 'de', 'fr': 'fr', 'it': 'it', 'en': 'de'}  # Fallback to German for English

    for result in results:
        payload = result.get('payload', {})
        sr_number = payload.get('sr_number', '')

        if sr_number in abbrevs:
            correct_data = abbrevs[sr_number]
            lang_key = lang_map.get(language, 'de')
            correct_abbrev = correct_data.get(lang_key)
            correct_title = correct_data.get(f'title_{lang_key}')

            current_abbrev = payload.get('abbreviation')

            if correct_abbrev and current_abbrev and current_abbrev != correct_abbrev:
                logger.debug(f"Correcting SR {sr_number}: {current_abbrev} -> {correct_abbrev}")
                payload['abbreviation'] = correct_abbrev
                payload['abbreviations_all'] = {
                    'de': correct_data.get('de', ''),
                    'fr': correct_data.get('fr', ''),
                    'it': correct_data.get('it', '')
                }
                if correct_title:
                    payload['sr_name'] = correct_title
                corrected_count += 1

    if corrected_count > 0:
        logger.info(f"Corrected {corrected_count} outdated abbreviations")

    return results


def split_laws_and_ordinances(results: List[Dict], top_laws: int = 15, top_ordinances: int = 10) -> List[Dict]:
    """
    Split results into laws and ordinances, returning top of each.

    Laws define principles, ordinances/regulations specify implementation.
    Qwen needs both to give complete legal advice.
    E.g., LE Art. 2 (principle) + RLE Art. 8 (procedure).

    Args:
        results: Search results
        top_laws: How many laws to return (default 15)
        top_ordinances: How many ordinances/regulations to return (default 10)

    Returns:
        Combined list: top_laws + top_ordinances (default 25 total)
    """
    # Keywords indicating an ordinance/regulation (in DE/FR/IT titles)
    ORDINANCE_KEYWORDS = ['Verordnung', 'Ordonnance', 'Ordinanza', 'Reglement', 'Règlement', 'Regolamento']

    laws = []
    ordinances = []

    for result in results:
        payload = result.get('payload', {})
        sr_name = payload.get('sr_name', '')

        is_ordinance = any(kw in sr_name for kw in ORDINANCE_KEYWORDS)

        if is_ordinance:
            result['is_ordinance'] = True
            ordinances.append(result)
        else:
            result['is_ordinance'] = False
            laws.append(result)

    # Take top of each, already sorted by score from reranking
    selected_laws = laws[:top_laws]
    selected_ordinances = ordinances[:top_ordinances]

    logger.info(f"Codex split: {len(selected_laws)} laws + {len(selected_ordinances)} ordinances")

    # Return laws first, then ordinances
    return selected_laws + selected_ordinances


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
            # Step 0: Detect query language and canton for smart boosting
            query_context = detect_query_context(query)
            if query_context['language'] or query_context['canton']:
                logger.info(f"Query context: lang={query_context['language']}, canton={query_context['canton']}")

            # Step 1: Generate query embedding (dense + sparse)
            query_vectors = await self.embedder.encode_async(query)

            # Step 2: Search all lanes in parallel using hybrid search
            lane_tasks = [
                self._search_lane("codex", query, query_vectors, filters, top_k, query_context),
                self._search_lane("library", query, query_vectors, filters, top_k, query_context),
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
        top_k: int,
        query_context: Optional[Dict] = None
    ) -> Dict:
        """
        Search single lane with full hybrid pipeline.

        Pipeline:
        1. Hybrid search - RRF fusion of dense + sparse (top 500)
        2. Context boosting (language + canton)
        3. MMR diversification (top 100)
        4. Rerank with cross-encoder (top 100)
        5. Confidence scoring
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

            # Step 1.5: Apply language and canton boosting for Codex
            if collection_name == 'codex' and query_context and candidates:
                candidates = boost_by_context(candidates, query_context)

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
            # This ensures we feed Qwen with unique cases/laws, not chunks from same docs
            # For Codex: need enough candidates for 15 laws + 10 ordinances = 25 after split
            # Use max(top_k * 3, 35) to ensure enough candidates even after filtering
            if reranked.get('results'):
                dedupe_limit = max(top_k * 3, 35) if collection_name == 'codex' else top_k
                reranked['results'] = deduplicate_by_document(
                    reranked['results'],
                    top_k=dedupe_limit
                )
                logger.info(f"{collection_name}: {len(reranked['results'])} unique documents after deduplication")

                # Step 4.5: Filter to only active laws from Codex
                # Uses discovered_laws.json whitelist to ensure only current laws are cited
                if collection_name == 'codex':
                    reranked['results'] = filter_to_active_laws(reranked['results'])
                    # Step 4.6: Correct outdated abbreviations using authoritative Fedlex data
                    # Qdrant may have old abbreviations (e.g., ANAG instead of AIG for SR 142.20)
                    reranked['results'] = correct_abbreviations(reranked['results'])
                    # Step 4.7: Split into laws + ordinances for balanced coverage
                    # Laws = principles (LE Art. 2), Ordinances = implementation (RLE Art. 8)
                    # Qwen gets 15 laws + 10 ordinances = 25 total for complete legal advice
                    reranked['results'] = split_laws_and_ordinances(reranked['results'], top_laws=15, top_ordinances=10)

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
