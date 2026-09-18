"""
Chat/Query Endpoints.

Thin HTTP layer over src.pipeline.service.LegalQueryService (the same
orchestration the Chainlit UI uses). Two flavours:
- POST /chat         -> one JSON response
- POST /chat/stream  -> Server-Sent Events, one JSON object per event
"""
import logging
import time
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse

from ..models import ChatRequest, ChatResponse, SourceReference, ErrorResponse
from ..deps import check_rate_limit, get_db
from ...database.auth_db import AuthDB
from ...pipeline import LegalQueryService, PipelineOptions, get_query_service
from ...pipeline.service import event_to_json

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["Chat"])

_ERROR_STATUS = {
    "blocked": status.HTTP_400_BAD_REQUEST,
    "no_sources": status.HTTP_404_NOT_FOUND,
    "failed": status.HTTP_503_SERVICE_UNAVAILABLE,
}


def get_service() -> LegalQueryService:
    """Dependency hook (overridden in tests)."""
    return get_query_service()


def _options(req: ChatRequest, stream: bool) -> PipelineOptions:
    filters = {}
    if req.language and req.language != "auto":
        filters["language"] = req.language
    return PipelineOptions(
        language=req.language or "auto",
        search_scope=req.search_scope or "both",
        max_laws=req.max_laws or 25,
        max_decisions=req.max_decisions or 10,
        web_search=bool(req.enable_web_search),
        filters=filters or None,
        top_k=max(req.max_laws or 25, 10),
        stream=stream,
        use_followup_context=False,   # the REST API is stateless
    )


def _record_usage(db: AuthDB, user_id: str, usage: Dict) -> None:
    """Persist token usage; never fail the request because of accounting."""
    try:
        db.record_token_usage(
            user_id=user_id,
            usage_record={
                "model": "pipeline",
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cost_chf": usage.get("total_cost_chf", 0.0),
                "operation": "chat",
            },
        )
    except Exception as e:
        logger.error(f"Failed to record token usage: {e}")


@router.post(
    "",
    response_model=ChatResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Query blocked by the guard"},
        404: {"model": ErrorResponse, "description": "No relevant sources found"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        503: {"model": ErrorResponse, "description": "A pipeline stage failed"},
    },
)
async def chat(
    request: Request,
    chat_request: ChatRequest,
    user: Dict = Depends(check_rate_limit),
    db: AuthDB = Depends(get_db),
    service: LegalQueryService = Depends(get_service),
):
    """
    Submit a legal question and receive a complete analysis with citations.

    Pipeline: PII scrub -> Guard & Enhance -> TriadSearch -> Reformulate -> Analyze.
    """
    start = time.time()
    answer = None
    pii_types = []
    search_conf: Dict[str, str] = {}

    async for event in service.run(chat_request.query, options=_options(chat_request, stream=False)):
        if event.stage == "error":
            raise HTTPException(
                status_code=_ERROR_STATUS.get(event.status, status.HTTP_503_SERVICE_UNAVAILABLE),
                detail=event.data.get("message", "Pipeline error"),
            )
        if event.stage == "pii":
            pii_types = event.data.get("types", [])
        elif event.stage == "search":
            search_conf = {
                "codex": event.data.get("codex_confidence", "NONE"),
                "library": event.data.get("library_confidence", "NONE"),
                "overall": event.data.get("overall_confidence", "NONE"),
            }
        elif event.stage == "complete":
            answer = event.data["answer_obj"]

    if answer is None:  # defensive: the service always ends with complete or error
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Pipeline ended without a result")

    _record_usage(db, str(user["user_id"]), answer.token_usage)

    return ChatResponse(
        answer=answer.answer,
        consistency=answer.consistency,
        confidence=answer.confidence,
        detected_language=answer.language,
        sources=[SourceReference(**s) for s in answer.sources],
        token_usage={
            **answer.token_usage,
            "search_confidence": search_conf,
            "pii_scrubbed": answer.pii_detected,
            "pii_types": pii_types,
        },
        processing_time_ms=(time.time() - start) * 1000,
    )


@router.post("/stream")
async def chat_stream(
    request: Request,
    chat_request: ChatRequest,
    user: Dict = Depends(check_rate_limit),
    db: AuthDB = Depends(get_db),
    service: LegalQueryService = Depends(get_service),
):
    """
    Streaming variant: Server-Sent Events. Each `data:` line is a JSON object
    with `stage` and `status` (pii, guard, search, reformulate, context,
    analyze[chunk], complete, error). The stream ends with `data: [DONE]`.
    """
    async def generate():
        async for event in service.run(chat_request.query, options=_options(chat_request, stream=True)):
            if event.stage == "complete":
                _record_usage(db, str(user["user_id"]), event.data["answer_obj"].token_usage)
            yield f"data: {event_to_json(event)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
