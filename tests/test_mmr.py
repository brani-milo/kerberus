"""Tests for MMR algorithm."""

import pytest
from src.search.mmr import apply_mmr, cosine_similarity


def test_cosine_similarity():
    """Test cosine similarity calculation."""
    vec1 = [1.0, 0.0, 0.0]
    vec2 = [1.0, 0.0, 0.0]
    vec3 = [0.0, 1.0, 0.0]

    # Identical vectors
    assert cosine_similarity(vec1, vec2) == pytest.approx(1.0)

    # Orthogonal vectors
    assert cosine_similarity(vec1, vec3) == pytest.approx(0.0)


def test_mmr_diversity():
    """Test that MMR promotes diversity."""
    query_embedding = [1.0] * 1024

    # Create documents where:
    # - doc1, doc2, doc3 are all very similar to each other
    # - doc4 is different but still relevant to query
    candidates = [
        {'id': 'doc1', 'embedding': [1.0] * 512 + [0.1] * 512, 'score': 0.95, 'text': 'Doc 1'},
        {'id': 'doc2', 'embedding': [1.0] * 512 + [0.1] * 512, 'score': 0.94, 'text': 'Doc 2'},
        {'id': 'doc3', 'embedding': [1.0] * 512 + [0.1] * 512, 'score': 0.93, 'text': 'Doc 3'},
        {'id': 'doc4', 'embedding': [0.1] * 512 + [1.0] * 512, 'score': 0.80, 'text': 'Doc 4'},
    ]

    # Apply MMR with strong diversity emphasis
    selected = apply_mmr(candidates, query_embedding, lambda_param=0.3, top_k=3)

    # Verify we got 3 results
    assert len(selected) == 3

    # First result should always be doc1 (highest score)
    assert selected[0]['id'] == 'doc1'

    # With low lambda (high diversity emphasis), doc4 should be selected
    # because it's very different from doc1/2/3 even though less relevant
    ids = [doc['id'] for doc in selected]
    assert 'doc4' in ids, f"Expected doc4 in results for diversity, got {ids}"


def test_mmr_preserves_top_result():
    """Test that MMR always includes top result."""
    query_embedding = [1.0] * 1024

    candidates = [
        {'id': 'top', 'embedding': [1.0] * 1024, 'score': 0.95, 'text': 'Top result'},
        {'id': 'second', 'embedding': [0.8] * 1024, 'score': 0.85, 'text': 'Second result'}
    ]

    selected = apply_mmr(candidates, query_embedding, lambda_param=0.7, top_k=2)

    # Top result should always be first
    assert selected[0]['id'] == 'top'
