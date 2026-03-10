"""SSH profile management routes — user self-service + admin override."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.database import get_db
from ...db.models import User
from ...services import ssh_profile_service as svc
from ...services.vault_encryption import VaultEncryption
from ...services.vault_service import VaultService
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

# Lazy-initialized vault service (needs master key from secrets.yaml)
_vault: VaultService | None = None


def _get_vault() -> VaultService:
    """Get or create VaultService with encryption from secrets.yaml."""
    global _vault
    if _vault is None:
        import yaml
        from ...config import CONFIG_DIR
        secrets_path = CONFIG_DIR / "secrets.yaml"
        secrets_data = {}
        if secrets_path.exists():
            with secrets_path.open("r", encoding="utf-8") as f:
                secrets_data = yaml.safe_load(f) or {}
        master_key = (secrets_data.get("fernet_key", "") or "").encode()
        if not master_key:
            raise RuntimeError("No fernet_key in secrets.yaml — cannot use vault")
        encryption = VaultEncryption(master_key=master_key)
        _vault = VaultService(vault_encryption=encryption)
    return _vault


# =========================================================================
# User self-service endpoints
# =========================================================================

@router.get("/ssh-profiles", response_model=SSHProfileListResponse)
async def list_my_profiles(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all SSH profiles for the current user."""
    records = await svc.get_profiles(db, user.id)
    profiles = []
    for r in records:
        resp = await svc.build_profile_response(db, _get_vault(), r, user.id)
        profiles.append(resp)
    return SSHProfileListResponse(profiles=profiles, count=len(profiles))


@router.post("/ssh-profiles", response_model=SSHProfileResponse,
             status_code=status.HTTP_201_CREATED)
async def create_profile(
    req: CreateSSHProfileRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new SSH profile for the current user."""
    try:
        record = await svc.create_profile(
            db=db,
            vault=_get_vault(),
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

    return await svc.build_profile_response(db, _get_vault(), record, user.id)


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

    try:
        record = await svc.update_profile(
            db, _get_vault(), user.id, profile_id, **updates
        )
    except ValueError as e:
        error_msg = str(e)
        if "already exists" in error_msg:
            raise HTTPException(status_code=409, detail=error_msg)
        raise HTTPException(status_code=422, detail=error_msg)

    if not record:
        raise HTTPException(status_code=404, detail="Profile not found")
    return await svc.build_profile_response(db, _get_vault(), record, user.id)


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
    if not await check_rate_limit(
        f"rate:ssh_test:user:{user.id}", max_attempts=5, window_seconds=60
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many SSH connection tests. Please wait and try again.",
        )
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
    if not await check_rate_limit(
        f"rate:ssh_test:user:{user.id}", max_attempts=5, window_seconds=60
    ):
        raise HTTPException(
            status_code=429,
            detail="Too many SSH connection tests. Please wait and try again.",
        )
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
    records = await svc.get_profiles(db, user_id)
    profiles = []
    for r in records:
        resp = await svc.build_profile_response(db, _get_vault(), r, user_id)
        profiles.append(resp)
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
