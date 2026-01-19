"""
Health check endpoint for Ag3ntum API.
"""
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_config_loader
from ...db.database import get_db
from ...services.agent_runner import agent_runner
from ..models import ComponentHealth, ConfigResponse, DeepHealthResponse, HealthResponse

router = APIRouter(tags=["health"])

API_VERSION = "1.0.0"


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Check API health status.

    Returns basic health information including version and timestamp.
    """
    return HealthResponse(
        status="ok",
        version=API_VERSION,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/health/deep", response_model=DeepHealthResponse)
async def deep_health_check(db: AsyncSession = Depends(get_db)) -> DeepHealthResponse:
    """
    Deep health check with component-level status.

    Checks database and Redis connectivity with latency measurements.
    Returns overall status: ok (all healthy), degraded (some issues), unhealthy (critical failures).
    """
    db_health = await _check_database_health(db)
    redis_health = await _check_redis_health()

    # Determine overall status
    if db_health.status == "unhealthy" or redis_health.status == "unhealthy":
        overall_status = "unhealthy"
    elif db_health.status == "degraded" or redis_health.status == "degraded":
        overall_status = "degraded"
    else:
        overall_status = "ok"

    return DeepHealthResponse(
        status=overall_status,
        version=API_VERSION,
        timestamp=datetime.now(timezone.utc),
        database=db_health,
        redis=redis_health,
    )


async def _check_database_health(db: AsyncSession) -> ComponentHealth:
    """Check database connectivity and measure latency."""
    try:
        start = time.perf_counter()
        await db.execute(text("SELECT 1"))
        latency_ms = (time.perf_counter() - start) * 1000

        # Consider slow responses as degraded (>100ms for simple query)
        if latency_ms > 100:
            return ComponentHealth(status="degraded", latency_ms=latency_ms)

        return ComponentHealth(status="ok", latency_ms=latency_ms)
    except Exception as e:
        return ComponentHealth(status="unhealthy", error=str(e))


async def _check_redis_health() -> ComponentHealth:
    """Check Redis connectivity and measure latency."""
    try:
        if agent_runner._event_hub is None:
            return ComponentHealth(status="unhealthy", error="Redis event hub not initialized")

        start = time.perf_counter()
        # Try to ensure the pool is connected and ping
        await agent_runner._event_hub._ensure_pool()
        pool = agent_runner._event_hub._pool
        if pool is None:
            return ComponentHealth(status="unhealthy", error="Redis pool not available")

        async with pool.client() as conn:
            await conn.ping()

        latency_ms = (time.perf_counter() - start) * 1000

        # Consider slow responses as degraded (>50ms for ping)
        if latency_ms > 50:
            return ComponentHealth(status="degraded", latency_ms=latency_ms)

        return ComponentHealth(status="ok", latency_ms=latency_ms)
    except Exception as e:
        return ComponentHealth(status="unhealthy", error=str(e))


@router.get("/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    """
    Get application configuration.

    Returns available models and default model for the UI.
    Models with ':mode=thinking' suffix enable extended thinking mode.
    """
    loader = get_config_loader()
    config = loader.get_config()

    # Get models_available, default_model, and thinking_tokens from agent.yaml config
    models_available = config.get("models_available", [])
    default_model = config.get("default_model", config.get("model", ""))
    thinking_tokens = config.get("thinking_tokens")

    return ConfigResponse(
        models_available=models_available,
        default_model=default_model,
        thinking_tokens=thinking_tokens,
    )

