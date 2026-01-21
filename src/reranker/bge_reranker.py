"""
BGE-Reranker-v2-M3 for KERBERUS.

Uses cross-encoder (query-document interaction) for precise relevance scoring.
Optimized for CPU (faster than MPS for small batches).
"""

import logging
from typing import List, Dict, Optional
from datetime import datetime
import numpy as np
from FlagEmbedding import FlagReranker

logger = logging.getLogger(__name__)


class BGEReranker:
    """
    BGE-Reranker-v2-M3 for refining search results.

    Features:
    - Cross-encoder scoring (better than cosine similarity)
    - Multilingual support (100+ languages)
    - Fast on CPU for small batches (10-50 documents)
    - Confidence scoring based on top score and variance
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        device: str = "cpu",
        max_length: int = 512
    ):
        """
        Initialize BGE-Reranker.

        Args:
            model_name: HuggingFace model identifier
            device: "cpu" (recommended) or "mps"
            max_length: Maximum token length for reranking
        """
        self.model_name = model_name
        self.device = device
        self.max_length = max_length
        self._reranker = None

        logger.info(f"Initializing BGE-Reranker-v2-M3 (device={device})")
        self._load_model()

    def _load_model(self):
        """Load reranker model."""
        try:
            self._reranker = FlagReranker(
                self.model_name,
                use_fp16=False,  # CPU doesn't benefit from fp16
                device=self.device
            )

            # Warmup
            _ = self._reranker.compute_score([["warmup query", "warmup document"]])

            logger.info("✅ BGE-Reranker-v2-M3 loaded successfully")

        except Exception as e:
            logger.error(f"Failed to load reranker: {e}", exc_info=True)
            raise RuntimeError(f"Reranker initialization failed: {e}")

    def calculate_recency_score(self, year: int) -> float:
        """
        Calculate recency score (0.0 to 1.0) based on system date.

        Args:
            year: Year of the document

        Returns:
            Normalized score where 1900=0.0, current_year=1.0

        Note:
            Uses datetime.now().year to get current year dynamically.
            This ensures the score remains accurate over time.
        """
        # IMPORTANT: Get current year from system, not hardcoded
        current_year = datetime.now().year

        # Clamp year to valid range
        if year < 1900:
            year = 1900
        if year > current_year:
            year = current_year

        # Normalize to 0.0-1.0 scale
        return (year - 1900) / (current_year - 1900)

    def extract_year(self, doc: Dict) -> int:
        """
        Extract year from document with fallback chain.

        Checks (in order):
        1. metadata.year (primary)
        2. year (top-level)
        3. metadata.date (extract year from date string)
        4. metadata.date_decided (extract year)
        5. 2000 (default neutral year)

        Args:
            doc: Document dictionary

        Returns:
            Year as integer
        """
        # Check metadata.year first
        year = doc.get('metadata', {}).get('year')
        if year:
            return int(year)

        # Check top-level year
        year = doc.get('year')
        if year:
            return int(year)

        # Try extracting from date string
        date_str = doc.get('metadata', {}).get('date') or doc.get('metadata', {}).get('date_decided')
        if date_str:
            try:
                # Handles formats like '2024-03-15', '2024/03/15', '15.03.2024'
                if '-' in date_str:
                    year = int(date_str.split('-')[0])
                elif '/' in date_str:
                    year = int(date_str.split('/')[0])
                elif '.' in date_str:
                    parts = date_str.split('.')
                    year = int(parts[-1])  # Assumes DD.MM.YYYY
                else:
                    year = 2000

                if 1900 <= year <= datetime.now().year + 1:
                    return year
            except (ValueError, IndexError):
                pass

        # Default
        return 2000

    def rerank(
        self,
        query: str,
        documents: List[Dict],
        top_k: int = 10,
        score_threshold: Optional[float] = None,
        recency_weight: float = 0.1
    ) -> List[Dict]:
        """
        Rerank documents by relevance to query with optional recency boost.

        Args:
            query: User query
            documents: List of dicts with 'text' and 'metadata' keys
            top_k: Number of top results to return
            score_threshold: Optional minimum score filter
            recency_weight: Weight for recency boost (default: 0.1)

        Returns:
            Top-k reranked documents with scoring fields added:
            - 'base_score': Original rerank score
            - 'recency_score': Normalized year score (0.0-1.0)
            - 'final_score': base_score + (recency_weight × recency_score)
            - 'year': Extracted year for display
            - All original metadata preserved
        """
        if not documents:
            return []

        try:
            # Prepare pairs for reranking
            pairs = [[query, doc['text']] for doc in documents]

            # Compute scores
            scores = self._reranker.compute_score(pairs, max_length=self.max_length)

            # Ensure scores is a list
            if not isinstance(scores, list):
                scores = scores.tolist()

            # Add scores to documents with recency boost
            for doc, score in zip(documents, scores):
                # Store base score
                doc['base_score'] = float(score)
                doc['rerank_score'] = float(score)  # Keep for backward compatibility

                # Extract year with fallback chain
                year = self.extract_year(doc)

                # Make year easily accessible at top level (for display)
                doc['year'] = year

                # Calculate recency boost
                recency_score = self.calculate_recency_score(year)
                doc['recency_score'] = recency_score

                # Calculate final score
                doc['final_score'] = doc['base_score'] + (recency_weight * recency_score)

            # Filter by threshold if specified (using final_score)
            if score_threshold is not None:
                documents = [doc for doc in documents if doc['final_score'] >= score_threshold]

            # Sort by final_score (descending)
            documents = sorted(documents, key=lambda x: x['final_score'], reverse=True)

            # Return top-k
            return documents[:top_k]

        except Exception as e:
            logger.error(f"Reranking failed: {e}", exc_info=True)
            # Return original documents on error
            return documents[:top_k]

    def rerank_with_confidence(
        self,
        query: str,
        documents: List[Dict],
        top_k: int = 10
    ) -> Dict:
        """
        Rerank documents and calculate confidence level.

        Confidence levels:
        - HIGH: top_score > 0.75 AND variance < 0.1
        - MEDIUM: top_score > 0.55
        - LOW: top_score <= 0.55
        - NONE: no documents

        Returns:
            {
                'results': List[Dict] (reranked documents),
                'confidence': str ('HIGH', 'MEDIUM', 'LOW', 'NONE'),
                'message': str (explanation),
                'top_score': float,
                'score_variance': float
            }
        """
        # Rerank
        reranked = self.rerank(query, documents, top_k=top_k)

        if not reranked:
            return {
                'results': [],
                'confidence': 'NONE',
                'message': 'No relevant documents found',
                'top_score': 0.0,
                'score_variance': 0.0
            }

        # Calculate statistics using final_score
        scores = [doc['final_score'] for doc in reranked]
        top_score = scores[0]

        if len(scores) > 1:
            score_variance = float(np.var(scores))
        else:
            score_variance = 0.0

        # Extract metadata from top document
        top_doc = reranked[0]
        top_year = top_doc.get('year', 'unknown')
        case_id = top_doc.get('metadata', {}).get('case_id', 'unknown')

        # Determine confidence with metadata context
        if top_score > 0.75 and score_variance < 0.1:
            confidence = 'HIGH'
            message = f'High confidence result ({case_id}, {top_year}, score: {top_score:.2f})'
        elif top_score > 0.55:
            confidence = 'MEDIUM'
            message = f'Moderate confidence ({case_id}, {top_year}, score: {top_score:.2f})'
        else:
            confidence = 'LOW'
            message = f'Low confidence - manual verification recommended ({case_id}, {top_year}, score: {top_score:.2f})'

        return {
            'results': reranked,
            'confidence': confidence,
            'message': message,
            'top_score': top_score,
            'score_variance': score_variance
        }


# Singleton instance
_reranker_instance: Optional[BGEReranker] = None


def get_reranker(device: str = "cpu") -> BGEReranker:
    """Get shared reranker instance."""
    global _reranker_instance
    if _reranker_instance is None:
        _reranker_instance = BGEReranker(device=device)
    return _reranker_instance
