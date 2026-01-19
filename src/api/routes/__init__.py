"""
API Routes package for Ag3ntum.

Contains all FastAPI route handlers organized by domain.
"""
from .auth import router as auth_router
from .config import router as config_router
from .files import router as files_router
from .health import router as health_router
from .llm_proxy import router as llm_proxy_router
from .sessions import router as sessions_router
from .skills import router as skills_router

__all__ = [
    "auth_router",
    "config_router",
    "files_router",
    "health_router",
    "llm_proxy_router",
    "sessions_router",
    "skills_router",
]
