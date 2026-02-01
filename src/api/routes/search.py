"""
Search Endpoints.

Provides search functionality for laws and court decisions.
"""
import time
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request

from ..models import SearchRequest, SearchResponse, SearchResult, ErrorResponse
from ..deps import get_current_user, check_rate_limit, get_processing_time
from ...search.hybrid_search import HybridSearchEngine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["Search"])

# Lazy-initialized search engines
_codex_engine: Optional[HybridSearchEngine] = None
_library_engine: Optional[HybridSearchEngine] = None


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


@router.post(
    "",
    response_model=SearchResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid search parameters"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
)
async def search(
    request: Request,
    search_request: SearchRequest,
    user: Dict = Depends(check_rate_limit),
):
    """
    Search laws and court decisions.

    Supports hybrid search (semantic + lexical) with optional filters.
    """
    start_time = time.time()

    results = []

    # Build filters
    filters = {}
    if search_request.language:
        filters["language"] = search_request.language
    if search_request.year_min or search_request.year_max:
        filters["year_range"] = {
            "min": search_request.year_min or 1900,
            "max": search_request.year_max or 2030,
        }

    # Search codex (laws)
    if search_request.collection in ["codex", "both"]:
        try:
            codex_engine = get_codex_engine()
            codex_results = codex_engine.search(
                query=search_request.query,
                limit=search_request.limit,
                filters=filters if filters else None,
                multilingual=search_request.multilingual,
            )

            for r in codex_results:
                results.append(SearchResult(
                    id=str(r.get("id", "")),
                    score=r.get("score", 0.0),
                    collection="codex",
                    payload=r.get("payload", {}),
                ))
        except Exception as e:
            logger.error(f"Codex search error: {e}")
            # Continue with library search

    # Search library (decisions)
    if search_request.collection in ["library", "both"]:
        try:
            library_engine = get_library_engine()
            library_results = library_engine.search(
                query=search_request.query,
                limit=search_request.limit,
                filters=filters if filters else None,
                multilingual=search_request.multilingual,
            )

            for r in library_results:
                results.append(SearchResult(
                    id=str(r.get("id", "")),
                    score=r.get("score", 0.0),
                    collection="library",
                    payload=r.get("payload", {}),
                ))
        except Exception as e:
            logger.error(f"Library search error: {e}")

    # Sort by score and limit
    results.sort(key=lambda x: x.score, reverse=True)
    results = results[:search_request.limit]

    processing_time = (time.time() - start_time) * 1000

    return SearchResponse(
        query=search_request.query,
        results=results,
        total_count=len(results),
        processing_time_ms=processing_time,
    )


@router.get(
    "/laws",
    response_model=SearchResponse,
    responses={
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
)
async def search_laws(
    request: Request,
    q: str = Query(..., min_length=2, max_length=500, description="Search query"),
    limit: int = Query(10, ge=1, le=50),
    language: Optional[str] = Query(None, description="Filter by language: de, fr, it"),
    multilingual: bool = Query(False, description="Enable cross-language search"),
    user: Dict = Depends(check_rate_limit),
):
    """
    Search Swiss laws (Codex collection).

    Quick endpoint for law-only searches.
    """
    start_time = time.time()

    filters = {}
    if language:
        filters["language"] = language

    try:
        codex_engine = get_codex_engine()
        raw_results = codex_engine.search(
            query=q,
            limit=limit,
            filters=filters if filters else None,
            multilingual=multilingual,
        )

        results = [
            SearchResult(
                id=str(r.get("id", "")),
                score=r.get("score", 0.0),
                collection="codex",
                payload=r.get("payload", {}),
            )
            for r in raw_results
        ]
    except Exception as e:
        logger.error(f"Law search error: {e}")
        results = []

    processing_time = (time.time() - start_time) * 1000

    return SearchResponse(
        query=q,
        results=results,
        total_count=len(results),
        processing_time_ms=processing_time,
    )


@router.get(
    "/decisions",
    response_model=SearchResponse,
    responses={
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
)
async def search_decisions(
    request: Request,
    q: str = Query(..., min_length=2, max_length=500, description="Search query"),
    limit: int = Query(10, ge=1, le=50),
    language: Optional[str] = Query(None, description="Filter by language: de, fr, it"),
    year_min: Optional[int] = Query(None, ge=1900, le=2030),
    year_max: Optional[int] = Query(None, ge=1900, le=2030),
    multilingual: bool = Query(False, description="Enable cross-language search"),
    user: Dict = Depends(check_rate_limit),
):
    """
    Search court decisions (Library collection).

    Quick endpoint for decision-only searches.
    """
    start_time = time.time()

    filters = {}
    if language:
        filters["language"] = language
    if year_min or year_max:
        filters["year_range"] = {
            "min": year_min or 1900,
            "max": year_max or 2030,
        }

    try:
        library_engine = get_library_engine()
        raw_results = library_engine.search(
            query=q,
            limit=limit,
            filters=filters if filters else None,
            multilingual=multilingual,
        )

        results = [
            SearchResult(
                id=str(r.get("id", "")),
                score=r.get("score", 0.0),
                collection="library",
                payload=r.get("payload", {}),
            )
            for r in raw_results
        ]
    except Exception as e:
        logger.error(f"Decision search error: {e}")
        results = []

    processing_time = (time.time() - start_time) * 1000

    return SearchResponse(
        query=q,
        results=results,
        total_count=len(results),
        processing_time_ms=processing_time,
    )
