"""
Prompt Builder for KERBERUS.

This module provides the hybrid prompt system that combines:
- System prompt (Swiss legal assistant persona)
- Chat history (Bucket A - preserved across turns)
- Legal context (Bucket B - replaced on each turn)
- User query

The prompt builder ensures consistent formatting and token management
while maintaining the "Sliding Window of Truth" pattern.
"""
import os
from typing import List, Dict, Optional
from dataclasses import dataclass

import tiktoken


@dataclass
class PromptConfig:
    """Configuration for prompt building."""
    max_context_tokens: int = 4000
    max_chat_history_turns: int = 5
    hard_token_limit: int = 12000
    warn_token_threshold: int = 10000


# Swiss Legal Assistant system prompt
SYSTEM_PROMPT = """Du bist KERBERUS, ein Schweizer Rechtsassistent für Anwälte und Treuhänder.

DEINE AUFGABEN:
- Rechtsfragen nach Schweizer Recht beantworten (OR, ZGB, StGB, kantonale Gesetze)
- Relevante Rechtsprechung (BGE, kantonale Entscheide) zitieren
- Rechtsdokumente im Stil des Nutzers erstellen

WICHTIGE REGELN:
1. Antworte IMMER auf Deutsch, Französisch oder Italienisch - je nach Sprache der Frage
2. Zitiere IMMER die genaue Rechtsquelle (z.B. "Art. 337 OR", "BGE 130 III 213")
3. Unterscheide zwischen Bundesrecht und kantonalem Recht
4. Wenn du unsicher bist, sage es klar und empfehle eine anwaltliche Beratung
5. Erfinde NIEMALS Gesetze oder Entscheide - nur das, was im Kontext steht

DEIN WISSEN KOMMT AUS:
- [CODEX] Schweizer Gesetze (vom System bereitgestellt)
- [LIBRARY] Bundesgerichtsentscheide und kantonale Urteile (vom System bereitgestellt)
- [DOSSIER] Frühere Dokumente des Nutzers (vom System bereitgestellt)

Wenn kein Kontext bereitgestellt wird, antworte basierend auf deinem allgemeinen Rechtswissen,
aber weise darauf hin, dass du die spezifische Rechtsquelle nicht verifizieren konntest.

---
Tu es KERBERUS, un assistant juridique suisse pour avocats et fiduciaires.
Tu réponds en français si la question est en français.

---
Sei KERBERUS, un assistente giuridico svizzero per avvocati e fiduciari.
Rispondi in italiano se la domanda è in italiano.
"""


class PromptBuilder:
    """
    Build prompts for the Qwen3-VL model with context management.

    Implements the "Sliding Window of Truth" pattern:
    - Bucket A (Chat History): Preserved across turns (small, ~500 tokens)
    - Bucket B (Legal Context): Replaced on each turn (large, ~4000 tokens)
    """

    def __init__(self, config: Optional[PromptConfig] = None):
        """
        Initialize the prompt builder.

        Args:
            config: Prompt configuration. Uses environment defaults if None.
        """
        self.config = config or PromptConfig(
            max_context_tokens=int(os.getenv("MAX_CONTEXT_TOKENS", 4000)),
            max_chat_history_turns=int(os.getenv("MAX_CHAT_HISTORY_TURNS", 5)),
            hard_token_limit=int(os.getenv("HARD_TOKEN_LIMIT", 12000)),
            warn_token_threshold=int(os.getenv("WARN_TOKEN_THRESHOLD", 10000)),
        )

        # Use tiktoken for token counting (cl100k_base is close to Qwen's tokenizer)
        try:
            self.tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self.tokenizer = None

    def count_tokens(self, text: str) -> int:
        """
        Count tokens in a text string.

        Args:
            text: Text to count tokens for.

        Returns:
            Number of tokens (estimated if tokenizer unavailable).
        """
        if self.tokenizer:
            return len(self.tokenizer.encode(text))
        # Fallback: rough estimate (4 chars per token for multilingual)
        return len(text) // 4

    def truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """
        Truncate text to a maximum number of tokens.

        Args:
            text: Text to truncate.
            max_tokens: Maximum number of tokens.

        Returns:
            Truncated text.
        """
        if self.tokenizer:
            tokens = self.tokenizer.encode(text)
            if len(tokens) <= max_tokens:
                return text
            return self.tokenizer.decode(tokens[:max_tokens])
        # Fallback: rough character-based truncation
        chars = max_tokens * 4
        return text[:chars] if len(text) > chars else text

    def format_chat_history(
        self,
        history: List[Dict[str, str]],
        max_turns: Optional[int] = None
    ) -> str:
        """
        Format chat history for inclusion in prompt (Bucket A).

        Args:
            history: List of {"role": "user"|"assistant", "content": "..."} dicts.
            max_turns: Maximum number of turns to include (default from config).

        Returns:
            Formatted chat history string.
        """
        max_turns = max_turns or self.config.max_chat_history_turns

        # Take last N turns (each turn = user + assistant message)
        recent_history = history[-(max_turns * 2):]

        if not recent_history:
            return ""

        formatted_lines = ["[CHAT HISTORY]"]
        for msg in recent_history:
            role = "User" if msg["role"] == "user" else "Assistant"
            formatted_lines.append(f"{role}: {msg['content']}")
        formatted_lines.append("[END CHAT HISTORY]")

        return "\n".join(formatted_lines)

    def format_legal_context(
        self,
        codex_results: List[Dict],
        library_results: List[Dict],
        dossier_results: List[Dict],
        max_tokens: Optional[int] = None
    ) -> str:
        """
        Format legal context for inclusion in prompt (Bucket B).

        This context is REPLACED on every turn to prevent token bloat.

        Args:
            codex_results: Results from Swiss law search.
            library_results: Results from case law search.
            dossier_results: Results from user dossier search.
            max_tokens: Maximum tokens for context (default from config).

        Returns:
            Formatted legal context string.
        """
        max_tokens = max_tokens or self.config.max_context_tokens
        sections = []

        # Format each section
        if codex_results:
            codex_lines = ["[CODEX - Swiss Laws]"]
            for result in codex_results:
                text = result.get("text", "")
                source = result.get("id", "unknown")
                codex_lines.append(f"- {source}: {text}")
            codex_lines.append("[END CODEX]")
            sections.append("\n".join(codex_lines))

        if library_results:
            library_lines = ["[LIBRARY - Case Law]"]
            for result in library_results:
                text = result.get("text", "")
                source = result.get("id", "unknown")
                library_lines.append(f"- {source}: {text}")
            library_lines.append("[END LIBRARY]")
            sections.append("\n".join(library_lines))

        if dossier_results:
            dossier_lines = ["[DOSSIER - Your Documents]"]
            for result in dossier_results:
                text = result.get("text", "")
                source = result.get("id", "unknown")
                dossier_lines.append(f"- {source}: {text}")
            dossier_lines.append("[END DOSSIER]")
            sections.append("\n".join(dossier_lines))

        # Combine and truncate to max tokens
        combined = "\n\n".join(sections)
        return self.truncate_to_tokens(combined, max_tokens)

    def build_prompt(
        self,
        query: str,
        chat_history: List[Dict[str, str]],
        codex_results: List[Dict],
        library_results: List[Dict],
        dossier_results: List[Dict],
    ) -> Dict[str, any]:
        """
        Build complete prompt for the AI model.

        Implements the "Sliding Window of Truth" pattern:
        - System prompt: Fixed
        - Chat history (Bucket A): Preserved, limited to N turns
        - Legal context (Bucket B): Replaced entirely on each turn
        - User query: Current question

        Args:
            query: Current user query.
            chat_history: Previous conversation turns.
            codex_results: Search results from Swiss law.
            library_results: Search results from case law.
            dossier_results: Search results from user dossier.

        Returns:
            Dict with 'messages' list and 'token_counts' breakdown.
        """
        # Format components
        history_str = self.format_chat_history(chat_history)
        context_str = self.format_legal_context(
            codex_results, library_results, dossier_results
        )

        # Build messages array
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]

        # Add context as system message (gets replaced each turn)
        if context_str:
            messages.append({
                "role": "system",
                "content": f"[CURRENT LEGAL CONTEXT]\n{context_str}\n[END CONTEXT]"
            })

        # Add chat history summary if exists
        if history_str:
            messages.append({
                "role": "system",
                "content": history_str
            })

        # Add current query
        messages.append({
            "role": "user",
            "content": query
        })

        # Calculate token counts for monitoring
        system_tokens = self.count_tokens(SYSTEM_PROMPT)
        context_tokens = self.count_tokens(context_str) if context_str else 0
        history_tokens = self.count_tokens(history_str) if history_str else 0
        query_tokens = self.count_tokens(query)
        total_tokens = system_tokens + context_tokens + history_tokens + query_tokens

        # Warn if approaching limits
        if total_tokens > self.config.warn_token_threshold:
            print(f"WARNING: Prompt approaching token limit ({total_tokens} tokens)")

        if total_tokens > self.config.hard_token_limit:
            print(f"ERROR: Prompt exceeds hard limit ({total_tokens} > {self.config.hard_token_limit})")
            # Truncate context to fit
            remaining = self.config.hard_token_limit - system_tokens - history_tokens - query_tokens
            context_str = self.truncate_to_tokens(context_str, remaining)
            context_tokens = self.count_tokens(context_str)

        return {
            "messages": messages,
            "token_counts": {
                "system": system_tokens,
                "context": context_tokens,
                "history": history_tokens,
                "query": query_tokens,
                "total": system_tokens + context_tokens + history_tokens + query_tokens,
            }
        }
