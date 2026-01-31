"""
Embedding generation using BGE-M3.

This package provides multilingual embedding generation for:
- Swiss legal documents (DE/FR/IT)
- User queries
- Document similarity matching
"""

from .bge_embedder import BGEEmbedder, get_embedder
from .batch_processor import BatchEmbeddingProcessor

__all__ = ["BGEEmbedder", "get_embedder", "BatchEmbeddingProcessor"]
