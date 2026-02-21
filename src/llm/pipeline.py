"""
KERBERUS Legal Analysis Pipeline.

Three-stage LLM pipeline using Infomaniak AI:
1. Guard & Enhance (cheap model - security + query improvement)
2. Search + Rerank + MMR
3. Query Reformulator (cheap model - structure for analysis)
4. Full Legal Analysis (premium model - with citations)
"""
import os
import json
import logging
import re
from typing import Dict, List, Optional, Tuple, Generator
from dataclasses import dataclass

from .client import InfomaniakClient, LLMResponse, get_infomaniak_client
from .prompts import GuardEnhancePrompts, ReformulatorPrompts, LegalAnalysisPrompts, WebSearchLegalPrompts
from .context import ContextAssembler, _normalize_decision_id

logger = logging.getLogger(__name__)

# Models from environment
GUARD_MODEL = os.getenv("INFOMANIAK_GUARD_MODEL", "mistral-small-3.2-24b-instruct-2506")
ANALYSIS_MODEL = os.getenv("INFOMANIAK_ANALYSIS_MODEL", "qwen3-235b-a22b-instruct")


@dataclass
class GuardResult:
    """Result from Mistral 1: Guard & Enhance."""
    status: str  # "OK" or "BLOCKED"
    block_reason: Optional[str]
    detected_language: str
    original_query: str
    enhanced_query: str
    legal_concepts: List[str]
    is_followup: bool  # True if this is a follow-up to previous answer
    followup_type: Optional[str]  # "draft_request", "clarification", "elaboration", or None
    # New task detection fields
    tasks: List[str]  # e.g., ["legal_analysis", "drafting", "contract_review"]
    primary_task: str  # Main task for prioritization
    search_needed: bool  # Whether RAG search is needed
    target_language: Optional[str]  # For translation requests
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
    1. Guard & Enhance (cheap model)
    2. Search (external - called by Chainlit)
    3. Reformulate (cheap model)
    4. Analyze (premium model)
    """

    def __init__(self):
        self.client = get_infomaniak_client()
        self.guard_model = GUARD_MODEL
        self.analysis_model = ANALYSIS_MODEL
        self.context_assembler = ContextAssembler()

    def guard_and_enhance(self, query: str, chat_history: List[Dict] = None) -> GuardResult:
        """
        Stage 1: Guard & Enhance (cheap model)

        - Check for prompt injections
        - Detect language
        - Detect follow-up questions
        - Enhance vague queries
        """
        # Format chat context from history (last exchange only)
        chat_context = ""
        if chat_history and len(chat_history) >= 2:
            # Get last user message and assistant response
            last_messages = chat_history[-2:]
            for msg in last_messages:
                role = msg.get("role", "")
                content = msg.get("content", "")[:500]  # Truncate for context
                if role == "user":
                    chat_context += f"User: {content}\n"
                elif role == "assistant":
                    chat_context += f"Assistant: {content}...\n"

        # Choose template based on history
        if chat_context:
            user_content = GuardEnhancePrompts.USER_TEMPLATE.format(
                query=query,
                chat_context=chat_context
            )
        else:
            user_content = GuardEnhancePrompts.USER_TEMPLATE_NO_HISTORY.format(query=query)

        messages = [
            {"role": "system", "content": GuardEnhancePrompts.SYSTEM},
            {"role": "user", "content": user_content},
        ]

        response = self.client.chat(messages, max_tokens=1024, temperature=0.1, model=self.guard_model)

        # Parse JSON response
        try:
            # Extract JSON from response (handle markdown code blocks)
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            data = json.loads(content.strip())

            # Parse tasks (default to legal_analysis if not present)
            tasks = data.get("tasks", ["legal_analysis"])
            if not tasks:
                tasks = ["legal_analysis"]

            return GuardResult(
                status=data.get("status", "OK"),
                block_reason=data.get("block_reason"),
                detected_language=data.get("detected_language", "de"),
                original_query=data.get("original_query", query),
                enhanced_query=data.get("enhanced_query", query),
                legal_concepts=data.get("legal_concepts", []),
                is_followup=data.get("is_followup", False),
                followup_type=data.get("followup_type"),
                tasks=tasks,
                primary_task=data.get("primary_task", tasks[0] if tasks else "legal_analysis"),
                search_needed=data.get("search_needed", True),
                target_language=data.get("target_language"),
                response=response,
            )

        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse guard response: {e}")
            # Fallback: pass through with defaults
            return GuardResult(
                status="OK",
                block_reason=None,
                detected_language="de",
                original_query=query,
                enhanced_query=query,
                legal_concepts=[],
                is_followup=False,
                followup_type=None,
                tasks=["legal_analysis"],
                primary_task="legal_analysis",
                search_needed=True,
                target_language=None,
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
        tasks: List[str] = None,
        primary_task: str = None,
    ) -> Tuple[str, LLMResponse]:
        """
        Stage 3: Reformulate Query (Mistral 2)

        - Reiterate user intent clearly
        - Pass detected TASKS to Qwen
        - Structure for Qwen
        - NO interpretation
        """
        language_names = {
            "de": "German",
            "fr": "French",
            "it": "Italian",
            "en": "English",
        }

        # Default tasks if not provided
        if tasks is None:
            tasks = ["legal_analysis"]
        if primary_task is None:
            primary_task = tasks[0] if tasks else "legal_analysis"

        # Format tasks for display
        tasks_str = ", ".join(tasks).upper().replace("_", " ")

        messages = [
            {"role": "system", "content": ReformulatorPrompts.SYSTEM},
            {"role": "user", "content": ReformulatorPrompts.USER_TEMPLATE.format(
                query=original_query,
                enhanced_query=enhanced_query,
                tasks=tasks_str,
                primary_task=primary_task.upper().replace("_", " "),
                language=language,
                language_name=language_names.get(language, "German"),
                law_count=law_count,
                decision_count=decision_count,
                topics=", ".join(topics) if topics else "general legal question",
            )},
        ]

        response = self.client.chat(messages, max_tokens=512, temperature=0.2, model=self.guard_model)
        return response.content, response

    def analyze(
        self,
        reformulated_query: str,
        laws_context: str,
        decisions_context: str,
        language: str,
        web_search: bool = False,
    ) -> Generator[str, None, LLMResponse]:
        """
        Stage 4: Legal Analysis (premium model)

        Streams the response for real-time display.

        Args:
            reformulated_query: Structured query from reformulator
            laws_context: Formatted laws from RAG
            decisions_context: Formatted decisions from RAG
            language: Response language
            web_search: If True, use web search enhanced prompts and API
        """
        # Select appropriate prompt based on web search setting
        if web_search:
            system_prompt = WebSearchLegalPrompts.get_system_prompt(language)
            user_content = WebSearchLegalPrompts.USER_TEMPLATE.format(
                reformulated_query=reformulated_query,
                laws_context=laws_context,
                decisions_context=decisions_context,
                language=language,
            )
            logger.info("Using web search enhanced analysis")
        else:
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

        # Use web search API if enabled (placeholder - falls back to standard)
        if web_search:
            return self.client.chat_stream_with_web_search(
                messages, max_tokens=4096, temperature=0.4, model=self.analysis_model
            )
        else:
            return self.client.chat_stream(
                messages, max_tokens=4096, temperature=0.4, model=self.analysis_model
            )

    def analyze_sync(
        self,
        reformulated_query: str,
        laws_context: str,
        decisions_context: str,
        language: str,
        web_search: bool = False,
    ) -> Tuple[str, LLMResponse]:
        """Non-streaming version of analyze.

        Args:
            reformulated_query: Structured query from Mistral 2
            laws_context: Formatted laws from RAG
            decisions_context: Formatted decisions from RAG
            language: Response language
            web_search: If True, use Qwen with web search capability
        """
        # Select appropriate prompt based on web search setting
        if web_search:
            system_prompt = WebSearchLegalPrompts.get_system_prompt(language)
            user_content = WebSearchLegalPrompts.USER_TEMPLATE.format(
                reformulated_query=reformulated_query,
                laws_context=laws_context,
                decisions_context=decisions_context,
                language=language,
            )
            logger.info("Using web search enhanced analysis (sync)")
        else:
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

        # Use web search API if enabled (placeholder - falls back to standard)
        if web_search:
            response = self.client.chat_with_web_search(
                messages, max_tokens=4096, temperature=0.4, model=self.analysis_model
            )
        else:
            response = self.client.chat(messages, max_tokens=4096, temperature=0.4, model=self.analysis_model)
        return response.content, response

    def build_context(
        self,
        codex_results: List[Dict],
        library_results: List[Dict],
        max_laws: int = 25,
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

        # Use the full texts for decisions - limit to 10 unique decisions
        decision_parts = []
        seen_ids = set()
        MAX_UNIQUE_DECISIONS = 10

        for result in library_results:
            # Stop once we have 10 unique decisions
            if len(seen_ids) >= MAX_UNIQUE_DECISIONS:
                break

            payload = result.get("payload", {})
            decision_id = payload.get("decision_id", "") or payload.get("_original_id", "")

            # Normalize for consistent deduplication (handles case differences like BGE 102 IA vs Ia)
            base_id = _normalize_decision_id(str(decision_id))

            if base_id in seen_ids:
                continue
            seen_ids.add(base_id)

            year = payload.get("year", "")
            court = payload.get("court", "")
            lang = payload.get("language", "de")

            # Get full text if available (try normalized key first, then original)
            text = full_texts.get(base_id) or full_texts.get(decision_id, "")
            if not text:
                text = payload.get("text_preview", "")
                logger.warning(f"No full text for {base_id}, using chunk preview")

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
