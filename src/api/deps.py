"""
FastAPI dependencies for Ag3ntum API.

Provides dependency injection for authentication, database sessions, etc.
"""
from typing import Optional

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.database import get_db
from ..services.auth_service import auth_service

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
        HTTPException: If token is invalid or expired.
    """
    token = credentials.credentials
    user_id = await auth_service.validate_token(token, db)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id


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
        HTTPException: If no token provided or token is invalid/expired.
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

    user_id = await auth_service.validate_token(actual_token, db)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id

