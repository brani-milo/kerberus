"""
Per-login record of a passed TOTP check.

Chainlit re-creates the session user on every websocket connection (for example
when a thread is opened from the sidebar), so an in-memory "mfa_verified" flag is
lost and the login token itself is issued BEFORE the second factor. This store
remembers, server-side, that a given login token has passed MFA for a given
user, so resuming a conversation needs no new code while a fresh login still does.

Keys are SHA-256 hashes of the login token (the token itself is never stored).
Redis when available, in-process memory otherwise.
"""
import hashlib
import logging
import os
import threading
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

MFA_SESSION_TTL_SECONDS = int(os.getenv("MFA_SESSION_TTL_SECONDS", str(12 * 3600)))
_PREFIX = "kerberus:mfa_ok:"

_memory: Dict[str, Tuple[str, float]] = {}
_lock = threading.Lock()


def _key(token: str) -> str:
    return _PREFIX + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _redis():
    try:
        from ..api.deps import get_redis_client
        return get_redis_client()
    except Exception:
        return None


def remember_mfa(token: Optional[str], user_id: str, ttl: int = MFA_SESSION_TTL_SECONDS) -> None:
    """Record that `token` passed the TOTP check for `user_id`."""
    if not token or not user_id:
        return
    key = _key(token)
    client = _redis()
    if client is not None:
        try:
            client.setex(key, ttl, str(user_id))
            return
        except Exception as e:
            logger.warning(f"Redis unavailable for MFA session store, using memory: {e}")
    with _lock:
        _memory[key] = (str(user_id), time.time() + ttl)


def mfa_passed(token: Optional[str], user_id: str) -> bool:
    """True if `token` passed the TOTP check for exactly this `user_id` and has not expired."""
    if not token or not user_id:
        return False
    key = _key(token)
    client = _redis()
    if client is not None:
        try:
            stored = client.get(key)
            if stored is not None:
                stored = stored.decode() if isinstance(stored, bytes) else stored
                return stored == str(user_id)
        except Exception as e:
            logger.warning(f"Redis unavailable for MFA session store, using memory: {e}")
    with _lock:
        entry = _memory.get(key)
        if not entry:
            return False
        stored_user, expires = entry
        if time.time() > expires:
            _memory.pop(key, None)
            return False
        return stored_user == str(user_id)


def forget_mfa(token: Optional[str]) -> None:
    """Drop the record (logout)."""
    if not token:
        return
    key = _key(token)
    client = _redis()
    if client is not None:
        try:
            client.delete(key)
        except Exception:
            pass
    with _lock:
        _memory.pop(key, None)
