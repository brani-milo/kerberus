"""
Chat/Query Endpoints.

Provides the main legal analysis endpoint using the three-stage LLM pipeline.
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
from ...search.hybrid_search import HybridSearchEngine
from ...database.auth_db import AuthDB

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])

# Lazy-initialized components
_codex_engine: Optional[HybridSearchEngine] = None
_library_engine: Optional[HybridSearchEngine] = None
_context_assembler: Optional[ContextAssembler] = None


def get_codex_engine() -> HybridSearchEngine:
    """Get codex (laws) search engine."""
    global _codex_engine
    if _codex_engine is None:
        _codex_engine = HybridSearchEngine(collection_name="codex")
    return _codex_engine


def get_library_engine() -> HybridSearchEngine:
    """Get library (decisions) search engine."""
    global _library_engine
    if _library_engine is None:
        _library_engine = HybridSearchEngine(collection_name="library")
    return _library_engine


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

    Uses the three-stage LLM pipeline:
    1. Guard & Enhance (Mistral) - Security + query optimization
    2. Search & Rerank - Find relevant laws and decisions
    3. Reformulate (Mistral) - Structure for analysis
    4. Analyze (Qwen) - Full legal analysis with citations
    """
    start_time = time.time()

    # Get pipeline and components
    pipeline = get_pipeline()
    codex_engine = get_codex_engine()
    library_engine = get_library_engine()

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
    # STAGE 2: Search
    # ============================================
    codex_results = []
    library_results = []

    # Build filters
    filters = {}
    if detected_language and detected_language != "auto":
        filters["language"] = detected_language

    # Search laws
    if chat_request.search_scope in ["both", "laws"]:
        try:
            codex_results = codex_engine.search(
                query=enhanced_query,
                limit=chat_request.max_laws,
                filters=filters if filters else None,
                multilingual=chat_request.multilingual,
            )
        except Exception as e:
            logger.error(f"Codex search failed: {e}")

    # Search decisions
    if chat_request.search_scope in ["both", "decisions"]:
        try:
            library_results = library_engine.search(
                query=enhanced_query,
                limit=chat_request.max_decisions,
                filters=filters if filters else None,
                multilingual=chat_request.multilingual,
            )
        except Exception as e:
            logger.error(f"Library search failed: {e}")

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
        )
    except Exception as e:
        logger.error(f"Reformulate stage failed: {e}")
        reformulated_query = enhanced_query
        reformulate_response = None

    # ============================================
    # STAGE 4: Build Context & Analyze
    # ============================================
    try:
        laws_context, decisions_context, context_meta = pipeline.build_context(
            codex_results=codex_results,
            library_results=library_results,
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
            relevance_score=result.get("score", 0.0),
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
            url=None,  # Could add BGer URL builder
            relevance_score=result.get("score", 0.0),
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
                "input_tokens": total_tokens // 2,  # Rough estimate
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
            "stages": {
                "guard": guard_result.response.total_tokens if guard_result.response else 0,
                "reformulate": reformulate_response.total_tokens if reformulate_response else 0,
                "analyze": analysis_response.total_tokens if analysis_response else 0,
            }
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
        codex_engine = get_codex_engine()
        library_engine = get_library_engine()

        # Stage 1: Guard & Enhance
        yield f"data: {{'stage': 'guard', 'status': 'processing'}}\n\n"

        try:
            guard_result = pipeline.guard_and_enhance(chat_request.query)
        except Exception as e:
            yield f"data: {{'error': 'Guard stage failed: {str(e)}'}}\n\n"
            return

        if guard_result.status == "BLOCKED":
            yield f"data: {{'error': 'Query blocked: {guard_result.block_reason}'}}\n\n"
            return

        yield f"data: {{'stage': 'guard', 'status': 'complete', 'language': '{guard_result.detected_language}'}}\n\n"

        # Stage 2: Search
        yield f"data: {{'stage': 'search', 'status': 'processing'}}\n\n"

        codex_results = []
        library_results = []

        if chat_request.search_scope in ["both", "laws"]:
            try:
                codex_results = codex_engine.search(
                    query=guard_result.enhanced_query,
                    limit=chat_request.max_laws,
                    multilingual=chat_request.multilingual,
                )
            except Exception as e:
                logger.error(f"Codex search failed: {e}")

        if chat_request.search_scope in ["both", "decisions"]:
            try:
                library_results = library_engine.search(
                    query=guard_result.enhanced_query,
                    limit=chat_request.max_decisions,
                    multilingual=chat_request.multilingual,
                )
            except Exception as e:
                logger.error(f"Library search failed: {e}")

        yield f"data: {{'stage': 'search', 'status': 'complete', 'laws': {len(codex_results)}, 'decisions': {len(library_results)}}}\n\n"

        if not codex_results and not library_results:
            yield f"data: {{'error': 'No relevant sources found'}}\n\n"
            return

        # Stage 3: Reformulate
        yield f"data: {{'stage': 'reformulate', 'status': 'processing'}}\n\n"

        try:
            reformulated_query, _ = pipeline.reformulate(
                original_query=chat_request.query,
                enhanced_query=guard_result.enhanced_query,
                language=guard_result.detected_language,
                law_count=len(codex_results),
                decision_count=len(library_results),
                topics=guard_result.legal_concepts or ["general"],
            )
        except Exception as e:
            reformulated_query = guard_result.enhanced_query

        yield f"data: {{'stage': 'reformulate', 'status': 'complete'}}\n\n"

        # Stage 4: Build context
        yield f"data: {{'stage': 'context', 'status': 'processing'}}\n\n"

        try:
            laws_context, decisions_context, _ = pipeline.build_context(
                codex_results=codex_results,
                library_results=library_results,
            )
        except Exception as e:
            yield f"data: {{'error': 'Context building failed: {str(e)}'}}\n\n"
            return

        yield f"data: {{'stage': 'context', 'status': 'complete'}}\n\n"

        # Stage 5: Stream analysis
        yield f"data: {{'stage': 'analyze', 'status': 'streaming'}}\n\n"

        try:
            stream_gen = pipeline.analyze(
                reformulated_query=reformulated_query,
                laws_context=laws_context,
                decisions_context=decisions_context,
                language=guard_result.detected_language,
            )

            full_response = []
            for chunk in stream_gen:
                full_response.append(chunk)
                # Escape for JSON
                escaped_chunk = chunk.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
                yield f"data: {{'stage': 'analyze', 'chunk': \"{escaped_chunk}\"}}\n\n"

            # Get final response
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
