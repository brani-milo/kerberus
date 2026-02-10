"""
LLM Clients for KERBERUS.

Supports:
- Mistral AI (for Guard, Enhance, Reformulate)
- Qwen via Infomaniak (for final legal analysis)
- Mock mode for development
"""
import os
import json
import logging
import time
from typing import Optional, Dict, List, Generator
from dataclasses import dataclass
from enum import Enum

import requests

from ..utils.secrets import get_secret

logger = logging.getLogger(__name__)


class LLMProvider(Enum):
    MISTRAL = "mistral"
    QWEN = "qwen"
    MOCK = "mock"


@dataclass
class LLMResponse:
    """Response from LLM."""
    content: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    latency_ms: float
    cost_chf: float = 0.0


class MistralClient:
    """
    Mistral AI client for preprocessing tasks.

    Used for:
    - Mistral 1: Guard & Enhance (sanitize, improve query)
    - Mistral 2: Query Reformulator (structure for Qwen)
    """

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
    ):
        self.api_key = api_key or get_secret("MISTRAL_API_KEY")
        self.model = model
        self.base_url = base_url

        if not self.api_key:
            logger.warning("No Mistral API key found")

    def chat(
        self,
        messages: List[Dict],
        max_tokens: int = 2048,
        temperature: float = 0.3,
    ) -> LLMResponse:
        """Send chat request to Mistral."""
        if not self.api_key:
            return self._mock_response(messages)

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
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()

            latency_ms = (time.time() - start_time) * 1000
            usage = data.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)

            return LLMResponse(
                content=data["choices"][0]["message"]["content"],
                model=self.model,
                provider="mistral",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                latency_ms=latency_ms,
                cost_chf=self._calculate_cost(input_tokens, output_tokens),
            )

        except Exception as e:
            logger.error(f"Mistral API error: {e}")
            raise RuntimeError(f"Mistral request failed: {e}")

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        pricing = self.PRICING.get(self.model, {"input": 0.20, "output": 0.60})
        return round(
            (input_tokens / 1_000_000) * pricing["input"] +
            (output_tokens / 1_000_000) * pricing["output"],
            6
        )

    def _mock_response(self, messages: List[Dict]) -> LLMResponse:
        """Mock response for development."""
        # Check if this is a guard request by looking at messages
        user_content = messages[-1].get("content", "") if messages else ""
        system_content = messages[0].get("content", "") if messages else ""

        if "USER'S ORIGINAL QUESTION:" in user_content:
            # Reformulator mock response (Mistral 2)
            mock_content = "Der Benutzer möchte wissen, unter welchen Umständen eine fristlose Kündigung im Schweizer Arbeitsrecht zulässig ist. Es wurden 5 relevante Gesetzesartikel und 3 Gerichtsentscheide gefunden."
        elif "QUERY:" in user_content and "security" in system_content.lower():
            # Guard & Enhance mock response (Mistral 1)
            # Extract query from message
            query = user_content.split("QUERY:")[-1].split("\n")[0].strip()
            mock_content = json.dumps({
                "status": "OK",
                "block_reason": None,
                "detected_language": "de",
                "original_query": query,
                "enhanced_query": query,
                "legal_concepts": ["Arbeitsrecht", "Kündigung"],
                "query_type": "legal_question"
            })
        else:
            mock_content = "[MOCK] Mistral response"

        return LLMResponse(
            content=mock_content,
            model="mock",
            provider="mock",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            latency_ms=100,
            cost_chf=0.0,
        )


class InfomaniakClient:
    """
    Infomaniak AI client for legal analysis.

    Uses Infomaniak's OpenAI-compatible API endpoint.
    Supports multiple models (Qwen, Mistral, Gemma, Llama).

    Used for generating comprehensive legal answers with:
    - Dual-language citations
    - Consistency indicators
    - Full legal analysis
    """

    # Pricing per 1M tokens (CHF) - varies by model
    PRICING = {
        # Premium models
        "qwen3-235b-a22b-instruct": {"input": 0.70, "output": 2.20},
        "llama-4-maverick-17b-128e-instruct": {"input": 0.50, "output": 1.50},
        # Cheap models
        "mistral-small-3.2-24b-instruct-2506": {"input": 0.10, "output": 0.30},
        "gemma-3n-e4b-it": {"input": 0.10, "output": 0.30},
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        product_id: Optional[str] = None,
        model: str = None,
    ):
        # API key from secrets or env
        self.api_key = api_key or get_secret("INFOMANIAK_API_KEY") or get_secret("LLM_API_KEY")

        # Product ID from env
        self.product_id = product_id or os.getenv("INFOMANIAK_PRODUCT_ID")

        # Model defaults to analysis model from env
        self.model = model or os.getenv("INFOMANIAK_ANALYSIS_MODEL", "qwen3-235b-a22b-instruct")

        # Build base URL
        if self.product_id:
            self.base_url = f"https://api.infomaniak.com/1/ai/{self.product_id}/openai"
        else:
            self.base_url = None
            logger.warning("No INFOMANIAK_PRODUCT_ID found - API calls will fail")

        if not self.api_key:
            logger.warning("No Infomaniak API key found (INFOMANIAK_API_KEY or LLM_API_KEY)")

    def chat(
        self,
        messages: List[Dict],
        max_tokens: int = 8192,
        temperature: float = 0.4,
        model: Optional[str] = None,
    ) -> LLMResponse:
        """Send chat request to Infomaniak.

        Args:
            messages: Chat messages
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            model: Override model for this request
        """
        if not self.api_key or not self.base_url:
            return self._mock_response(messages)

        use_model = model or self.model
        start_time = time.time()

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": use_model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=180,  # Longer timeout for complex analysis
            )
            response.raise_for_status()
            data = response.json()

            latency_ms = (time.time() - start_time) * 1000
            usage = data.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)

            return LLMResponse(
                content=data["choices"][0]["message"]["content"],
                model=use_model,
                provider="infomaniak",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                latency_ms=latency_ms,
                cost_chf=self._calculate_cost(use_model, input_tokens, output_tokens),
            )

        except Exception as e:
            logger.error(f"Infomaniak API error: {e}")
            raise RuntimeError(f"Infomaniak request failed: {e}")

    def chat_stream(
        self,
        messages: List[Dict],
        max_tokens: int = 8192,
        temperature: float = 0.4,
        model: Optional[str] = None,
    ) -> Generator[str, None, LLMResponse]:
        """Stream chat response from Infomaniak.

        Args:
            messages: Chat messages
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            model: Override model for this request
        """
        if not self.api_key or not self.base_url:
            response = self._mock_response(messages)
            for word in response.content.split():
                yield word + " "
                time.sleep(0.02)
            return response

        use_model = model or self.model
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
                    "model": use_model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "stream": True,
                },
                stream=True,
                timeout=180,
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

                            if "usage" in chunk:
                                input_tokens = chunk["usage"].get("prompt_tokens", 0)
                                output_tokens = chunk["usage"].get("completion_tokens", 0)
                        except json.JSONDecodeError:
                            continue

            latency_ms = (time.time() - start_time) * 1000

            return LLMResponse(
                content="".join(full_content),
                model=use_model,
                provider="infomaniak",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                latency_ms=latency_ms,
                cost_chf=self._calculate_cost(use_model, input_tokens, output_tokens),
            )

        except Exception as e:
            logger.error(f"Infomaniak streaming error: {e}")
            raise RuntimeError(f"Infomaniak streaming failed: {e}")

    def _calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        pricing = self.PRICING.get(model, {"input": 0.50, "output": 1.50})
        return round(
            (input_tokens / 1_000_000) * pricing["input"] +
            (output_tokens / 1_000_000) * pricing["output"],
            6
        )

    def _mock_response(self, messages: List[Dict]) -> LLMResponse:
        """Mock response for development."""
        mock_content = """```json
{"consistency": "MIXED", "confidence": "high"}
```

## 1. Gesetzesanalyse

**Art. 337 Abs. 1 OR** regelt die fristlose Kündigung aus wichtigem Grund.

« L'employeur et le travailleur peuvent résilier immédiatement le contrat en tout temps pour de justes motifs. »

> Original (DE): "Aus wichtigen Gründen kann der Arbeitgeber wie der Arbeitnehmer jederzeit das Arbeitsverhältnis fristlos auflösen."

🔗 [Fedlex SR 220](https://www.fedlex.admin.ch/eli/cc/27/317_321_377/de#art_337)

## 2. Rechtsprechungsanalyse

Das Bundesgericht hat in mehreren Entscheiden die Voraussetzungen präzisiert:

« Le licenciement immédiat est justifié en cas de faute grave. »

> Original (DE): "Die fristlose Kündigung ist bei schwerem Verschulden gerechtfertigt."

— [BGE 140 III 348, E. 4.2](https://www.bger.ch/ext/eurospider/live/de/php/clir/http/index.php?highlight_docid=atf://140-III-348:de)

## 3. Synthese

Die Rechtslage zeigt ein gemischtes Bild (🟡 MIXED).

## 4. Risikobeurteilung

- Beweislast liegt beim Arbeitgeber
- Reaktionszeit ist kritisch

## 5. Praktische Hinweise

- Sofortige schriftliche Begründung erforderlich
- Frist: unverzüglich nach Kenntnisnahme

## 6. Einschränkungen

⚠️ Diese Analyse ersetzt keine Rechtsberatung. Konsultieren Sie einen Anwalt für Ihren spezifischen Fall.

---
*Generiert von KERBERUS (Mock-Modus)*"""

        return LLMResponse(
            content=mock_content,
            model="mock",
            provider="mock",
            input_tokens=500,
            output_tokens=400,
            total_tokens=900,
            latency_ms=500,
            cost_chf=0.0,
        )


# Singleton instances
_mistral_client: Optional[MistralClient] = None
_infomaniak_client: Optional[InfomaniakClient] = None


def get_mistral_client(**kwargs) -> MistralClient:
    """Get shared Mistral client instance."""
    global _mistral_client
    if _mistral_client is None:
        _mistral_client = MistralClient(**kwargs)
    return _mistral_client


def get_infomaniak_client(**kwargs) -> InfomaniakClient:
    """Get shared Infomaniak client instance."""
    global _infomaniak_client
    if _infomaniak_client is None:
        _infomaniak_client = InfomaniakClient(**kwargs)
    return _infomaniak_client


# Alias for backwards compatibility
def get_qwen_client(**kwargs) -> InfomaniakClient:
    """Get shared Infomaniak client instance (alias for backwards compatibility)."""
    return get_infomaniak_client(**kwargs)


QwenClient = InfomaniakClient  # Alias for backwards compatibility
