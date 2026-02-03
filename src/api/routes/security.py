"""
Security Endpoints.

Provides PII detection and scrubbing utilities.
"""
import logging
from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..deps import get_current_user
from ...security import get_pii_scrubber, PIIEntity

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/security", tags=["Security"])


class PIICheckRequest(BaseModel):
    """Request to check text for PII."""
    text: str = Field(..., min_length=1, max_length=50000)
    language: str = Field("de", description="Language code: de, fr, it, en")
    scrub: bool = Field(False, description="If true, return scrubbed text")


class PIIEntityResponse(BaseModel):
    """Detected PII entity."""
    entity_type: str
    text: str
    start: int
    end: int
    score: float


class PIICheckResponse(BaseModel):
    """Response from PII check."""
    has_pii: bool
    entities: List[PIIEntityResponse]
    scrubbed_text: str | None = None
    entity_summary: Dict[str, int]


@router.post("/pii/check", response_model=PIICheckResponse)
async def check_pii(
    request: PIICheckRequest,
    user: Dict = Depends(get_current_user),
):
    """
    Check text for PII (Personally Identifiable Information).

    Detects Swiss-specific PII including:
    - AHV numbers (Swiss social security)
    - Swiss phone numbers
    - Swiss IBAN numbers
    - Email addresses
    - Names and locations

    Optionally returns scrubbed version with PII replaced by placeholders.
    """
    scrubber = get_pii_scrubber()

    # Detect PII
    entities = scrubber.detect(request.text, language=request.language)

    # Build response
    entity_responses = [
        PIIEntityResponse(
            entity_type=e.entity_type,
            text=e.text,
            start=e.start,
            end=e.end,
            score=e.score,
        )
        for e in entities
    ]

    # Get summary
    summary = scrubber.get_pii_summary(request.text, language=request.language)

    # Optionally scrub
    scrubbed = None
    if request.scrub:
        scrubbed = scrubber.scrub(request.text, language=request.language)

    return PIICheckResponse(
        has_pii=len(entities) > 0,
        entities=entity_responses,
        scrubbed_text=scrubbed,
        entity_summary=summary,
    )


@router.post("/pii/scrub")
async def scrub_pii(
    request: PIICheckRequest,
    user: Dict = Depends(get_current_user),
) -> Dict[str, str]:
    """
    Scrub PII from text.

    Returns text with all detected PII replaced by type placeholders.
    Example: "My email is test@example.com" -> "My email is <EMAIL_ADDRESS>"
    """
    scrubber = get_pii_scrubber()
    scrubbed = scrubber.scrub(request.text, language=request.language)

    return {
        "original_length": len(request.text),
        "scrubbed_length": len(scrubbed),
        "scrubbed_text": scrubbed,
    }
