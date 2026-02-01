"""
Context Assembly for KERBERUS RAG Pipeline.

Handles:
- Fetching full documents when chunks are found
- Deduplication of results
- Token budget management
- Context formatting for LLM
"""
import logging
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

from ..database.vector_db import QdrantManager
from .prompts import LegalPrompts

logger = logging.getLogger(__name__)


class ContextAssembler:
    """
    Assembles context for LLM from search results.

    Key feature: When a chunk is found, fetches the FULL document
    instead of just the chunk, as legal reasoning requires complete context.
    """

    def __init__(self, max_context_tokens: int = 8000):
        """
        Initialize context assembler.

        Args:
            max_context_tokens: Maximum tokens for context (rough estimate: 4 chars = 1 token)
        """
        self.qdrant = QdrantManager()
        self.max_context_tokens = max_context_tokens
        # Rough estimate: 4 characters per token
        self.max_context_chars = max_context_tokens * 4

    def assemble(
        self,
        codex_results: List[Dict],
        library_results: List[Dict],
        fetch_full_documents: bool = True
    ) -> Tuple[str, Dict]:
        """
        Assemble context from search results.

        Args:
            codex_results: Law article search results
            library_results: Court decision search results
            fetch_full_documents: If True, fetch full decisions (not just chunks)

        Returns:
            Tuple of (formatted_context, metadata)
        """
        # Process laws (already at article level, no need to fetch more)
        laws = self._process_laws(codex_results)

        # Process decisions - fetch full documents if requested
        if fetch_full_documents:
            decisions, full_texts = self._fetch_full_decisions(library_results)
        else:
            decisions = library_results
            full_texts = {}

        # Format context
        context = LegalPrompts.format_full_context(laws, decisions, full_texts)

        # Truncate if too long
        if len(context) > self.max_context_chars:
            context = self._truncate_context(context)
            logger.warning(f"Context truncated to {self.max_context_chars} chars")

        metadata = {
            "law_count": len(laws),
            "decision_count": len(set(
                d.get("payload", {}).get("decision_id", "").split("_chunk_")[0]
                for d in decisions
            )),
            "context_chars": len(context),
            "context_tokens_estimate": len(context) // 4,
            "truncated": len(context) >= self.max_context_chars,
        }

        return context, metadata

    def _process_laws(self, results: List[Dict]) -> List[Dict]:
        """Process and deduplicate law results."""
        seen = set()
        unique_laws = []

        for result in results:
            payload = result.get("payload", {})
            # Create unique key from SR number + article
            key = f"{payload.get('sr_number', '')}_{payload.get('article_number', '')}"

            if key not in seen:
                seen.add(key)
                unique_laws.append(result)

        return unique_laws[:5]  # Limit to top 5 laws

    def _fetch_full_decisions(
        self,
        results: List[Dict]
    ) -> Tuple[List[Dict], Dict[str, str]]:
        """
        Fetch full decision texts from all chunks.

        When search returns chunk4, this fetches ALL chunks of that decision
        and concatenates them to provide full context to the LLM.

        Args:
            results: Search results (may be individual chunks)

        Returns:
            Tuple of (deduplicated results, dict mapping decision_id to full text)
        """
        # Group chunks by decision ID
        decision_chunks = defaultdict(list)
        decision_metadata = {}

        for result in results:
            payload = result.get("payload", {})
            decision_id = payload.get("decision_id", "")

            # Handle case where decision_id contains chunk suffix
            if "_chunk_" in str(decision_id):
                base_id = decision_id.split("_chunk_")[0]
            else:
                base_id = decision_id or payload.get("_original_id", "unknown")
                if "_chunk_" in str(base_id):
                    base_id = base_id.split("_chunk_")[0]

            if base_id and base_id != "unknown":
                decision_chunks[base_id].append(result)
                if base_id not in decision_metadata:
                    decision_metadata[base_id] = payload

        # Fetch all chunks for each unique decision
        full_texts = {}
        unique_results = []

        for decision_id, chunks in list(decision_chunks.items())[:3]:  # Limit to top 3 decisions
            try:
                # Fetch all chunks for this decision from Qdrant
                all_chunks = self._fetch_all_chunks(decision_id)

                if all_chunks:
                    # Sort by chunk index and concatenate
                    sorted_chunks = sorted(
                        all_chunks,
                        key=lambda x: x.get("payload", {}).get("chunk_index", 0)
                    )

                    full_text_parts = []
                    for chunk in sorted_chunks:
                        chunk_payload = chunk.get("payload", {})
                        chunk_type = chunk_payload.get("chunk_type", "")
                        text = chunk_payload.get("text_preview", "")

                        if chunk_type:
                            full_text_parts.append(f"[{chunk_type.upper()}]\n{text}")
                        else:
                            full_text_parts.append(text)

                    full_texts[decision_id] = "\n\n".join(full_text_parts)

                    # Use first chunk as representative result
                    unique_results.append(chunks[0])

                    logger.debug(
                        f"Fetched {len(all_chunks)} chunks for {decision_id}, "
                        f"total {len(full_texts[decision_id])} chars"
                    )
                else:
                    # Fallback: use what we have
                    unique_results.append(chunks[0])
                    full_texts[decision_id] = chunks[0].get("payload", {}).get("text_preview", "")

            except Exception as e:
                logger.error(f"Error fetching chunks for {decision_id}: {e}")
                # Fallback to original chunk
                unique_results.append(chunks[0])

        return unique_results, full_texts

    def _fetch_all_chunks(self, decision_id: str) -> List[Dict]:
        """
        Fetch all chunks for a decision from Qdrant.

        Args:
            decision_id: Base decision identifier (without chunk suffix)

        Returns:
            List of all chunk results for this decision
        """
        try:
            from qdrant_client import models

            # Search for all chunks with this decision_id
            results = self.qdrant.client.scroll(
                collection_name="library",
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="decision_id",
                            match=models.MatchValue(value=decision_id)
                        )
                    ]
                ),
                limit=50,  # Max chunks per decision
                with_payload=True,
                with_vectors=False,
            )

            points = results[0] if results else []

            return [
                {
                    "id": point.id,
                    "payload": point.payload,
                    "score": 1.0,  # No score for scroll results
                }
                for point in points
            ]

        except Exception as e:
            logger.error(f"Error scrolling chunks for {decision_id}: {e}")
            return []

    def _truncate_context(self, context: str) -> str:
        """
        Truncate context to fit token budget.

        Tries to truncate at section boundaries.
        """
        if len(context) <= self.max_context_chars:
            return context

        # Try to find a good truncation point
        truncated = context[:self.max_context_chars]

        # Look for last complete section
        last_section = truncated.rfind("\n## ")
        if last_section > self.max_context_chars * 0.5:
            truncated = truncated[:last_section]

        # Or last paragraph
        else:
            last_para = truncated.rfind("\n\n")
            if last_para > self.max_context_chars * 0.7:
                truncated = truncated[:last_para]

        return truncated + "\n\n[... Context truncated for token limit ...]"

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count (rough: 4 chars per token for German)."""
        return len(text) // 4
