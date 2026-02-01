"""
LLM Client for KERBERUS.

Supports:
- Mistral AI (mistral-small, mistral-large)
- Mock mode for development/testing
- Token usage tracking
"""
import os
import json
import logging
import time
from typing import Optional, Dict, List, Generator
from dataclasses import dataclass

import requests

from ..utils.secrets import get_secret

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Response from LLM."""
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: float
    cost_chf: float = 0.0


class LLMClient:
    """
    LLM client supporting Mistral and mock mode.

    Usage:
        client = LLMClient()
        response = client.chat("What is Art. 337 OR?", context="...")
        print(response.content)
    """

    # Mistral pricing (CHF per 1M tokens) - approximate
    PRICING = {
        "mistral-small-latest": {"input": 0.20, "output": 0.60},
        "mistral-large-latest": {"input": 2.00, "output": 6.00},
        "open-mistral-nemo": {"input": 0.15, "output": 0.15},
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "mistral-small-latest",
        base_url: str = "https://api.mistral.ai/v1",
        use_mock: Optional[bool] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ):
        """
        Initialize LLM client.

        Args:
            api_key: Mistral API key (reads from env if not provided)
            model: Model to use
            base_url: API base URL
            use_mock: Force mock mode (auto-detects if None)
            max_tokens: Maximum response tokens
            temperature: Sampling temperature (lower = more focused)
        """
        self.api_key = api_key or get_secret("MISTRAL_API_KEY") or get_secret("LLM_API_KEY")
        self.model = model
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.temperature = temperature

        # Auto-detect mock mode if not specified
        if use_mock is None:
            use_mock = os.getenv("USE_MOCK_AI", "false").lower() == "true"
            if not self.api_key:
                logger.warning("No API key found, enabling mock mode")
                use_mock = True

        self.use_mock = use_mock

        if self.use_mock:
            logger.info("LLM Client initialized in MOCK mode")
        else:
            logger.info(f"LLM Client initialized with model: {model}")

    def chat(
        self,
        query: str,
        context: str,
        system_prompt: Optional[str] = None,
        chat_history: Optional[List[Dict]] = None,
    ) -> LLMResponse:
        """
        Send a chat request to the LLM.

        Args:
            query: User's question
            context: Legal context (laws, cases)
            system_prompt: System prompt override
            chat_history: Previous conversation turns

        Returns:
            LLMResponse with content and metadata
        """
        if self.use_mock:
            return self._mock_response(query, context)

        return self._mistral_chat(query, context, system_prompt, chat_history)

    def chat_stream(
        self,
        query: str,
        context: str,
        system_prompt: Optional[str] = None,
        chat_history: Optional[List[Dict]] = None,
    ) -> Generator[str, None, LLMResponse]:
        """
        Stream a chat response from the LLM.

        Yields content chunks, returns final LLMResponse.

        Args:
            query: User's question
            context: Legal context
            system_prompt: System prompt override
            chat_history: Previous conversation

        Yields:
            Content chunks as they arrive

        Returns:
            Final LLMResponse with full content and metadata
        """
        if self.use_mock:
            response = self._mock_response(query, context)
            # Simulate streaming
            words = response.content.split()
            for i, word in enumerate(words):
                yield word + (" " if i < len(words) - 1 else "")
                time.sleep(0.02)  # Simulate latency
            return response

        return self._mistral_stream(query, context, system_prompt, chat_history)

    def _mistral_chat(
        self,
        query: str,
        context: str,
        system_prompt: Optional[str],
        chat_history: Optional[List[Dict]],
    ) -> LLMResponse:
        """Call Mistral API (non-streaming)."""
        messages = self._build_messages(query, context, system_prompt, chat_history)

        start_time = time.time()

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": self.max_tokens,
                    "temperature": self.temperature,
                },
                timeout=120,
            )
            response.raise_for_status()
            data = response.json()

            latency_ms = (time.time() - start_time) * 1000
            usage = data.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)

            content = data["choices"][0]["message"]["content"]

            return LLMResponse(
                content=content,
                model=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                latency_ms=latency_ms,
                cost_chf=self._calculate_cost(input_tokens, output_tokens),
            )

        except requests.exceptions.RequestException as e:
            logger.error(f"Mistral API error: {e}")
            raise RuntimeError(f"LLM request failed: {e}")

    def _mistral_stream(
        self,
        query: str,
        context: str,
        system_prompt: Optional[str],
        chat_history: Optional[List[Dict]],
    ) -> Generator[str, None, LLMResponse]:
        """Call Mistral API with streaming."""
        messages = self._build_messages(query, context, system_prompt, chat_history)

        start_time = time.time()
        full_content = []
        input_tokens = 0
        output_tokens = 0

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": self.max_tokens,
                    "temperature": self.temperature,
                    "stream": True,
                },
                stream=True,
                timeout=120,
            )
            response.raise_for_status()

            for line in response.iter_lines():
                if line:
                    line = line.decode("utf-8")
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                full_content.append(content)
                                yield content

                            # Get usage from final chunk
                            if "usage" in chunk:
                                input_tokens = chunk["usage"].get("prompt_tokens", 0)
                                output_tokens = chunk["usage"].get("completion_tokens", 0)
                        except json.JSONDecodeError:
                            continue

            latency_ms = (time.time() - start_time) * 1000

            return LLMResponse(
                content="".join(full_content),
                model=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                latency_ms=latency_ms,
                cost_chf=self._calculate_cost(input_tokens, output_tokens),
            )

        except requests.exceptions.RequestException as e:
            logger.error(f"Mistral streaming error: {e}")
            raise RuntimeError(f"LLM streaming failed: {e}")

    def _build_messages(
        self,
        query: str,
        context: str,
        system_prompt: Optional[str],
        chat_history: Optional[List[Dict]],
    ) -> List[Dict]:
        """Build messages array for API call."""
        from .prompts import LegalPrompts

        messages = []

        # System prompt
        system = system_prompt or LegalPrompts.SYSTEM_PROMPT
        messages.append({"role": "system", "content": system})

        # Chat history (if any)
        if chat_history:
            for turn in chat_history[-5:]:  # Last 5 turns
                messages.append(turn)

        # Current query with context
        user_message = LegalPrompts.format_user_prompt(query, context)
        messages.append({"role": "user", "content": user_message})

        return messages

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost in CHF based on token usage."""
        pricing = self.PRICING.get(self.model, {"input": 0.20, "output": 0.60})
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        return round(input_cost + output_cost, 6)

    def _mock_response(self, query: str, context: str) -> LLMResponse:
        """Generate a mock response for development."""
        # Simulate processing time
        time.sleep(0.5)

        mock_content = f"""Basierend auf den gefundenen Rechtsquellen:

**Rechtliche Analyse:**

Ihre Frage betrifft einen wichtigen Aspekt des Schweizer Rechts. Die relevanten Bestimmungen finden sich in den unten aufgeführten Quellen.

**Zusammenfassung:**

Die Suche hat relevante Gesetzesartikel und Entscheide gefunden, die Ihre Frage beantworten. Bitte beachten Sie, dass dies eine automatisierte Analyse ist und keine Rechtsberatung darstellt.

**Hinweis:** Dies ist eine Mock-Antwort für Entwicklungszwecke. Für Produktivbetrieb konfigurieren Sie bitte den Mistral API-Schlüssel.

---
*Generiert von KERBERUS (Mock-Modus)*"""

        return LLMResponse(
            content=mock_content,
            model="mock",
            input_tokens=len(context.split()) + len(query.split()),
            output_tokens=len(mock_content.split()),
            total_tokens=len(context.split()) + len(query.split()) + len(mock_content.split()),
            latency_ms=500,
            cost_chf=0.0,
        )


# Singleton instance
_llm_client: Optional[LLMClient] = None


def get_llm_client(**kwargs) -> LLMClient:
    """Get shared LLM client instance."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient(**kwargs)
    return _llm_client
