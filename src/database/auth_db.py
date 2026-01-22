"""
PostgreSQL Database Manager for Authentication and Metadata.

This module provides connection management and operations for:
- User authentication (registration, login)
- Session management
- Firm membership
- Token usage tracking

SECURITY NOTE: This database does NOT contain sensitive legal content.
All legal documents are stored in SQLCipher-encrypted databases (dossier_db.py).
"""
import os
from typing import Optional, Dict, List
from datetime import datetime, timedelta
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool


class AuthDB:
    """
    PostgreSQL connection manager for authentication and metadata.

    This class handles all non-sensitive data:
    - User accounts (email, password hash, MFA)
    - Sessions and JWT tracking
    - Firm memberships
    - Token usage for cost monitoring

    Example usage:
        auth_db = AuthDB()

        # Create user
        user_id = auth_db.create_user("user@lawfirm.ch", hashed_password)

        # Track token usage
        auth_db.record_token_usage(user_id, token_record)

        # Get monthly costs
        costs = auth_db.get_user_monthly_costs(user_id)
    """

    def __init__(self, connection_string: Optional[str] = None):
        """
        Initialize database connection.

        Args:
            connection_string: PostgreSQL connection string.
                             Uses environment variables if not provided.
        """
        if connection_string is None:
            host = os.getenv("POSTGRES_HOST", "localhost")
            port = os.getenv("POSTGRES_PORT", "5432")
            db = os.getenv("POSTGRES_DB", "kerberus_dev")
            user = os.getenv("POSTGRES_USER", "kerberus_user")
            password = os.getenv("POSTGRES_PASSWORD", "")
            connection_string = f"postgresql://{user}:{password}@{host}:{port}/{db}"

        self.engine = create_engine(
            connection_string,
            poolclass=QueuePool,
            pool_size=10,
            max_overflow=20,
            pool_timeout=30
        )
        self.Session = sessionmaker(bind=self.engine)

    @contextmanager
    def get_session(self):
        """
        Get a database session with automatic cleanup.

        Usage:
            with auth_db.get_session() as session:
                result = session.execute(query)
        """
        session = self.Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ==========================================
    # User Management (Stub - Day 2-3)
    # ==========================================

    def create_user(self, email: str, password_hash: str, totp_secret: Optional[str] = None) -> str:
        """
        Create a new user account.

        Args:
            email: User's email address.
            password_hash: Bcrypt-hashed password.
            totp_secret: Optional TOTP secret for MFA.

        Returns:
            UUID of created user.

        Raises:
            ValueError: If email already exists.
        """
        # TODO: Implement on Day 2-3
        raise NotImplementedError("User creation will be implemented on Day 2-3")

    def get_user_by_email(self, email: str) -> Optional[Dict]:
        """
        Get user by email address.

        Args:
            email: User's email address.

        Returns:
            User dict or None if not found.
        """
        # TODO: Implement on Day 2-3
        raise NotImplementedError("User retrieval will be implemented on Day 2-3")

    def update_last_login(self, user_id: str) -> None:
        """
        Update user's last login timestamp.

        Args:
            user_id: UUID of user.
        """
        # TODO: Implement on Day 2-3
        raise NotImplementedError("Last login update will be implemented on Day 2-3")

    # ==========================================
    # Session Management (Stub - Day 2-3)
    # ==========================================

    def create_session(self, user_id: str, device_fingerprint: Optional[str] = None) -> str:
        """
        Create a new session for a user.

        Args:
            user_id: UUID of user.
            device_fingerprint: Optional device identifier.

        Returns:
            Session token.
        """
        # TODO: Implement on Day 2-3
        raise NotImplementedError("Session creation will be implemented on Day 2-3")

    def validate_session(self, session_token: str) -> Optional[Dict]:
        """
        Validate a session token.

        Args:
            session_token: The session token to validate.

        Returns:
            User dict if valid, None if invalid/expired.
        """
        # TODO: Implement on Day 2-3
        raise NotImplementedError("Session validation will be implemented on Day 2-3")

    def invalidate_session(self, session_token: str) -> None:
        """
        Invalidate (logout) a session.

        Args:
            session_token: The session token to invalidate.
        """
        # TODO: Implement on Day 2-3
        raise NotImplementedError("Session invalidation will be implemented on Day 2-3")

    # ==========================================
    # Token Usage Tracking (Stub - Day 5)
    # ==========================================

    def record_token_usage(self, user_id: str, usage_record: Dict) -> int:
        """
        Record token usage for cost tracking.

        Args:
            user_id: UUID of user.
            usage_record: Dict with token counts and costs.

        Returns:
            ID of created record.
        """
        # TODO: Implement on Day 5
        raise NotImplementedError("Token usage tracking will be implemented on Day 5")

    def get_user_monthly_costs(self, user_id: str, year: int = None, month: int = None) -> Dict:
        """
        Get user's token usage and costs for a month.

        Args:
            user_id: UUID of user.
            year: Year (default: current year).
            month: Month (default: current month).

        Returns:
            Dict with total_tokens, total_cost_chf, query_count.
        """
        # TODO: Implement on Day 5
        raise NotImplementedError("Monthly cost retrieval will be implemented on Day 5")

    def get_users_exceeding_threshold(self, threshold_chf: float) -> List[Dict]:
        """
        Get users who have exceeded cost threshold this month.

        Args:
            threshold_chf: Cost threshold in CHF.

        Returns:
            List of user dicts with their costs.
        """
        # TODO: Implement on Day 5
        raise NotImplementedError("Threshold alerting will be implemented on Day 5")

    # ==========================================
    # Firm Management (Stub - Phase 2)
    # ==========================================

    def create_firm(self, firm_name: str, master_key_reference: str) -> str:
        """
        Create a new law firm.

        Args:
            firm_name: Name of the firm.
            master_key_reference: Reference to KMS-stored master key.

        Returns:
            UUID of created firm.
        """
        # TODO: Implement in Phase 2
        raise NotImplementedError("Firm creation will be implemented in Phase 2")

    def add_firm_member(self, user_id: str, firm_id: str, role: str) -> None:
        """
        Add a user as a firm member.

        Args:
            user_id: UUID of user.
            firm_id: UUID of firm.
            role: Role in firm (partner, associate, paralegal).
        """
        # TODO: Implement in Phase 2
        raise NotImplementedError("Firm membership will be implemented in Phase 2")

    def get_user_firms(self, user_id: str) -> List[Dict]:
        """
        Get all firms a user belongs to.

        Args:
            user_id: UUID of user.

        Returns:
            List of firm dicts with user's role.
        """
        # TODO: Implement in Phase 2
        raise NotImplementedError("Firm listing will be implemented in Phase 2")


# Singleton instance
_auth_db_instance: Optional[AuthDB] = None


def get_auth_db() -> AuthDB:
    """
    Get singleton AuthDB instance.

    Returns:
        AuthDB instance.
    """
    global _auth_db_instance
    if _auth_db_instance is None:
        _auth_db_instance = AuthDB()
    return _auth_db_instance
