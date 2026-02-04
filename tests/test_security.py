"""
Tests for security features.

Covers:
- Security headers middleware
- Logout session invalidation
- Password change endpoint
- Account lockout
- Auth rate limiting
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, timezone

from src.api.main import app
from src.api.deps import get_db, get_current_user, check_login_rate_limit
from src.database.auth_db import hash_password


# No-op rate limit dependency for tests
async def no_rate_limit():
    """No-op rate limit check for tests."""
    pass


# ============================================
# Test Fixtures
# ============================================

@pytest.fixture
def mock_db():
    """Create a mock AuthDB instance."""
    mock = MagicMock()
    mock.validate_session.return_value = {
        "user_id": "test-user-id",
        "email": "test@example.com",
        "is_active": True,
        "mfa_enabled": False,
    }
    mock.get_failed_login_count.return_value = 0
    return mock


@pytest.fixture
def authenticated_user():
    """Return a mock authenticated user dict."""
    return {
        "user_id": "test-user-id",
        "email": "test@example.com",
        "is_active": True,
        "mfa_enabled": False,
        "_session_token": "test-token-123",
    }


@pytest.fixture
def client_with_mocks(mock_db, authenticated_user):
    """Create test client with mocked dependencies."""
    # Override dependencies
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: authenticated_user

    client = TestClient(app)
    yield client, mock_db, authenticated_user

    # Clean up overrides
    app.dependency_overrides.clear()


# ============================================
# Security Headers Tests
# ============================================

class TestSecurityHeaders:
    """Test security headers middleware."""

    def test_security_headers_present(self):
        """Test that all security headers are present in response."""
        client = TestClient(app)
        response = client.get("/health")

        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-XSS-Protection") == "1; mode=block"
        assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "geolocation=()" in response.headers.get("Permissions-Policy", "")
        assert "default-src 'none'" in response.headers.get("Content-Security-Policy", "")

    def test_process_time_header(self):
        """Test that X-Process-Time-Ms header is present."""
        client = TestClient(app)
        response = client.get("/health")

        assert "X-Process-Time-Ms" in response.headers
        process_time = float(response.headers["X-Process-Time-Ms"])
        assert process_time >= 0


# ============================================
# Session Invalidation Tests
# ============================================

class TestLogoutInvalidation:
    """Test logout properly invalidates sessions."""

    def test_logout_invalidates_session(self, client_with_mocks):
        """Test that logout calls invalidate_session."""
        client, mock_db, user = client_with_mocks

        response = client.post(
            "/auth/logout",
            headers={"Authorization": "Bearer test-token-123"}
        )

        assert response.status_code == 204
        mock_db.invalidate_session.assert_called_once_with("test-token-123")


# ============================================
# Password Change Tests
# ============================================

class TestPasswordChange:
    """Test password change endpoint."""

    def test_password_change_wrong_current_password(self, client_with_mocks):
        """Test password change fails with wrong current password."""
        client, mock_db, user = client_with_mocks

        mock_db.get_user_by_id.return_value = {
            "user_id": "test-user-id",
            "email": "test@example.com",
            "password_hash": hash_password("correct_password"),
        }

        response = client.post(
            "/auth/password/change",
            headers={"Authorization": "Bearer test-token"},
            json={
                "current_password": "wrong_password",
                "new_password": "new_secure_password_123"
            }
        )

        assert response.status_code == 401
        assert "incorrect" in response.json()["detail"].lower()

    def test_password_change_success(self, client_with_mocks):
        """Test successful password change."""
        client, mock_db, user = client_with_mocks

        mock_db.get_user_by_id.return_value = {
            "user_id": "test-user-id",
            "email": "test@example.com",
            "password_hash": hash_password("current_password"),
        }

        response = client.post(
            "/auth/password/change",
            headers={"Authorization": "Bearer test-token"},
            json={
                "current_password": "current_password",
                "new_password": "new_secure_password_123"
            }
        )

        assert response.status_code == 204
        mock_db.update_password.assert_called_once()
        mock_db.invalidate_all_sessions.assert_called_once()


# ============================================
# Account Lockout Tests
# ============================================

class TestAccountLockout:
    """Test account lockout after failed logins."""

    def test_lockout_after_max_attempts(self):
        """Test account is locked after MAX_FAILED_ATTEMPTS."""
        mock_db = MagicMock()
        mock_db.get_failed_login_count.return_value = 5  # At limit

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[check_login_rate_limit] = no_rate_limit

        try:
            client = TestClient(app)
            response = client.post(
                "/auth/login",
                json={"email": "test@example.com", "password": "any_password"}
            )

            assert response.status_code == 429
            assert "locked" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    def test_failed_login_records_attempt(self):
        """Test failed login records the attempt."""
        mock_db = MagicMock()
        mock_db.get_failed_login_count.return_value = 0
        mock_db.get_user_by_email.return_value = None  # User not found

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[check_login_rate_limit] = no_rate_limit

        try:
            client = TestClient(app)
            response = client.post(
                "/auth/login",
                json={"email": "nonexistent@example.com", "password": "any_password"}
            )

            assert response.status_code == 401
            mock_db.record_failed_login.assert_called_once_with("nonexistent@example.com")
        finally:
            app.dependency_overrides.clear()

    def test_successful_login_clears_failed_attempts(self):
        """Test successful login clears failed attempts."""
        mock_db = MagicMock()
        mock_db.get_failed_login_count.return_value = 2
        mock_db.get_user_by_email.return_value = {
            "user_id": "test-id",
            "email": "test@example.com",
            "password_hash": hash_password("correct_password"),
            "is_active": True,
            "mfa_enabled": False,
        }
        mock_db.create_session.return_value = "new-session-token"

        app.dependency_overrides[get_db] = lambda: mock_db
        app.dependency_overrides[check_login_rate_limit] = no_rate_limit

        try:
            client = TestClient(app)
            response = client.post(
                "/auth/login",
                json={"email": "test@example.com", "password": "correct_password"}
            )

            assert response.status_code == 200
            mock_db.clear_failed_logins.assert_called_once_with("test@example.com")
        finally:
            app.dependency_overrides.clear()


# ============================================
# Auth Rate Limiting Tests
# ============================================

class TestAuthRateLimiting:
    """Test IP-based rate limiting for auth endpoints."""

    def test_rate_limiter_initialization(self):
        """Test AuthRateLimiter initializes correctly."""
        from src.api.deps import AuthRateLimiter

        limiter = AuthRateLimiter(redis_client=None)
        assert limiter.register_limit == 5
        assert limiter.login_limit == 10

    def test_register_limit_check(self):
        """Test register rate limit checking."""
        from src.api.deps import AuthRateLimiter

        limiter = AuthRateLimiter(redis_client=None)

        # First 5 requests should be allowed
        for i in range(5):
            allowed, remaining = limiter.check_register_limit("192.168.1.1")
            if allowed:
                limiter.record_register("192.168.1.1")

        # 6th request should be denied
        allowed, remaining = limiter.check_register_limit("192.168.1.1")
        assert not allowed
        assert remaining == 0

    def test_login_limit_check(self):
        """Test login rate limit checking."""
        from src.api.deps import AuthRateLimiter

        limiter = AuthRateLimiter(redis_client=None)

        # First 10 requests should be allowed
        for i in range(10):
            allowed, remaining = limiter.check_login_limit("192.168.1.1")
            if allowed:
                limiter.record_login("192.168.1.1")

        # 11th request should be denied
        allowed, remaining = limiter.check_login_limit("192.168.1.1")
        assert not allowed

    def test_different_ips_have_separate_limits(self):
        """Test that different IPs have separate rate limits."""
        from src.api.deps import AuthRateLimiter

        limiter = AuthRateLimiter(redis_client=None)

        # Exhaust limit for IP 1
        for _ in range(5):
            limiter.record_register("192.168.1.1")

        # IP 1 should be blocked
        allowed1, _ = limiter.check_register_limit("192.168.1.1")
        assert not allowed1

        # IP 2 should still be allowed
        allowed2, _ = limiter.check_register_limit("192.168.1.2")
        assert allowed2


# ============================================
# Failed Login Tracking DB Tests
# ============================================

class TestFailedLoginTracking:
    """Test failed login tracking in database."""

    def test_record_failed_login(self):
        """Test recording failed login attempt."""
        from src.database.auth_db import AuthDB

        with patch.object(AuthDB, 'get_session') as mock_session:
            mock_context = MagicMock()
            mock_cursor = MagicMock()
            mock_context.__enter__ = MagicMock(return_value=mock_cursor)
            mock_context.__exit__ = MagicMock(return_value=False)
            mock_session.return_value = mock_context

            db = AuthDB.__new__(AuthDB)
            db.Session = MagicMock()

            # Mock get_failed_login_count to avoid recursion
            with patch.object(db, 'get_failed_login_count', return_value=1):
                count = db.record_failed_login("test@example.com")

            mock_cursor.execute.assert_called()
            assert count == 1

    def test_get_failed_login_count(self):
        """Test getting failed login count."""
        from src.database.auth_db import AuthDB

        with patch.object(AuthDB, 'get_session') as mock_session:
            # Create the mock chain: session.execute(...).fetchone() -> (3,)
            mock_session_instance = MagicMock()
            mock_result = MagicMock()
            mock_result.fetchone.return_value = (3,)
            mock_session_instance.execute.return_value = mock_result

            # Setup context manager
            mock_context = MagicMock()
            mock_context.__enter__ = MagicMock(return_value=mock_session_instance)
            mock_context.__exit__ = MagicMock(return_value=False)
            mock_session.return_value = mock_context

            db = AuthDB.__new__(AuthDB)
            db.Session = MagicMock()

            count = db.get_failed_login_count("test@example.com")

            assert count == 3
