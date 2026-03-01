"""
FastAPI dependencies for Ag3ntum API.

Provides dependency injection for authentication, database sessions, etc.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.database import get_db
from ..services.auth_service import auth_service, UserEnvironmentError
from ..services.connection_token import validate_connection_token
from ..core.sandbox_path_resolver import (
    configure_sandbox_path_resolver,
    has_sandbox_path_resolver,
)

logger = logging.getLogger(__name__)

# HTTP Bearer authentication scheme
bearer_scheme = HTTPBearer(auto_error=True)
# Optional bearer for endpoints that also accept query param tokens
bearer_scheme_optional = HTTPBearer(auto_error=False)


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> str:
    """
    Dependency that extracts and validates the JWT token.

    Returns the user_id from the token.

    Raises:
        HTTPException: 401 if token is invalid/expired, 403 if user environment misconfigured.
    """
    token = credentials.credentials

    try:
        user_id = await auth_service.validate_token(token, db)
    except UserEnvironmentError as e:
        # User account exists but filesystem is misconfigured
        # Return 403 Forbidden - user must be recreated
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id


async def get_proxy_caller_id(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme_optional),
    db: AsyncSession = Depends(get_db),
) -> str:
    """
    Auth dependency for the LLM proxy endpoint.

    Accepts two authentication methods:
    1. Loopback requests (127.0.0.1) with x-api-key header → returns "internal-agent"
       (This is how the Claude Agent SDK authenticates when ANTHROPIC_BASE_URL is set)
    2. Standard JWT Bearer token → falls back to get_current_user_id logic

    This is needed because the SDK sends x-api-key (Anthropic API auth), not
    JWT Bearer tokens, when making requests to the proxy endpoint.
    """
    client_host = request.client.host if request.client else None
    x_api_key = request.headers.get("x-api-key")

    # Path 1: Loopback traffic with x-api-key (internal SDK calls)
    if client_host == "127.0.0.1" and x_api_key:
        logger.info("LLM Proxy: loopback auth accepted from %s", client_host)
        return "internal-agent"

    # Path 2: Standard JWT Bearer auth
    if credentials and credentials.credentials:
        token = credentials.credentials
        try:
            user_id = await auth_service.validate_token(token, db)
        except UserEnvironmentError as e:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(e),
            )
        if user_id:
            return user_id

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
):
    """
    Dependency that extracts, validates JWT token and returns the full User object.

    Returns the User object from the database.

    Raises:
        HTTPException: 401 if token is invalid/expired, 403 if user environment misconfigured.
    """
    token = credentials.credentials

    try:
        user_id = await auth_service.validate_token(token, db)
    except UserEnvironmentError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await auth_service.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def require_admin(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
):
    """
    Dependency that requires admin role.

    Returns the User object if user is an admin.

    Raises:
        HTTPException: 401 if not authenticated, 403 if not admin.
    """
    user = await get_current_user(credentials, db)

    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return user


async def get_current_user_id_from_query_or_header(
    token: Optional[str] = Query(None, description="JWT token for authentication"),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme_optional),
    db: AsyncSession = Depends(get_db),
) -> str:
    """
    Dependency that accepts JWT token from either:
    1. Query parameter 'token' (for file downloads via browser)
    2. Authorization header (standard Bearer token)

    This is needed for file download endpoints where window.open() cannot set headers.

    Returns the user_id from the token.

    Raises:
        HTTPException: 401 if not authenticated/invalid, 403 if user environment misconfigured.
    """
    # Prefer header token if available, fall back to query param
    actual_token = None
    if credentials and credentials.credentials:
        actual_token = credentials.credentials
    elif token:
        actual_token = token

    if not actual_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = await auth_service.validate_token(actual_token, db)
    except UserEnvironmentError as e:
        # User account exists but filesystem is misconfigured
        # Return 403 Forbidden - user must be recreated
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id


async def validate_sse_token(
    token: Optional[str],
    authorization: Optional[str],
    db: AsyncSession,
) -> str:
    """Validate an SSE connection token or JWT for SSE/polling endpoints.

    Tries connection token first (preferred, single-use, short-lived),
    then falls back to JWT validation for backward compatibility.

    Args:
        token: Query parameter token (connection token or JWT).
        authorization: Authorization header value.
        db: Database session for JWT validation.

    Returns:
        The authenticated user_id.

    Raises:
        HTTPException: 401 if neither token is valid.
    """
    # Extract token from header if not provided as query param
    actual_token = token
    if not actual_token and authorization and authorization.lower().startswith("bearer "):
        actual_token = authorization.split(" ", 1)[1]

    if not actual_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing access token",
        )

    # Try connection token first (preferred for SSE)
    user_id = await validate_connection_token(actual_token)
    if user_id:
        return user_id

    # Fall back to JWT validation (backward compatibility)
    user_id = await auth_service.validate_token(actual_token, db)
    if user_id:
        return user_id

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
    )


@dataclass
class AuthContext:
    """Unified authentication context for reseller/admin endpoints."""

    user_id: str
    role: str  # "admin", "reseller", "user"
    reseller_id: Optional[str] = None
    api_key_id: Optional[str] = None
    api_key_scopes: list = field(default_factory=list)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_reseller(self) -> bool:
        return self.role == "reseller"

    def has_scope(self, scope: str) -> bool:
        """Check if auth context has a specific API key scope.

        JWT auth (no API key) has all scopes implicitly.
        """
        if not self.api_key_id:
            return True  # JWT auth = all scopes
        return scope in self.api_key_scopes


async def get_auth_context(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme_optional),
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """Unified auth: accepts JWT Bearer OR API key in X-API-Key header.

    For JWT: extracts user from token, determines role from User.role,
    if role=reseller, looks up reseller_id.

    For API key: validates via APIKeyService, returns context with
    key's scopes and reseller_id.
    """
    # Try API key first (X-API-Key header)
    api_key_header = request.headers.get("x-api-key")
    if api_key_header and (
        api_key_header.startswith("ag3_res_") or api_key_header.startswith("ag3_adm_")
    ):
        from ..services.api_key_service import api_key_service
        import json

        key = await api_key_service.validate_key(db, api_key_header)
        if not key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired API key",
            )

        # Check IP allowlist
        client_ip = request.client.host if request.client else "unknown"
        if not api_key_service.check_ip_allowed(key, client_ip):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="IP address not in allowlist",
            )

        # Check per-key rate limit
        from ..services.api_key_rate_limiter import check_api_key_rate_limit
        if not await check_api_key_rate_limit(key.id, key.rate_limit_per_minute):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="API key rate limit exceeded",
            )

        # Update last used
        await api_key_service.update_last_used(db, key.id, client_ip)

        scopes = json.loads(key.scopes) if key.scopes else []

        # Determine role from key prefix
        role = "admin" if api_key_header.startswith("ag3_adm_") else "reseller"

        return AuthContext(
            user_id=key.user_id,
            role=role,
            reseller_id=key.reseller_id,
            api_key_id=key.id,
            api_key_scopes=scopes,
        )

    # Fall back to JWT Bearer auth
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        user_id = await auth_service.validate_token(token, db)
    except UserEnvironmentError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await auth_service.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # reseller_id is stored directly on the User row (set during reseller creation)
    reseller_id = user.reseller_id if user.role == "reseller" else None

    return AuthContext(
        user_id=user.id,
        role=user.role,
        reseller_id=reseller_id,
    )


async def require_reseller(
    auth: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    """Require reseller role (or admin for override access)."""
    if auth.role not in ("reseller", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Reseller access required",
        )
    return auth


def require_scope(scope: str):
    """Factory for scope-checking dependencies."""

    async def _check(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not auth.has_scope(scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required scope: {scope}",
            )
        return auth

    return _check


def configure_sandbox_path_resolver_if_needed(
    session_id: str,
    username: str,
    workspace_docker: str,
) -> None:
    """
    Configure SandboxPathResolver for a session if not already configured.

    This is used by the File Explorer API to configure the resolver on-demand
    when accessing existing sessions after a server restart.

    Args:
        session_id: The session ID
        username: The username for the session
        workspace_docker: The Docker workspace path
    """
    if has_sandbox_path_resolver(session_id):
        return

    try:
        configure_sandbox_path_resolver(
            session_id=session_id,
            username=username,
            workspace_docker=workspace_docker,
        )
        logger.info(
            f"On-demand SandboxPathResolver configured for session {session_id}"
        )
    except Exception as e:
        logger.warning(f"Failed to configure SandboxPathResolver on-demand: {e}")
