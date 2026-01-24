"""
Conversation Manager for KERBERUS.

This module implements the "Dynamic Context Swapping" pattern to prevent
token bloat in long conversations while maintaining natural dialogue flow.

Key Concepts:
- Bucket A (Chat History): Preserved across turns, keeps conversation natural
- Bucket B (Legal Context): Replaced on every turn with fresh, relevant context

The Problem (Traditional RAG):
    Turn 1:  5,000 tokens
    Turn 5:  25,000 tokens
    Turn 10: 50,000 tokens  <- 10x cost explosion!

Our Solution (Dynamic Swapping):
    Every turn: ~5,000 tokens (flat cost regardless of conversation length)
"""
import os
import json
import uuid
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class ConversationTurn:
    """A single turn in a conversation."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat()
        }


@dataclass
class TokenUsageRecord:
    """Record of token usage for a single turn."""
    conversation_id: str
    turn_number: int
    input_tokens: int
    output_tokens: int
    chat_history_tokens: int
    legal_context_tokens: int
    query_tokens: int
    context_swapped: bool = True
    model_used: str = "qwen3-vl-235b-instruct"
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def total_tokens(self) -> int:
        """Total tokens used in this turn."""
        return self.input_tokens + self.output_tokens

    @property
    def input_cost_chf(self) -> float:
        """Calculate input cost in CHF."""
        cost_per_m = float(os.getenv("QWEN_INPUT_COST_PER_M", 0.70))
        return (self.input_tokens / 1_000_000) * cost_per_m

    @property
    def output_cost_chf(self) -> float:
        """Calculate output cost in CHF."""
        cost_per_m = float(os.getenv("QWEN_OUTPUT_COST_PER_M", 2.20))
        return (self.output_tokens / 1_000_000) * cost_per_m

    @property
    def total_cost_chf(self) -> float:
        """Calculate total cost in CHF."""
        return self.input_cost_chf + self.output_cost_chf

    def to_dict(self) -> Dict:
        """Convert to dictionary for logging."""
        return {
            "conversation_id": self.conversation_id,
            "turn_number": self.turn_number,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "chat_history_tokens": self.chat_history_tokens,
            "legal_context_tokens": self.legal_context_tokens,
            "query_tokens": self.query_tokens,
            "input_cost_chf": round(self.input_cost_chf, 6),
            "output_cost_chf": round(self.output_cost_chf, 6),
            "total_cost_chf": round(self.total_cost_chf, 6),
            "context_swapped": self.context_swapped,
            "model_used": self.model_used,
            "timestamp": self.timestamp.isoformat()
        }


class ConversationManager:
    """
    Manage conversation state with Dynamic Context Swapping.

    This class maintains two separate "buckets" of information:

    Bucket A (Chat History):
        - Preserved across turns
        - Limited to last N turns (configurable)
        - Keeps conversation natural
        - Small token footprint (~500 tokens)

    Bucket B (Legal Context):
        - REPLACED on every turn
        - Fresh context from search results
        - Large token footprint (~4000 tokens)
        - Prevents token bloat

    Result: Flat ~5000 tokens per turn regardless of conversation length.
    """

    def __init__(
        self,
        user_id: str,
        conversation_id: Optional[str] = None,
        max_history_turns: Optional[int] = None
    ):
        """
        Initialize conversation manager.

        Args:
            user_id: UUID of the user.
            conversation_id: Optional conversation UUID. Generated if not provided.
            max_history_turns: Max turns to keep in history. Uses env default if None.
        """
        self.user_id = user_id
        self.conversation_id = conversation_id or str(uuid.uuid4())
        self.max_history_turns = max_history_turns or int(
            os.getenv("MAX_CHAT_HISTORY_TURNS", 5)
        )

        # Bucket A: Chat history (preserved, limited)
        self._chat_history: List[ConversationTurn] = []

        # Bucket B: Current legal context (replaced each turn)
        self._current_context: Dict = {
            "codex": [],
            "library": [],
            "dossier": []
        }

        # Token usage tracking
        self._turn_number = 0
        self._usage_records: List[TokenUsageRecord] = []

        # Token tracking settings
        self.enable_tracking = os.getenv("ENABLE_TOKEN_TRACKING", "true").lower() == "true"
        self.token_log_path = Path(os.getenv("TOKEN_USAGE_LOG_PATH", "./logs/token_usage.jsonl"))

    @property
    def chat_history(self) -> List[Dict[str, str]]:
        """
        Get chat history for prompt building (Bucket A).

        Returns only last N turns as configured.
        """
        # Each "turn" is a user message + assistant response
        max_messages = self.max_history_turns * 2
        recent = self._chat_history[-max_messages:]
        return [{"role": t.role, "content": t.content} for t in recent]

    def add_user_message(self, content: str) -> None:
        """
        Add a user message to chat history.

        Args:
            content: The user's message.
        """
        self._chat_history.append(ConversationTurn(role="user", content=content))

    def add_assistant_message(self, content: str) -> None:
        """
        Add an assistant message to chat history.

        Args:
            content: The assistant's response.
        """
        self._chat_history.append(ConversationTurn(role="assistant", content=content))
        self._turn_number += 1

    def swap_context(
        self,
        codex_results: List[Dict],
        library_results: List[Dict],
        dossier_results: List[Dict]
    ) -> None:
        """
        Replace legal context with fresh search results (Bucket B swap).

        This is the core of Dynamic Context Swapping. Old context is
        completely discarded and replaced with new, relevant context.

        Args:
            codex_results: Fresh results from Swiss law search.
            library_results: Fresh results from case law search.
            dossier_results: Fresh results from user dossier search.
        """
        # Complete replacement - old context is discarded
        self._current_context = {
            "codex": codex_results,
            "library": library_results,
            "dossier": dossier_results
        }

    def get_current_context(self) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """
        Get current legal context for prompt building.

        Returns:
            Tuple of (codex_results, library_results, dossier_results).
        """
        return (
            self._current_context["codex"],
            self._current_context["library"],
            self._current_context["dossier"]
        )

    def record_token_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        chat_history_tokens: int,
        legal_context_tokens: int,
        query_tokens: int
    ) -> TokenUsageRecord:
        """
        Record token usage for this turn.

        Args:
            input_tokens: Total input tokens sent to model.
            output_tokens: Total output tokens received from model.
            chat_history_tokens: Tokens used for chat history.
            legal_context_tokens: Tokens used for legal context.
            query_tokens: Tokens used for user query.

        Returns:
            The created TokenUsageRecord.
        """
        record = TokenUsageRecord(
            conversation_id=self.conversation_id,
            turn_number=self._turn_number,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            chat_history_tokens=chat_history_tokens,
            legal_context_tokens=legal_context_tokens,
            query_tokens=query_tokens
        )

        self._usage_records.append(record)

        # Log to file if enabled
        if self.enable_tracking:
            self._log_usage(record)

        return record

    def _log_usage(self, record: TokenUsageRecord) -> None:
        """
        Log token usage to JSONL file.

        Args:
            record: The usage record to log.
        """
        try:
            self.token_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.token_log_path, "a") as f:
                f.write(json.dumps(record.to_dict()) + "\n")
        except Exception as e:
            print(f"Warning: Failed to log token usage: {e}")

    def get_session_stats(self) -> Dict:
        """
        Get statistics for this conversation session.

        Returns:
            Dict with total tokens, costs, and per-turn breakdown.
        """
        if not self._usage_records:
            return {
                "conversation_id": self.conversation_id,
                "total_turns": 0,
                "total_tokens": 0,
                "total_cost_chf": 0.0,
                "average_tokens_per_turn": 0,
                "context_swap_efficiency": "N/A"
            }

        total_tokens = sum(r.total_tokens for r in self._usage_records)
        total_cost = sum(r.total_cost_chf for r in self._usage_records)

        return {
            "conversation_id": self.conversation_id,
            "total_turns": len(self._usage_records),
            "total_tokens": total_tokens,
            "total_cost_chf": round(total_cost, 4),
            "average_tokens_per_turn": total_tokens // len(self._usage_records),
            "context_swap_efficiency": "FLAT" if self._is_cost_flat() else "GROWING"
        }

    def _is_cost_flat(self) -> bool:
        """
        Check if token usage is staying flat (Dynamic Swapping working).

        Returns:
            True if cost is flat, False if growing (problem detected).
        """
        if len(self._usage_records) < 3:
            return True

        # Check if later turns have similar token counts to earlier turns
        early_avg = sum(r.total_tokens for r in self._usage_records[:3]) / 3
        late_avg = sum(r.total_tokens for r in self._usage_records[-3:]) / 3

        # Allow 20% growth tolerance
        return late_avg < early_avg * 1.2

    def clear_history(self) -> None:
        """
        Clear chat history (useful for starting a new topic).

        Note: This only clears Bucket A (chat history).
        Bucket B (legal context) is cleared automatically on next swap.
        """
        self._chat_history = []

    def export_conversation(self) -> Dict:
        """
        Export full conversation for debugging or analysis.

        Returns:
            Dict with full conversation state.
        """
        return {
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "turn_number": self._turn_number,
            "chat_history": [t.to_dict() for t in self._chat_history],
            "current_context_summary": {
                "codex_count": len(self._current_context["codex"]),
                "library_count": len(self._current_context["library"]),
                "dossier_count": len(self._current_context["dossier"])
            },
            "usage_records": [r.to_dict() for r in self._usage_records],
            "session_stats": self.get_session_stats()
        }
