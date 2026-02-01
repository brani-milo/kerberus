"""
KERBERUS Legal Analysis Pipeline.

Three-stage LLM pipeline:
1. Mistral 1: Guard & Enhance (security + query improvement)
2. Search + Rerank + MMR
3. Mistral 2: Query Reformulator (structure for Qwen)
4. Qwen: Full Legal Analysis (with citations)
"""
import json
import logging
import re
from typing import Dict, List, Optional, Tuple, Generator
from dataclasses import dataclass

from .client import MistralClient, QwenClient, LLMResponse, get_mistral_client, get_qwen_client
from .prompts import GuardEnhancePrompts, ReformulatorPrompts, LegalAnalysisPrompts
from .context import ContextAssembler

logger = logging.getLogger(__name__)


@dataclass
class GuardResult:
    """Result from Mistral 1: Guard & Enhance."""
    status: str  # "OK" or "BLOCKED"
    block_reason: Optional[str]
    detected_language: str
    original_query: str
    enhanced_query: str
    legal_concepts: List[str]
    query_type: str
    response: LLMResponse


@dataclass
class PipelineResult:
    """Complete pipeline result."""
    # Stage results
    guard_result: GuardResult
    reformulated_query: str
    final_answer: str

    # Metadata
    consistency: str  # CONSISTENT, MIXED, DIVERGENT
    confidence: str  # high, medium, low
    detected_language: str

    # Sources
    laws_used: int
    decisions_used: int

    # Costs
    total_tokens: int
    total_cost_chf: float
    stage_costs: Dict[str, float]


class LegalPipeline:
    """
    Orchestrates the complete legal analysis pipeline.

    Flow:
    1. Guard & Enhance (Mistral 1)
    2. Search (external - called by Chainlit)
    3. Reformulate (Mistral 2)
    4. Analyze (Qwen)
    """

    def __init__(self):
        self.mistral = get_mistral_client()
        self.qwen = get_qwen_client()
        self.context_assembler = ContextAssembler()

    def guard_and_enhance(self, query: str) -> GuardResult:
        """
        Stage 1: Guard & Enhance (Mistral 1)

        - Check for prompt injections
        - Detect language
        - Enhance vague queries
        """
        messages = [
            {"role": "system", "content": GuardEnhancePrompts.SYSTEM},
            {"role": "user", "content": GuardEnhancePrompts.USER_TEMPLATE.format(query=query)},
        ]

        response = self.mistral.chat(messages, max_tokens=1024, temperature=0.1)

        # Parse JSON response
        try:
            # Extract JSON from response (handle markdown code blocks)
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            data = json.loads(content.strip())

            return GuardResult(
                status=data.get("status", "OK"),
                block_reason=data.get("block_reason"),
                detected_language=data.get("detected_language", "de"),
                original_query=data.get("original_query", query),
                enhanced_query=data.get("enhanced_query", query),
                legal_concepts=data.get("legal_concepts", []),
                query_type=data.get("query_type", "unclear"),
                response=response,
            )

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse guard response: {e}")
            # Fallback: pass through
            return GuardResult(
                status="OK",
                block_reason=None,
                detected_language="de",
                original_query=query,
                enhanced_query=query,
                legal_concepts=[],
                query_type="unclear",
                response=response,
            )

    def reformulate(
        self,
        original_query: str,
        enhanced_query: str,
        language: str,
        law_count: int,
        decision_count: int,
        topics: List[str],
    ) -> Tuple[str, LLMResponse]:
        """
        Stage 3: Reformulate Query (Mistral 2)

        - Reiterate user intent clearly
        - Structure for Qwen
        - NO interpretation
        """
        language_names = {
            "de": "German",
            "fr": "French",
            "it": "Italian",
            "en": "English",
        }

        messages = [
            {"role": "system", "content": ReformulatorPrompts.SYSTEM},
            {"role": "user", "content": ReformulatorPrompts.USER_TEMPLATE.format(
                query=original_query,
                enhanced_query=enhanced_query,
                language=language,
                language_name=language_names.get(language, "German"),
                law_count=law_count,
                decision_count=decision_count,
                topics=", ".join(topics) if topics else "general legal question",
            )},
        ]

        response = self.mistral.chat(messages, max_tokens=512, temperature=0.2)
        return response.content, response

    def analyze(
        self,
        reformulated_query: str,
        laws_context: str,
        decisions_context: str,
        language: str,
    ) -> Generator[str, None, LLMResponse]:
        """
        Stage 4: Legal Analysis (Qwen)

        Streams the response for real-time display.
        """
        system_prompt = LegalAnalysisPrompts.get_system_prompt(language)

        user_content = LegalAnalysisPrompts.USER_TEMPLATE.format(
            reformulated_query=reformulated_query,
            laws_context=laws_context,
            decisions_context=decisions_context,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        return self.qwen.chat_stream(messages, max_tokens=8192, temperature=0.4)

    def analyze_sync(
        self,
        reformulated_query: str,
        laws_context: str,
        decisions_context: str,
        language: str,
    ) -> Tuple[str, LLMResponse]:
        """Non-streaming version of analyze."""
        system_prompt = LegalAnalysisPrompts.get_system_prompt(language)

        user_content = LegalAnalysisPrompts.USER_TEMPLATE.format(
            reformulated_query=reformulated_query,
            laws_context=laws_context,
            decisions_context=decisions_context,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        response = self.qwen.chat(messages, max_tokens=8192, temperature=0.4)
        return response.content, response

    def build_context(
        self,
        codex_results: List[Dict],
        library_results: List[Dict],
        max_laws: int = 10,
        max_decisions: int = 10,
    ) -> Tuple[str, str, Dict]:
        """
        Build context strings for Qwen from search results.

        Fetches full documents (not just chunks).
        """
        # Limit results
        codex_results = codex_results[:max_laws]
        library_results = library_results[:max_decisions]

        # Build laws context
        laws_parts = []
        for result in codex_results:
            payload = result.get("payload", {})
            sr_number = payload.get("sr_number", "")
            abbrev = payload.get("abbreviation", "")
            art_num = payload.get("article_number", "")
            art_title = payload.get("article_title", "")
            text = payload.get("article_text", payload.get("text_preview", ""))
            lang = payload.get("language", "de")

            header = f"### {abbrev} Art. {art_num}"
            if art_title:
                header += f" - {art_title}"
            header += f" (SR {sr_number}, {lang.upper()})"

            laws_parts.append(f"{header}\n\n{text}")

        laws_context = "\n\n---\n\n".join(laws_parts) if laws_parts else "Keine relevanten Gesetze gefunden."

        # Build decisions context (fetch full documents)
        decisions_context, full_texts = self.context_assembler.assemble(
            codex_results=[],  # Already processed
            library_results=library_results,
            fetch_full_documents=True,
        )

        # Use the full texts for decisions
        decision_parts = []
        seen_ids = set()

        for result in library_results:
            payload = result.get("payload", {})
            decision_id = payload.get("decision_id", "")
            base_id = decision_id.split("_chunk_")[0] if "_chunk_" in str(decision_id) else decision_id

            if base_id in seen_ids:
                continue
            seen_ids.add(base_id)

            year = payload.get("year", "")
            court = payload.get("court", "")
            lang = payload.get("language", "de")

            # Get full text if available
            if base_id in full_texts:
                text = full_texts[base_id]
            else:
                text = payload.get("text_preview", "")

            # Build header
            if "BGE" in str(base_id):
                citation = f"BGE {base_id.replace('BGE-', '').replace('-', ' ')}"
            else:
                citation = base_id

            header = f"### {citation}"
            if year:
                header += f" ({year})"
            if court:
                header += f" - {court}"
            header += f" [{lang.upper()}]"

            decision_parts.append(f"{header}\n\n{text}")

        decisions_context = "\n\n---\n\n".join(decision_parts) if decision_parts else "Keine relevanten Entscheide gefunden."

        metadata = {
            "laws_count": len(codex_results),
            "decisions_count": len(seen_ids),
        }

        return laws_context, decisions_context, metadata

    @staticmethod
    def parse_consistency(response_text: str) -> Tuple[str, str]:
        """Extract consistency and confidence from Qwen response."""
        try:
            # Look for JSON block
            if "```json" in response_text:
                json_str = response_text.split("```json")[1].split("```")[0]
                data = json.loads(json_str.strip())
                return data.get("consistency", "MIXED"), data.get("confidence", "medium")
            elif "```" in response_text:
                # Try first code block
                json_str = response_text.split("```")[1].split("```")[0]
                if json_str.strip().startswith("{"):
                    data = json.loads(json_str.strip())
                    return data.get("consistency", "MIXED"), data.get("confidence", "medium")
        except (json.JSONDecodeError, IndexError):
            pass

        # Fallback: look for keywords
        text_lower = response_text.lower()
        if "consistent" in text_lower or "einheitlich" in text_lower:
            consistency = "CONSISTENT"
        elif "divergent" in text_lower or "widersprüchlich" in text_lower:
            consistency = "DIVERGENT"
        else:
            consistency = "MIXED"

        return consistency, "medium"


# Singleton
_pipeline: Optional[LegalPipeline] = None


def get_pipeline() -> LegalPipeline:
    """Get shared pipeline instance."""
    global _pipeline
    if _pipeline is None:
        _pipeline = LegalPipeline()
    return _pipeline
