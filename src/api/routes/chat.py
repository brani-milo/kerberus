"""
Chat/Query Endpoints.

Provides the main legal analysis endpoint using the four-stage pipeline:
1. Guard & Enhance (Mistral)
2. TriadSearch (Hybrid + MMR + Rerank)
3. Reformulate (Mistral)
4. Analyze (Qwen)
"""
import time
import logging
from typing import Dict, Optional, AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse

from ..models import (
    ChatRequest,
    ChatResponse,
    SourceReference,
    ErrorResponse,
)
from ..deps import get_current_user, check_rate_limit, get_db
from ...llm import get_pipeline, ContextAssembler
from ...search.triad_search import TriadSearch
from ...database.auth_db import AuthDB
from ...security import get_pii_scrubber, PIIScrubber

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])

# Lazy-initialized components
_triad_search: Optional[TriadSearch] = None
_context_assembler: Optional[ContextAssembler] = None


def get_triad_search() -> TriadSearch:
    """Get TriadSearch engine (Hybrid + MMR + Rerank)."""
    global _triad_search
    if _triad_search is None:
        _triad_search = TriadSearch()
    return _triad_search


def get_context_assembler() -> ContextAssembler:
    """Get context assembler."""
    global _context_assembler
    if _context_assembler is None:
        _context_assembler = ContextAssembler()
    return _context_assembler


@router.post(
    "",
    response_model=ChatResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        503: {"model": ErrorResponse, "description": "Service unavailable"},
    },
)
async def chat(
    request: Request,
    chat_request: ChatRequest,
    user: Dict = Depends(check_rate_limit),
    db: AuthDB = Depends(get_db),
):
    """
    Submit a legal question and receive a comprehensive analysis.

    Uses the four-stage pipeline:
    1. Guard & Enhance (Mistral) - Security + query optimization
    2. TriadSearch - Hybrid + MMR + Rerank
    3. Reformulate (Mistral) - Structure for analysis
    4. Analyze (Qwen) - Full legal analysis with citations
    """
    start_time = time.time()

    # Get pipeline and components
    pipeline = get_pipeline()
    triad = get_triad_search()
    pii_scrubber = get_pii_scrubber()

    # ============================================
    # PII Detection & Scrubbing
    # ============================================
    original_query = chat_request.query
    pii_detected = False
    pii_types_found = []

    if pii_scrubber.enabled:
        # Detect PII in query
        pii_entities = pii_scrubber.detect(original_query, language="de")
        if pii_entities:
            pii_detected = True
            pii_types_found = list(set(e.entity_type for e in pii_entities))
            # Scrub query before sending to LLM
            chat_request.query = pii_scrubber.scrub(original_query, language="de")
            logger.info(f"PII scrubbed from query: {pii_types_found}")

    # ============================================
    # STAGE 1: Guard & Enhance
    # ============================================
    try:
        guard_result = pipeline.guard_and_enhance(chat_request.query)
    except Exception as e:
        logger.error(f"Guard stage failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Query processing failed",
        )

    # Check if blocked
    if guard_result.status == "BLOCKED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Query blocked: {guard_result.block_reason}",
        )

    detected_language = guard_result.detected_language
    enhanced_query = guard_result.enhanced_query

    # Override language if specified
    if chat_request.language and chat_request.language != "auto":
        detected_language = chat_request.language

    # ============================================
    # STAGE 2: TriadSearch (Hybrid + MMR + Rerank)
    # ============================================
    # Build filters
    filters = {}
    if detected_language and detected_language != "auto":
        filters["language"] = detected_language

    try:
        search_results = await triad.search(
            query=enhanced_query,
            user_id=None,
            firm_id=None,
            filters=filters if filters else None,
            top_k=chat_request.max_laws  # Use as general limit
        )
    except Exception as e:
        logger.error(f"TriadSearch failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Search failed",
        )

    # Extract results based on scope
    codex_results = []
    library_results = []

    if chat_request.search_scope in ["both", "laws"]:
        codex_data = search_results.get('codex', {})
        codex_results = codex_data.get('results', [])

    if chat_request.search_scope in ["both", "decisions"]:
        library_data = search_results.get('library', {})
        library_results = library_data.get('results', [])

    # Check if we found anything
    if not codex_results and not library_results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No relevant legal sources found for this query",
        )

    # ============================================
    # STAGE 3: Reformulate
    # ============================================
    topics = guard_result.legal_concepts if guard_result.legal_concepts else ["general legal question"]

    try:
        reformulated_query, reformulate_response = pipeline.reformulate(
            original_query=chat_request.query,
            enhanced_query=enhanced_query,
            language=detected_language,
            law_count=len(codex_results),
            decision_count=len(library_results),
            topics=topics,
            tasks=guard_result.tasks,
            primary_task=guard_result.primary_task,
        )
    except Exception as e:
        logger.error(f"Reformulate stage failed: {e}")
        reformulated_query = enhanced_query
        reformulate_response = None

    # ============================================
    # STAGE 4: Build Context & Analyze
    # ============================================
    # Convert reranked results to standard format
    codex_for_context = [
        {"id": r.get("id"), "score": r.get("final_score", r.get("score", 0)), "payload": r.get("payload", {})}
        for r in codex_results
    ]
    library_for_context = [
        {"id": r.get("id"), "score": r.get("final_score", r.get("score", 0)), "payload": r.get("payload", {})}
        for r in library_results
    ]

    try:
        laws_context, decisions_context, context_meta = pipeline.build_context(
            codex_results=codex_for_context,
            library_results=library_for_context,
            max_laws=chat_request.max_laws,
            max_decisions=chat_request.max_decisions,
        )
    except Exception as e:
        logger.error(f"Context building failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to build legal context",
        )

    # Generate analysis (non-streaming for REST API)
    try:
        answer, analysis_response = pipeline.analyze_sync(
            reformulated_query=reformulated_query,
            laws_context=laws_context,
            decisions_context=decisions_context,
            language=detected_language,
            web_search=chat_request.enable_web_search or False,
        )
    except Exception as e:
        logger.error(f"Analysis stage failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Legal analysis failed",
        )

    # Parse consistency from response
    consistency, confidence = pipeline.parse_consistency(answer)

    # Build source references
    sources = []

    for result in codex_results:
        payload = result.get("payload", {})
        abbrev = payload.get("abbreviation", "")
        art_num = payload.get("article_number", "")
        sr_num = payload.get("sr_number", "")

        sources.append(SourceReference(
            id=str(result.get("id", "")),
            type="law",
            citation=f"{abbrev} Art. {art_num}" if abbrev else f"SR {sr_num}",
            language=payload.get("language", "de"),
            url=f"https://www.fedlex.admin.ch/eli/cc/{sr_num}/de" if sr_num else None,
            relevance_score=result.get("final_score", result.get("score", 0.0)),
        ))

    for result in library_results:
        payload = result.get("payload", {})
        decision_id = payload.get("decision_id", "")
        base_id = decision_id.split("_chunk_")[0] if "_chunk_" in str(decision_id) else decision_id

        # Build citation
        if "BGE" in str(base_id):
            citation = f"BGE {base_id.replace('BGE-', '').replace('-', ' ')}"
        else:
            citation = base_id

        sources.append(SourceReference(
            id=str(result.get("id", "")),
            type="decision",
            citation=citation,
            language=payload.get("language", "de"),
            url=None,
            relevance_score=result.get("final_score", result.get("score", 0.0)),
        ))

    # Calculate token usage
    total_tokens = 0
    total_cost = 0.0

    if guard_result.response:
        total_tokens += guard_result.response.total_tokens
        total_cost += guard_result.response.cost_chf

    if reformulate_response:
        total_tokens += reformulate_response.total_tokens
        total_cost += reformulate_response.cost_chf

    if analysis_response:
        total_tokens += analysis_response.total_tokens
        total_cost += analysis_response.cost_chf

    # Record token usage
    try:
        db.record_token_usage(
            user_id=str(user["user_id"]),
            usage_record={
                "model": "pipeline",
                "input_tokens": total_tokens // 2,
                "output_tokens": total_tokens // 2,
                "cost_chf": total_cost,
                "operation": "chat",
            }
        )
    except Exception as e:
        logger.error(f"Failed to record token usage: {e}")

    processing_time = (time.time() - start_time) * 1000

    return ChatResponse(
        answer=answer,
        consistency=consistency,
        confidence=confidence,
        detected_language=detected_language,
        sources=sources,
        token_usage={
            "total_tokens": total_tokens,
            "total_cost_chf": total_cost,
            "search_confidence": {
                "codex": search_results.get('codex', {}).get('confidence', 'NONE'),
                "library": search_results.get('library', {}).get('confidence', 'NONE'),
                "overall": search_results.get('overall_confidence', 'NONE'),
            },
            "stages": {
                "guard": guard_result.response.total_tokens if guard_result.response else 0,
                "reformulate": reformulate_response.total_tokens if reformulate_response else 0,
                "analyze": analysis_response.total_tokens if analysis_response else 0,
            },
            "pii_scrubbed": pii_detected,
            "pii_types": pii_types_found if pii_detected else [],
        },
        processing_time_ms=processing_time,
    )


@router.post("/stream")
async def chat_stream(
    request: Request,
    chat_request: ChatRequest,
    user: Dict = Depends(check_rate_limit),
    db: AuthDB = Depends(get_db),
):
    """
    Submit a legal question and receive a streaming response.

    Returns Server-Sent Events (SSE) with progressive updates.
    """
    async def generate() -> AsyncGenerator[str, None]:
        start_time = time.time()

        pipeline = get_pipeline()
        triad = get_triad_search()
        pii_scrubber = get_pii_scrubber()

        # PII scrubbing
        query_to_process = chat_request.query
        pii_detected = False

        if pii_scrubber.enabled:
            pii_entities = pii_scrubber.detect(query_to_process, language="de")
            if pii_entities:
                pii_detected = True
                pii_types = [e.entity_type for e in pii_entities]
                query_to_process = pii_scrubber.scrub(query_to_process, language="de")
                yield f"data: {{'stage': 'pii', 'status': 'scrubbed', 'types': {pii_types}}}\n\n"

        # Stage 1: Guard & Enhance
        yield f"data: {{'stage': 'guard', 'status': 'processing'}}\n\n"

        try:
            guard_result = pipeline.guard_and_enhance(query_to_process)
        except Exception as e:
            yield f"data: {{'error': 'Guard stage failed: {str(e)}'}}\n\n"
            return

        if guard_result.status == "BLOCKED":
            yield f"data: {{'error': 'Query blocked: {guard_result.block_reason}'}}\n\n"
            return

        yield f"data: {{'stage': 'guard', 'status': 'complete', 'language': '{guard_result.detected_language}'}}\n\n"

        # Stage 2: TriadSearch
        yield f"data: {{'stage': 'search', 'status': 'processing', 'method': 'hybrid+mmr+rerank'}}\n\n"

        try:
            search_results = await triad.search(
                query=guard_result.enhanced_query,
                user_id=None,
                firm_id=None,
                filters=None,
                top_k=10
            )
        except Exception as e:
            yield f"data: {{'error': 'Search failed: {str(e)}'}}\n\n"
            return

        codex_results = search_results.get('codex', {}).get('results', [])
        library_results = search_results.get('library', {}).get('results', [])
        codex_conf = search_results.get('codex', {}).get('confidence', 'NONE')
        library_conf = search_results.get('library', {}).get('confidence', 'NONE')

        yield f"data: {{'stage': 'search', 'status': 'complete', 'laws': {len(codex_results)}, 'decisions': {len(library_results)}, 'codex_confidence': '{codex_conf}', 'library_confidence': '{library_conf}'}}\n\n"

        if not codex_results and not library_results:
            yield f"data: {{'error': 'No relevant sources found'}}\n\n"
            return

        # Stage 3: Reformulate
        yield f"data: {{'stage': 'reformulate', 'status': 'processing'}}\n\n"

        try:
            reformulated_query, _ = pipeline.reformulate(
                original_query=query_to_process,
                enhanced_query=guard_result.enhanced_query,
                language=guard_result.detected_language,
                law_count=len(codex_results),
                decision_count=len(library_results),
                topics=guard_result.legal_concepts or ["general"],
                tasks=guard_result.tasks,
                primary_task=guard_result.primary_task,
            )
        except Exception as e:
            reformulated_query = guard_result.enhanced_query

        yield f"data: {{'stage': 'reformulate', 'status': 'complete'}}\n\n"

        # Stage 4: Build context
        yield f"data: {{'stage': 'context', 'status': 'processing'}}\n\n"

        codex_for_context = [
            {"id": r.get("id"), "score": r.get("final_score", 0), "payload": r.get("payload", {})}
            for r in codex_results
        ]
        library_for_context = [
            {"id": r.get("id"), "score": r.get("final_score", 0), "payload": r.get("payload", {})}
            for r in library_results
        ]

        try:
            laws_context, decisions_context, _ = pipeline.build_context(
                codex_results=codex_for_context,
                library_results=library_for_context,
            )
        except Exception as e:
            yield f"data: {{'error': 'Context building failed: {str(e)}'}}\n\n"
            return

        yield f"data: {{'stage': 'context', 'status': 'complete'}}\n\n"

        # Stage 5: Stream analysis
        web_search_enabled = chat_request.enable_web_search or False
        yield f"data: {{'stage': 'analyze', 'status': 'streaming', 'web_search': {str(web_search_enabled).lower()}}}\n\n"

        try:
            stream_gen = pipeline.analyze(
                reformulated_query=reformulated_query,
                laws_context=laws_context,
                decisions_context=decisions_context,
                language=guard_result.detected_language,
                web_search=web_search_enabled,
            )

            full_response = []
            for chunk in stream_gen:
                full_response.append(chunk)
                escaped_chunk = chunk.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
                yield f"data: {{'stage': 'analyze', 'chunk': \"{escaped_chunk}\"}}\n\n"

            try:
                final_response = stream_gen.send(None)
            except StopIteration as e:
                final_response = e.value

        except Exception as e:
            yield f"data: {{'error': 'Analysis failed: {str(e)}'}}\n\n"
            return

        # Parse consistency
        response_text = "".join(full_response)
        consistency, confidence = pipeline.parse_consistency(response_text)

        processing_time = (time.time() - start_time) * 1000

        yield f"data: {{'stage': 'complete', 'consistency': '{consistency}', 'confidence': '{confidence}', 'processing_time_ms': {processing_time:.1f}}}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
