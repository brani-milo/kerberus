"""
Pydantic Models for KERBERUS API.

Request and response models for all API endpoints.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, EmailStr, Field


# ============================================
# Authentication Models
# ============================================

class UserRegister(BaseModel):
    """User registration request."""
    email: EmailStr
    password: str = Field(..., min_length=8, description="Minimum 8 characters")

    class Config:
        json_schema_extra = {
            "example": {
                "email": "lawyer@lawfirm.ch",
                "password": "securepassword123"
            }
        }


class UserLogin(BaseModel):
    """User login request."""
    email: EmailStr
    password: str
    totp_code: Optional[str] = Field(None, description="6-digit TOTP code if MFA enabled")

    class Config:
        json_schema_extra = {
            "example": {
                "email": "lawyer@lawfirm.ch",
                "password": "securepassword123",
                "totp_code": "123456"
            }
        }


class TokenResponse(BaseModel):
    """Authentication token response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Token expiration in seconds")
    user_id: str
    email: str
    mfa_enabled: bool


class MFASetupResponse(BaseModel):
    """MFA setup response with QR code."""
    secret: str
    qr_code_base64: str
    provisioning_uri: str


class MFAVerifyRequest(BaseModel):
    """MFA verification request."""
    totp_code: str = Field(..., min_length=6, max_length=6)


class UserResponse(BaseModel):
    """User profile response."""
    user_id: str
    email: str
    mfa_enabled: bool
    created_at: datetime
    last_login: Optional[datetime]


# ============================================
# Chat/Query Models
# ============================================

class ChatRequest(BaseModel):
    """Chat query request."""
    query: str = Field(..., min_length=3, max_length=2000)
    language: Optional[str] = Field("auto", description="Language: de, fr, it, en, or auto")
    search_scope: Optional[str] = Field("both", description="Search scope: both, laws, decisions")
    multilingual: Optional[bool] = Field(False, description="Enable cross-language search")
    max_laws: Optional[int] = Field(5, ge=1, le=10)
    max_decisions: Optional[int] = Field(7, ge=1, le=15)

    class Config:
        json_schema_extra = {
            "example": {
                "query": "Unter welchen Umständen ist eine fristlose Kündigung gerechtfertigt?",
                "language": "de",
                "search_scope": "both",
                "multilingual": False
            }
        }


class SourceReference(BaseModel):
    """Reference to a legal source."""
    id: str
    type: str = Field(..., description="law or decision")
    citation: str
    language: str
    url: Optional[str] = None
    relevance_score: float


class ChatResponse(BaseModel):
    """Chat query response."""
    answer: str
    consistency: str = Field(..., description="CONSISTENT, MIXED, or DIVERGENT")
    confidence: str = Field(..., description="high, medium, or low")
    detected_language: str
    sources: List[SourceReference]
    token_usage: Dict[str, Any]
    processing_time_ms: float


class StreamChatRequest(ChatRequest):
    """Streaming chat request (same as ChatRequest)."""
    pass


# ============================================
# Search Models
# ============================================

class SearchRequest(BaseModel):
    """Search request."""
    query: str = Field(..., min_length=2, max_length=500)
    collection: str = Field("both", description="codex, library, or both")
    limit: int = Field(10, ge=1, le=50)
    language: Optional[str] = Field(None, description="Filter by language: de, fr, it")
    year_min: Optional[int] = Field(None, ge=1900, le=2030)
    year_max: Optional[int] = Field(None, ge=1900, le=2030)
    multilingual: bool = Field(False)

    class Config:
        json_schema_extra = {
            "example": {
                "query": "fristlose Kündigung Art. 337 OR",
                "collection": "both",
                "limit": 10
            }
        }


class SearchResult(BaseModel):
    """Individual search result."""
    id: str
    score: float
    collection: str
    payload: Dict[str, Any]


class SearchResponse(BaseModel):
    """Search response."""
    query: str
    results: List[SearchResult]
    total_count: int
    processing_time_ms: float


# ============================================
# Usage/Stats Models
# ============================================

class UsageStats(BaseModel):
    """Token usage statistics."""
    year: int
    month: int
    total_tokens: int
    total_cost_chf: float
    query_count: int
    by_model: Dict[str, Dict[str, Any]]


class RateLimitInfo(BaseModel):
    """Rate limit information."""
    requests_remaining: int
    requests_limit: int
    reset_at: datetime


# ============================================
# Health Models
# ============================================

class HealthStatus(BaseModel):
    """Health check response."""
    status: str = Field(..., description="healthy or unhealthy")
    version: str
    services: Dict[str, str]
    timestamp: datetime


class ServiceHealth(BaseModel):
    """Individual service health."""
    name: str
    status: str
    latency_ms: Optional[float] = None
    error: Optional[str] = None


# ============================================
# Error Models
# ============================================

class ErrorResponse(BaseModel):
    """Standard error response."""
    error: str
    detail: Optional[str] = None
    code: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "error": "Unauthorized",
                "detail": "Invalid or expired token",
                "code": "AUTH_TOKEN_INVALID"
            }
        }
