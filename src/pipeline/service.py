"""
LegalQueryService: the ONE implementation of the query pipeline.

    PII scrub -> Guard & Enhance -> TriadSearch -> Reformulate -> Context -> Analyze

Both the REST API (src/api/routes/chat.py) and the Chainlit UI (frontend/app.py)
consume the async event stream produced by `run()`, so retrieval or prompt
changes are made exactly once. Every blocking call (LLM HTTP requests, reranker,
Qdrant) runs off the event loop.
"""
import asyncio
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from ..llm import get_pipeline
from ..llm.client import LLMResponse
from ..llm.context import _normalize_decision_id
from ..search.triad_search import TriadSearch
from ..security import get_pii_scrubber

logger = logging.getLogger(__name__)

# Keys in event data that are large in-process objects (never sent over SSE)
_HEAVY_KEYS = {"codex_results", "library_results", "answer_obj"}

_CONSISTENCY_BLOCK = re.compile(r"```json\s*(\{[^}]+\})\s*```")


@dataclass
class PipelineOptions:
    language: Optional[str] = "auto"     # forced response language, or "auto" (guard decides)
    search_scope: str = "both"           # both | laws | decisions
    max_laws: int = 25
    max_decisions: int = 10
    web_search: bool = False
    filters: Optional[Dict] = None       # Qdrant filters: {"language": "de", "year_range": {...}}
    top_k: int = 50
    stream: bool = True                  # stream analysis tokens as they arrive
    use_followup_context: bool = True    # reuse previous results for follow-up questions
    pii_language: str = "de"


@dataclass
class PipelineEvent:
    stage: str    # pii | guard | search | reformulate | context | analyze | complete | error
    status: str   # processing | complete | skipped | chunk | blocked | failed | no_sources
    data: Dict[str, Any] = field(default_factory=dict)

    def public(self) -> Dict[str, Any]:
        """JSON-safe view (drops heavy in-process objects) for SSE / logs."""
        return {"stage": self.stage, "status": self.status,
                **{k: v for k, v in self.data.items() if k not in _HEAVY_KEYS}}


@dataclass
class LegalAnswer:
    answer: str                        # analysis text without the trailing consistency JSON block
    raw_answer: str
    consistency: str
    confidence: str
    language: str
    sources: List[Dict[str, Any]]
    codex_results: List[Dict]
    library_results: List[Dict]
    codex_confidence: str
    library_confidence: str
    overall_confidence: str
    token_usage: Dict[str, Any]
    processing_time_ms: float
    reformulated_query: str
    enhanced_query: str
    pii_detected: bool
    pii_types: List[str]
    followup_used: bool

    def context_snapshot(self, original_query: str) -> Dict[str, Any]:
        """What a caller should keep to answer follow-up questions without re-searching."""
        return {
            "codex_results": self.codex_results,
            "library_results": self.library_results,
            "codex_conf": self.codex_confidence,
            "library_conf": self.library_confidence,
            "original_query": original_query,
            "detected_language": self.language,
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def iter_sync_generator(gen) -> AsyncIterator[Tuple[str, Any]]:
    """
    Drive a synchronous generator on a worker thread.

    Yields ("chunk", value) per item and finally ("done", return_value), so a
    generator's return value (the LLMResponse with token usage) is preserved.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def run() -> None:
        try:
            while True:
                try:
                    chunk = next(gen)
                except StopIteration as stop:
                    loop.call_soon_threadsafe(queue.put_nowait, ("done", stop.value))
                    return
                loop.call_soon_threadsafe(queue.put_nowait, ("chunk", chunk))
        except BaseException as exc:
            loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))

    threading.Thread(target=run, name="llm-stream", daemon=True).start()
    while True:
        kind, value = await queue.get()
        if kind == "error":
            raise value
        yield kind, value
        if kind == "done":
            return


def sum_usage(*responses: Optional[LLMResponse]) -> Tuple[int, int, float]:
    """(input_tokens, output_tokens, cost_chf) over the pipeline stages."""
    input_tokens = output_tokens = 0
    cost = 0.0
    for r in responses:
        if r is None:
            continue
        input_tokens += int(getattr(r, "input_tokens", 0) or 0)
        output_tokens += int(getattr(r, "output_tokens", 0) or 0)
        cost += float(getattr(r, "cost_chf", 0.0) or 0.0)
    return input_tokens, output_tokens, cost


def strip_consistency_block(text: str) -> str:
    """Remove the trailing ```json {consistency...}``` block the analysis model appends."""
    return _CONSISTENCY_BLOCK.sub("", text or "").strip()


def build_sources(codex_results: List[Dict], library_results: List[Dict]) -> List[Dict[str, Any]]:
    """Citation references (plain dicts; the API wraps them in SourceReference)."""
    sources: List[Dict[str, Any]] = []
    for r in codex_results:
        p = r.get("payload", {})
        abbrev, art, sr = p.get("abbreviation", ""), p.get("article_number", ""), p.get("sr_number", "")
        lang = p.get("language", "de")
        sources.append({
            "id": str(r.get("id", "")), "type": "law",
            "citation": f"{abbrev} Art. {art}" if abbrev else f"SR {sr}",
            "language": lang,
            "url": f"https://www.fedlex.admin.ch/eli/cc/{sr}/{lang}" if sr else None,
            "relevance_score": float(r.get("final_score", r.get("score", 0.0)) or 0.0),
        })
    seen = set()
    for r in library_results:
        p = r.get("payload", {})
        base = _normalize_decision_id(str(p.get("decision_id", "") or p.get("_original_id", "")))
        if base in seen:
            continue
        seen.add(base)
        citation = f"BGE {base.replace('BGE-', '').replace('-', ' ')}" if "BGE" in base else base
        sources.append({
            "id": str(r.get("id", "")), "type": "decision", "citation": citation,
            "language": p.get("language", "de"), "url": None,
            "relevance_score": float(r.get("final_score", r.get("score", 0.0)) or 0.0),
        })
    return sources


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------

class LegalQueryService:
    """Runs the pipeline and yields PipelineEvents. Instances are shareable."""

    def __init__(self, pipeline=None, triad: Optional[TriadSearch] = None, pii_scrubber=None):
        self._pipeline = pipeline
        self._triad = triad
        self._pii = pii_scrubber
        self._lock = threading.Lock()

    # lazy, thread-safe component access (models load on first use)
    @property
    def pipeline(self):
        if self._pipeline is None:
            self._pipeline = get_pipeline()
        return self._pipeline

    def triad(self) -> TriadSearch:
        if self._triad is None:
            with self._lock:
                if self._triad is None:
                    self._triad = TriadSearch()
        return self._triad

    @property
    def pii(self):
        if self._pii is None:
            self._pii = get_pii_scrubber()
        return self._pii

    def warm_up(self) -> None:
        """Load models eagerly (call from a startup hook, off the event loop)."""
        self.triad()
        self.pipeline

    # ------------------------------------------------------------------

    def _scrub(self, query: str, language: str) -> Tuple[str, bool, List[str]]:
        if not getattr(self.pii, "enabled", False):
            return query, False, []
        entities = self.pii.detect(query, language=language)
        if not entities:
            return query, False, []
        types = sorted({e.entity_type for e in entities})
        return self.pii.scrub(query, language=language), True, types

    async def run(
        self,
        query: str,
        *,
        display_query: Optional[str] = None,
        chat_history: Optional[List[Dict]] = None,
        options: Optional[PipelineOptions] = None,
        previous_context: Optional[Dict] = None,
    ) -> AsyncIterator[PipelineEvent]:
        """
        Execute the pipeline.

        Args:
            query: full text sent to the models (may include uploaded documents)
            display_query: what the user typed (used for reformulation / snapshots)
            chat_history: prior turns [{"role","content"}] for follow-up detection
            options: PipelineOptions
            previous_context: snapshot from LegalAnswer.context_snapshot() of the last turn
        """
        opts = options or PipelineOptions()
        display_query = display_query or query
        start = time.time()
        pipeline = self.pipeline

        # --- PII -------------------------------------------------------------
        query, pii_detected, pii_types = self._scrub(query, opts.pii_language)
        if pii_detected:
            logger.info(f"PII scrubbed from query: {pii_types}")
            yield PipelineEvent("pii", "scrubbed", {"types": pii_types})

        # --- Stage 1: Guard & Enhance ------------------------------------------
        yield PipelineEvent("guard", "processing")
        try:
            guard = await asyncio.to_thread(pipeline.guard_and_enhance, query, chat_history or None)
            guard_failed = False
        except Exception as e:
            logger.warning(f"Guard stage failed, continuing with defaults: {e}")
            guard = None
            guard_failed = True

        if guard is not None and guard.status == "BLOCKED":
            yield PipelineEvent("guard", "blocked", {"reason": guard.block_reason})
            yield PipelineEvent("error", "blocked", {"message": f"Query blocked: {guard.block_reason}"})
            return

        language = guard.detected_language if guard else "de"
        if opts.language and opts.language != "auto":
            language = opts.language
        enhanced_query = guard.enhanced_query if guard else query
        legal_concepts = (guard.legal_concepts if guard else []) or ["general legal question"]
        tasks = guard.tasks if guard else ["legal_analysis"]
        primary_task = guard.primary_task if guard else "legal_analysis"
        is_followup = bool(guard and guard.is_followup)
        followup_type = guard.followup_type if guard else None

        yield PipelineEvent("guard", "complete", {
            "language": language, "is_followup": is_followup, "followup_type": followup_type,
            "fallback": guard_failed, "enhanced_query": enhanced_query,
        })

        # --- Stage 2: Search (or reuse the previous context for follow-ups) ---
        followup_used = False
        if is_followup and previous_context and opts.use_followup_context:
            followup_used = True
            codex_results = list(previous_context.get("codex_results", []))
            library_results = list(previous_context.get("library_results", []))
            codex_conf = previous_context.get("codex_conf", "NONE")
            library_conf = previous_context.get("library_conf", "NONE")
            overall_conf = min((codex_conf, library_conf), key=lambda c: ["NONE", "LOW", "MEDIUM", "HIGH"].index(c) if c in ("NONE", "LOW", "MEDIUM", "HIGH") else 0)
            enhanced_query = (
                f"[FOLLOW-UP REQUEST: {followup_type}]\n"
                f"Original analysis topic: {previous_context.get('original_query', '')}\n"
                f"User's follow-up: {display_query}"
            )
            yield PipelineEvent("search", "skipped", {
                "reason": "follow-up", "laws": len(codex_results), "decisions": len(library_results),
                "codex_confidence": codex_conf, "library_confidence": library_conf,
                "codex_results": codex_results, "library_results": library_results,
            })
        else:
            yield PipelineEvent("search", "processing", {"method": "hybrid+mmr+rerank"})
            try:
                triad = await asyncio.to_thread(self.triad)
                results = await triad.search(
                    query=enhanced_query, user_id=None, firm_id=None,
                    filters=opts.filters or None, top_k=opts.top_k,
                )
            except Exception as e:
                logger.error(f"TriadSearch failed: {e}", exc_info=True)
                yield PipelineEvent("error", "failed", {"stage": "search", "message": "Search failed", "detail": str(e)})
                return

            codex = results.get("codex", {}) if opts.search_scope in ("both", "laws") else {}
            library = results.get("library", {}) if opts.search_scope in ("both", "decisions") else {}
            codex_results = codex.get("results", [])
            library_results = library.get("results", [])
            codex_conf = codex.get("confidence", "NONE")
            library_conf = library.get("confidence", "NONE")
            overall_conf = results.get("overall_confidence", "NONE")

            yield PipelineEvent("search", "complete", {
                "laws": len(codex_results), "decisions": len(library_results),
                "codex_confidence": codex_conf, "library_confidence": library_conf,
                "overall_confidence": overall_conf,
                "codex_results": codex_results, "library_results": library_results,
            })

            if not codex_results and not library_results:
                yield PipelineEvent("error", "no_sources", {"message": "No relevant legal sources found for this query"})
                return

        # --- Stage 3: Reformulate --------------------------------------------
        yield PipelineEvent("reformulate", "processing")
        try:
            reformulated_query, reformulate_response = await asyncio.to_thread(
                pipeline.reformulate,
                original_query=display_query, enhanced_query=enhanced_query, language=language,
                law_count=len(codex_results), decision_count=len(library_results),
                topics=legal_concepts, tasks=tasks, primary_task=primary_task,
            )
        except Exception as e:
            logger.warning(f"Reformulate failed, using enhanced query: {e}")
            reformulated_query, reformulate_response = enhanced_query, None
        yield PipelineEvent("reformulate", "complete")

        # --- Stage 4: Context ------------------------------------------------
        yield PipelineEvent("context", "processing")
        try:
            laws_context, decisions_context, context_meta = await asyncio.to_thread(
                pipeline.build_context,
                codex_results=codex_results, library_results=library_results,
                max_laws=opts.max_laws, max_decisions=opts.max_decisions,
            )
        except Exception as e:
            logger.error(f"Context building failed: {e}", exc_info=True)
            yield PipelineEvent("error", "failed", {"stage": "context", "message": "Failed to build legal context", "detail": str(e)})
            return
        yield PipelineEvent("context", "complete", dict(context_meta or {}))

        # --- Stage 5: Analyze --------------------------------------------------
        yield PipelineEvent("analyze", "processing", {"streaming": opts.stream, "web_search": opts.web_search})
        chunks: List[str] = []
        analysis_response: Optional[LLMResponse] = None
        try:
            if opts.stream:
                gen = pipeline.analyze(
                    reformulated_query=reformulated_query, laws_context=laws_context,
                    decisions_context=decisions_context, language=language, web_search=opts.web_search,
                )
                async for kind, value in iter_sync_generator(gen):
                    if kind == "chunk":
                        chunks.append(value)
                        yield PipelineEvent("analyze", "chunk", {"chunk": value})
                    else:
                        analysis_response = value
            else:
                text, analysis_response = await asyncio.to_thread(
                    pipeline.analyze_sync,
                    reformulated_query=reformulated_query, laws_context=laws_context,
                    decisions_context=decisions_context, language=language, web_search=opts.web_search,
                )
                chunks.append(text)
        except Exception as e:
            logger.error(f"Analysis failed: {e}", exc_info=True)
            yield PipelineEvent("error", "failed", {"stage": "analyze", "message": "Legal analysis failed", "detail": str(e)})
            return

        raw_answer = "".join(chunks)
        consistency, confidence = pipeline.parse_consistency(raw_answer)
        input_tokens, output_tokens, cost = sum_usage(
            guard.response if guard else None, reformulate_response, analysis_response
        )

        answer = LegalAnswer(
            answer=strip_consistency_block(raw_answer),
            raw_answer=raw_answer,
            consistency=consistency,
            confidence=confidence,
            language=language,
            sources=build_sources(codex_results, library_results),
            codex_results=codex_results,
            library_results=library_results,
            codex_confidence=codex_conf,
            library_confidence=library_conf,
            overall_confidence=overall_conf,
            token_usage={
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens, "total_cost_chf": cost,
                "stages": {
                    "guard": guard.response.total_tokens if guard and guard.response else 0,
                    "reformulate": reformulate_response.total_tokens if reformulate_response else 0,
                    "analyze": analysis_response.total_tokens if analysis_response else 0,
                },
            },
            processing_time_ms=(time.time() - start) * 1000,
            reformulated_query=reformulated_query,
            enhanced_query=enhanced_query,
            pii_detected=pii_detected,
            pii_types=pii_types,
            followup_used=followup_used,
        )
        yield PipelineEvent("complete", "complete", {
            "consistency": consistency, "confidence": confidence, "language": language,
            "sources": answer.sources, "token_usage": answer.token_usage,
            "processing_time_ms": round(answer.processing_time_ms, 1),
            "followup_used": followup_used, "answer_obj": answer,
        })


# ---------------------------------------------------------------------------
# singleton
# ---------------------------------------------------------------------------

_service: Optional[LegalQueryService] = None
_service_lock = threading.Lock()


def get_query_service() -> LegalQueryService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = LegalQueryService()
    return _service


def event_to_json(event: PipelineEvent) -> str:
    return json.dumps(event.public(), ensure_ascii=False, default=str)
