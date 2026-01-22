"""
Maximal Marginal Relevance (MMR) for diversifying search results.

Eliminates redundant documents while maintaining relevance.
"""

import logging
import numpy as np
from typing import List, Dict

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


def apply_mmr(
    candidates: List[Dict],
    query_embedding: List[float],
    lambda_param: float = 0.7,
    top_k: int = 20
) -> List[Dict]:
    """
    Apply Maximal Marginal Relevance to diversify results.

    MMR balances:
    - Relevance to query (lambda_param)
    - Diversity from already-selected docs (1 - lambda_param)

    Args:
        candidates: List of dicts with 'embedding' and 'score' keys
        query_embedding: Query vector
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

    try:
        # Always include top result (highest relevance)
        selected = [candidates[0]]
        remaining = candidates[1:]

        # Select remaining documents
        while len(selected) < top_k and remaining:
            best_score = -float('inf')
            best_idx = 0

            for idx, candidate in enumerate(remaining):
                # Relevance to query
                relevance = cosine_similarity(candidate['embedding'], query_embedding)

                # Maximum similarity to already-selected documents
                max_similarity = max(
                    cosine_similarity(candidate['embedding'], selected_doc['embedding'])
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
