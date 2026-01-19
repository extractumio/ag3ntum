"""
FastAPI application for Ag3ntum API.

Main entry point that configures the FastAPI app with:
- CORS middleware
- Database initialization
- Route registration
- Lifespan management
- Dual logging (console with colors + file)
"""
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

import yaml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..config import CONFIG_DIR, ConfigNotFoundError, ConfigValidationError
from ..services.session_service import InvalidSessionIdError, SessionNotFoundError
from ..core.logging_config import setup_backend_logging
from ..core.subagent_manager import get_subagent_manager
from ..db.database import init_db, DATABASE_PATH
from .routes import auth_router, config_router, files_router, health_router, llm_proxy_router, sessions_router, skills_router
from .waf_filter import validate_request_size

logger = logging.getLogger(__name__)

# API configuration file
API_CONFIG_FILE: Path = CONFIG_DIR / "api.yaml"

# Required fields in api.yaml
REQUIRED_API_FIELDS = ["host", "port", "cors_origins"]

# Patterns for sensitive field names (case-insensitive)
SENSITIVE_PATTERNS = re.compile(
    r"(secret|key|password|token|credential|auth)", re.IGNORECASE
)


# =============================================================================
# Configuration Utilities
# =============================================================================

def mask_sensitive_value(value: str, visible_chars: int = 4) -> str:
    """
    Mask a sensitive value, showing only first and last few characters.

    Args:
        value: The sensitive string to mask.
        visible_chars: Number of characters to show at start and end.

    Returns:
        Masked string like "sk-a...xyz" or "****" if too short.
    """
    if not isinstance(value, str):
        return "****"
    if len(value) <= visible_chars * 2:
        return "*" * len(value)
    return f"{value[:visible_chars]}...{value[-visible_chars:]}"


def format_config_value(key: str, value: Any, indent: int = 0) -> list[str]:
    """
    Format a configuration value for logging, masking sensitive values.

    Args:
        key: The configuration key name.
        value: The configuration value.
        indent: Current indentation level.

    Returns:
        List of formatted log lines.
    """
    prefix = "  " * indent
    lines = []

    if isinstance(value, dict):
        lines.append(f"{prefix}{key}:")
        for k, v in value.items():
            lines.extend(format_config_value(k, v, indent + 1))
    elif isinstance(value, list):
        lines.append(f"{prefix}{key}:")
        for item in value:
            if isinstance(item, dict):
                lines.append(f"{prefix}  -")
                for k, v in item.items():
                    lines.extend(format_config_value(k, v, indent + 2))
            else:
                lines.append(f"{prefix}  - {item}")
    else:
        # Check if key matches sensitive patterns
        if SENSITIVE_PATTERNS.search(key) and value:
            display_value = mask_sensitive_value(str(value))
        else:
            display_value = value
        lines.append(f"{prefix}{key}: {display_value}")

    return lines


def log_configuration(config: dict[str, Any]) -> None:
    """
    Log all loaded configuration with sensitive values masked.

    Args:
        config: The full configuration dictionary.
    """
    logger.info("=" * 60)
    logger.info("AG3NTUM API CONFIGURATION")
    logger.info("=" * 60)

    # Log config file path
    logger.info(f"Config file: {API_CONFIG_FILE}")
    logger.info(f"Database: {DATABASE_PATH}")
    logger.info("-" * 60)

    # Format and log all config values
    for key, value in config.items():
        for line in format_config_value(key, value):
            logger.info(line)

    logger.info("=" * 60)


def load_api_config() -> dict[str, Any]:
    """
    Load API configuration from api.yaml.

    Raises:
        ConfigNotFoundError: If api.yaml doesn't exist.
        ConfigValidationError: If required fields are missing or invalid.
    """
    if not API_CONFIG_FILE.exists():
        raise ConfigNotFoundError(
            f"API configuration not found: {API_CONFIG_FILE}\n"
            f"Create config/api.yaml with required fields: {', '.join(REQUIRED_API_FIELDS)}"
        )

    try:
        with API_CONFIG_FILE.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigValidationError(
            f"Failed to parse api.yaml: {e}"
        )

    if config is None:
        raise ConfigValidationError(
            f"API configuration file is empty: {API_CONFIG_FILE}"
        )

    api_config = config.get("api")
    if not api_config:
        raise ConfigValidationError(
            f"No 'api' section found in {API_CONFIG_FILE}"
        )

    missing = [field for field in REQUIRED_API_FIELDS if field not in api_config]
    if missing:
        raise ConfigValidationError(
            f"Missing required fields in {API_CONFIG_FILE}:\n"
            f"  {', '.join(missing)}\n"
            f"All fields must be explicitly defined - no default values."
        )

    return config


# =============================================================================
# Application Lifespan
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    FastAPI lifespan context manager.

    Handles startup and shutdown events:
    - Startup: Initialize database, load subagent configurations
    - Shutdown: Cleanup resources
    """
    # Startup
    logger.info("Starting Ag3ntum API...")
    await init_db()
    logger.info("Database initialized")

    # Initialize SubagentManager singleton
    # This loads config/subagents.yaml and renders all prompt templates ONCE.
    # The same subagent definitions are shared across ALL users and sessions.
    # See src/core/subagent_manager.py for architecture details.
    subagent_manager = get_subagent_manager()
    logger.info(
        f"SubagentManager initialized: {subagent_manager.agent_count} subagents "
        f"({subagent_manager.enabled_count} enabled, "
        f"{subagent_manager.disabled_count} disabled)"
    )

    yield

    # Shutdown
    logger.info("Shutting down Ag3ntum API...")


# =============================================================================
# Application Factory
# =============================================================================

def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Returns:
        Configured FastAPI app instance.

    Raises:
        ConfigNotFoundError: If api.yaml doesn't exist.
        ConfigValidationError: If required fields are missing.
    """
    # Configure dual logging (console with colors + file)
    setup_backend_logging()

    config = load_api_config()
    api_config = config["api"]

    # Log all loaded configuration
    log_configuration(config)

    app = FastAPI(
        title="Ag3ntum API",
        description="REST API for Ag3ntum - Self-Improving Agent",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=api_config["cors_origins"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # WAF Filter Middleware - validates request sizes before processing
    @app.middleware("http")
    async def waf_middleware(request: Request, call_next):
        """WAF filter to validate request body sizes."""
        # Validate request size (throws HTTPException if too large)
        await validate_request_size(request)
        
        # Continue processing request
        response = await call_next(request)
        return response

    # Register routes under /api/v1 prefix
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(sessions_router, prefix="/api/v1")
    app.include_router(files_router, prefix="/api/v1")
    app.include_router(llm_proxy_router, prefix="/api")
    app.include_router(skills_router, prefix="/api/v1")
    app.include_router(config_router, prefix="/api/v1")

    # Exception handlers for session-related errors
    @app.exception_handler(InvalidSessionIdError)
    async def invalid_session_id_handler(
        request: Request, exc: InvalidSessionIdError
    ) -> JSONResponse:
        """Convert InvalidSessionIdError to 404 response."""
        # Extract session ID from the error message for the response
        return JSONResponse(
            status_code=404,
            content={"detail": str(exc)},
        )

    @app.exception_handler(SessionNotFoundError)
    async def session_not_found_handler(
        request: Request, exc: SessionNotFoundError
    ) -> JSONResponse:
        """Convert SessionNotFoundError to 404 response."""
        return JSONResponse(
            status_code=404,
            content={"detail": str(exc)},
        )

    return app


# Create the app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    config = load_api_config()
    api_config = config["api"]

    uvicorn.run(
        "src.api.main:app",
        host=api_config["host"],
        port=api_config["port"],
        reload=api_config.get("reload", False),
        reload_dirs=["src"],
    )
