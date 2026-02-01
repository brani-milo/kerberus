"""
Search Endpoints.

Provides search functionality for laws and court decisions.
Uses TriadSearch with Hybrid + MMR + Reranking.
"""
import time
import asyncio
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request

from ..models import SearchRequest, SearchResponse, SearchResult, ErrorResponse
from ..deps import get_current_user, check_rate_limit
from ...search.triad_search import TriadSearch

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["Search"])

# Lazy-initialized search engine
_triad_search: Optional[TriadSearch] = None


def get_triad_search() -> TriadSearch:
    """Get TriadSearch engine (Hybrid + MMR + Rerank)."""
    global _triad_search
    if _triad_search is None:
        _triad_search = TriadSearch()
    return _triad_search


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

    Uses TriadSearch pipeline:
    1. Hybrid search (dense + sparse with RRF)
    2. MMR diversification
    3. Cross-encoder reranking
    """
    start_time = time.time()

    triad = get_triad_search()

    # Build filters
    filters = {}
    if search_request.language:
        filters["language"] = search_request.language
    if search_request.year_min or search_request.year_max:
        filters["year_range"] = {
            "min": search_request.year_min or 1900,
            "max": search_request.year_max or 2030,
        }

    try:
        # Execute triad search
        search_results = await triad.search(
            query=search_request.query,
            user_id=None,
            firm_id=None,
            filters=filters if filters else None,
            top_k=search_request.limit
        )

        results = []

        # Collect codex results
        if search_request.collection in ["codex", "both"]:
            codex_data = search_results.get('codex', {})
            for r in codex_data.get('results', []):
                results.append(SearchResult(
                    id=str(r.get("id", "")),
                    score=r.get("final_score", r.get("score", 0.0)),
                    collection="codex",
                    payload=r.get("payload", {}),
                ))

        # Collect library results
        if search_request.collection in ["library", "both"]:
            library_data = search_results.get('library', {})
            for r in library_data.get('results', []):
                results.append(SearchResult(
                    id=str(r.get("id", "")),
                    score=r.get("final_score", r.get("score", 0.0)),
                    collection="library",
                    payload=r.get("payload", {}),
                ))

        # Sort by score and limit
        results.sort(key=lambda x: x.score, reverse=True)
        results = results[:search_request.limit]

    except Exception as e:
        logger.error(f"Search error: {e}")
        results = []

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
    user: Dict = Depends(check_rate_limit),
):
    """
    Search Swiss laws (Codex collection).

    Uses TriadSearch with Hybrid + MMR + Reranking.
    """
    start_time = time.time()

    triad = get_triad_search()

    filters = {}
    if language:
        filters["language"] = language

    try:
        search_results = await triad.search(
            query=q,
            user_id=None,
            firm_id=None,
            filters=filters if filters else None,
            top_k=limit
        )

        codex_data = search_results.get('codex', {})
        results = [
            SearchResult(
                id=str(r.get("id", "")),
                score=r.get("final_score", r.get("score", 0.0)),
                collection="codex",
                payload=r.get("payload", {}),
            )
            for r in codex_data.get('results', [])
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
    user: Dict = Depends(check_rate_limit),
):
    """
    Search court decisions (Library collection).

    Uses TriadSearch with Hybrid + MMR + Reranking.
    """
    start_time = time.time()

    triad = get_triad_search()

    filters = {}
    if language:
        filters["language"] = language
    if year_min or year_max:
        filters["year_range"] = {
            "min": year_min or 1900,
            "max": year_max or 2030,
        }

    try:
        search_results = await triad.search(
            query=q,
            user_id=None,
            firm_id=None,
            filters=filters if filters else None,
            top_k=limit
        )

        library_data = search_results.get('library', {})
        results = [
            SearchResult(
                id=str(r.get("id", "")),
                score=r.get("final_score", r.get("score", 0.0)),
                collection="library",
                payload=r.get("payload", {}),
            )
            for r in library_data.get('results', [])
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
