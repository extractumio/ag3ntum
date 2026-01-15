"""
Health check endpoint for Ag3ntum API.
"""
from datetime import datetime, timezone

from fastapi import APIRouter

from ...config import get_config_loader
from ..models import ConfigResponse, HealthResponse

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


@router.get("/config", response_model=ConfigResponse)
async def get_config() -> ConfigResponse:
    """
    Get application configuration.

    Returns available models and default model for the UI.
    """
    loader = get_config_loader()
    config = loader.get_config()

    # Get models_available and default_model from agent.yaml config
    models_available = config.get("models_available", [])
    default_model = config.get("default_model", config.get("model", ""))

    return ConfigResponse(
        models_available=models_available,
        default_model=default_model,
    )

