"""
SQLCipher Database Manager for Encrypted Dossier Storage.

This module provides zero-knowledge encrypted storage for:
- Personal dossiers (per-user, password-derived key)
- Firm dossiers (shared, master key encrypted)

SECURITY MODEL:
- User dossiers: Encryption key derived from user's password
- We CANNOT decrypt user data without their password
- Password loss = permanent data loss (by design)

ENCRYPTION:
- Algorithm: AES-256-GCM
- Key derivation: PBKDF2-SHA512 with 256,000 iterations
- Library: pysqlcipher3 (SQLCipher 4.x)
"""
import os
from pathlib import Path
from typing import Optional, Dict, List, Any
from datetime import datetime
from contextlib import contextmanager


class DossierDB:
    """
    SQLCipher-encrypted database manager for user dossiers.

    Each user has their own encrypted database file:
    - data/dossier/user_{uuid}.db

    The encryption key is derived from the user's password:
    - Key = PBKDF2(password, salt, iterations=256000)
    - We never store the key - derived fresh each session
    - User's password is their encryption key

    Example usage:
        # Open user's dossier (requires their password)
        dossier = DossierDB(user_id="uuid", user_password="their_password")

        # Store a document
        dossier.store_document(content, metadata)

        # Search documents (returns content only if password correct)
        results = dossier.search_documents("termination clause")

        # IMPORTANT: Close when done
        dossier.close()
    """

    def __init__(
        self,
        user_id: str,
        user_password: str,
        storage_path: Optional[str] = None,
        is_firm: bool = False,
        firm_id: Optional[str] = None
    ):
        """
        Initialize encrypted dossier connection.

        Args:
            user_id: UUID of the user.
            user_password: User's password (used to derive encryption key).
            storage_path: Path to dossier directory. Uses env default if None.
            is_firm: If True, this is a firm dossier (uses firm_id instead).
            firm_id: UUID of firm (only if is_firm=True).

        Raises:
            ValueError: If password is empty or incorrect.
            IOError: If database file cannot be accessed.
        """
        self.user_id = user_id
        self.is_firm = is_firm
        self.firm_id = firm_id

        # Determine storage path
        self.storage_path = Path(storage_path or os.getenv(
            "DOSSIER_STORAGE_PATH", "./data/dossier"
        ))

        # Determine database filename
        if is_firm and firm_id:
            self.db_path = self.storage_path / f"firm_{firm_id}.db"
        else:
            self.db_path = self.storage_path / f"user_{user_id}.db"

        # SQLCipher configuration
        self.iterations = int(os.getenv("SQLCIPHER_ITERATIONS", 256000))

        # Connection (lazy initialized)
        self._conn = None
        self._password = user_password

        # Don't connect yet - wait for first operation
        self._initialized = False

    def _connect(self) -> None:
        """
        Establish encrypted database connection.

        This is called lazily on first database operation.
        Creates the database file if it doesn't exist.
        """
        if self._conn is not None:
            return

        try:
            try:
                from pysqlcipher3 import dbapi2 as sqlcipher
            except ImportError:
                from sqlcipher3 import dbapi2 as sqlcipher
        except ImportError:
            raise ImportError(
                "Neither pysqlcipher3 nor sqlcipher3 found. "
                "On Python 3.13+, please use: pip install sqlcipher3"
            )

        # Ensure storage directory exists
        self.storage_path.mkdir(parents=True, exist_ok=True)

        # Set restrictive permissions on directory
        os.chmod(self.storage_path, 0o700)

        # Connect to database (creates if not exists)
        is_new_db = not self.db_path.exists()
        self._conn = sqlcipher.connect(str(self.db_path))
        cursor = self._conn.cursor()

        # Set encryption key
        cursor.execute(f"PRAGMA key = '{self._password}'")
        cursor.execute("PRAGMA cipher_compatibility = 4")

        # Verify connection (will fail if wrong password)
        try:
            cursor.execute("SELECT count(*) FROM sqlite_master")
        except sqlcipher.DatabaseError:
            self._conn.close()
            self._conn = None
            raise ValueError("Invalid password or corrupted database")

        # Initialize schema if new database
        if is_new_db:
            self._create_schema(cursor)
            self._conn.commit()

        self._initialized = True

    def _create_schema(self, cursor) -> None:
        """
        Create database schema for dossier storage.

        Tables:
        - documents: Stores document content and metadata
        - document_chunks: Stores chunked text for embedding
        """
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                doc_type TEXT,  -- letter, contract, brief, etc.
                language TEXT,  -- de, fr, it
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT  -- JSON string for additional data
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS document_chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT REFERENCES documents(doc_id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding_id TEXT,  -- Reference to Qdrant vector
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunks_doc ON document_chunks(doc_id)
        """)

    @contextmanager
    def get_cursor(self):
        """
        Get a database cursor with automatic commit/rollback.

        Usage:
            with dossier.get_cursor() as cursor:
                cursor.execute("SELECT * FROM documents")
        """
        self._connect()
        cursor = self._conn.cursor()
        try:
            yield cursor
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ==========================================
    # Document Operations (Stub - Day 4)
    # ==========================================

    def store_document(
        self,
        doc_id: str,
        title: str,
        content: str,
        doc_type: Optional[str] = None,
        language: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> str:
        """
        Store a document in the encrypted dossier.

        Args:
            doc_id: Unique document identifier.
            title: Document title.
            content: Full document content.
            doc_type: Type of document (letter, contract, brief).
            language: Document language (de, fr, it).
            metadata: Additional metadata as dict.

        Returns:
            Document ID.
        """
        import json
        now = datetime.now().isoformat()

        with self.get_cursor() as cursor:
            # Check if document exists (for update)
            cursor.execute(
                "SELECT doc_id FROM documents WHERE doc_id = ?",
                (doc_id,)
            )
            exists = cursor.fetchone() is not None

            if exists:
                # Update existing document
                cursor.execute("""
                    UPDATE documents
                    SET title = ?, content = ?, doc_type = ?, language = ?,
                        metadata = ?, updated_at = ?
                    WHERE doc_id = ?
                """, (
                    title, content, doc_type, language,
                    json.dumps(metadata) if metadata else None,
                    now, doc_id
                ))
            else:
                # Insert new document
                cursor.execute("""
                    INSERT INTO documents (
                        doc_id, title, content, doc_type, language,
                        created_at, updated_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    doc_id, title, content, doc_type, language,
                    now, now,
                    json.dumps(metadata) if metadata else None
                ))

        return doc_id

    def get_document(self, doc_id: str) -> Optional[Dict]:
        """
        Retrieve a document by ID.

        Args:
            doc_id: Document identifier.

        Returns:
            Document dict or None if not found.
        """
        import json

        with self.get_cursor() as cursor:
            cursor.execute("""
                SELECT doc_id, title, content, doc_type, language,
                       created_at, updated_at, metadata
                FROM documents
                WHERE doc_id = ?
            """, (doc_id,))
            row = cursor.fetchone()

            if not row:
                return None

            return {
                "doc_id": row[0],
                "title": row[1],
                "content": row[2],
                "doc_type": row[3],
                "language": row[4],
                "created_at": row[5],
                "updated_at": row[6],
                "metadata": json.loads(row[7]) if row[7] else {}
            }

    def delete_document(self, doc_id: str) -> bool:
        """
        Delete a document and its chunks.

        Args:
            doc_id: Document identifier.

        Returns:
            True if deleted, False if not found.
        """
        with self.get_cursor() as cursor:
            # First check if document exists
            cursor.execute(
                "SELECT doc_id FROM documents WHERE doc_id = ?",
                (doc_id,)
            )
            if not cursor.fetchone():
                return False

            # Delete chunks first (cascade should handle this, but be explicit)
            cursor.execute(
                "DELETE FROM document_chunks WHERE doc_id = ?",
                (doc_id,)
            )

            # Delete the document
            cursor.execute(
                "DELETE FROM documents WHERE doc_id = ?",
                (doc_id,)
            )

        return True

    def list_documents(
        self,
        doc_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict]:
        """
        List documents in the dossier.

        Args:
            doc_type: Filter by document type.
            limit: Maximum documents to return.
            offset: Number of documents to skip.

        Returns:
            List of document metadata dicts (without full content).
        """
        import json

        with self.get_cursor() as cursor:
            if doc_type:
                cursor.execute("""
                    SELECT doc_id, title, doc_type, language,
                           created_at, updated_at, metadata,
                           LENGTH(content) as content_length
                    FROM documents
                    WHERE doc_type = ?
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                """, (doc_type, limit, offset))
            else:
                cursor.execute("""
                    SELECT doc_id, title, doc_type, language,
                           created_at, updated_at, metadata,
                           LENGTH(content) as content_length
                    FROM documents
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                """, (limit, offset))

            rows = cursor.fetchall()

            return [
                {
                    "doc_id": row[0],
                    "title": row[1],
                    "doc_type": row[2],
                    "language": row[3],
                    "created_at": row[4],
                    "updated_at": row[5],
                    "metadata": json.loads(row[6]) if row[6] else {},
                    "content_length": row[7]
                }
                for row in rows
            ]

    # ==========================================
    # Chunk Operations (Stub - Day 4)
    # ==========================================

    def store_chunks(
        self,
        doc_id: str,
        chunks: List[Dict[str, Any]]
    ) -> List[str]:
        """
        Store document chunks for embedding.

        Args:
            doc_id: Parent document ID.
            chunks: List of {"content": str, "embedding_id": str} dicts.

        Returns:
            List of chunk IDs.
        """
        import uuid
        now = datetime.now().isoformat()
        chunk_ids = []

        with self.get_cursor() as cursor:
            # First delete any existing chunks for this document
            cursor.execute(
                "DELETE FROM document_chunks WHERE doc_id = ?",
                (doc_id,)
            )

            # Insert new chunks
            for idx, chunk in enumerate(chunks):
                chunk_id = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO document_chunks (
                        chunk_id, doc_id, chunk_index, content,
                        embedding_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    chunk_id,
                    doc_id,
                    idx,
                    chunk.get("content", ""),
                    chunk.get("embedding_id"),
                    now
                ))
                chunk_ids.append(chunk_id)

        return chunk_ids

    def get_chunks_by_embedding_ids(
        self,
        embedding_ids: List[str]
    ) -> List[Dict]:
        """
        Retrieve chunks by their embedding IDs.

        Used to get original content after vector search.

        Args:
            embedding_ids: List of embedding IDs from Qdrant.

        Returns:
            List of chunk dicts with content and document info.
        """
        if not embedding_ids:
            return []

        with self.get_cursor() as cursor:
            # Create placeholders for IN clause
            placeholders = ",".join("?" * len(embedding_ids))

            cursor.execute(f"""
                SELECT c.chunk_id, c.doc_id, c.chunk_index, c.content,
                       c.embedding_id, d.title, d.doc_type
                FROM document_chunks c
                JOIN documents d ON c.doc_id = d.doc_id
                WHERE c.embedding_id IN ({placeholders})
                ORDER BY c.doc_id, c.chunk_index
            """, embedding_ids)

            rows = cursor.fetchall()

            return [
                {
                    "chunk_id": row[0],
                    "doc_id": row[1],
                    "chunk_index": row[2],
                    "content": row[3],
                    "embedding_id": row[4],
                    "doc_title": row[5],
                    "doc_type": row[6]
                }
                for row in rows
            ]

    # ==========================================
    # Lifecycle
    # ==========================================

    def close(self) -> None:
        """
        Close the database connection.

        IMPORTANT: Always call this when done with the dossier.
        """
        if self._conn:
            self._conn.close()
            self._conn = None
            self._initialized = False

    def __enter__(self):
        """Support context manager usage."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close connection on context exit."""
        self.close()
        return False

    def vacuum(self) -> None:
        """
        Reclaim space after deletions.

        Run periodically if many documents are deleted.
        """
        self._connect()
        self._conn.execute("VACUUM")

    def get_stats(self) -> Dict:
        """
        Get dossier statistics.

        Returns:
            Dict with document count, total size, etc.
        """
        self._connect()
        cursor = self._conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM documents")
        doc_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM document_chunks")
        chunk_count = cursor.fetchone()[0]

        # Get file size
        file_size_mb = self.db_path.stat().st_size / (1024 * 1024) if self.db_path.exists() else 0

        return {
            "document_count": doc_count,
            "chunk_count": chunk_count,
            "file_size_mb": round(file_size_mb, 2),
            "is_firm": self.is_firm,
            "db_path": str(self.db_path)
        }
