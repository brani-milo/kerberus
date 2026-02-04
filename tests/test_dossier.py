"""
Tests for Dossier API endpoints.

Covers:
- Document upload
- Document listing
- Document retrieval
- Document deletion
- Dossier search
- Dossier statistics
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from fastapi.testclient import TestClient
from io import BytesIO

from src.api.main import app
from src.database.auth_db import hash_password


# ============================================
# Test Fixtures
# ============================================

@pytest.fixture
def mock_auth():
    """Mock authentication for tests."""
    with patch("src.api.deps.get_db") as mock_db, \
         patch("src.api.routes.dossier.get_db") as mock_dossier_db:

        mock_db_instance = MagicMock()
        mock_db_instance.validate_session.return_value = {
            "user_id": "test-user-123",
            "email": "test@example.com",
            "is_active": True,
            "mfa_enabled": False,
        }
        mock_db_instance.get_user_by_id.return_value = {
            "user_id": "test-user-123",
            "email": "test@example.com",
            "password_hash": hash_password("test_password"),
        }

        mock_db.return_value = mock_db_instance
        mock_dossier_db.return_value = mock_db_instance

        yield mock_db_instance


@pytest.fixture
def mock_dossier_service():
    """Mock DossierSearchService."""
    with patch("src.api.routes.dossier.get_dossier_service") as mock:
        mock_service = MagicMock()
        mock_service.__enter__ = MagicMock(return_value=mock_service)
        mock_service.__exit__ = MagicMock(return_value=False)
        mock.return_value = mock_service
        yield mock_service


@pytest.fixture
def mock_doc_processor():
    """Mock DocumentProcessor."""
    with patch("src.api.routes.dossier.get_document_processor") as mock:
        mock_processor = MagicMock()

        # Create a mock parsed document
        mock_parsed = MagicMock()
        mock_parsed.full_text = "This is the document content."
        mock_parsed.filename = "test.pdf"
        mock_parsed.file_type = "pdf"
        mock_parsed.file_size = 1024
        mock_parsed.total_pages = 2
        mock_parsed.file_hash = "abc123"

        mock_processor.parse_bytes.return_value = mock_parsed
        mock.return_value = mock_processor
        yield mock_processor


# ============================================
# Document Upload Tests
# ============================================

class TestDocumentUpload:
    """Test document upload endpoint."""

    def test_upload_requires_auth(self):
        """Test that upload requires authentication."""
        client = TestClient(app)
        response = client.post(
            "/dossier/documents",
            files={"file": ("test.txt", b"content", "text/plain")},
            data={"password": "test"}
        )
        assert response.status_code == 401

    def test_upload_requires_password(self, mock_auth):
        """Test that upload requires password for dossier access."""
        client = TestClient(app)

        # Wrong password
        mock_auth.get_user_by_id.return_value = {
            "user_id": "test-user-123",
            "password_hash": hash_password("correct_password"),
        }

        response = client.post(
            "/dossier/documents",
            headers={"Authorization": "Bearer test-token"},
            files={"file": ("test.txt", b"content", "text/plain")},
            data={"password": "wrong_password"}
        )
        assert response.status_code == 400
        assert "password" in response.json()["detail"].lower()

    def test_upload_success(self, mock_auth, mock_dossier_service, mock_doc_processor):
        """Test successful document upload."""
        mock_auth.get_user_by_id.return_value = {
            "user_id": "test-user-123",
            "password_hash": hash_password("test_password"),
        }

        mock_dossier_service.add_document.return_value = "doc-123"
        mock_dossier_service.get_stats.return_value = {"chunk_count": 5}

        client = TestClient(app)
        response = client.post(
            "/dossier/documents",
            headers={"Authorization": "Bearer test-token"},
            files={"file": ("test.pdf", b"PDF content here", "application/pdf")},
            data={
                "password": "test_password",
                "title": "Test Document",
                "doc_type": "contract",
                "language": "de"
            }
        )

        assert response.status_code == 201
        data = response.json()
        assert data["doc_id"] == "doc-123"
        assert data["title"] == "Test Document"
        assert data["doc_type"] == "contract"

    def test_upload_with_pii_scrubbing(self, mock_auth, mock_dossier_service, mock_doc_processor):
        """Test document upload with PII scrubbing enabled."""
        mock_auth.get_user_by_id.return_value = {
            "user_id": "test-user-123",
            "password_hash": hash_password("test_password"),
        }

        # Set document content with PII
        mock_doc_processor.parse_bytes.return_value.full_text = "Email: test@example.com"

        mock_dossier_service.add_document.return_value = "doc-456"
        mock_dossier_service.get_stats.return_value = {"chunk_count": 1}

        client = TestClient(app)
        response = client.post(
            "/dossier/documents",
            headers={"Authorization": "Bearer test-token"},
            files={"file": ("test.txt", b"Email: test@example.com", "text/plain")},
            data={
                "password": "test_password",
                "scrub_pii": "true"
            }
        )

        assert response.status_code == 201
        # PII scrubbing is enabled, check response indicates it
        data = response.json()
        assert "pii_scrubbed" in data


# ============================================
# Document List Tests
# ============================================

class TestDocumentList:
    """Test document listing endpoint."""

    def test_list_documents(self, mock_auth, mock_dossier_service):
        """Test listing documents."""
        mock_auth.get_user_by_id.return_value = {
            "user_id": "test-user-123",
            "password_hash": hash_password("test_password"),
        }

        mock_dossier_service.list_documents.return_value = [
            {
                "doc_id": "doc-1",
                "title": "Contract 1",
                "doc_type": "contract",
                "language": "de",
                "created_at": "2024-01-01T00:00:00",
                "updated_at": "2024-01-01T00:00:00",
                "content_length": 1000,
            },
            {
                "doc_id": "doc-2",
                "title": "Letter 1",
                "doc_type": "letter",
                "language": "fr",
                "created_at": "2024-01-02T00:00:00",
                "updated_at": "2024-01-02T00:00:00",
                "content_length": 500,
            },
        ]

        client = TestClient(app)
        response = client.post(
            "/dossier/documents/list",
            headers={"Authorization": "Bearer test-token"},
            json={"password": "test_password"}
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["doc_id"] == "doc-1"
        assert data[1]["doc_id"] == "doc-2"


# ============================================
# Document Retrieval Tests
# ============================================

class TestDocumentRetrieval:
    """Test document retrieval endpoint."""

    def test_get_document(self, mock_auth, mock_dossier_service):
        """Test getting a specific document."""
        mock_auth.get_user_by_id.return_value = {
            "user_id": "test-user-123",
            "password_hash": hash_password("test_password"),
        }

        mock_dossier_service.get_document.return_value = {
            "doc_id": "doc-123",
            "title": "Test Contract",
            "content": "Full document content here...",
            "doc_type": "contract",
            "language": "de",
            "created_at": "2024-01-01T00:00:00",
            "updated_at": "2024-01-01T00:00:00",
            "metadata": {"original_filename": "contract.pdf"},
        }

        client = TestClient(app)
        response = client.post(
            "/dossier/documents/doc-123",
            headers={"Authorization": "Bearer test-token"},
            json={"password": "test_password"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["doc_id"] == "doc-123"
        assert "content" in data
        assert data["content"] == "Full document content here..."

    def test_get_document_not_found(self, mock_auth, mock_dossier_service):
        """Test getting non-existent document."""
        mock_auth.get_user_by_id.return_value = {
            "user_id": "test-user-123",
            "password_hash": hash_password("test_password"),
        }

        mock_dossier_service.get_document.return_value = None

        client = TestClient(app)
        response = client.post(
            "/dossier/documents/nonexistent",
            headers={"Authorization": "Bearer test-token"},
            json={"password": "test_password"}
        )

        assert response.status_code == 404


# ============================================
# Document Deletion Tests
# ============================================

class TestDocumentDeletion:
    """Test document deletion endpoint."""

    def test_delete_document(self, mock_auth, mock_dossier_service):
        """Test deleting a document."""
        mock_auth.get_user_by_id.return_value = {
            "user_id": "test-user-123",
            "password_hash": hash_password("test_password"),
        }

        mock_dossier_service.delete_document.return_value = True

        client = TestClient(app)
        response = client.request(
            "DELETE",
            "/dossier/documents/doc-123",
            headers={"Authorization": "Bearer test-token"},
            json={"password": "test_password"}
        )

        assert response.status_code == 204
        mock_dossier_service.delete_document.assert_called_once_with("doc-123")

    def test_delete_document_not_found(self, mock_auth, mock_dossier_service):
        """Test deleting non-existent document."""
        mock_auth.get_user_by_id.return_value = {
            "user_id": "test-user-123",
            "password_hash": hash_password("test_password"),
        }

        mock_dossier_service.delete_document.return_value = False

        client = TestClient(app)
        response = client.request(
            "DELETE",
            "/dossier/documents/nonexistent",
            headers={"Authorization": "Bearer test-token"},
            json={"password": "test_password"}
        )

        assert response.status_code == 404


# ============================================
# Dossier Search Tests
# ============================================

class TestDossierSearch:
    """Test dossier search endpoint."""

    def test_search_dossier(self, mock_auth, mock_dossier_service):
        """Test searching the dossier."""
        mock_auth.get_user_by_id.return_value = {
            "user_id": "test-user-123",
            "password_hash": hash_password("test_password"),
        }

        mock_dossier_service.search.return_value = [
            {
                "doc_id": "doc-1",
                "title": "Employment Contract",
                "doc_type": "contract",
                "score": 0.95,
                "text_preview": "The employee shall...",
                "chunk_index": 0,
            },
            {
                "doc_id": "doc-2",
                "title": "Termination Letter",
                "doc_type": "letter",
                "score": 0.82,
                "text_preview": "We regret to inform...",
                "chunk_index": 1,
            },
        ]

        client = TestClient(app)
        response = client.post(
            "/dossier/search",
            headers={"Authorization": "Bearer test-token"},
            json={
                "password": "test_password",
                "query": "termination clause",
                "limit": 10
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "termination clause"
        assert len(data["results"]) == 2
        assert data["results"][0]["score"] == 0.95

    def test_search_with_filters(self, mock_auth, mock_dossier_service):
        """Test searching with document type filter."""
        mock_auth.get_user_by_id.return_value = {
            "user_id": "test-user-123",
            "password_hash": hash_password("test_password"),
        }

        mock_dossier_service.search.return_value = []

        client = TestClient(app)
        response = client.post(
            "/dossier/search",
            headers={"Authorization": "Bearer test-token"},
            json={
                "password": "test_password",
                "query": "termination",
                "doc_type": "contract",
                "multilingual": True
            }
        )

        assert response.status_code == 200
        mock_dossier_service.search.assert_called_once()
        call_kwargs = mock_dossier_service.search.call_args[1]
        assert call_kwargs["doc_type"] == "contract"
        assert call_kwargs["multilingual"] == True


# ============================================
# Dossier Stats Tests
# ============================================

class TestDossierStats:
    """Test dossier statistics endpoint."""

    def test_get_stats(self, mock_auth, mock_dossier_service):
        """Test getting dossier statistics."""
        mock_auth.get_user_by_id.return_value = {
            "user_id": "test-user-123",
            "password_hash": hash_password("test_password"),
        }

        mock_dossier_service.get_stats.return_value = {
            "document_count": 15,
            "chunk_count": 120,
            "vector_count": 120,
            "file_size_mb": 2.5,
            "collection_name": "dossier_user_test-user-123",
        }

        client = TestClient(app)
        response = client.post(
            "/dossier/stats",
            headers={"Authorization": "Bearer test-token"},
            json={"password": "test_password"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["document_count"] == 15
        assert data["chunk_count"] == 120
        assert data["file_size_mb"] == 2.5


# ============================================
# Error Handling Tests
# ============================================

class TestErrorHandling:
    """Test error handling in dossier endpoints."""

    def test_invalid_file_type(self, mock_auth, mock_doc_processor):
        """Test upload with unsupported file type."""
        mock_auth.get_user_by_id.return_value = {
            "user_id": "test-user-123",
            "password_hash": hash_password("test_password"),
        }

        mock_doc_processor.parse_bytes.side_effect = ValueError("Unsupported file type: .xyz")

        client = TestClient(app)
        response = client.post(
            "/dossier/documents",
            headers={"Authorization": "Bearer test-token"},
            files={"file": ("test.xyz", b"content", "application/octet-stream")},
            data={"password": "test_password"}
        )

        assert response.status_code == 400
        assert "unsupported" in response.json()["detail"].lower()

    def test_dossier_decryption_failure(self, mock_auth, mock_dossier_service):
        """Test handling of dossier decryption failure."""
        mock_auth.get_user_by_id.return_value = {
            "user_id": "test-user-123",
            "password_hash": hash_password("test_password"),
        }

        # Simulate decryption error
        mock_dossier_service.__enter__.side_effect = ValueError("Invalid password or corrupted database")

        client = TestClient(app)
        response = client.post(
            "/dossier/documents/list",
            headers={"Authorization": "Bearer test-token"},
            json={"password": "test_password"}
        )

        assert response.status_code == 400
