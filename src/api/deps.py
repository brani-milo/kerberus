"""
FastAPI Dependencies for KERBERUS API.

Provides:
- Authentication dependencies
- Rate limiting (Redis-backed)
- Database connections
- Redis client
"""
import os
import time
import logging
from typing import Optional, Dict
from datetime import datetime, timezone
from functools import lru_cache

import redis
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from ..database.auth_db import AuthDB, get_auth_db

logger = logging.getLogger(__name__)


# ============================================
# Redis Client
# ============================================

_redis_client: Optional[redis.Redis] = None


def get_redis_client() -> Optional[redis.Redis]:
    """
    Get Redis client singleton.

    Returns None if Redis is not configured or unavailable.
    """
    global _redis_client

    if _redis_client is not None:
        return _redis_client

    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD", "") or None
    db = int(os.getenv("REDIS_DB", "0"))

    try:
        _redis_client = redis.Redis(
            host=host,
            port=port,
            password=password,
            db=db,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        # Test connection
        _redis_client.ping()
        logger.info(f"Redis connected: {host}:{port}")
        return _redis_client
    except redis.ConnectionError as e:
        logger.warning(f"Redis connection failed: {e}. Rate limiting will use in-memory fallback.")
        _redis_client = None
        return None

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

    # Store token in user dict for logout
    user["_session_token"] = token
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
# Rate Limiting (Redis-backed with in-memory fallback)
# ============================================

class RateLimiter:
    """
    Redis-backed rate limiter with in-memory fallback.

    Uses Redis INCR with TTL for atomic, distributed rate limiting.
    Falls back to in-memory if Redis is unavailable.
    """

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self.redis = redis_client
        self.daily_limit = int(os.getenv("RATE_LIMIT_DAILY", "300"))
        self.hourly_limit = int(os.getenv("RATE_LIMIT_HOURLY", "50"))
        # In-memory fallback storage
        self._memory_store: Dict[str, list] = {}

    def _get_redis_count(self, key: str, ttl_seconds: int) -> int:
        """Get current count from Redis, creating key if needed."""
        if self.redis is None:
            return self._get_memory_count(key, ttl_seconds)

        try:
            full_key = f"kerberus:ratelimit:{key}"
            count = self.redis.get(full_key)
            return int(count) if count else 0
        except redis.RedisError as e:
            logger.warning(f"Redis error in rate limit check: {e}")
            return self._get_memory_count(key, ttl_seconds)

    def _increment_redis(self, key: str, ttl_seconds: int) -> int:
        """Atomically increment counter in Redis."""
        if self.redis is None:
            return self._increment_memory(key, ttl_seconds)

        try:
            full_key = f"kerberus:ratelimit:{key}"
            pipe = self.redis.pipeline()
            pipe.incr(full_key)
            pipe.expire(full_key, ttl_seconds)
            results = pipe.execute()
            return results[0]  # New count after increment
        except redis.RedisError as e:
            logger.warning(f"Redis error in rate limit increment: {e}")
            return self._increment_memory(key, ttl_seconds)

    def _get_memory_count(self, key: str, window_seconds: int) -> int:
        """Get count from in-memory fallback."""
        now = time.time()
        if key not in self._memory_store:
            return 0
        # Clean old entries
        self._memory_store[key] = [
            ts for ts in self._memory_store[key]
            if now - ts < window_seconds
        ]
        return len(self._memory_store[key])

    def _increment_memory(self, key: str, window_seconds: int) -> int:
        """Increment in-memory fallback counter."""
        now = time.time()
        if key not in self._memory_store:
            self._memory_store[key] = []
        # Clean old entries first
        self._memory_store[key] = [
            ts for ts in self._memory_store[key]
            if now - ts < window_seconds
        ]
        self._memory_store[key].append(now)
        return len(self._memory_store[key])

    def check_rate_limit(self, user_id: str) -> tuple[bool, int, int]:
        """
        Check if user is within rate limits.

        Returns:
            Tuple of (allowed, remaining_hourly, remaining_daily)
        """
        hourly_key = f"{user_id}:hourly"
        daily_key = f"{user_id}:daily"

        # Check current counts
        hourly_count = self._get_redis_count(hourly_key, 3600)
        daily_count = self._get_redis_count(daily_key, 86400)

        hourly_remaining = self.hourly_limit - hourly_count
        daily_remaining = self.daily_limit - daily_count

        if hourly_remaining <= 0 or daily_remaining <= 0:
            return False, max(0, hourly_remaining), max(0, daily_remaining)

        return True, hourly_remaining, daily_remaining

    def record_request(self, user_id: str) -> None:
        """Record a request for rate limiting."""
        hourly_key = f"{user_id}:hourly"
        daily_key = f"{user_id}:daily"

        self._increment_redis(hourly_key, 3600)
        self._increment_redis(daily_key, 86400)


# Singleton rate limiter
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get singleton rate limiter (Redis-backed if available)."""
    global _rate_limiter
    if _rate_limiter is None:
        redis_client = get_redis_client()
        _rate_limiter = RateLimiter(redis_client)
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
# Auth Rate Limiting (IP-based for unauthenticated endpoints)
# ============================================

class AuthRateLimiter:
    """
    Rate limiter for authentication endpoints (IP-based).

    Provides separate limits for register and login endpoints.
    Uses Redis with in-memory fallback.
    """

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self.redis = redis_client
        self.register_limit = 5   # per hour
        self.login_limit = 10     # per 15 minutes
        # In-memory fallback storage
        self._memory_store: Dict[str, list] = {}

    def _get_count(self, key: str, window_seconds: int) -> int:
        """Get current count for a key."""
        if self.redis is not None:
            try:
                full_key = f"kerberus:auth_ratelimit:{key}"
                count = self.redis.get(full_key)
                return int(count) if count else 0
            except redis.RedisError as e:
                logger.warning(f"Redis error in auth rate limit check: {e}")

        # In-memory fallback
        now = time.time()
        if key not in self._memory_store:
            return 0
        self._memory_store[key] = [
            ts for ts in self._memory_store[key]
            if now - ts < window_seconds
        ]
        return len(self._memory_store[key])

    def _increment(self, key: str, window_seconds: int) -> int:
        """Increment counter for a key."""
        if self.redis is not None:
            try:
                full_key = f"kerberus:auth_ratelimit:{key}"
                pipe = self.redis.pipeline()
                pipe.incr(full_key)
                pipe.expire(full_key, window_seconds)
                results = pipe.execute()
                return results[0]
            except redis.RedisError as e:
                logger.warning(f"Redis error in auth rate limit increment: {e}")

        # In-memory fallback
        now = time.time()
        if key not in self._memory_store:
            self._memory_store[key] = []
        self._memory_store[key] = [
            ts for ts in self._memory_store[key]
            if now - ts < window_seconds
        ]
        self._memory_store[key].append(now)
        return len(self._memory_store[key])

    def check_register_limit(self, ip: str) -> tuple[bool, int]:
        """
        Check if IP is within register rate limit.

        Returns:
            Tuple of (allowed, remaining_requests)
        """
        key = f"register:{ip}"
        count = self._get_count(key, 3600)  # 1 hour window
        remaining = self.register_limit - count
        return remaining > 0, max(0, remaining)

    def check_login_limit(self, ip: str) -> tuple[bool, int]:
        """
        Check if IP is within login rate limit.

        Returns:
            Tuple of (allowed, remaining_requests)
        """
        key = f"login:{ip}"
        count = self._get_count(key, 900)  # 15 minute window
        remaining = self.login_limit - count
        return remaining > 0, max(0, remaining)

    def record_register(self, ip: str) -> None:
        """Record a register attempt from an IP."""
        key = f"register:{ip}"
        self._increment(key, 3600)

    def record_login(self, ip: str) -> None:
        """Record a login attempt from an IP."""
        key = f"login:{ip}"
        self._increment(key, 900)


# Singleton auth rate limiter
_auth_rate_limiter: Optional[AuthRateLimiter] = None


def get_auth_rate_limiter() -> AuthRateLimiter:
    """Get singleton auth rate limiter."""
    global _auth_rate_limiter
    if _auth_rate_limiter is None:
        redis_client = get_redis_client()
        _auth_rate_limiter = AuthRateLimiter(redis_client)
    return _auth_rate_limiter


async def check_register_rate_limit(request: Request) -> None:
    """
    Dependency to check register rate limit by IP.

    Raises HTTPException 429 if limit exceeded.
    """
    ip = request.client.host if request.client else "unknown"
    limiter = get_auth_rate_limiter()

    allowed, remaining = limiter.check_register_limit(ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many registration attempts. Try again later.",
            headers={
                "Retry-After": "3600",
                "X-RateLimit-Remaining": "0",
            },
        )

    # Record this attempt
    limiter.record_register(ip)


async def check_login_rate_limit(request: Request) -> None:
    """
    Dependency to check login rate limit by IP.

    Raises HTTPException 429 if limit exceeded.
    """
    ip = request.client.host if request.client else "unknown"
    limiter = get_auth_rate_limiter()

    allowed, remaining = limiter.check_login_limit(ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts from this IP. Try again later.",
            headers={
                "Retry-After": "900",
                "X-RateLimit-Remaining": "0",
            },
        )

    # Record this attempt
    limiter.record_login(ip)


# ============================================
# MFA Pending Secret Storage (Redis-backed)
# ============================================

# TTL for pending MFA secrets (10 minutes)
MFA_PENDING_TTL = 600

# In-memory fallback for MFA pending secrets
_mfa_pending_memory: Dict[str, tuple[str, float]] = {}


def store_pending_mfa_secret(user_id: str, secret: str) -> bool:
    """
    Store a pending MFA secret for verification.

    The secret expires after MFA_PENDING_TTL seconds.

    Args:
        user_id: User's UUID.
        secret: TOTP secret to store.

    Returns:
        True if stored successfully.
    """
    redis_client = get_redis_client()

    if redis_client is not None:
        try:
            key = f"kerberus:mfa_pending:{user_id}"
            redis_client.setex(key, MFA_PENDING_TTL, secret)
            return True
        except redis.RedisError as e:
            logger.warning(f"Redis error storing MFA secret: {e}")

    # Fallback to in-memory
    _mfa_pending_memory[user_id] = (secret, time.time() + MFA_PENDING_TTL)
    return True


def get_pending_mfa_secret(user_id: str) -> Optional[str]:
    """
    Retrieve and delete a pending MFA secret.

    Args:
        user_id: User's UUID.

    Returns:
        The pending secret, or None if not found/expired.
    """
    redis_client = get_redis_client()

    if redis_client is not None:
        try:
            key = f"kerberus:mfa_pending:{user_id}"
            secret = redis_client.get(key)
            if secret:
                redis_client.delete(key)
                return secret
            return None
        except redis.RedisError as e:
            logger.warning(f"Redis error retrieving MFA secret: {e}")

    # Fallback to in-memory
    if user_id in _mfa_pending_memory:
        secret, expires_at = _mfa_pending_memory[user_id]
        del _mfa_pending_memory[user_id]
        if time.time() < expires_at:
            return secret
    return None


def clear_pending_mfa_secret(user_id: str) -> None:
    """
    Clear a pending MFA secret without retrieving it.

    Args:
        user_id: User's UUID.
    """
    redis_client = get_redis_client()

    if redis_client is not None:
        try:
            key = f"kerberus:mfa_pending:{user_id}"
            redis_client.delete(key)
        except redis.RedisError as e:
            logger.warning(f"Redis error clearing MFA secret: {e}")

    # Also clear from memory fallback
    _mfa_pending_memory.pop(user_id, None)


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
