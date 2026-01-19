"""
FastAPI dependencies for Ag3ntum API.

Provides dependency injection for authentication, database sessions, etc.
"""
from typing import Optional

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.database import get_db
from ..services.auth_service import auth_service, UserEnvironmentError

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

