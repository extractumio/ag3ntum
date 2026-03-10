"""SSH profile management routes — user self-service + admin override."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.database import get_db
from ...db.models import User
from ...services import ssh_profile_service as svc
from ...services.vault_service import VaultService, get_vault_service
from ..deps import get_current_user, require_admin
from ...services.rate_limiter import check_rate_limit
from ..ssh_profile_models import (
    CreateSSHProfileRequest,
    SSHProfileListResponse,
    SSHProfileResponse,
    TestSSHConnectionRequest,
    TestSSHConnectionResponse,
    UpdateSSHProfileRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ssh-profiles"])

def _get_vault() -> VaultService:
    """Get or create VaultService — delegates to shared factory."""
    return get_vault_service()


async def _check_ssh_test_rate(user: User) -> None:
    """Shared rate limit check for SSH test endpoints."""
    key = svc.SSH_TEST_RATE_KEY.format(user_id=user.id)
    if not await check_rate_limit(
        key,
        max_attempts=svc.SSH_TEST_MAX_ATTEMPTS,
        window_seconds=svc.SSH_TEST_WINDOW_SECONDS,
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many SSH connection tests. Please wait and try again.",
        )


# =========================================================================
# User self-service endpoints
# =========================================================================

@router.get("/ssh-profiles", response_model=SSHProfileListResponse)
async def list_my_profiles(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all SSH profiles for the current user."""
    profiles = await svc.build_profiles_list(db, _get_vault(), user.id)
    return SSHProfileListResponse(profiles=profiles, count=len(profiles))


@router.post("/ssh-profiles", response_model=SSHProfileResponse,
             status_code=status.HTTP_201_CREATED)
async def create_profile(
    req: CreateSSHProfileRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new SSH profile for the current user."""
    vault = _get_vault()
    try:
        record = await svc.create_profile(
            db=db,
            vault=vault,
            user_id=user.id,
            name=req.name,
            host=req.host,
            port=req.port,
            username=req.username,
            private_key=req.private_key,
            passphrase=req.passphrase,
            mode=req.mode,
            privilege_level=req.privilege_level,
            allowed_operations=req.allowed_operations,
            description=req.description,
            created_by="self",
        )
    except ValueError as e:
        error_msg = str(e)
        if "already exists" in error_msg:
            raise HTTPException(status_code=409, detail=error_msg)
        raise HTTPException(status_code=422, detail=error_msg)

    return await svc.build_profile_response(db, vault, record, user.id)


@router.get("/ssh-profiles/{profile_id}", response_model=SSHProfileResponse)
async def get_profile(
    profile_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single SSH profile by ID."""
    record = await svc.get_profile(db, user.id, profile_id)
    if not record:
        raise HTTPException(status_code=404, detail="Profile not found")
    return await svc.build_profile_response(db, _get_vault(), record, user.id)


@router.put("/ssh-profiles/{profile_id}", response_model=SSHProfileResponse)
async def update_profile(
    profile_id: str,
    req: UpdateSSHProfileRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an SSH profile."""
    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    vault = _get_vault()
    try:
        record = await svc.update_profile(
            db, vault, user.id, profile_id, **updates
        )
    except ValueError as e:
        error_msg = str(e)
        if "already exists" in error_msg:
            raise HTTPException(status_code=409, detail=error_msg)
        raise HTTPException(status_code=422, detail=error_msg)

    if not record:
        raise HTTPException(status_code=404, detail="Profile not found")
    return await svc.build_profile_response(db, vault, record, user.id)


@router.delete("/ssh-profiles/{profile_id}")
async def delete_profile(
    profile_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an SSH profile and its vault secrets."""
    deleted = await svc.delete_profile(db, _get_vault(), user.id, profile_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "deleted"}


@router.post("/ssh-profiles/test",
             response_model=TestSSHConnectionResponse)
async def test_connection(
    req: TestSSHConnectionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Test an SSH connection without saving."""
    await _check_ssh_test_rate(user)
    result = await svc.test_connection(
        host=req.host,
        port=req.port,
        username=req.username,
        private_key=req.private_key,
        passphrase=req.passphrase,
    )
    return TestSSHConnectionResponse(**result)


@router.post("/ssh-profiles/{profile_id}/test",
             response_model=TestSSHConnectionResponse)
async def test_saved_connection(
    profile_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Test an existing saved profile's connection."""
    await _check_ssh_test_rate(user)
    result = await svc.test_saved_connection(
        db, _get_vault(), user.id, profile_id
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return TestSSHConnectionResponse(**result)


# =========================================================================
# Admin override endpoints
# =========================================================================

@router.get("/admin/users/{user_id}/ssh-profiles",
            response_model=SSHProfileListResponse)
async def admin_list_user_profiles(
    user_id: str,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin: list all SSH profiles for a user."""
    profiles = await svc.build_profiles_list(db, _get_vault(), user_id)
    return SSHProfileListResponse(profiles=profiles, count=len(profiles))


@router.get("/admin/users/{user_id}/ssh-profiles/{profile_id}",
            response_model=SSHProfileResponse)
async def admin_get_user_profile(
    user_id: str,
    profile_id: str,
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin: get a single SSH profile for a user."""
    record = await svc.get_profile(db, user_id, profile_id)
    if not record:
        raise HTTPException(status_code=404, detail="Profile not found")
    return await svc.build_profile_response(db, _get_vault(), record, user_id)


@router.delete("/admin/users/{user_id}/ssh-profiles/{profile_id}")
async def admin_delete_user_profile(
    user_id: str,
    profile_id: str,
    confirm: bool = Query(default=False),
    _admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin: delete a user's SSH profile. Requires ?confirm=true."""
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Add ?confirm=true to confirm deletion"
        )
    deleted = await svc.delete_profile(db, _get_vault(), user_id, profile_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"status": "deleted"}
