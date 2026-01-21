"""
BGE-M3 Embedder for KERBERUS Legal Intelligence.

Optimized for:
- Multilingual Swiss legal text (DE/FR/IT)
- Apple Silicon (MPS acceleration)
- Memory efficiency (<3GB RAM)
- Both single-query and batch processing
"""

import logging
import asyncio
from typing import List, Optional, Dict
from functools import lru_cache
import torch
import numpy as np
from FlagEmbedding import BGEM3FlagModel

logger = logging.getLogger(__name__)


class BGEEmbedder:
    """
    BGE-M3 embedder with Apple Silicon optimization and connection pooling.

    Features:
    - Native multilingual support (100+ languages)
    - Dense embeddings (1024 dimensions)
    - Async support for concurrent requests
    - LRU cache for frequent queries
    - Memory-efficient batch processing
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        device: str = "mps",
        max_length: int = 8192,
        use_fp16: bool = True
    ):
        """
        Initialize BGE-M3 embedder.

        Args:
            model_name: HuggingFace model identifier
            device: "mps" (Apple Silicon), "cuda" (NVIDIA), or "cpu"
            max_length: Maximum token length (8192 for BGE-M3)
            use_fp16: Use half-precision (saves memory, slight speed boost)
        """
        self.model_name = model_name
        self.device = device
        self.max_length = max_length
        self.use_fp16 = use_fp16
        self._model = None
        self._lock = asyncio.Lock()

        logger.info(f"Initializing BGE-M3 Embedder (device={device}, fp16={use_fp16})")
        self._load_model()

    def _load_model(self):
        """Load model into memory with error handling."""
        try:
            # Initialize model
            self._model = BGEM3FlagModel(
                self.model_name,
                use_fp16=self.use_fp16,
                device=self.device
            )

            # Warmup (loads model into RAM/GPU memory)
            logger.info("Running model warmup...")
            _ = self._model.encode(
                "Warmup text",
                max_length=self.max_length
            )

            logger.info("✅ BGE-M3 model loaded successfully")

            # Log memory usage
            if self.device == "mps":
                # MPS memory tracking (if available)
                logger.info("Model loaded on Apple Silicon MPS")

        except Exception as e:
            logger.error(f"Failed to load BGE-M3 model: {e}", exc_info=True)
            raise RuntimeError(f"BGE-M3 initialization failed: {e}")

    async def encode_async(self, text: str) -> List[float]:
        """
        Encode single text asynchronously (for user queries).

        Args:
            text: Input text (e.g., legal query)

        Returns:
            1024-dimensional embedding vector

        Raises:
            RuntimeError: If encoding fails
        """
        async with self._lock:
            return await asyncio.to_thread(self._encode_single, text)

    def _encode_single(self, text: str) -> List[float]:
        """Internal sync encoding method."""
        try:
            # Check token length
            # Note: Approximate - real tokenization happens inside model
            word_count = len(text.split())
            if word_count > (self.max_length // 2):  # Rough estimate
                logger.warning(
                    f"Input might exceed {self.max_length} tokens "
                    f"(~{word_count} words). Text will be truncated."
                )

            # Generate embedding (pass as single string for consistent API)
            embeddings = self._model.encode(
                text,
                max_length=self.max_length,
                return_dense=True,
                return_sparse=False,
                return_colbert_vecs=False
            )

            # Extract dense embedding
            # When encoding a single string, dense_vecs is 1D array of shape (1024,)
            dense_embedding = embeddings['dense_vecs']

            # Convert to list
            if isinstance(dense_embedding, torch.Tensor):
                dense_embedding = dense_embedding.cpu().numpy()

            # Ensure it's a numpy array
            if isinstance(dense_embedding, np.ndarray):
                return dense_embedding.tolist()
            else:
                return list(dense_embedding)

        except Exception as e:
            logger.error(f"Encoding failed: {e}", exc_info=True)
            raise RuntimeError(f"Failed to encode text: {e}")

    def encode_batch(
        self,
        texts: List[str],
        batch_size: int = 32,
        show_progress: bool = False
    ) -> List[List[float]]:
        """
        Encode multiple texts in batches (for nightly processing).

        Args:
            texts: List of input texts
            batch_size: Number of texts per batch (lower = less RAM)
            show_progress: Show progress logging

        Returns:
            List of 1024-dimensional embedding vectors
        """
        all_embeddings = []

        try:
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]

                # Generate embeddings for batch
                embeddings = self._model.encode(
                    batch,
                    max_length=self.max_length,
                    return_dense=True,
                    return_sparse=False,
                    return_colbert_vecs=False
                )

                # Extract dense embeddings
                dense_embeddings = embeddings['dense_vecs']

                # Convert to list of lists
                if isinstance(dense_embeddings, torch.Tensor):
                    dense_embeddings = dense_embeddings.cpu().numpy()

                all_embeddings.extend(dense_embeddings.tolist())

                if show_progress:
                    logger.info(f"Encoded {i + len(batch)}/{len(texts)} documents")

            return all_embeddings

        except Exception as e:
            logger.error(f"Batch encoding failed: {e}", exc_info=True)
            raise

    @lru_cache(maxsize=1000)
    def encode_cached(self, text: str) -> tuple:
        """
        Cached encoding for frequently-repeated queries.

        Useful for common legal queries like "Art. 337 OR".
        Returns tuple (hashable for lru_cache).
        """
        embedding = self._encode_single(text)
        return tuple(embedding)

    def get_embedding_dimension(self) -> int:
        """Return embedding dimension (1024 for BGE-M3)."""
        return 1024


# Singleton instance (loaded once at startup)
_embedder_instance: Optional[BGEEmbedder] = None


def get_embedder(device: str = "mps") -> BGEEmbedder:
    """
    Get shared embedder instance (connection pooling).

    Args:
        device: "mps" for Apple Silicon, "cpu" for fallback
    """
    global _embedder_instance
    if _embedder_instance is None:
        _embedder_instance = BGEEmbedder(device=device)
    return _embedder_instance
