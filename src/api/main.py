"""
KERBERUS REST API - Main Application.

FastAPI-based REST API for the Swiss Legal Intelligence Platform.

Usage:
    # Development
    uvicorn src.api.main:app --reload --port 8000

    # Production
    uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --workers 4
"""
import os
import time
import uuid
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from .routes import auth_router, chat_router, search_router, health_router, security_router, dossier_router
from .models import ErrorResponse

# Configure logging with request context support
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.getenv(
    "LOG_FORMAT",
    "%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] %(message)s"
)

# Custom filter to add request_id to all log records
class RequestIdFilter(logging.Filter):
    """Add request_id to log records."""
    def filter(self, record):
        if not hasattr(record, 'request_id'):
            record.request_id = '-'
        return True

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format=LOG_FORMAT,
)
# Add filter to root logger
logging.getLogger().addFilter(RequestIdFilter())
logger = logging.getLogger(__name__)

# API metadata
API_TITLE = "KERBERUS API"
API_DESCRIPTION = """
**Swiss Legal Intelligence Platform**

A sovereign AI-powered legal assistant for Swiss law, providing:

- **Hybrid Search** - Semantic + lexical search across laws and court decisions
- **Three-Stage LLM Pipeline** - Guard, Reformulate, and Analyze
- **Multilingual Support** - German, French, Italian, and English
- **Dual-Language Citations** - Translated + original with source links

## Authentication

All endpoints (except `/health`) require Bearer token authentication.

1. Register: `POST /auth/register`
2. Login: `POST /auth/login`
3. Use token: `Authorization: Bearer <token>`

## Rate Limits

- 50 requests/hour
- 300 requests/day

## Collections

- **Codex**: Swiss federal laws (OR, ZGB, StGB, etc.)
- **Library**: Court decisions (BGE, BGer, BVGE, etc.)
"""
API_VERSION = os.getenv("APP_VERSION", "0.1.0")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan handler.

    Runs on startup and shutdown.
    """
    # Startup
    logger.info(f"Starting KERBERUS API v{API_VERSION}")

    # Initialize database schema
    try:
        from ..database.auth_db import get_auth_db
        db = get_auth_db()
        db.init_schema()
        logger.info("Database schema initialized")
    except Exception as e:
        logger.warning(f"Database initialization skipped: {e}")

    yield

    # Shutdown
    logger.info("Shutting down KERBERUS API")


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI instance.
    """
    app = FastAPI(
        title=API_TITLE,
        description=API_DESCRIPTION,
        version=API_VERSION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # CORS middleware
    allowed_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:8501").split(",")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request tracking and security headers middleware
    @app.middleware("http")
    async def add_request_tracking_and_security(request: Request, call_next):
        # Generate or extract request ID for tracing
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])

        # Store in request state for access in route handlers
        request.state.request_id = request_id

        # Add to logging context
        # Note: For async handlers, use contextvars in production

        start_time = time.time()

        try:
            response = await call_next(request)
        except Exception as e:
            logger.error(f"[{request_id}] Request failed: {e}", exc_info=True)
            raise

        process_time = (time.time() - start_time) * 1000

        # Request tracking headers
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-Ms"] = f"{process_time:.2f}"

        # Log request completion (skip health checks to reduce noise)
        if not request.url.path.startswith("/health"):
            logger.info(
                f"[{request_id}] {request.method} {request.url.path} "
                f"-> {response.status_code} ({process_time:.1f}ms)"
            )

        # Security headers
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # CSP for API (restrictive)
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response

    # Exception handlers
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        errors = []
        for error in exc.errors():
            loc = " -> ".join(str(l) for l in error["loc"])
            errors.append(f"{loc}: {error['msg']}")

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "Validation Error",
                "detail": "; ".join(errors),
                "code": "VALIDATION_ERROR",
            },
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        request_id = getattr(request.state, 'request_id', 'unknown')
        logger.error(f"[{request_id}] Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "Internal Server Error",
                "request_id": request_id,
                "detail": str(exc) if os.getenv("APP_ENV") == "development" else None,
                "code": "INTERNAL_ERROR",
            },
        )

    # Include routers
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(search_router)
    app.include_router(chat_router)
    app.include_router(security_router)
    app.include_router(dossier_router)

    # Root endpoint
    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "name": API_TITLE,
            "version": API_VERSION,
            "docs": "/docs",
            "health": "/health",
        }

    return app


# Create app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
