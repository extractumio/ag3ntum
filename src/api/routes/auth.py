"""
Authentication endpoints for Ag3ntum API.
"""
from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.database import get_db
from ...services.auth_service import auth_service, UserEnvironmentError
from ..deps import get_current_user_id
from ..models import TokenResponse, UserResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    email: str = Body(...),
    password: str = Body(...),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Login with email and password.

    Returns a JWT token valid for 7 days.

    Returns 403 Forbidden if user account is misconfigured (missing home/venv).
    """
    try:
        user, token, expires_in = await auth_service.authenticate(db, email, password)
        return TokenResponse(
            access_token=token,
            token_type="bearer",
            user_id=user.id,
            expires_in=expires_in,
        )
    except UserEnvironmentError as e:
        # User exists but filesystem is misconfigured - must be recreated
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )


@router.post("/logout")
async def logout(
    user_id: str = Depends(get_current_user_id),
) -> dict:
    """
    Logout (client-side token deletion).

    The server does not track tokens, so logout is handled client-side
    by deleting the token from storage.
    """
    return {"status": "logged_out"}


@router.get("/me", response_model=UserResponse)
async def get_current_user(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """
    Get current user info.

    Returns information about the authenticated user.
    """
    user = await auth_service.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        created_at=user.created_at,
    )

