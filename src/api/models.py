"""
Pydantic Models for KERBERUS API.

Request and response models for all API endpoints.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ============================================
# Authentication Models
# ============================================

class UserRegister(BaseModel):
    """
    User registration request.

    Creates a new user account with email and password.
    Password must be at least 8 characters.
    """
    email: EmailStr = Field(..., description="Valid email address")
    password: str = Field(..., min_length=8, description="Password (minimum 8 characters)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "lawyer@lawfirm.ch",
                "password": "securepassword123"
            }
        }
    )


class UserLogin(BaseModel):
    """
    User login request.

    Authenticate with email and password. If MFA is enabled,
    provide either totp_code or backup_code.
    """
    email: EmailStr = Field(..., description="Registered email address")
    password: str = Field(..., description="Account password")
    totp_code: Optional[str] = Field(None, description="6-digit TOTP code from authenticator app")
    backup_code: Optional[str] = Field(None, description="One-time backup code (format: XXXX-XXXX)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "lawyer@lawfirm.ch",
                "password": "securepassword123",
                "totp_code": "123456"
            }
        }
    )


class TokenResponse(BaseModel):
    """Authentication token response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Token expiration in seconds")
    user_id: str
    email: str
    mfa_enabled: bool


class PasswordChangeRequest(BaseModel):
    """
    Password change request.

    Requires current password verification. All other sessions
    are invalidated after successful password change.
    """
    current_password: str = Field(..., description="Current account password")
    new_password: str = Field(..., min_length=8, description="New password (minimum 8 characters)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "current_password": "oldpassword123",
                "new_password": "newsecurepassword456"
            }
        }
    )


class MFASetupResponse(BaseModel):
    """MFA setup response with QR code."""
    secret: str
    qr_code_base64: str
    provisioning_uri: str


class MFAVerifyRequest(BaseModel):
    """MFA verification request."""
    totp_code: str = Field(..., min_length=6, max_length=6)


class MFAVerifyResponse(BaseModel):
    """
    MFA verification success response.

    Contains backup codes that should be stored securely.
    Each backup code can only be used once.
    """
    message: str = "MFA enabled successfully"
    backup_codes: List[str] = Field(..., description="One-time backup codes for account recovery (store securely!)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "MFA enabled successfully",
                "backup_codes": [
                    "A1B2-C3D4",
                    "E5F6-G7H8",
                    "I9J0-K1L2",
                    "M3N4-O5P6",
                    "Q7R8-S9T0",
                    "U1V2-W3X4",
                    "Y5Z6-A7B8",
                    "C9D0-E1F2"
                ]
            }
        }
    )


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
    """
    Chat query request for legal analysis.

    Submits a legal question for analysis using the four-stage pipeline:
    1. Guard & Enhance (security + query optimization)
    2. TriadSearch (hybrid + MMR + rerank)
    3. Reformulate (structure for analysis)
    4. Analyze (full legal analysis with citations)
    """
    query: str = Field(..., min_length=3, max_length=2000, description="Legal question to analyze")
    language: Optional[str] = Field("auto", description="Response language: de, fr, it, en, or auto-detect")
    search_scope: Optional[str] = Field("both", description="Search scope: both, laws, decisions")
    multilingual: Optional[bool] = Field(False, description="Enable cross-language search (searches all languages)")
    max_laws: Optional[int] = Field(10, ge=1, le=15, description="Maximum laws to include in context")
    max_decisions: Optional[int] = Field(10, ge=1, le=20, description="Maximum court decisions to include")
    enable_web_search: Optional[bool] = Field(False, description="Enable web search for additional sources")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "Unter welchen Umständen ist eine fristlose Kündigung gerechtfertigt?",
                "language": "de",
                "search_scope": "both",
                "multilingual": False,
                "enable_web_search": False
            }
        }
    )


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
    """
    Direct search request for laws and court decisions.

    Performs hybrid search (dense + sparse vectors) with optional filters.
    Use 'codex' for laws, 'library' for court decisions, or 'both'.
    """
    query: str = Field(..., min_length=2, max_length=500, description="Search query")
    collection: str = Field("both", description="Collection: codex (laws), library (decisions), or both")
    limit: int = Field(10, ge=1, le=50, description="Maximum results to return")
    language: Optional[str] = Field(None, description="Filter by language: de, fr, it")
    year_min: Optional[int] = Field(None, ge=1900, le=2030, description="Minimum year filter")
    year_max: Optional[int] = Field(None, ge=1900, le=2030, description="Maximum year filter")
    multilingual: bool = Field(False, description="Search across all languages")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "query": "fristlose Kündigung Art. 337 OR",
                "collection": "both",
                "limit": 10
            }
        }
    )


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
    """
    Standard error response.

    All API errors return this format with an error message,
    optional detail, and error code for programmatic handling.
    """
    error: str = Field(..., description="Error type/summary")
    detail: Optional[str] = Field(None, description="Detailed error message")
    code: Optional[str] = Field(None, description="Error code for programmatic handling")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": "Unauthorized",
                "detail": "Invalid or expired token",
                "code": "AUTH_TOKEN_INVALID"
            }
        }
    )
