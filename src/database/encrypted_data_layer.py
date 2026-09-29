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

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
import base64
from contextlib import contextmanager

from sqlalchemy import create_engine, Column, String, Text, DateTime, Boolean, Integer, ForeignKey, Index, text
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.dialects.postgresql import UUID

logger = logging.getLogger(__name__)

Base = declarative_base()


# =============================================================================
# ENCRYPTION UTILITIES
# =============================================================================

_V2_PREFIX = "v2:"
_NONCE_BYTES = 12


class ConversationEncryptor:
    """
    Handles encryption/decryption of conversation content.

    Uses AES-256-GCM with Associated Authenticated Data (AAD) to bind each
    ciphertext to its owning user_id / thread_id / message_id, preventing
    cross-user or cross-thread ciphertext substitution in PostgreSQL.
    Retains backward-compatible decryption for legacy Fernet (v1) ciphertexts.
    """

    def __init__(self, key: Optional[str] = None):
        """
        Initialize encryptor with key from environment or parameter.

        Args:
            key: Base64-encoded 32-byte key (or secret string). If None, reads from env.
        """
        if key is None:
            key = self._load_key_from_env()

        if not key:
            raise ValueError(
                "CONVERSATION_ENCRYPTION_KEY not set. "
                "Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )

        raw_key_bytes = key.encode("utf-8") if isinstance(key, str) else key

        # Derive a 256-bit AES-GCM key via HKDF-SHA256
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"kerberus-conversation-aes256gcm-v2",
            info=b"conversation-encryption",
        )
        self._aesgcm = AESGCM(hkdf.derive(raw_key_bytes))

        # Keep legacy Fernet instance if key is valid Fernet format for v1 migration
        try:
            self._legacy_fernet: Optional[Fernet] = Fernet(raw_key_bytes)
        except Exception:
            self._legacy_fernet = None

    def _load_key_from_env(self) -> Optional[str]:
        """Load encryption key from environment or Docker secret."""
        # Try Docker secret file first
        secret_file = os.getenv("CONVERSATION_ENCRYPTION_KEY_FILE")
        if secret_file and os.path.exists(secret_file):
            with open(secret_file, 'r') as f:
                return f.read().strip()

        # Fall back to environment variable
        return os.getenv("CONVERSATION_ENCRYPTION_KEY")

    def encrypt(self, plaintext: str, aad: Optional[str] = None) -> str:
        """
        Encrypt plaintext string using AES-256-GCM with optional AAD binding.

        Args:
            plaintext: String to encrypt.
            aad: Optional Associated Authenticated Data (e.g. thread_id/user_id).

        Returns:
            Version-prefixed base64-encoded ciphertext ("v2:...").
        """
        if not plaintext:
            return ""

        nonce = os.urandom(_NONCE_BYTES)
        aad_bytes = aad.encode("utf-8") if aad else None
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), aad_bytes)
        blob = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
        return f"{_V2_PREFIX}{blob}"

    def decrypt(self, ciphertext: str, aad: Optional[str] = None) -> str:
        """
        Decrypt ciphertext string, verifying AAD integrity when present.

        Args:
            ciphertext: Version-prefixed AES-256-GCM or legacy Fernet ciphertext.
            aad: Optional Associated Authenticated Data expected for this record.

        Returns:
            Decrypted plaintext, or "[Decryption failed]" on tampering/mismatch.
        """
        if not ciphertext:
            return ""

        try:
            if ciphertext.startswith(_V2_PREFIX):
                blob = base64.urlsafe_b64decode(ciphertext[len(_V2_PREFIX):].encode("ascii"))
                nonce, ct = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
                aad_bytes = aad.encode("utf-8") if aad else None
                try:
                    plaintext = self._aesgcm.decrypt(nonce, ct, aad_bytes)
                except InvalidTag:
                    # Fallback if record was encrypted without AAD
                    if aad_bytes is not None:
                        plaintext = self._aesgcm.decrypt(nonce, ct, None)
                    else:
                        raise
                return plaintext.decode("utf-8")

            # Legacy v1 Fernet ciphertext fallback
            if self._legacy_fernet is not None:
                raw_ciphertext = base64.urlsafe_b64decode(ciphertext.encode("utf-8"))
                plaintext = self._legacy_fernet.decrypt(raw_ciphertext)
                return plaintext.decode("utf-8")

            raise ValueError("Unsupported ciphertext format")
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return "[Decryption failed]"

    def encrypt_dict(self, data: Dict, aad: Optional[str] = None) -> str:
        """Encrypt a dictionary as JSON."""
        if not data:
            return ""
        return self.encrypt(json.dumps(data, default=str), aad=aad)

    def decrypt_dict(self, ciphertext: str, aad: Optional[str] = None) -> Dict:
        """Decrypt a dictionary from encrypted JSON."""
        if not ciphertext:
            return {}
        try:
            return json.loads(self.decrypt(ciphertext, aad=aad))
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

_UNSET: Any = object()  # distinguishes "argument not passed" from None

# Session keys that may be stored in (and returned from) thread metadata. Everything
# else Chainlit would persist (chat history, search results, MFA secrets) is dropped.
SAFE_METADATA_KEYS = {"chat_settings", "chat_profile", "client_type", "mode"}


def _safe_metadata(metadata: Optional[Dict]) -> Dict:
    return {k: v for k, v in (metadata or {}).items() if k in SAFE_METADATA_KEYS}


def _neutral_thread_name() -> str:
    """Thread title that reveals nothing about the conversation."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Europe/Zurich"))
    except Exception:
        now = datetime.now()
    return f"Conversation {now:%d.%m.%Y %H:%M}"


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
        self._identifier_cache: Dict[str, str] = {}

        # Create tables if they don't exist
        Base.metadata.create_all(self._engine)
        logger.info("Encrypted conversation data layer initialized")

    def _build_database_url(self) -> str:
        """PostgreSQL DSN from Settings (POSTGRES_* env vars / Docker secrets)."""
        from ..config import get_settings
        return get_settings().postgres_dsn

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

    def _owner_identifier(self, user_id: Any) -> str:
        """
        Map the stored owner (internal user UUID) to the login identifier (email).

        Threads are stored and AAD-bound under the internal user_id, but Chainlit
        authorises resume and its thread endpoints by comparing the thread's
        `userIdentifier` / `get_thread_author()` with `user.identifier`, which is
        the email. Without this mapping every resume was rejected as "not found".
        Uses its own connection so a failed lookup cannot abort the caller's transaction.
        """
        uid = str(user_id)
        if uid in self._identifier_cache:
            return self._identifier_cache[uid]
        identifier = uid
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text("SELECT email FROM users WHERE CAST(user_id AS TEXT) = :uid"),
                    {"uid": uid},
                ).fetchone()
            if row and row[0]:
                identifier = row[0]
        except Exception as e:
            logger.debug(f"Owner identifier lookup failed, using stored id: {e}")
        self._identifier_cache[uid] = identifier
        return identifier

    @staticmethod
    def _thread_aad(thread_id: Any, user_id: str) -> str:
        return f"thread:{thread_id}:user:{user_id}"

    @staticmethod
    def _message_aad(message_id: Any, thread_id: Any) -> str:
        return f"msg:{message_id}:thread:{thread_id}"

    @staticmethod
    def _feedback_aad(feedback_id: Any, message_id: Any) -> str:
        return f"feedback:{feedback_id}:msg:{message_id}"

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
        aad = self._thread_aad(thread_id, user_id)

        with self._session() as session:
            thread = EncryptedThread(
                id=thread_id,
                user_id=user_id,
                name_encrypted=self._encryptor.encrypt(name or _neutral_thread_name(), aad=aad),
                metadata_encrypted=self._encryptor.encrypt_dict(_safe_metadata(metadata), aad=aad),
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
        t_uuid = uuid.UUID(thread_id)
        aad = self._message_aad(message_id, t_uuid)

        with self._session() as session:
            # Get next sequence number
            max_seq = session.query(EncryptedMessage).filter(
                EncryptedMessage.thread_id == t_uuid
            ).count()

            message = EncryptedMessage(
                id=message_id,
                thread_id=t_uuid,
                role=role,
                content_encrypted=self._encryptor.encrypt(content, aad=aad),
                metadata_encrypted=self._encryptor.encrypt_dict(metadata or {}, aad=aad),
                sequence=max_seq,
            )
            session.add(message)

            # Update thread's updated_at
            session.query(EncryptedThread).filter(
                EncryptedThread.id == t_uuid
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
        t_uuid = uuid.UUID(thread_id)
        with self._session() as session:
            messages = session.query(EncryptedMessage).filter(
                EncryptedMessage.thread_id == t_uuid
            ).order_by(
                EncryptedMessage.sequence.asc()
            ).offset(offset).limit(limit).all()

            return [
                {
                    "id": str(m.id),
                    "thread_id": str(m.thread_id),
                    "role": m.role,
                    "content": self._encryptor.decrypt(
                        m.content_encrypted,
                        aad=self._message_aad(m.id, m.thread_id),
                    ),
                    "metadata": self._encryptor.decrypt_dict(
                        m.metadata_encrypted,
                        aad=self._message_aad(m.id, m.thread_id),
                    ),
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
        m_uuid = uuid.UUID(message_id)
        aad = self._feedback_aad(feedback_id, m_uuid)

        with self._session() as session:
            feedback = EncryptedFeedback(
                id=feedback_id,
                message_id=m_uuid,
                value=value,
                comment_encrypted=self._encryptor.encrypt(comment or "", aad=aad),
            )
            session.add(feedback)

        return str(feedback_id)

    # =========================================================================
    # CHAINLIT INTERFACE METHODS
    # =========================================================================

    async def get_user(self, identifier: str):
        """
        The account behind a login identifier (email), as a Chainlit PersistedUser.

        Chainlit re-fetches the user on every HTTP request and websocket connection
        and uses THIS object's metadata for the session, so the MFA state here is
        derived from the server (users table), never from the login token:
        mfa_verified always starts False and is only set after a TOTP check in the
        current session (see src/auth/mfa_session.py).
        """
        from chainlit.user import PersistedUser

        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text("SELECT user_id, email, mfa_enabled, created_at, is_active "
                         "FROM users WHERE LOWER(email) = LOWER(:email)"),
                    {"email": identifier},
                ).fetchone()
        except Exception as e:
            logger.warning(f"User lookup failed: {e}")
            return None
        if not row or (row[4] is not None and not row[4]):
            return None

        user_id, email, mfa_enabled = str(row[0]), row[1], bool(row[2])
        created = row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3] or "")
        self._identifier_cache[user_id] = email
        return PersistedUser(
            id=user_id,
            createdAt=created,
            identifier=email,
            metadata={
                "user_id": user_id,
                "email": email,
                "mfa_required": mfa_enabled,
                "mfa_verified": False,
                "mfa_setup_required": not mfa_enabled,
                "is_new_user": False,
            },
        )

    async def create_user(self, user):
        """Accounts are created by the auth system; return the persisted view of it."""
        identifier = getattr(user, "identifier", None) or (user.get("identifier") if isinstance(user, dict) else None)
        return await self.get_user(identifier) if identifier else None

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
        """Author of a thread as Chainlit's ACL expects it: the login identifier (email)."""
        with self._session() as session:
            owner = session.query(EncryptedThread.user_id).filter(
                EncryptedThread.id == uuid.UUID(thread_id),
                EncryptedThread.is_active == True,
            ).scalar()
        return self._owner_identifier(owner) if owner else None

    async def delete_thread(self, thread_id: str) -> bool:
        """
        Delete a thread: purges all encrypted messages and wipes thread metadata
        to safeguard client confidentiality, then marks the thread inactive.

        Args:
            thread_id: Thread UUID string.

        Returns:
            True if deleted, False if not found.
        """
        t_uuid = uuid.UUID(thread_id)
        with self._session() as session:
            thread = session.query(EncryptedThread).filter(
                EncryptedThread.id == t_uuid
            ).first()

            if not thread:
                return False

            # Hard-delete all messages in the thread so confidential content is purged
            session.query(EncryptedMessage).filter(
                EncryptedMessage.thread_id == t_uuid
            ).delete(synchronize_session=False)

            thread.name_encrypted = None
            thread.metadata_encrypted = None
            thread.is_active = False

        logger.info(f"Purged messages and deactivated thread {thread_id}")
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
        from chainlit.types import PageInfo, PaginatedResponse

        user_id = filters.userId if filters else None
        limit = pagination.first if pagination else 20

        if not user_id:
            return PaginatedResponse(
                data=[],
                pageInfo=PageInfo(hasNextPage=False, startCursor=None, endCursor=None)
            )

        cursor = getattr(pagination, "cursor", None) if pagination else None

        with self._session() as session:
            has_messages = session.query(EncryptedMessage.id).filter(
                EncryptedMessage.thread_id == EncryptedThread.id
            ).exists()
            query = session.query(EncryptedThread).filter(
                EncryptedThread.user_id == user_id,
                EncryptedThread.is_active == True,
                has_messages,
            )
            if cursor:
                anchor = session.query(EncryptedThread.updated_at).filter(
                    EncryptedThread.id == uuid.UUID(cursor)).scalar()
                if anchor is not None:
                    query = query.filter(EncryptedThread.updated_at < anchor)
            threads = query.order_by(EncryptedThread.updated_at.desc()).limit(limit + 1).all()

            has_next = len(threads) > limit
            threads = threads[:limit]

            data = []
            for t in threads:
                aad = self._thread_aad(t.id, t.user_id)
                data.append({
                    "id": str(t.id),
                    "name": self._encryptor.decrypt(t.name_encrypted, aad=aad) or "Untitled",
                    "createdAt": t.created_at.isoformat() if t.created_at else None,
                    "updatedAt": t.updated_at.isoformat() if t.updated_at else None,
                    "userId": t.user_id,
                    "userIdentifier": self._owner_identifier(t.user_id),
                    "tags": [],
                    "metadata": {},
                    "steps": [],
                    "elements": [],
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
        Get an active thread by ID (Chainlit 2.x format).

        Args:
            thread_id: Thread UUID string.

        Returns:
            Thread dict or None.
        """
        with self._session() as session:
            thread = session.query(EncryptedThread).filter(
                EncryptedThread.id == uuid.UUID(thread_id),
                EncryptedThread.is_active == True,
            ).first()

            if not thread:
                return None

            aad = self._thread_aad(thread.id, thread.user_id)
            return {
                "id": str(thread.id),
                "name": self._encryptor.decrypt(thread.name_encrypted, aad=aad) or "Untitled",
                "metadata": _safe_metadata(self._encryptor.decrypt_dict(thread.metadata_encrypted, aad=aad)),
                "createdAt": thread.created_at.isoformat() if thread.created_at else None,
                "updatedAt": thread.updated_at.isoformat() if thread.updated_at else None,
                "userId": thread.user_id,
                "user_id": thread.user_id,
                "userIdentifier": self._owner_identifier(thread.user_id),
                # Deliberately empty: Chainlit sends these to the browser BEFORE on_chat_resume
                # runs, i.e. before the TOTP check. The app renders the history itself after it.
                "steps": [],
                "elements": [],  # Elements loaded separately
            }

    async def update_thread(
        self,
        thread_id: str,
        name: Optional[str] = None,
        user_id: Any = _UNSET,
        metadata: Optional[Dict] = None,
        tags: Any = _UNSET,
    ) -> Dict:
        """
        Create or update a thread (Chainlit 2.x interface).

        Confidentiality rules, because Chainlit's thread endpoints and sidebar are
        reachable with the login token alone (issued before the TOTP step):
        - Chainlit names a new thread after the FIRST THING THE USER TYPED (which can
          be the legal question or even a TOTP code). That text is never stored as the
          name: new threads get a neutral date-based name. A later `name` is applied
          only for an explicit rename, i.e. a call that passes `name` without the
          `user_id`/`tags` arguments Chainlit's first-interaction call always sends.
        - Chainlit persists the whole session dictionary as `metadata` on disconnect
          (chat history, search results, pending MFA secret). Only the UI keys in
          SAFE_METADATA_KEYS are kept.
        """
        explicit_rename = name is not None and user_id is _UNSET and tags is _UNSET
        owner_arg = None if user_id is _UNSET else user_id
        safe_metadata = _safe_metadata(metadata) if metadata is not None else None

        t_uuid = uuid.UUID(thread_id)
        with self._session() as session:
            thread = session.query(EncryptedThread).filter(
                EncryptedThread.id == t_uuid
            ).first()

            if not thread:
                owner = owner_arg or "unknown"
                aad = self._thread_aad(t_uuid, owner)
                initial_name = name if explicit_rename else _neutral_thread_name()
                thread = EncryptedThread(
                    id=t_uuid,
                    user_id=owner,
                    name_encrypted=self._encryptor.encrypt(initial_name, aad=aad),
                    metadata_encrypted=self._encryptor.encrypt_dict(safe_metadata or {}, aad=aad),
                )
                session.add(thread)
            else:
                # A thread created before the user was known is adopted by its first
                # real owner; its ciphertexts are re-bound to the new owner's AAD.
                if thread.user_id == "unknown" and owner_arg and owner_arg != "unknown":
                    old_aad = self._thread_aad(thread.id, thread.user_id)
                    old_name = self._encryptor.decrypt(thread.name_encrypted, aad=old_aad)
                    old_meta = self._encryptor.decrypt_dict(thread.metadata_encrypted, aad=old_aad)
                    thread.user_id = owner_arg
                    new_aad = self._thread_aad(thread.id, owner_arg)
                    thread.name_encrypted = self._encryptor.encrypt(old_name or _neutral_thread_name(), aad=new_aad)
                    thread.metadata_encrypted = self._encryptor.encrypt_dict(_safe_metadata(old_meta), aad=new_aad)

                aad = self._thread_aad(thread.id, thread.user_id)
                if explicit_rename:
                    thread.name_encrypted = self._encryptor.encrypt(name, aad=aad)
                if safe_metadata is not None:
                    thread.metadata_encrypted = self._encryptor.encrypt_dict(safe_metadata, aad=aad)
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
