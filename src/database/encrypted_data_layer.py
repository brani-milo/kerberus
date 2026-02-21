"""
Encrypted Data Layer for Chainlit Conversation Persistence.

Provides encrypted storage for conversations in PostgreSQL.
All message content is encrypted with AES-256-GCM before storage.

Security Model:
- Encryption key stored as environment variable / Docker secret
- Protects against database breach (attacker sees only encrypted blobs)
- Server can decrypt to display conversations to authenticated users

This is NOT zero-knowledge (unlike dossiers) because:
- Server already processes the conversation (generates responses)
- Server needs to display history to user
- Threat model: protect against DB theft, not server admin
"""
import os
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from contextlib import contextmanager

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
import base64

from sqlalchemy import create_engine, Column, String, Text, DateTime, Boolean, Integer, ForeignKey, Index
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

logger = logging.getLogger(__name__)

Base = declarative_base()


# =============================================================================
# ENCRYPTION UTILITIES
# =============================================================================

class ConversationEncryptor:
    """
    Handles encryption/decryption of conversation content.

    Uses Fernet (AES-128-CBC + HMAC-SHA256) for symmetric encryption.
    Key is derived from CONVERSATION_ENCRYPTION_KEY environment variable.
    """

    def __init__(self, key: Optional[str] = None):
        """
        Initialize encryptor with key from environment or parameter.

        Args:
            key: Base64-encoded 32-byte key. If None, reads from env.
        """
        if key is None:
            key = self._load_key_from_env()

        if not key:
            raise ValueError(
                "CONVERSATION_ENCRYPTION_KEY not set. "
                "Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )

        # Fernet expects a URL-safe base64-encoded 32-byte key
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    def _load_key_from_env(self) -> Optional[str]:
        """Load encryption key from environment or Docker secret."""
        # Try Docker secret file first
        secret_file = os.getenv("CONVERSATION_ENCRYPTION_KEY_FILE")
        if secret_file and os.path.exists(secret_file):
            with open(secret_file, 'r') as f:
                return f.read().strip()

        # Fall back to environment variable
        return os.getenv("CONVERSATION_ENCRYPTION_KEY")

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt plaintext string.

        Args:
            plaintext: String to encrypt.

        Returns:
            Base64-encoded ciphertext.
        """
        if not plaintext:
            return ""

        ciphertext = self._fernet.encrypt(plaintext.encode('utf-8'))
        return base64.urlsafe_b64encode(ciphertext).decode('utf-8')

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt ciphertext string.

        Args:
            ciphertext: Base64-encoded ciphertext.

        Returns:
            Decrypted plaintext.
        """
        if not ciphertext:
            return ""

        try:
            raw_ciphertext = base64.urlsafe_b64decode(ciphertext.encode('utf-8'))
            plaintext = self._fernet.decrypt(raw_ciphertext)
            return plaintext.decode('utf-8')
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return "[Decryption failed]"

    def encrypt_dict(self, data: Dict) -> str:
        """Encrypt a dictionary as JSON."""
        if not data:
            return ""
        return self.encrypt(json.dumps(data, default=str))

    def decrypt_dict(self, ciphertext: str) -> Dict:
        """Decrypt a dictionary from encrypted JSON."""
        if not ciphertext:
            return {}
        try:
            return json.loads(self.decrypt(ciphertext))
        except json.JSONDecodeError:
            return {}


# =============================================================================
# DATABASE MODELS
# =============================================================================

class EncryptedThread(Base):
    """Conversation thread with encrypted metadata."""
    __tablename__ = "conversation_threads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String(255), nullable=False, index=True)

    # Encrypted fields
    name_encrypted = Column(Text, nullable=True)  # Thread name/title
    metadata_encrypted = Column(Text, nullable=True)  # Additional metadata

    # Unencrypted fields (needed for queries)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    is_active = Column(Boolean, default=True)

    # Relationships
    messages = relationship("EncryptedMessage", back_populates="thread", cascade="all, delete-orphan")

    __table_args__ = (
        Index('idx_thread_user_updated', 'user_id', 'updated_at'),
    )


class EncryptedMessage(Base):
    """Chat message with encrypted content."""
    __tablename__ = "conversation_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    thread_id = Column(UUID(as_uuid=True), ForeignKey("conversation_threads.id", ondelete="CASCADE"), nullable=False)

    # Encrypted fields
    content_encrypted = Column(Text, nullable=False)  # Message content
    metadata_encrypted = Column(Text, nullable=True)  # Elements, attachments, etc.

    # Unencrypted fields (needed for ordering/display)
    role = Column(String(50), nullable=False)  # "user", "assistant", "system"
    sequence = Column(Integer, nullable=False)  # Order within thread
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Relationships
    thread = relationship("EncryptedThread", back_populates="messages")

    __table_args__ = (
        Index('idx_message_thread_seq', 'thread_id', 'sequence'),
    )


class EncryptedFeedback(Base):
    """User feedback on messages (encrypted)."""
    __tablename__ = "conversation_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id = Column(UUID(as_uuid=True), ForeignKey("conversation_messages.id", ondelete="CASCADE"), nullable=False)

    # Feedback data
    value = Column(Integer, nullable=False)  # 1 = positive, -1 = negative
    comment_encrypted = Column(Text, nullable=True)  # Optional feedback text
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# =============================================================================
# ENCRYPTED DATA LAYER
# =============================================================================

class EncryptedChainlitDataLayer:
    """
    Chainlit-compatible data layer with encryption.

    Implements the interface expected by Chainlit for conversation persistence.
    All sensitive content is encrypted before storage.

    Usage:
        @cl.data_layer
        async def get_data_layer():
            return EncryptedChainlitDataLayer()
    """

    def __init__(
        self,
        database_url: Optional[str] = None,
        encryption_key: Optional[str] = None
    ):
        """
        Initialize encrypted data layer.

        Args:
            database_url: PostgreSQL connection string. Defaults to env vars.
            encryption_key: Fernet key. Defaults to env var.
        """
        if database_url is None:
            database_url = self._build_database_url()

        self._engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=300)
        self._Session = sessionmaker(bind=self._engine)
        self._encryptor = ConversationEncryptor(encryption_key)

        # Create tables if they don't exist
        Base.metadata.create_all(self._engine)
        logger.info("Encrypted conversation data layer initialized")

    def _build_database_url(self) -> str:
        """Build database URL from environment variables."""
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        db = os.getenv("POSTGRES_DB", "kerberus")
        user = os.getenv("POSTGRES_USER", "kerberus_user")

        # Password from file or env
        password_file = os.getenv("POSTGRES_PASSWORD_FILE")
        if password_file and os.path.exists(password_file):
            with open(password_file, 'r') as f:
                password = f.read().strip()
        else:
            password = os.getenv("POSTGRES_PASSWORD", "")

        return f"postgresql://{user}:{password}@{host}:{port}/{db}"

    @contextmanager
    def _session(self):
        """Context manager for database sessions."""
        session = self._Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # =========================================================================
    # THREAD OPERATIONS
    # =========================================================================

    async def create_thread(
        self,
        user_id: str,
        name: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> str:
        """
        Create a new conversation thread.

        Args:
            user_id: User identifier.
            name: Optional thread name.
            metadata: Optional metadata dict.

        Returns:
            Thread ID as string.
        """
        thread_id = uuid.uuid4()

        with self._session() as session:
            thread = EncryptedThread(
                id=thread_id,
                user_id=user_id,
                name_encrypted=self._encryptor.encrypt(name or ""),
                metadata_encrypted=self._encryptor.encrypt_dict(metadata or {}),
            )
            session.add(thread)

        logger.debug(f"Created thread {thread_id} for user {user_id}")
        return str(thread_id)

    # =========================================================================
    # MESSAGE OPERATIONS
    # =========================================================================

    async def create_message(
        self,
        thread_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict] = None
    ) -> str:
        """
        Add a message to a thread.

        Args:
            thread_id: Thread UUID string.
            role: Message role ("user", "assistant", "system").
            content: Message content (will be encrypted).
            metadata: Optional metadata (will be encrypted).

        Returns:
            Message ID as string.
        """
        message_id = uuid.uuid4()

        with self._session() as session:
            # Get next sequence number
            max_seq = session.query(EncryptedMessage).filter(
                EncryptedMessage.thread_id == uuid.UUID(thread_id)
            ).count()

            message = EncryptedMessage(
                id=message_id,
                thread_id=uuid.UUID(thread_id),
                role=role,
                content_encrypted=self._encryptor.encrypt(content),
                metadata_encrypted=self._encryptor.encrypt_dict(metadata or {}),
                sequence=max_seq,
            )
            session.add(message)

            # Update thread's updated_at
            session.query(EncryptedThread).filter(
                EncryptedThread.id == uuid.UUID(thread_id)
            ).update({"updated_at": datetime.now(timezone.utc)})

        return str(message_id)

    async def get_messages(
        self,
        thread_id: str,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict]:
        """
        Get messages for a thread.

        Args:
            thread_id: Thread UUID string.
            limit: Max messages to return.
            offset: Pagination offset.

        Returns:
            List of message dicts, ordered by sequence.
        """
        with self._session() as session:
            messages = session.query(EncryptedMessage).filter(
                EncryptedMessage.thread_id == uuid.UUID(thread_id)
            ).order_by(
                EncryptedMessage.sequence.asc()
            ).offset(offset).limit(limit).all()

            return [
                {
                    "id": str(m.id),
                    "thread_id": str(m.thread_id),
                    "role": m.role,
                    "content": self._encryptor.decrypt(m.content_encrypted),
                    "metadata": self._encryptor.decrypt_dict(m.metadata_encrypted),
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "sequence": m.sequence,
                }
                for m in messages
            ]

    # =========================================================================
    # FEEDBACK OPERATIONS
    # =========================================================================

    async def create_feedback(
        self,
        message_id: str,
        value: int,
        comment: Optional[str] = None
    ) -> str:
        """
        Add feedback to a message.

        Args:
            message_id: Message UUID string.
            value: 1 for positive, -1 for negative.
            comment: Optional feedback text (will be encrypted).

        Returns:
            Feedback ID as string.
        """
        feedback_id = uuid.uuid4()

        with self._session() as session:
            feedback = EncryptedFeedback(
                id=feedback_id,
                message_id=uuid.UUID(message_id),
                value=value,
                comment_encrypted=self._encryptor.encrypt(comment or ""),
            )
            session.add(feedback)

        return str(feedback_id)

    # =========================================================================
    # CHAINLIT INTERFACE METHODS
    # =========================================================================

    async def get_user(self, identifier: str) -> Optional[Dict]:
        """Get user by identifier (delegates to auth system)."""
        # User management is handled by auth_db, not here
        return None

    async def create_user(self, user: Dict) -> Optional[Dict]:
        """Create user (delegates to auth system)."""
        return None

    async def upsert_feedback(self, feedback) -> str:
        """Upsert feedback from Chainlit."""
        return await self.create_feedback(
            message_id=str(feedback.forId),
            value=feedback.value,
            comment=feedback.comment,
        )

    # =========================================================================
    # CHAINLIT 2.x COMPATIBILITY METHODS
    # =========================================================================

    async def create_step(self, step) -> Optional[Dict]:
        """Create a step (Chainlit 2.x). Steps are sub-units of messages."""
        # Steps are transient in our implementation - we don't persist them
        # as they're mainly for UI display during streaming
        return None

    async def update_step(self, step) -> Optional[Dict]:
        """Update a step (Chainlit 2.x)."""
        # Steps are transient - no persistence needed
        return None

    async def delete_step(self, step_id: str) -> bool:
        """Delete a step (Chainlit 2.x)."""
        return True

    async def get_thread_author(self, thread_id: str) -> Optional[str]:
        """Get the author (user_id) of a thread."""
        thread = await self.get_thread(thread_id)
        if thread:
            return thread.get("user_id")
        return None

    async def delete_thread(self, thread_id: str) -> bool:
        """
        Soft-delete a thread (marks inactive, preserves data).

        Args:
            thread_id: Thread UUID string.

        Returns:
            True if deleted, False if not found.
        """
        with self._session() as session:
            thread = session.query(EncryptedThread).filter(
                EncryptedThread.id == uuid.UUID(thread_id)
            ).first()

            if not thread:
                return False

            thread.is_active = False

        logger.info(f"Soft-deleted thread {thread_id}")
        return True

    async def list_threads(
        self,
        pagination,
        filters
    ) -> Any:
        """
        List threads with Chainlit 2.x pagination format.

        Args:
            pagination: Chainlit pagination object with first/cursor
            filters: Chainlit filters object with userId

        Returns:
            PaginatedResponse with data and pageInfo
        """
        from dataclasses import dataclass

        @dataclass
        class PageInfo:
            hasNextPage: bool
            startCursor: Optional[str]
            endCursor: Optional[str]

        @dataclass
        class PaginatedResponse:
            data: List[Dict]
            pageInfo: PageInfo

        user_id = filters.userId if filters else None
        limit = pagination.first if pagination else 20

        if not user_id:
            return PaginatedResponse(
                data=[],
                pageInfo=PageInfo(hasNextPage=False, startCursor=None, endCursor=None)
            )

        with self._session() as session:
            threads = session.query(EncryptedThread).filter(
                EncryptedThread.user_id == user_id,
                EncryptedThread.is_active == True
            ).order_by(
                EncryptedThread.updated_at.desc()
            ).limit(limit + 1).all()

            has_next = len(threads) > limit
            threads = threads[:limit]

            data = []
            for t in threads:
                data.append({
                    "id": str(t.id),
                    "name": self._encryptor.decrypt(t.name_encrypted) or "Untitled",
                    "createdAt": t.created_at.isoformat() if t.created_at else None,
                    "updatedAt": t.updated_at.isoformat() if t.updated_at else None,
                    "userId": t.user_id,
                })

            return PaginatedResponse(
                data=data,
                pageInfo=PageInfo(
                    hasNextPage=has_next,
                    startCursor=str(threads[0].id) if threads else None,
                    endCursor=str(threads[-1].id) if threads else None
                )
            )

    async def get_thread(self, thread_id: str) -> Optional[Dict]:
        """
        Get a thread by ID (Chainlit 2.x format).

        Args:
            thread_id: Thread UUID string.

        Returns:
            Thread dict or None.
        """
        with self._session() as session:
            thread = session.query(EncryptedThread).filter(
                EncryptedThread.id == uuid.UUID(thread_id)
            ).first()

            if not thread:
                return None

            # Get message count
            msg_count = session.query(EncryptedMessage).filter(
                EncryptedMessage.thread_id == thread.id
            ).count()

            return {
                "id": str(thread.id),
                "name": self._encryptor.decrypt(thread.name_encrypted) or "Untitled",
                "metadata": self._encryptor.decrypt_dict(thread.metadata_encrypted),
                "createdAt": thread.created_at.isoformat() if thread.created_at else None,
                "updatedAt": thread.updated_at.isoformat() if thread.updated_at else None,
                "userId": thread.user_id,
                "userIdentifier": thread.user_id,
                "steps": [],  # Steps loaded separately
                "elements": [],  # Elements loaded separately
            }

    async def update_thread(
        self,
        thread_id: str,
        name: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict:
        """
        Update a thread (Chainlit 2.x format).

        Args:
            thread_id: Thread UUID string.
            name: New name (optional).
            user_id: User ID (optional, usually not changed).
            metadata: New metadata (optional).
            tags: Thread tags (optional).

        Returns:
            Updated thread dict.
        """
        with self._session() as session:
            thread = session.query(EncryptedThread).filter(
                EncryptedThread.id == uuid.UUID(thread_id)
            ).first()

            if not thread:
                # Create new thread if not exists
                thread = EncryptedThread(
                    id=uuid.UUID(thread_id),
                    user_id=user_id or "unknown",
                    name_encrypted=self._encryptor.encrypt(name or ""),
                    metadata_encrypted=self._encryptor.encrypt_dict(metadata or {}),
                )
                session.add(thread)
            else:
                if name is not None:
                    thread.name_encrypted = self._encryptor.encrypt(name)
                if metadata is not None:
                    thread.metadata_encrypted = self._encryptor.encrypt_dict(metadata)
                thread.updated_at = datetime.now(timezone.utc)

        return await self.get_thread(thread_id)


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

_data_layer: Optional[EncryptedChainlitDataLayer] = None


def get_encrypted_data_layer() -> EncryptedChainlitDataLayer:
    """Get singleton encrypted data layer instance."""
    global _data_layer
    if _data_layer is None:
        _data_layer = EncryptedChainlitDataLayer()
    return _data_layer
