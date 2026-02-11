"""
BGE-M3 Embedder for KERBERUS Legal Intelligence.

Optimized for:
- Multilingual Swiss legal text (DE/FR/IT)
- Auto-detection: CUDA (NVIDIA) → MPS (Apple) → CPU
- Memory efficiency (<3GB RAM)
- Both single-query and batch processing
"""

import logging
import os

# Fix for "Option::unwrap() on None" panic in tokenizers/multiprocessing
os.environ["TOKENIZERS_PARALLELISM"] = "false"

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
        device: Optional[str] = None,
        max_length: int = 2048,
        use_fp16: bool = True
    ):
        """
        Initialize BGE-M3 embedder.

        Args:
            model_name: HuggingFace model identifier
            device: None (auto-detect), "cuda" (NVIDIA), "mps" (Apple), or "cpu"
            max_length: Maximum token length (8192 for BGE-M3)
            use_fp16: Use half-precision (saves memory, slight speed boost)
        """
        self.model_name = model_name
        self.device = device if device else get_best_device()
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

    async def encode_async(self, text: str) -> Dict[str, any]:
        """
        Encode single text asynchronously (for user queries).

        Args:
            text: Input text (e.g., legal query)

        Returns:
            Dict with 'dense' (1024-dimensional vector) and 'sparse' (lexical weights)

        Raises:
            RuntimeError: If encoding fails
        """
        async with self._lock:
            return await asyncio.to_thread(self._encode_single, text)

    def _encode_single(self, text: str) -> Dict[str, any]:
        """Internal sync encoding method."""
        try:
            # Check token length
            word_count = len(text.split())
            if word_count > (self.max_length // 2):
                logger.warning(
                    f"Input might exceed {self.max_length} tokens "
                    f"(~{word_count} words). Text will be truncated."
                )

            # Generate embedding (pass as single string for consistent API)
            embeddings = self._model.encode(
                text,
                max_length=self.max_length,
                return_dense=True,
                return_sparse=True,
                return_colbert_vecs=False
            )

            # Extract dense embedding
            dense_embedding = embeddings['dense_vecs']
            if isinstance(dense_embedding, torch.Tensor):
                dense_embedding = dense_embedding.cpu().numpy()
            
            if isinstance(dense_embedding, np.ndarray):
                dense_list = dense_embedding.tolist()
            else:
                dense_list = list(dense_embedding)

            # Extract sparse embedding
            sparse_embedding = embeddings['lexical_weights']
            # sparse_embedding is already a dict of {str: float} or similar from BGE
            
            return {
                "dense": dense_list,
                "sparse": sparse_embedding
            }

        except Exception as e:
            logger.error(f"Encoding failed: {e}", exc_info=True)
            raise RuntimeError(f"Failed to encode text: {e}")

    def encode_batch(
        self,
        texts: List[str],
        batch_size: int = 32,
        show_progress: bool = False
    ) -> List[Dict[str, any]]:
        """
        Encode multiple texts in batches (dense + sparse).

        Args:
            texts: List of input texts
            batch_size: Number of texts per batch
            show_progress: Show progress logging

        Returns:
            List of dicts: [{'dense': [...], 'sparse': {...}}, ...]
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
                    return_sparse=True,
                    return_colbert_vecs=False
                )

                # Extract dense embeddings
                dense_embeddings = embeddings['dense_vecs']
                if isinstance(dense_embeddings, torch.Tensor):
                    dense_embeddings = dense_embeddings.cpu().numpy()
                dense_list = dense_embeddings.tolist()

                # Extract sparse embeddings (list of dicts)
                sparse_embeddings = embeddings['lexical_weights']

                # Combine into result structure
                for d, s in zip(dense_list, sparse_embeddings):
                    all_embeddings.append({
                        "dense": d,
                        "sparse": s
                    })

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


def get_best_device() -> str:
    """
    Auto-detect the best available device for inference.

    Priority: CUDA (NVIDIA GPU) > MPS (Apple Silicon) > CPU

    Returns:
        Device string: "cuda", "mps", or "cpu"
    """
    # Check CUDA first
    if torch.cuda.is_available():
        logger.info(f"CUDA available: {torch.cuda.get_device_name(0)}")
        return "cuda"

    # Check MPS (Apple Silicon) with safety wrapper
    try:
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            # Additional check: verify MPS is actually usable
            if torch.backends.mps.is_built():
                logger.info("Apple Silicon MPS available")
                return "mps"
    except Exception as e:
        logger.debug(f"MPS check failed (expected on Linux): {e}")

    # Fallback to CPU
    logger.info("No GPU detected, using CPU")
    return "cpu"


# Singleton instance (loaded once at startup)
_embedder_instance: Optional[BGEEmbedder] = None


def get_embedder(device: Optional[str] = None) -> BGEEmbedder:
    """
    Get shared embedder instance (connection pooling).

    Args:
        device: Override device selection. If None, auto-detects best device.
                Options: "cuda" (NVIDIA), "mps" (Apple Silicon), "cpu"
    """
    global _embedder_instance
    if _embedder_instance is None:
        selected_device = device or get_best_device()
        _embedder_instance = BGEEmbedder(device=selected_device)
    return _embedder_instance
