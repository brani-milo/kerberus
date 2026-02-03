"""
API Routes for KERBERUS.
"""
from .auth import router as auth_router
from .chat import router as chat_router
from .search import router as search_router
from .health import router as health_router
from .security import router as security_router

__all__ = [
    "auth_router",
    "chat_router",
    "search_router",
    "health_router",
    "security_router",
]
