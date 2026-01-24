"""
Pytest configuration and shared fixtures for KERBERUS tests.

This module provides common test fixtures for:
- Temporary database paths
- Mock database clients
- Sample legal documents
"""
import pytest
import os
from pathlib import Path

# Add src to Python path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ============================================
# Database Fixtures
# ============================================

@pytest.fixture
def temp_dossier_path(tmp_path):
    """
    Provide temporary directory for test databases.
    Automatically cleaned up after test completes.
    """
    dossier_dir = tmp_path / "dossier"
    dossier_dir.mkdir()
    return dossier_dir


@pytest.fixture
def mock_qdrant_client():
    """
    Mock Qdrant client for testing without actual database.
    Provides search and upsert functionality with mock data.
    """
    class MockQdrantClient:
        def __init__(self):
            self.collections = {}
            self.points = {}

        def create_collection(self, collection_name, vectors_config):
            self.collections[collection_name] = vectors_config
            self.points[collection_name] = []
            return {"status": "ok"}

        def search(self, collection_name, query_vector, limit=10, **kwargs):
            # Return mock search results
            return [
                {"id": "doc1", "score": 0.95, "payload": {"text": "Art. 337 OR: Der Arbeitgeber kann das Arbeitsverhältnis fristlos kündigen..."}},
                {"id": "doc2", "score": 0.85, "payload": {"text": "BGE 130 III 213: Zur fristlosen Kündigung wegen schwerer Pflichtverletzung..."}},
                {"id": "doc3", "score": 0.75, "payload": {"text": "TI_2023_045: Die Beschwerde wird abgewiesen..."}},
            ][:limit]

        def upsert(self, collection_name, points):
            if collection_name not in self.points:
                self.points[collection_name] = []
            self.points[collection_name].extend(points)
            return {"status": "ok", "operation_id": 1}

        def delete(self, collection_name, points_selector):
            return {"status": "ok"}

    return MockQdrantClient()


@pytest.fixture
def mock_redis_client():
    """
    Mock Redis client for testing sessions and rate limiting.
    Implements basic get/set/delete operations with in-memory store.
    """
    class MockRedisClient:
        def __init__(self):
            self.store = {}
            self.expiry = {}

        def get(self, key):
            return self.store.get(key)

        def set(self, key, value, ex=None):
            self.store[key] = value
            if ex:
                self.expiry[key] = ex
            return True

        def delete(self, key):
            if key in self.store:
                del self.store[key]
            if key in self.expiry:
                del self.expiry[key]
            return True

        def exists(self, key):
            return key in self.store

        def incr(self, key):
            if key not in self.store:
                self.store[key] = 0
            self.store[key] = int(self.store[key]) + 1
            return self.store[key]

        def expire(self, key, seconds):
            self.expiry[key] = seconds
            return True

    return MockRedisClient()


# ============================================
# Legal Content Fixtures
# ============================================

@pytest.fixture
def sample_legal_text():
    """
    Provide sample Swiss legal text in all three official languages.
    Art. 337 OR - Immediate termination for cause.
    """
    return {
        "german": "Art. 337 OR: Der Arbeitgeber und der Arbeitnehmer können das Arbeitsverhältnis aus wichtigen Gründen jederzeit fristlos auflösen; er muss die fristlose Vertragsauflösung schriftlich begründen, wenn die andere Partei dies verlangt.",
        "french": "Art. 337 CO: L'employeur et le travailleur peuvent résilier immédiatement le contrat en tout temps pour de justes motifs; la partie qui résilie immédiatement le contrat doit motiver sa décision par écrit si l'autre partie le demande.",
        "italian": "Art. 337 CO: Il datore di lavoro e il lavoratore possono in ogni tempo recedere immediatamente dal rapporto di lavoro per cause gravi; chi recede immediatamente dal contratto deve motivare per scritto la risoluzione immediata, se l'altra parte lo richiede."
    }


@pytest.fixture
def sample_judgment():
    """
    Provide sample judgment structure for testing parsers.
    Based on Ticino cantonal court format.
    """
    return {
        "case_id": "TI_2023_045",
        "court": "ti_cantonal",
        "date": "2023-06-15",
        "language": "de",
        "url": "https://example.com/ti/2023/045",
        "sections": {
            "facts": "Der Arbeitnehmer wurde am 15. März 2023 fristlos entlassen. Der Arbeitgeber begründete die Kündigung mit wiederholten unentschuldigten Abwesenheiten.",
            "law": "Gemäss Art. 337 OR kann der Arbeitgeber das Arbeitsverhältnis aus wichtigen Gründen jederzeit fristlos auflösen.",
            "outcome": "Die Beschwerde wird abgewiesen. Der Arbeitgeber hat die fristlose Kündigung rechtmässig ausgesprochen."
        },
        "outcome_class": "DENIED",
        "citations": ["Art. 337 OR", "BGE 130 III 213"]
    }


@pytest.fixture
def sample_user():
    """
    Provide sample user data for authentication tests.
    """
    return {
        "user_id": "550e8400-e29b-41d4-a716-446655440000",
        "email": "test@lawfirm.ch",
        "password": "secure_test_password_123!",
        "firm_id": "660e8400-e29b-41d4-a716-446655440001",
        "role": "associate"
    }


# ============================================
# Token Tracking Fixtures
# ============================================

@pytest.fixture
def sample_token_usage():
    """
    Provide sample token usage record for cost tracking tests.
    """
    return {
        "user_id": "550e8400-e29b-41d4-a716-446655440000",
        "conversation_id": "770e8400-e29b-41d4-a716-446655440002",
        "turn_number": 3,
        "input_tokens": 4500,
        "output_tokens": 800,
        "total_tokens": 5300,
        "chat_history_tokens": 450,
        "legal_context_tokens": 3800,
        "query_tokens": 250,
        "input_cost_chf": 0.00315,
        "output_cost_chf": 0.00176,
        "total_cost_chf": 0.00491,
        "model_used": "qwen3-vl-235b-instruct",
        "context_swapped": True
    }


# ============================================
# Context Management Fixtures
# ============================================

@pytest.fixture
def sample_conversation_context():
    """
    Provide sample conversation context for context swapping tests.
    """
    return {
        "chat_history": [
            {"role": "user", "content": "Can an employer terminate immediately?"},
            {"role": "assistant", "content": "Yes, under Art. 337 OR, immediate termination is possible for serious cause."},
            {"role": "user", "content": "What qualifies as serious cause?"},
        ],
        "legal_context": [
            {
                "source": "codex",
                "id": "or_337",
                "text": "Art. 337 OR: Der Arbeitgeber und der Arbeitnehmer können das Arbeitsverhältnis aus wichtigen Gründen jederzeit fristlos auflösen...",
                "relevance_score": 0.95
            },
            {
                "source": "library",
                "id": "bge_130_iii_213",
                "text": "BGE 130 III 213: Zur fristlosen Kündigung wegen schwerer Pflichtverletzung...",
                "relevance_score": 0.88
            }
        ],
        "current_query": "What qualifies as serious cause?"
    }
