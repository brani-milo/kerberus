"""Tests for BGE-M3 embedder."""

import pytest
import asyncio
from src.embedder.bge_embedder import BGEEmbedder


def test_embedder_initialization():
    """Test that embedder loads successfully."""
    embedder = BGEEmbedder(device="cpu")  # Use CPU for tests
    assert embedder._model is not None


@pytest.mark.asyncio
async def test_encode_async_single():
    """Test async encoding of single text."""
    embedder = BGEEmbedder(device="cpu")
    embedding = await embedder.encode_async("Art. 337 OR")

    assert isinstance(embedding, list)
    assert len(embedding) == 1024
    assert all(isinstance(x, float) for x in embedding)


def test_encode_batch():
    """Test batch encoding."""
    embedder = BGEEmbedder(device="cpu")
    texts = [
        "Art. 337 OR - Fristlose Kündigung",
        "Art. 335c CO - Délai de congé",
        "Art. 337 CO - Licenziamento immediato"
    ]
    embeddings = embedder.encode_batch(texts, batch_size=2)

    assert len(embeddings) == 3
    assert all(len(emb) == 1024 for emb in embeddings)


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
        assert len(embedding) == 1024
