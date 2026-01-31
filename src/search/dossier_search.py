"""
Dossier Search Service for KERBERUS.

Integrates encrypted user dossiers with Qdrant vector search.
Each user has their own isolated dossier collection.
"""
import logging
from typing import List, Dict, Optional
import uuid

from ..embedder.bge_embedder import get_embedder
from ..database.vector_db import QdrantManager
from ..database.dossier_db import DossierDB

logger = logging.getLogger(__name__)


class DossierSearchService:
    """
    Service for managing dossier document embeddings and search.

    Architecture:
    - Documents are stored encrypted in SQLCipher (DossierDB)
    - Embeddings are stored in Qdrant with user-specific collection
    - Collection naming: dossier_user_{user_id} or dossier_firm_{firm_id}
    - Metadata in Qdrant links back to encrypted doc_id/chunk_id
    """

    def __init__(self, user_id: str, user_password: str, is_firm: bool = False, firm_id: Optional[str] = None):
        """
        Initialize dossier search service.

        Args:
            user_id: UUID of user.
            user_password: User's password (for decrypting dossier).
            is_firm: If True, use firm dossier.
            firm_id: UUID of firm (required if is_firm=True).
        """
        self.user_id = user_id
        self.is_firm = is_firm
        self.firm_id = firm_id

        # Collection name for Qdrant
        if is_firm and firm_id:
            self.collection_name = f"dossier_firm_{firm_id}"
        else:
            self.collection_name = f"dossier_user_{user_id}"

        # Initialize components
        self.embedder = get_embedder()
        self.qdrant = QdrantManager()
        self.dossier = DossierDB(
            user_id=user_id,
            user_password=user_password,
            is_firm=is_firm,
            firm_id=firm_id
        )

        # Ensure collection exists
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """Create Qdrant collection if it doesn't exist."""
        try:
            self.qdrant.create_collection(
                collection_name=self.collection_name,
                vector_size=1024,  # BGE-M3 dimension
                enable_sparse=True
            )
        except Exception as e:
            # Collection might already exist
            logger.debug(f"Collection check: {e}")

    def add_document(
        self,
        title: str,
        content: str,
        doc_type: Optional[str] = None,
        language: Optional[str] = None,
        metadata: Optional[Dict] = None,
        chunk_size: int = 500
    ) -> str:
        """
        Add a document to the dossier with embeddings.

        Args:
            title: Document title.
            content: Full document content.
            doc_type: Type (letter, contract, brief, etc.).
            language: Language code (de, fr, it).
            metadata: Additional metadata.
            chunk_size: Maximum words per chunk.

        Returns:
            Document ID.
        """
        doc_id = str(uuid.uuid4())

        # Store document in encrypted SQLCipher
        self.dossier.store_document(
            doc_id=doc_id,
            title=title,
            content=content,
            doc_type=doc_type,
            language=language,
            metadata=metadata
        )

        # Chunk the content
        chunks = self._chunk_text(content, chunk_size)

        # Generate embeddings for each chunk
        chunk_records = []
        vector_points = []

        for idx, chunk_text in enumerate(chunks):
            embedding_id = f"{doc_id}_chunk_{idx}"

            # Generate dense + sparse embeddings
            embeddings = self.embedder._encode_single(chunk_text)

            chunk_records.append({
                "content": chunk_text,
                "embedding_id": embedding_id
            })

            # Prepare Qdrant point
            vector_points.append({
                "id": embedding_id,
                "vector": {
                    "dense": embeddings["dense"],
                    "sparse": embeddings["sparse"]
                },
                "payload": {
                    "doc_id": doc_id,
                    "chunk_index": idx,
                    "title": title,
                    "doc_type": doc_type,
                    "language": language,
                    "text_preview": chunk_text[:200],
                    "user_id": self.user_id if not self.is_firm else None,
                    "firm_id": self.firm_id if self.is_firm else None
                }
            })

        # Store chunks in encrypted database
        self.dossier.store_chunks(doc_id, chunk_records)

        # Store embeddings in Qdrant
        if vector_points:
            self.qdrant.upsert_points(self.collection_name, vector_points)

        logger.info(f"Added document {doc_id} with {len(chunks)} chunks")
        return doc_id

    def delete_document(self, doc_id: str) -> bool:
        """
        Delete a document and its embeddings.

        Args:
            doc_id: Document identifier.

        Returns:
            True if deleted, False if not found.
        """
        # Get chunk count before deletion
        doc = self.dossier.get_document(doc_id)
        if not doc:
            return False

        # Delete from encrypted database (includes chunks)
        if not self.dossier.delete_document(doc_id):
            return False

        # Delete embeddings from Qdrant by doc_id filter
        try:
            from qdrant_client import models
            self.qdrant.client.delete(
                collection_name=self.collection_name,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="doc_id",
                                match=models.MatchValue(value=doc_id)
                            )
                        ]
                    )
                )
            )
            logger.info(f"Deleted document {doc_id} and its embeddings")
        except Exception as e:
            logger.error(f"Error deleting embeddings for {doc_id}: {e}")

        return True

    def search(
        self,
        query: str,
        limit: int = 10,
        doc_type: Optional[str] = None,
        multilingual: bool = False
    ) -> List[Dict]:
        """
        Search the dossier for relevant documents.

        Args:
            query: Search query.
            limit: Maximum results.
            doc_type: Filter by document type.
            multilingual: Use dense-only search for cross-language queries.

        Returns:
            List of results with document content from encrypted storage.
        """
        # Generate query embeddings
        query_vectors = self.embedder._encode_single(query)

        # Build filters
        filters = {}
        if doc_type:
            filters["doc_type"] = doc_type

        # Search Qdrant
        if multilingual:
            qdrant_results = self.qdrant.search_dense(
                collection_name=self.collection_name,
                dense_vector=query_vectors["dense"],
                limit=limit,
                filters=filters if filters else None
            )
        else:
            qdrant_results = self.qdrant.search_hybrid(
                collection_name=self.collection_name,
                dense_vector=query_vectors["dense"],
                sparse_vector=query_vectors["sparse"],
                limit=limit,
                filters=filters if filters else None
            )

        # Enrich with content from encrypted database
        results = []
        for res in qdrant_results:
            payload = res.get("payload", {})
            doc_id = payload.get("doc_id")

            # Get full content from encrypted storage
            full_doc = self.dossier.get_document(doc_id) if doc_id else None

            results.append({
                "score": res.get("score", 0),
                "doc_id": doc_id,
                "chunk_index": payload.get("chunk_index"),
                "title": payload.get("title"),
                "doc_type": payload.get("doc_type"),
                "language": payload.get("language"),
                "text_preview": payload.get("text_preview"),
                "full_content": full_doc.get("content") if full_doc else None,
                "metadata": full_doc.get("metadata") if full_doc else {}
            })

        return results

    def list_documents(self, doc_type: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """
        List all documents in the dossier.

        Args:
            doc_type: Filter by document type.
            limit: Maximum documents to return.

        Returns:
            List of document metadata.
        """
        return self.dossier.list_documents(doc_type=doc_type, limit=limit)

    def get_document(self, doc_id: str) -> Optional[Dict]:
        """
        Get a document by ID.

        Args:
            doc_id: Document identifier.

        Returns:
            Document dict or None.
        """
        return self.dossier.get_document(doc_id)

    def get_stats(self) -> Dict:
        """
        Get dossier statistics.

        Returns:
            Dict with document count, collection info, etc.
        """
        dossier_stats = self.dossier.get_stats()

        # Get Qdrant collection info
        try:
            collection_info = self.qdrant.client.get_collection(self.collection_name)
            vector_count = collection_info.points_count
        except Exception:
            vector_count = 0

        return {
            **dossier_stats,
            "collection_name": self.collection_name,
            "vector_count": vector_count
        }

    def _chunk_text(self, text: str, max_words: int = 500) -> List[str]:
        """
        Split text into chunks at paragraph boundaries.

        Args:
            text: Full text to chunk.
            max_words: Maximum words per chunk.

        Returns:
            List of text chunks.
        """
        # Split by paragraphs (double newline)
        paragraphs = text.split("\n\n")
        chunks = []
        current_chunk = []
        current_words = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            para_words = len(para.split())

            if current_words + para_words > max_words and current_chunk:
                # Save current chunk
                chunks.append("\n\n".join(current_chunk))
                current_chunk = [para]
                current_words = para_words
            else:
                current_chunk.append(para)
                current_words += para_words

        # Don't forget the last chunk
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        # If no paragraphs found, split by sentences as fallback
        if not chunks and text.strip():
            words = text.split()
            for i in range(0, len(words), max_words):
                chunk = " ".join(words[i:i + max_words])
                if chunk.strip():
                    chunks.append(chunk)

        return chunks if chunks else [text.strip()] if text.strip() else []

    def close(self) -> None:
        """Close dossier connection."""
        self.dossier.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
