"""
FastAPI Dependencies for KERBERUS API.

Provides:
- Authentication dependencies
- Rate limiting
- Database connections
"""
import os
import time
import logging
from typing import Optional, Dict
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from ..database.auth_db import AuthDB, get_auth_db

logger = logging.getLogger(__name__)

# Security scheme
security = HTTPBearer(auto_error=False)


# ============================================
# Database Dependencies
# ============================================

def get_db() -> AuthDB:
    """Get database connection."""
    return get_auth_db()


# ============================================
# Authentication Dependencies
# ============================================

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AuthDB = Depends(get_db),
) -> Dict:
    """
    Validate bearer token and return current user.

    Raises:
        HTTPException: If token is missing, invalid, or expired.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Validate session token
    user = db.validate_session(token)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AuthDB = Depends(get_db),
) -> Optional[Dict]:
    """
    Optionally validate bearer token.

    Returns None if no token provided, user dict if valid.
    """
    if credentials is None:
        return None

    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


# ============================================
# Rate Limiting
# ============================================

class RateLimiter:
    """
    Simple in-memory rate limiter.

    For production, use Redis-based rate limiting.
    """

    def __init__(self):
        self.requests: Dict[str, list] = {}
        self.daily_limit = int(os.getenv("RATE_LIMIT_DAILY", "300"))
        self.hourly_limit = int(os.getenv("RATE_LIMIT_HOURLY", "50"))

    def _clean_old_requests(self, key: str, window_seconds: int) -> list:
        """Remove requests outside the time window."""
        now = time.time()
        if key not in self.requests:
            self.requests[key] = []
        self.requests[key] = [
            ts for ts in self.requests[key]
            if now - ts < window_seconds
        ]
        return self.requests[key]

    def check_rate_limit(self, user_id: str) -> tuple[bool, int, int]:
        """
        Check if user is within rate limits.

        Returns:
            Tuple of (allowed, remaining_hourly, remaining_daily)
        """
        hourly_key = f"{user_id}:hourly"
        daily_key = f"{user_id}:daily"

        # Check hourly limit (3600 seconds)
        hourly_requests = self._clean_old_requests(hourly_key, 3600)
        hourly_remaining = self.hourly_limit - len(hourly_requests)

        # Check daily limit (86400 seconds)
        daily_requests = self._clean_old_requests(daily_key, 86400)
        daily_remaining = self.daily_limit - len(daily_requests)

        if hourly_remaining <= 0 or daily_remaining <= 0:
            return False, max(0, hourly_remaining), max(0, daily_remaining)

        return True, hourly_remaining, daily_remaining

    def record_request(self, user_id: str) -> None:
        """Record a request for rate limiting."""
        now = time.time()
        hourly_key = f"{user_id}:hourly"
        daily_key = f"{user_id}:daily"

        if hourly_key not in self.requests:
            self.requests[hourly_key] = []
        if daily_key not in self.requests:
            self.requests[daily_key] = []

        self.requests[hourly_key].append(now)
        self.requests[daily_key].append(now)


# Singleton rate limiter
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get singleton rate limiter."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


async def check_rate_limit(
    user: Dict = Depends(get_current_user),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> Dict:
    """
    Check rate limit for authenticated user.

    Raises:
        HTTPException: If rate limit exceeded.
    """
    enabled = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"

    if not enabled:
        return user

    user_id = str(user["user_id"])
    allowed, hourly_remaining, daily_remaining = rate_limiter.check_rate_limit(user_id)

    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Hourly: {hourly_remaining}, Daily: {daily_remaining}",
            headers={
                "X-RateLimit-Remaining-Hourly": str(hourly_remaining),
                "X-RateLimit-Remaining-Daily": str(daily_remaining),
                "Retry-After": "3600",
            },
        )

    # Record this request
    rate_limiter.record_request(user_id)

    # Add rate limit info to user dict
    user["rate_limit"] = {
        "hourly_remaining": hourly_remaining - 1,
        "daily_remaining": daily_remaining - 1,
    }

    return user


# ============================================
# Request Tracking
# ============================================

async def track_request_time(request: Request):
    """Track request processing time."""
    request.state.start_time = time.time()


def get_processing_time(request: Request) -> float:
    """Get request processing time in milliseconds."""
    start_time = getattr(request.state, "start_time", time.time())
    return (time.time() - start_time) * 1000
