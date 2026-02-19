"""
Maximal Marginal Relevance (MMR) for diversifying search results.

Eliminates redundant documents while maintaining relevance.
Supports both embedding-based and metadata-based diversity.
"""

import logging
import numpy as np
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """
    Calculate cosine similarity between two vectors.

    Args:
        vec1: First vector
        vec2: Second vector

    Returns:
        Similarity score between -1 and 1
    """
    vec1 = np.array(vec1)
    vec2 = np.array(vec2)

    dot_product = np.dot(vec1, vec2)
    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return float(dot_product / (norm1 * norm2))


def _normalize_decision_id(decision_id: str) -> str:
    """
    Normalize decision ID for consistent deduplication.

    Handles:
    - Case normalization (BGE 102 IA 35 == BGE 102 Ia 35)
    - Chunk suffix removal (BGE-102-IA-35_chunk_2 -> BGE-102-IA-35)
    - Whitespace/dash normalization
    """
    if not decision_id:
        return ""

    # Remove chunk suffix
    if "_chunk_" in decision_id:
        decision_id = decision_id.split("_chunk_")[0]

    # Normalize to uppercase for consistent comparison
    normalized = decision_id.upper().strip()

    # Normalize separators (spaces and dashes)
    normalized = normalized.replace(" ", "-").replace("--", "-")

    return normalized


def _get_document_key(doc: Dict) -> str:
    """
    Extract a unique key for a document based on payload metadata.
    Used for metadata-based diversity when embeddings are unavailable.
    """
    payload = doc.get('payload', {})

    # Try different document type identifiers
    # Fedlex articles - group by law (sr_number), not individual articles
    if payload.get('sr_number'):
        return payload.get('sr_number')

    # Court decisions - normalize for consistent deduplication
    if payload.get('decision_id'):
        return _normalize_decision_id(payload['decision_id'])

    # Dossier documents
    if payload.get('doc_id'):
        return payload['doc_id']

    # Original ID fallback
    if payload.get('_original_id'):
        return _normalize_decision_id(str(payload['_original_id']))

    # Last resort: use the document id
    return str(doc.get('id', ''))


def deduplicate_by_document(
    results: List[Dict],
    top_k: int = 10
) -> List[Dict]:
    """
    Keep only the best-scoring chunk per unique document.

    When multiple chunks from the same document are retrieved,
    this keeps only the highest-scoring one to ensure diversity.

    Args:
        results: Reranked results (sorted by score descending)
        top_k: Number of unique documents to return

    Returns:
        List of top_k unique documents (best chunk per document)
    """
    seen_docs = set()
    unique_results = []

    for doc in results:
        doc_key = _get_document_key(doc)

        if doc_key not in seen_docs:
            seen_docs.add(doc_key)
            unique_results.append(doc)

            if len(unique_results) >= top_k:
                break

    return unique_results


def _metadata_similarity(doc1: Dict, doc2: Dict) -> float:
    """
    Calculate similarity between documents based on metadata.
    Returns 1.0 if same source/article, 0.5 if same law/court, 0.0 otherwise.
    """
    p1 = doc1.get('payload', {})
    p2 = doc2.get('payload', {})

    # Same article = maximum similarity
    if _get_document_key(doc1) == _get_document_key(doc2):
        return 1.0

    # Same law (SR number) = high similarity
    if p1.get('sr_number') and p1.get('sr_number') == p2.get('sr_number'):
        return 0.7

    # Same court = medium similarity
    if p1.get('court') and p1.get('court') == p2.get('court'):
        return 0.5

    # Same source = low similarity
    if p1.get('source') and p1.get('source') == p2.get('source'):
        return 0.3

    return 0.0


def apply_mmr(
    candidates: List[Dict],
    query_embedding: Optional[List[float]] = None,
    lambda_param: float = 0.7,
    top_k: int = 20
) -> List[Dict]:
    """
    Apply Maximal Marginal Relevance to diversify results.

    MMR balances:
    - Relevance to query (lambda_param)
    - Diversity from already-selected docs (1 - lambda_param)

    Supports two modes:
    1. Embedding-based: Uses cosine similarity (when doc embeddings available)
    2. Metadata-based: Uses document metadata (when embeddings unavailable)

    Args:
        candidates: List of dicts with 'score' key and optionally 'embedding'
        query_embedding: Query vector (optional, used if doc embeddings available)
        lambda_param: Relevance vs diversity (0.0-1.0)
            - 1.0 = max relevance (no diversity)
            - 0.0 = max diversity (ignores relevance)
            - 0.7 = balanced (recommended)
        top_k: Number of diverse results to return

    Returns:
        Diversified list of documents
    """
    if not candidates:
        return []

    if len(candidates) <= top_k:
        return candidates

    # Check if embeddings are available
    has_embeddings = (
        candidates[0].get('embedding') is not None and
        query_embedding is not None
    )

    try:
        # Always include top result (highest relevance)
        selected = [candidates[0]]
        remaining = candidates[1:]

        # Select remaining documents
        while len(selected) < top_k and remaining:
            best_score = -float('inf')
            best_idx = 0

            for idx, candidate in enumerate(remaining):
                if has_embeddings:
                    # Embedding-based MMR
                    relevance = cosine_similarity(candidate['embedding'], query_embedding)
                    max_similarity = max(
                        cosine_similarity(candidate['embedding'], selected_doc['embedding'])
                        for selected_doc in selected
                    )
                else:
                    # Metadata-based MMR (fallback for hybrid search)
                    # Use retrieval score as relevance proxy
                    relevance = candidate.get('score', 0.5)
                    max_similarity = max(
                        _metadata_similarity(candidate, selected_doc)
                        for selected_doc in selected
                    )

                # MMR score
                mmr_score = lambda_param * relevance - (1 - lambda_param) * max_similarity

                if mmr_score > best_score:
                    best_score = mmr_score
                    best_idx = idx

            # Add best candidate to selected
            selected.append(remaining.pop(best_idx))

        return selected

    except Exception as e:
        logger.error(f"MMR failed: {e}", exc_info=True)
        # Return original top-k on error
        return candidates[:top_k]
