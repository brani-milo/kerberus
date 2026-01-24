"""Tests for BGE-Reranker."""

import pytest
from datetime import datetime
from src.reranker.bge_reranker import BGEReranker


def test_reranker_initialization():
    """Test that reranker loads successfully."""
    reranker = BGEReranker(device="cpu")
    assert reranker._reranker is not None


def test_rerank_basic():
    """Test basic reranking."""
    reranker = BGEReranker(device="cpu")

    query = "termination for cause"
    documents = [
        {'text': 'Art. 337 OR discusses termination for cause'},
        {'text': 'Art. 335c OR discusses notice periods'},
        {'text': 'Art. 336 OR discusses wrongful termination'}
    ]

    reranked = reranker.rerank(query, documents, top_k=3)

    assert len(reranked) == 3
    assert all('rerank_score' in doc for doc in reranked)
    # First result should have highest score
    assert reranked[0]['rerank_score'] >= reranked[1]['rerank_score']


def test_confidence_scoring():
    """Test confidence level calculation."""
    reranker = BGEReranker(device="cpu")

    query = "Art. 337 OR termination"
    documents = [
        {'text': 'Art. 337 OR allows termination for cause immediately'},
        {'text': 'This is completely unrelated text about vacation'}
    ]

    result = reranker.rerank_with_confidence(query, documents, top_k=2)

    assert 'confidence' in result
    assert result['confidence'] in ['HIGH', 'MEDIUM', 'LOW', 'NONE']
    assert 'top_score' in result
    assert 'score_variance' in result


def test_recency_boost_with_metadata():
    """Test recency boost with realistic Swiss legal metadata."""
    reranker = BGEReranker(device="cpu")

    query = "Art. 337 OR termination"
    current_year = datetime.now().year

    documents = [
        {
            'text': 'Bundesgericht confirms immediate termination for theft...',
            'metadata': {
                'case_id': 'BGE_140_III_348',
                'year': current_year - 12,
                'court': 'bundesgericht',
                'law_type': 'civil',
                'domain': 'employment',
                'outcome': 'ACCEPTED',
                'articles': ['Art. 337 OR']
            }
        },
        {
            'text': 'Ticino cantonal court rejects termination for minor theft...',
            'metadata': {
                'case_id': 'TI_2023_045',
                'year': current_year - 3,
                'court': 'ti_cantonal',
                'law_type': 'civil',
                'domain': 'employment',
                'outcome': 'REJECTED',
                'articles': ['Art. 337 OR', 'Art. 336 OR']
            }
        }
    ]

    reranked = reranker.rerank(query, documents, top_k=2, recency_weight=0.1)

    # Verify scoring fields added
    assert 'base_score' in reranked[0]
    assert 'recency_score' in reranked[0]
    assert 'final_score' in reranked[0]
    assert 'year' in reranked[0]

    # Verify metadata preserved
    assert 'metadata' in reranked[0]
    assert 'case_id' in reranked[0]['metadata']
    assert 'law_type' in reranked[0]['metadata']
    assert 'outcome' in reranked[0]['metadata']

    # If base scores similar, newer should rank higher
    if abs(reranked[0]['base_score'] - reranked[1]['base_score']) < 0.05:
        assert reranked[0]['year'] >= reranked[1]['year']


def test_year_extraction_fallbacks():
    """Test year extraction with various metadata formats."""
    reranker = BGEReranker(device="cpu")

    # Test metadata.year (primary)
    doc1 = {'metadata': {'year': 2023}}
    assert reranker.extract_year(doc1) == 2023

    # Test top-level year (fallback)
    doc2 = {'year': 2022}
    assert reranker.extract_year(doc2) == 2022

    # Test date string extraction (ISO format)
    doc3 = {'metadata': {'date': '2021-05-15'}}
    assert reranker.extract_year(doc3) == 2021

    # Test date string extraction (European format)
    doc4 = {'metadata': {'date': '15.05.2020'}}
    assert reranker.extract_year(doc4) == 2020

    # Test missing year (default)
    doc5 = {'metadata': {'case_id': 'TEST'}}
    assert reranker.extract_year(doc5) == 2000

    # Test date_decided fallback
    doc6 = {'metadata': {'date_decided': '2019-12-31'}}
    assert reranker.extract_year(doc6) == 2019


def test_metadata_preservation():
    """Test that ALL metadata is preserved through reranking."""
    reranker = BGEReranker(device="cpu")

    query = "employment law"
    documents = [
        {
            'text': 'Test document',
            'metadata': {
                'case_id': 'TEST_001',
                'year': 2023,
                'court': 'bundesgericht',
                'law_type': 'civil',
                'domain': 'employment',
                'outcome': 'ACCEPTED',
                'articles': ['Art. 337 OR', 'Art. 336 OR'],
                'amount_chf': 50000,
                'chamber': 'II',
                'is_landmark': True,
                'custom_field': 'custom_value'  # Even custom fields preserved
            }
        }
    ]

    reranked = reranker.rerank(query, documents, top_k=1)

    # All metadata fields should be preserved
    assert reranked[0]['metadata']['case_id'] == 'TEST_001'
    assert reranked[0]['metadata']['law_type'] == 'civil'
    assert reranked[0]['metadata']['outcome'] == 'ACCEPTED'
    assert reranked[0]['metadata']['amount_chf'] == 50000
    assert reranked[0]['metadata']['custom_field'] == 'custom_value'
    assert reranked[0]['metadata']['articles'] == ['Art. 337 OR', 'Art. 336 OR']
