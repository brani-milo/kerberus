"""Tests for BGE-M3 embedder with hybrid (dense + sparse) embeddings."""

import pytest
import asyncio
from src.embedder.bge_embedder import BGEEmbedder


def test_embedder_initialization():
    """Test that embedder loads successfully."""
    embedder = BGEEmbedder(device="cpu")  # Use CPU for tests
    assert embedder._model is not None


@pytest.mark.asyncio
async def test_encode_async_single():
    """Test async encoding of single text returns hybrid embedding."""
    embedder = BGEEmbedder(device="cpu")
    embedding = await embedder.encode_async("Art. 337 OR")

    # Should return dict with dense and sparse components
    assert isinstance(embedding, dict)
    assert "dense" in embedding
    assert "sparse" in embedding

    # Dense embedding should be 1024-dimensional
    assert len(embedding["dense"]) == 1024
    assert all(isinstance(x, (int, float)) for x in embedding["dense"])

    # Sparse embedding should be a dict of token weights
    assert isinstance(embedding["sparse"], dict)


def test_encode_batch():
    """Test batch encoding returns hybrid embeddings."""
    embedder = BGEEmbedder(device="cpu")
    texts = [
        "Art. 337 OR - Fristlose Kündigung",
        "Art. 335c CO - Délai de congé",
        "Art. 337 CO - Licenziamento immediato"
    ]
    embeddings = embedder.encode_batch(texts, batch_size=2)

    assert len(embeddings) == 3

    for emb in embeddings:
        # Each embedding should be a dict with dense and sparse
        assert isinstance(emb, dict)
        assert "dense" in emb
        assert "sparse" in emb
        assert len(emb["dense"]) == 1024


def test_multilingual_encoding():
    """Test encoding of German, French, Italian legal text."""
    embedder = BGEEmbedder(device="cpu")
    texts = {
        "de": "Das Bundesgericht weist die Beschwerde ab",
        "fr": "Le Tribunal fédéral rejette le recours",
        "it": "Il Tribunale federale respinge il ricorso"
    }

    for lang, text in texts.items():
        embedding = embedder._encode_single(text)
        # Should return hybrid embedding
        assert isinstance(embedding, dict)
        assert "dense" in embedding
        assert len(embedding["dense"]) == 1024


def test_sparse_embedding_contains_tokens():
    """Test that sparse embedding contains meaningful token weights."""
    embedder = BGEEmbedder(device="cpu")
    embedding = embedder._encode_single("Kündigung Arbeitsvertrag")

    # Sparse embedding should have some non-zero weights
    assert len(embedding["sparse"]) > 0

    # Weights should be positive numbers (may be numpy types)
    for token_id, weight in embedding["sparse"].items():
        assert float(weight) > 0  # Convert to float for comparison
