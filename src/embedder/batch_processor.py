"""
Batch embedding processor for KERBERUS Legal Intelligence.

Provides shared utilities for batch embedding operations including:
- Document processing with progress tracking
- Skip logic for existing documents
- Text chunking for long documents
"""

import logging
import re
from typing import List, Dict, Set, Optional, Callable
from tqdm import tqdm

logger = logging.getLogger(__name__)


class BatchEmbeddingProcessor:
    """
    Shared utilities for batch embedding operations.

    Features:
    - Process documents in batches with progress tracking
    - Skip already embedded documents
    - Chunk long text at paragraph boundaries
    - Statistics reporting
    """

    def __init__(
        self,
        embedder,
        qdrant_manager,
        collection_name: str
    ):
        """
        Initialize batch processor.

        Args:
            embedder: BGEEmbedder instance
            qdrant_manager: QdrantManager instance
            collection_name: Target collection name
        """
        self.embedder = embedder
        self.qdrant_manager = qdrant_manager
        self.collection_name = collection_name

    def get_existing_ids(self) -> Set[str]:
        """
        Get IDs already in collection for skip logic.

        Returns:
            Set of existing document IDs
        """
        try:
            # Use scroll to get all point IDs
            existing_ids = set()
            offset = None
            batch_size = 100

            while True:
                results = self.qdrant_manager.client.scroll(
                    collection_name=self.collection_name,
                    limit=batch_size,
                    offset=offset,
                    with_payload=False,
                    with_vectors=False
                )

                points, next_offset = results

                if not points:
                    break

                for point in points:
                    existing_ids.add(str(point.id))

                if next_offset is None:
                    break

                offset = next_offset

            logger.info(f"Found {len(existing_ids)} existing documents in {self.collection_name}")
            return existing_ids

        except Exception as e:
            logger.warning(f"Could not retrieve existing IDs: {e}")
            return set()

    def process_documents(
        self,
        documents: List[Dict],
        text_field: str,
        id_field: str,
        payload_builder: Callable[[Dict], Dict],
        batch_size: int = 32,
        skip_existing: bool = True,
        show_progress: bool = True
    ) -> Dict:
        """
        Process documents in batches.

        Args:
            documents: List of document dicts
            text_field: Field containing text to embed
            id_field: Field containing document ID
            payload_builder: Function to build payload from document
            batch_size: Number of documents per batch
            skip_existing: Skip already embedded documents
            show_progress: Show progress bar

        Returns:
            {"embedded": 150, "skipped": 50, "errors": 0}
        """
        stats = {"embedded": 0, "skipped": 0, "errors": 0}

        # Get existing IDs if skip_existing is True
        existing_ids = self.get_existing_ids() if skip_existing else set()

        # Filter documents to process
        docs_to_process = []
        for doc in documents:
            doc_id = doc.get(id_field)
            if skip_existing and doc_id in existing_ids:
                stats["skipped"] += 1
                continue
            docs_to_process.append(doc)

        if not docs_to_process:
            logger.info("No new documents to embed")
            return stats

        logger.info(f"Processing {len(docs_to_process)} documents (skipped {stats['skipped']} existing)")

        # Process in batches
        iterator = range(0, len(docs_to_process), batch_size)
        if show_progress:
            iterator = tqdm(iterator, desc=f"Embedding to {self.collection_name}")

        for i in iterator:
            batch = docs_to_process[i:i + batch_size]

            try:
                # Extract texts
                texts = [doc.get(text_field, "") for doc in batch]

                # Generate embeddings
                embeddings = self.embedder.encode_batch(texts, batch_size=batch_size)

                # Build points
                points = []
                for doc, embedding in zip(batch, embeddings):
                    doc_id = doc.get(id_field)
                    payload = payload_builder(doc)

                    points.append({
                        "id": doc_id,
                        "vector": embedding,
                        "payload": payload
                    })

                # Upsert to Qdrant
                self.qdrant_manager.upsert_points(self.collection_name, points)
                stats["embedded"] += len(points)

            except Exception as e:
                logger.error(f"Error processing batch: {e}")
                stats["errors"] += len(batch)

        return stats

    @staticmethod
    def chunk_long_text(
        text: str,
        max_words: int = 1000,
        min_words: int = 200
    ) -> List[str]:
        """
        Split long text into chunks at paragraph boundaries.

        Args:
            text: Input text
            max_words: Maximum words per chunk
            min_words: Minimum words per chunk (to avoid tiny chunks)

        Returns:
            List of text chunks
        """
        if not text:
            return []

        # Count words
        words = text.split()
        if len(words) <= max_words:
            return [text]

        # Split on double newlines (paragraphs)
        paragraphs = re.split(r'\n\n+', text)

        chunks = []
        current_chunk = []
        current_word_count = 0

        for para in paragraphs:
            para_words = len(para.split())

            # If adding this paragraph exceeds max and current chunk is not empty
            if current_word_count + para_words > max_words and current_chunk:
                # Save current chunk
                chunk_text = '\n\n'.join(current_chunk)
                chunks.append(chunk_text)
                current_chunk = [para]
                current_word_count = para_words
            else:
                current_chunk.append(para)
                current_word_count += para_words

        # Don't forget the last chunk
        if current_chunk:
            chunk_text = '\n\n'.join(current_chunk)
            # If last chunk is too small, merge with previous
            if len(chunks) > 0 and current_word_count < min_words:
                chunks[-1] = chunks[-1] + '\n\n' + chunk_text
            else:
                chunks.append(chunk_text)

        return chunks

    @staticmethod
    def create_text_preview(text: str, max_chars: int = 200) -> str:
        """
        Create a text preview for display.

        Args:
            text: Full text
            max_chars: Maximum characters for preview

        Returns:
            Truncated text with ellipsis if needed
        """
        if not text:
            return ""

        # Clean whitespace
        text = ' '.join(text.split())

        if len(text) <= max_chars:
            return text

        # Truncate at word boundary
        truncated = text[:max_chars]
        last_space = truncated.rfind(' ')
        if last_space > max_chars // 2:
            truncated = truncated[:last_space]

        return truncated + "..."
