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
import uuid
import json
import secrets
import logging
from typing import Optional, Dict, List
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

import bcrypt
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

logger = logging.getLogger(__name__)


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
            db = os.getenv("POSTGRES_DB", "kerberus")
            user = os.getenv("POSTGRES_USER", "kerberus_user")
            password = os.getenv("POSTGRES_PASSWORD", "")
            connection_string = f"postgresql://{user}:{password}@{host}:{port}/{db}"

        self.engine = create_engine(
            connection_string,
            poolclass=QueuePool,
            pool_size=10,
            max_overflow=20,
            pool_timeout=30,
            pool_pre_ping=True,  # Test connections before use (detect stale)
            pool_recycle=300,    # Recycle connections every 5 minutes
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
        user_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        with self.get_session() as session:
            # Check if email already exists
            result = session.execute(
                text("SELECT user_id FROM users WHERE email = :email"),
                {"email": email.lower().strip()}
            ).fetchone()

            if result:
                raise ValueError(f"User with email '{email}' already exists")

            # Insert new user
            session.execute(
                text("""
                    INSERT INTO users (
                        user_id, email, password_hash, totp_secret,
                        is_active, mfa_enabled, created_at, updated_at
                    ) VALUES (
                        CAST(:user_id AS UUID), :email, :password_hash, :totp_secret,
                        TRUE, :mfa_enabled, :created_at, :updated_at
                    )
                """),
                {
                    "user_id": user_id,
                    "email": email.lower().strip(),
                    "password_hash": password_hash,
                    "totp_secret": totp_secret,
                    "mfa_enabled": totp_secret is not None,
                    "created_at": now,
                    "updated_at": now
                }
            )

        logger.info(f"Created user: {email} (id={user_id})")
        return user_id

    def get_user_by_email(self, email: str) -> Optional[Dict]:
        """
        Get user by email address.

        Args:
            email: User's email address.

        Returns:
            User dict or None if not found.
        """
        with self.get_session() as session:
            result = session.execute(
                text("""
                    SELECT user_id, email, password_hash, totp_secret,
                           is_active, mfa_enabled, last_login, created_at
                    FROM users
                    WHERE email = :email
                """),
                {"email": email.lower().strip()}
            ).fetchone()

            if not result:
                return None

            return {
                "user_id": result[0],
                "email": result[1],
                "password_hash": result[2],
                "totp_secret": result[3],
                "is_active": result[4],
                "mfa_enabled": result[5],
                "last_login": result[6],
                "created_at": result[7]
            }

    def update_last_login(self, user_id: str) -> None:
        """
        Update user's last login timestamp.

        Args:
            user_id: UUID of user.
        """
        with self.get_session() as session:
            session.execute(
                text("""
                    UPDATE users
                    SET last_login = :now, updated_at = :now
                    WHERE user_id = :user_id
                """),
                {"user_id": user_id, "now": datetime.now(timezone.utc)}
            )

    # ==========================================
    # Session Management (Stub - Day 2-3)
    # ==========================================

    def create_session(
        self,
        user_id: str,
        device_fingerprint: Optional[str] = None,
        expires_hours: int = 24
    ) -> str:
        """
        Create a new session for a user.

        Args:
            user_id: UUID of user.
            device_fingerprint: Optional device identifier.
            expires_hours: Session expiration in hours (default 24).

        Returns:
            Session token (secure random 64-char hex string).
        """
        session_token = secrets.token_hex(32)  # 64 char hex string
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=expires_hours)

        with self.get_session() as session:
            session.execute(
                text("""
                    INSERT INTO sessions (
                        session_token, user_id, device_fingerprint,
                        created_at, expires_at, is_active
                    ) VALUES (
                        :session_token, CAST(:user_id AS UUID), :device_fingerprint,
                        :created_at, :expires_at, TRUE
                    )
                """),
                {
                    "session_token": session_token,
                    "user_id": user_id,
                    "device_fingerprint": device_fingerprint,
                    "created_at": now,
                    "expires_at": expires_at
                }
            )

        logger.debug(f"Created session for user {user_id}, expires {expires_at}")
        return session_token

    def validate_session(self, session_token: str) -> Optional[Dict]:
        """
        Validate a session token.

        Args:
            session_token: The session token to validate.

        Returns:
            User dict if valid, None if invalid/expired.
        """
        now = datetime.now(timezone.utc)

        with self.get_session() as session:
            result = session.execute(
                text("""
                    SELECT u.user_id, u.email, u.is_active, u.mfa_enabled,
                           s.expires_at, s.device_fingerprint
                    FROM sessions s
                    JOIN users u ON s.user_id = u.user_id
                    WHERE s.session_token = :token
                      AND s.is_active = TRUE
                      AND s.expires_at > :now
                      AND u.is_active = TRUE
                """),
                {"token": session_token, "now": now}
            ).fetchone()

            if not result:
                return None

            return {
                "user_id": result[0],
                "email": result[1],
                "is_active": result[2],
                "mfa_enabled": result[3],
                "session_expires_at": result[4],
                "device_fingerprint": result[5]
            }

    def invalidate_session(self, session_token: str) -> None:
        """
        Invalidate (logout) a session.

        Args:
            session_token: The session token to invalidate.
        """
        with self.get_session() as session:
            session.execute(
                text("""
                    UPDATE sessions
                    SET is_active = FALSE
                    WHERE session_token = :token
                """),
                {"token": session_token}
            )
        logger.debug(f"Invalidated session")

    # ==========================================
    # Token Usage Tracking (Stub - Day 5)
    # ==========================================

    def record_token_usage(self, user_id: str, usage_record: Dict) -> int:
        """
        Record token usage for cost tracking.

        Args:
            user_id: UUID of user.
            usage_record: Dict with keys:
                - model: str (e.g., "mistral-nemo")
                - input_tokens: int
                - output_tokens: int
                - cost_chf: float (calculated cost)
                - operation: str (e.g., "search", "chat", "embed")

        Returns:
            ID of created record.
        """
        now = datetime.now(timezone.utc)

        with self.get_session() as session:
            result = session.execute(
                text("""
                    INSERT INTO token_usage (
                        user_id, model, input_tokens, output_tokens,
                        cost_chf, operation, created_at
                    ) VALUES (
                        :user_id, :model, :input_tokens, :output_tokens,
                        :cost_chf, :operation, :created_at
                    )
                    RETURNING usage_id
                """),
                {
                    "user_id": user_id,
                    "model": usage_record.get("model", "unknown"),
                    "input_tokens": usage_record.get("input_tokens", 0),
                    "output_tokens": usage_record.get("output_tokens", 0),
                    "cost_chf": usage_record.get("cost_chf", 0.0),
                    "operation": usage_record.get("operation", "unknown"),
                    "created_at": now
                }
            ).fetchone()

            return result[0]

    def get_user_monthly_costs(self, user_id: str, year: int = None, month: int = None) -> Dict:
        """
        Get user's token usage and costs for a month.

        Args:
            user_id: UUID of user.
            year: Year (default: current year).
            month: Month (default: current month).

        Returns:
            Dict with total_tokens, total_cost_chf, query_count, by_model breakdown.
        """
        now = datetime.now(timezone.utc)
        year = year or now.year
        month = month or now.month

        # Calculate month boundaries
        start_date = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            end_date = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end_date = datetime(year, month + 1, 1, tzinfo=timezone.utc)

        with self.get_session() as session:
            # Aggregate totals
            result = session.execute(
                text("""
                    SELECT
                        COALESCE(SUM(input_tokens + output_tokens), 0) as total_tokens,
                        COALESCE(SUM(cost_chf), 0) as total_cost_chf,
                        COUNT(*) as query_count
                    FROM token_usage
                    WHERE user_id = :user_id
                      AND created_at >= :start_date
                      AND created_at < :end_date
                """),
                {"user_id": user_id, "start_date": start_date, "end_date": end_date}
            ).fetchone()

            # Breakdown by model
            model_breakdown = session.execute(
                text("""
                    SELECT
                        model,
                        SUM(input_tokens) as input_tokens,
                        SUM(output_tokens) as output_tokens,
                        SUM(cost_chf) as cost_chf,
                        COUNT(*) as queries
                    FROM token_usage
                    WHERE user_id = :user_id
                      AND created_at >= :start_date
                      AND created_at < :end_date
                    GROUP BY model
                """),
                {"user_id": user_id, "start_date": start_date, "end_date": end_date}
            ).fetchall()

            by_model = {
                row[0]: {
                    "input_tokens": row[1],
                    "output_tokens": row[2],
                    "cost_chf": float(row[3]),
                    "queries": row[4]
                }
                for row in model_breakdown
            }

            return {
                "year": year,
                "month": month,
                "total_tokens": result[0],
                "total_cost_chf": float(result[1]),
                "query_count": result[2],
                "by_model": by_model
            }

    def get_users_exceeding_threshold(self, threshold_chf: float) -> List[Dict]:
        """
        Get users who have exceeded cost threshold this month.

        Args:
            threshold_chf: Cost threshold in CHF.

        Returns:
            List of user dicts with their costs.
        """
        now = datetime.now(timezone.utc)
        start_date = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

        with self.get_session() as session:
            results = session.execute(
                text("""
                    SELECT
                        u.user_id,
                        u.email,
                        COALESCE(SUM(t.cost_chf), 0) as total_cost_chf,
                        COUNT(t.usage_id) as query_count
                    FROM users u
                    LEFT JOIN token_usage t ON u.user_id = t.user_id
                        AND t.created_at >= :start_date
                    WHERE u.is_active = TRUE
                    GROUP BY u.user_id, u.email
                    HAVING COALESCE(SUM(t.cost_chf), 0) >= :threshold
                    ORDER BY total_cost_chf DESC
                """),
                {"start_date": start_date, "threshold": threshold_chf}
            ).fetchall()

            return [
                {
                    "user_id": row[0],
                    "email": row[1],
                    "total_cost_chf": float(row[2]),
                    "query_count": row[3]
                }
                for row in results
            ]

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
        firm_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        with self.get_session() as session:
            session.execute(
                text("""
                    INSERT INTO firms (
                        firm_id, firm_name, master_key_reference,
                        is_active, created_at
                    ) VALUES (
                        :firm_id, :firm_name, :master_key_reference,
                        TRUE, :created_at
                    )
                """),
                {
                    "firm_id": firm_id,
                    "firm_name": firm_name,
                    "master_key_reference": master_key_reference,
                    "created_at": now
                }
            )

        logger.info(f"Created firm: {firm_name} (id={firm_id})")
        return firm_id

    def add_firm_member(self, user_id: str, firm_id: str, role: str) -> None:
        """
        Add a user as a firm member.

        Args:
            user_id: UUID of user.
            firm_id: UUID of firm.
            role: Role in firm (partner, associate, paralegal, admin).
        """
        valid_roles = {"partner", "associate", "paralegal", "admin"}
        if role.lower() not in valid_roles:
            raise ValueError(f"Invalid role. Must be one of: {valid_roles}")

        now = datetime.now(timezone.utc)

        with self.get_session() as session:
            # Check if membership already exists
            existing = session.execute(
                text("""
                    SELECT 1 FROM firm_members
                    WHERE user_id = :user_id AND firm_id = :firm_id
                """),
                {"user_id": user_id, "firm_id": firm_id}
            ).fetchone()

            if existing:
                # Update role instead
                session.execute(
                    text("""
                        UPDATE firm_members
                        SET role = :role, updated_at = :now
                        WHERE user_id = :user_id AND firm_id = :firm_id
                    """),
                    {"user_id": user_id, "firm_id": firm_id, "role": role.lower(), "now": now}
                )
            else:
                session.execute(
                    text("""
                        INSERT INTO firm_members (
                            user_id, firm_id, role, created_at, updated_at
                        ) VALUES (
                            :user_id, :firm_id, :role, :created_at, :updated_at
                        )
                    """),
                    {
                        "user_id": user_id,
                        "firm_id": firm_id,
                        "role": role.lower(),
                        "created_at": now,
                        "updated_at": now
                    }
                )

        logger.info(f"Added user {user_id} to firm {firm_id} as {role}")

    def get_user_firms(self, user_id: str) -> List[Dict]:
        """
        Get all firms a user belongs to.

        Args:
            user_id: UUID of user.

        Returns:
            List of firm dicts with user's role.
        """
        with self.get_session() as session:
            results = session.execute(
                text("""
                    SELECT f.firm_id, f.firm_name, fm.role, f.created_at
                    FROM firm_members fm
                    JOIN firms f ON fm.firm_id = f.firm_id
                    WHERE fm.user_id = :user_id
                      AND f.is_active = TRUE
                    ORDER BY f.firm_name
                """),
                {"user_id": user_id}
            ).fetchall()

            return [
                {
                    "firm_id": row[0],
                    "firm_name": row[1],
                    "role": row[2],
                    "joined_at": row[3]
                }
                for row in results
            ]


    # ==========================================
    # Additional User Operations
    # ==========================================

    def get_user_by_id(self, user_id: str) -> Optional[Dict]:
        """
        Get user by ID.

        Args:
            user_id: UUID of user.

        Returns:
            User dict or None if not found.
        """
        with self.get_session() as session:
            result = session.execute(
                text("""
                    SELECT user_id, email, password_hash, totp_secret,
                           is_active, mfa_enabled, last_login, created_at
                    FROM users
                    WHERE user_id = :user_id
                """),
                {"user_id": user_id}
            ).fetchone()

            if not result:
                return None

            return {
                "user_id": result[0],
                "email": result[1],
                "password_hash": result[2],
                "totp_secret": result[3],
                "is_active": result[4],
                "mfa_enabled": result[5],
                "last_login": result[6],
                "created_at": result[7]
            }

    def update_totp_secret(self, user_id: str, totp_secret: Optional[str]) -> None:
        """
        Update user's TOTP secret for MFA.

        Args:
            user_id: UUID of user.
            totp_secret: New TOTP secret, or None to disable MFA.
        """
        with self.get_session() as session:
            session.execute(
                text("""
                    UPDATE users
                    SET totp_secret = :totp_secret,
                        mfa_enabled = :mfa_enabled,
                        updated_at = :now
                    WHERE user_id = :user_id
                """),
                {
                    "user_id": user_id,
                    "totp_secret": totp_secret,
                    "mfa_enabled": totp_secret is not None,
                    "now": datetime.now(timezone.utc)
                }
            )
        logger.info(f"Updated MFA for user {user_id}: enabled={totp_secret is not None}")

    def store_backup_codes(self, user_id: str, hashed_codes: List[str]) -> None:
        """
        Store hashed backup codes for MFA recovery.

        Args:
            user_id: UUID of user.
            hashed_codes: List of bcrypt-hashed backup codes.
        """
        codes_json = json.dumps(hashed_codes)
        with self.get_session() as session:
            session.execute(
                text("""
                    UPDATE users
                    SET backup_codes = :backup_codes,
                        updated_at = :now
                    WHERE user_id = :user_id
                """),
                {
                    "user_id": user_id,
                    "backup_codes": codes_json,
                    "now": datetime.now(timezone.utc)
                }
            )
        logger.info(f"Stored {len(hashed_codes)} backup codes for user {user_id}")

    def get_backup_codes(self, user_id: str) -> List[str]:
        """
        Get hashed backup codes for a user.

        Args:
            user_id: UUID of user.

        Returns:
            List of hashed backup codes.
        """
        with self.get_session() as session:
            result = session.execute(
                text("SELECT backup_codes FROM users WHERE user_id = :user_id"),
                {"user_id": user_id}
            ).fetchone()

            if not result or not result[0]:
                return []

            return json.loads(result[0])

    def remove_backup_code(self, user_id: str, code_index: int) -> None:
        """
        Remove a used backup code by index.

        Args:
            user_id: UUID of user.
            code_index: Index of the code to remove.
        """
        codes = self.get_backup_codes(user_id)
        if 0 <= code_index < len(codes):
            codes.pop(code_index)
            codes_json = json.dumps(codes) if codes else None
            with self.get_session() as session:
                session.execute(
                    text("""
                        UPDATE users
                        SET backup_codes = :backup_codes,
                            updated_at = :now
                        WHERE user_id = :user_id
                    """),
                    {
                        "user_id": user_id,
                        "backup_codes": codes_json,
                        "now": datetime.now(timezone.utc)
                    }
                )
            logger.info(f"Removed backup code for user {user_id}, {len(codes)} remaining")

    def update_password(self, user_id: str, new_password_hash: str) -> None:
        """
        Update user's password hash.

        Args:
            user_id: UUID of user.
            new_password_hash: New bcrypt-hashed password.
        """
        with self.get_session() as session:
            session.execute(
                text("""
                    UPDATE users
                    SET password_hash = :password_hash, updated_at = :now
                    WHERE user_id = :user_id
                """),
                {
                    "user_id": user_id,
                    "password_hash": new_password_hash,
                    "now": datetime.now(timezone.utc)
                }
            )

    def deactivate_user(self, user_id: str) -> None:
        """
        Deactivate a user account (soft delete).

        Args:
            user_id: UUID of user.
        """
        with self.get_session() as session:
            # Deactivate user
            session.execute(
                text("""
                    UPDATE users
                    SET is_active = FALSE, updated_at = :now
                    WHERE user_id = :user_id
                """),
                {"user_id": user_id, "now": datetime.now(timezone.utc)}
            )
            # Invalidate all sessions
            session.execute(
                text("""
                    UPDATE sessions
                    SET is_active = FALSE
                    WHERE user_id = :user_id
                """),
                {"user_id": user_id}
            )
        logger.info(f"Deactivated user {user_id}")

    def invalidate_all_sessions(self, user_id: str) -> int:
        """
        Invalidate all sessions for a user (logout from all devices).

        Args:
            user_id: UUID of user.

        Returns:
            Number of sessions invalidated.
        """
        with self.get_session() as session:
            result = session.execute(
                text("""
                    UPDATE sessions
                    SET is_active = FALSE
                    WHERE user_id = :user_id AND is_active = TRUE
                """),
                {"user_id": user_id}
            )
            count = result.rowcount
        logger.info(f"Invalidated {count} sessions for user {user_id}")
        return count

    # ==========================================
    # Failed Login Tracking
    # ==========================================

    def record_failed_login(self, email: str) -> int:
        """
        Record a failed login attempt for an email.

        Args:
            email: Email address that failed login.

        Returns:
            Current count of failed attempts in the lockout window.
        """
        now = datetime.now(timezone.utc)
        email_lower = email.lower().strip()

        with self.get_session() as session:
            session.execute(
                text("""
                    INSERT INTO failed_logins (email, attempted_at)
                    VALUES (:email, :attempted_at)
                """),
                {"email": email_lower, "attempted_at": now}
            )

        return self.get_failed_login_count(email)

    def get_failed_login_count(self, email: str, window_minutes: int = 15) -> int:
        """
        Get the count of failed login attempts within the lockout window.

        Args:
            email: Email address to check.
            window_minutes: Lockout window in minutes (default 15).

        Returns:
            Number of failed attempts in the window.
        """
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=window_minutes)
        email_lower = email.lower().strip()

        with self.get_session() as session:
            result = session.execute(
                text("""
                    SELECT COUNT(*) FROM failed_logins
                    WHERE email = :email AND attempted_at > :window_start
                """),
                {"email": email_lower, "window_start": window_start}
            ).fetchone()

            return result[0] if result else 0

    def clear_failed_logins(self, email: str) -> None:
        """
        Clear failed login attempts for an email (after successful login).

        Args:
            email: Email address to clear.
        """
        email_lower = email.lower().strip()

        with self.get_session() as session:
            session.execute(
                text("DELETE FROM failed_logins WHERE email = :email"),
                {"email": email_lower}
            )
        logger.debug(f"Cleared failed login attempts for {email_lower}")

    # ==========================================
    # Schema Initialization
    # ==========================================

    def init_schema(self) -> None:
        """
        Initialize database schema (create tables if not exist).

        Call this once during application setup.
        """
        with self.get_session() as session:
            # Users table
            session.execute(text("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id UUID PRIMARY KEY,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    totp_secret VARCHAR(64),
                    backup_codes TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    mfa_enabled BOOLEAN DEFAULT FALSE,
                    last_login TIMESTAMP WITH TIME ZONE,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """))

            # Sessions table
            session.execute(text("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_token VARCHAR(64) PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    device_fingerprint VARCHAR(255),
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    expires_at TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """))

            # Token usage table
            session.execute(text("""
                CREATE TABLE IF NOT EXISTS token_usage (
                    usage_id SERIAL PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    model VARCHAR(100) NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_chf DECIMAL(10, 6) NOT NULL DEFAULT 0,
                    operation VARCHAR(50),
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """))

            # Firms table
            session.execute(text("""
                CREATE TABLE IF NOT EXISTS firms (
                    firm_id UUID PRIMARY KEY,
                    firm_name VARCHAR(255) NOT NULL,
                    master_key_reference VARCHAR(255),
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """))

            # Firm members table
            session.execute(text("""
                CREATE TABLE IF NOT EXISTS firm_members (
                    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    firm_id UUID NOT NULL REFERENCES firms(firm_id) ON DELETE CASCADE,
                    role VARCHAR(50) NOT NULL,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    PRIMARY KEY (user_id, firm_id)
                )
            """))

            # Failed logins table (for account lockout)
            session.execute(text("""
                CREATE TABLE IF NOT EXISTS failed_logins (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) NOT NULL,
                    attempted_at TIMESTAMP WITH TIME ZONE NOT NULL
                )
            """))

            # Create indexes
            session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)
            """))
            session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)
            """))
            session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_token_usage_user ON token_usage(user_id)
            """))
            session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_token_usage_created ON token_usage(created_at)
            """))
            session.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_failed_logins_email ON failed_logins(email, attempted_at)
            """))

        logger.info("Database schema initialized")

    def migrate_add_backup_codes_column(self) -> bool:
        """
        Migration: Add backup_codes column to users table if it doesn't exist.

        Safe to call multiple times - only adds column if missing.

        Returns:
            True if migration was applied, False if column already existed.
        """
        with self.get_session() as session:
            # Check if column exists
            result = session.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'users' AND column_name = 'backup_codes'
            """)).fetchone()

            if result:
                logger.debug("backup_codes column already exists")
                return False

            # Add the column
            session.execute(text("""
                ALTER TABLE users ADD COLUMN backup_codes TEXT
            """))
            logger.info("Added backup_codes column to users table")
            return True


# ==========================================
# Password Hashing Utilities
# ==========================================

def hash_password(password: str) -> str:
    """
    Hash a password using bcrypt.

    Args:
        password: Plain text password.

    Returns:
        Bcrypt hash string.
    """
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')


def verify_password(password: str, password_hash: str) -> bool:
    """
    Verify a password against its hash.

    Args:
        password: Plain text password to verify.
        password_hash: Stored bcrypt hash.

    Returns:
        True if password matches, False otherwise.
    """
    return bcrypt.checkpw(
        password.encode('utf-8'),
        password_hash.encode('utf-8')
    )


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
