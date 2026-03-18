"""
API Routes package for Ag3ntum.

Contains all FastAPI route handlers organized by domain.
"""
from .admin import router as admin_router
from .auth import router as auth_router
from .config import router as config_router
from .files import router as files_router
from .health import router as health_router
from .llm_proxy import router as llm_proxy_router
from .llm_proxy import session_router as llm_proxy_session_router
from .queue import router as queue_router
from .reseller import router as reseller_router
from .sessions import router as sessions_router
from .skills import router as skills_router
from .ssh_profiles import router as ssh_profiles_router

__all__ = [
    "admin_router",
    "auth_router",
    "config_router",
    "files_router",
    "health_router",
    "llm_proxy_router",
    "llm_proxy_session_router",
    "queue_router",
    "reseller_router",
    "sessions_router",
    "skills_router",
    "ssh_profiles_router",
]
