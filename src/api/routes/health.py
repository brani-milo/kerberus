"""
Health Check Endpoints.

Provides health status for the API and its dependencies.
"""
import time
import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from qdrant_client import QdrantClient

from ..models import HealthStatus, ServiceHealth
from ..deps import get_db, get_redis_client
from ..version import API_VERSION as VERSION
from ...config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["Health"])


def _qdrant_client() -> QdrantClient:
    """Short-timeout Qdrant client for health probes (honours QDRANT_API_KEY)."""
    s = get_settings()
    return QdrantClient(host=s.qdrant_host, port=s.qdrant_port, api_key=s.qdrant_api_key or None, timeout=5)


@router.get("", response_model=HealthStatus)
async def health_check():
    """
    Basic health check endpoint.

    Returns overall system status.
    """
    services = {}
    overall_healthy = True

    # Check PostgreSQL
    try:
        start = time.time()
        db = get_db()
        with db.get_session() as session:
            from sqlalchemy import text
            session.execute(text("SELECT 1"))
        latency = (time.time() - start) * 1000
        services["postgresql"] = f"healthy ({latency:.1f}ms)"
    except Exception as e:
        services["postgresql"] = f"unhealthy: {str(e)}"
        overall_healthy = False

    # Check Qdrant
    try:
        start = time.time()
        client = _qdrant_client()
        collections = client.get_collections()
        latency = (time.time() - start) * 1000
        services["qdrant"] = f"healthy ({latency:.1f}ms, {len(collections.collections)} collections)"
    except Exception as e:
        services["qdrant"] = f"unhealthy: {str(e)}"
        overall_healthy = False

    # Check Redis
    try:
        redis_client = get_redis_client()
        if redis_client:
            start = time.time()
            redis_client.ping()
            latency = (time.time() - start) * 1000
            services["redis"] = f"healthy ({latency:.1f}ms)"
        else:
            services["redis"] = "fallback_mode (in-memory)"
    except Exception as e:
        services["redis"] = f"unhealthy: {str(e)}"
        # Redis failure is not critical - we have in-memory fallback

    return HealthStatus(
        status="healthy" if overall_healthy else "unhealthy",
        version=VERSION,
        services=services,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/live")
async def liveness():
    """
    Kubernetes liveness probe.

    Returns 200 if the service is running.
    """
    return {"status": "alive"}


@router.get("/ready")
async def readiness():
    """
    Kubernetes readiness probe.

    Returns 200 if the service is ready to accept traffic.
    """
    # Check if critical services are available
    try:
        # Quick PostgreSQL check
        db = get_db()
        with db.get_session() as session:
            from sqlalchemy import text
            session.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "error": str(e)},
        )


@router.get("/detailed", response_model=dict)
async def detailed_health():
    """
    Detailed health check with all service statuses.

    Returns comprehensive health information for monitoring.
    """
    checks = []

    # PostgreSQL
    try:
        start = time.time()
        db = get_db()
        with db.get_session() as session:
            from sqlalchemy import text
            session.execute(text("SELECT 1"))
        latency = (time.time() - start) * 1000
        checks.append(ServiceHealth(
            name="postgresql",
            status="healthy",
            latency_ms=latency
        ))
    except Exception as e:
        checks.append(ServiceHealth(
            name="postgresql",
            status="unhealthy",
            error=str(e)
        ))

    # Qdrant
    try:
        start = time.time()
        client = _qdrant_client()
        client.get_collections()
        latency = (time.time() - start) * 1000
        checks.append(ServiceHealth(
            name="qdrant",
            status="healthy",
            latency_ms=latency
        ))
    except Exception as e:
        checks.append(ServiceHealth(
            name="qdrant",
            status="unhealthy",
            error=str(e)
        ))

    # Redis
    try:
        start = time.time()
        redis_client = get_redis_client()
        if redis_client:
            redis_client.ping()
            latency = (time.time() - start) * 1000
            checks.append(ServiceHealth(
                name="redis",
                status="healthy",
                latency_ms=latency
            ))
        else:
            checks.append(ServiceHealth(
                name="redis",
                status="fallback_mode"  # Using in-memory rate limiting
            ))
    except Exception as e:
        checks.append(ServiceHealth(
            name="redis",
            status="unhealthy",
            error=str(e)
        ))

    # LLM API (Mistral)
    try:
        from ...llm import get_mistral_client
        client = get_mistral_client()
        if client.api_key:
            checks.append(ServiceHealth(
                name="mistral_api",
                status="configured"
            ))
        else:
            checks.append(ServiceHealth(
                name="mistral_api",
                status="mock_mode"
            ))
    except Exception as e:
        checks.append(ServiceHealth(
            name="mistral_api",
            status="error",
            error=str(e)
        ))

    # LLM API (Qwen)
    try:
        from ...llm import get_qwen_client
        client = get_qwen_client()
        if client.api_key:
            checks.append(ServiceHealth(
                name="qwen_api",
                status="configured"
            ))
        else:
            checks.append(ServiceHealth(
                name="qwen_api",
                status="mock_mode"
            ))
    except Exception as e:
        checks.append(ServiceHealth(
            name="qwen_api",
            status="error",
            error=str(e)
        ))

    overall = "healthy" if all(c.status in ["healthy", "configured", "mock_mode"] for c in checks) else "degraded"

    return {
        "status": overall,
        "version": VERSION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": [c.model_dump() for c in checks],
    }
