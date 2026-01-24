"""
Integration Tests for UID Mode User Creation and Privilege Escalation Prevention.

These tests validate that users created in each UID mode:
1. Get UIDs in the correct range
2. Cannot escalate privileges to root (UID 0)
3. Cannot access other users' files
4. Cannot break out of the sandbox

Run with:
    pytest tests/security/test_uid_mode_integration.py -v

Inside Docker (with test permissions):
    ./run.sh test --subset uid_mode_integration
"""
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.uid_security import (
    UIDMode,
    UIDSecurityConfig,
    set_uid_security_config,
    validate_uid_for_setuid,
)
from src.services.user_service import UserService


# =============================================================================
# Constants
# =============================================================================

# Root UID - must NEVER be allocated
ROOT_UID = 0

# System UID range - must NEVER be allocated
SYSTEM_UID_MAX = 999

# API user UID - must NEVER be allocated to session users
API_USER_UID = 45045

# Isolated mode range
ISOLATED_UID_MIN = 50000
ISOLATED_UID_MAX = 60000

# Direct mode range
DIRECT_UID_MIN = 1000
DIRECT_UID_MAX = 65533


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def user_service() -> UserService:
    """Create a UserService instance."""
    return UserService()


@pytest.fixture
def isolated_mode():
    """Set isolated mode for the test."""
    config = UIDSecurityConfig(mode=UIDMode.ISOLATED)
    set_uid_security_config(config)
    yield config
    # Reset after test
    set_uid_security_config(UIDSecurityConfig(mode=UIDMode.ISOLATED))


@pytest.fixture
def direct_mode():
    """Set direct mode for the test."""
    config = UIDSecurityConfig(mode=UIDMode.DIRECT)
    set_uid_security_config(config)
    yield config
    # Reset after test
    set_uid_security_config(UIDSecurityConfig(mode=UIDMode.ISOLATED))


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    db = AsyncMock()
    # Mock execute to return empty result (no existing users)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result
    return db


# =============================================================================
# Test: Isolated Mode User Creation
# =============================================================================

class TestIsolatedModeUserCreation:
    """Test user creation in isolated mode."""

    @pytest.mark.asyncio
    async def test_uid_in_isolated_range(
        self,
        user_service: UserService,
        mock_db,
        isolated_mode,
    ) -> None:
        """New users in isolated mode get UIDs in 50000-60000 range."""
        uid = await user_service._generate_next_uid(mock_db, UIDMode.ISOLATED)

        assert ISOLATED_UID_MIN <= uid <= ISOLATED_UID_MAX, (
            f"UID {uid} should be in isolated range [{ISOLATED_UID_MIN}, {ISOLATED_UID_MAX}]"
        )

    @pytest.mark.asyncio
    async def test_uid_increments_correctly(
        self,
        user_service: UserService,
        mock_db,
        isolated_mode,
    ) -> None:
        """UIDs increment from the last allocated UID."""
        # Mock existing user with UID 50005
        result = MagicMock()
        result.scalar_one_or_none.return_value = 50005
        mock_db.execute.return_value = result

        uid = await user_service._generate_next_uid(mock_db, UIDMode.ISOLATED)

        assert uid == 50006, f"Expected UID 50006, got {uid}"

    @pytest.mark.asyncio
    async def test_uid_starts_at_isolated_min(
        self,
        user_service: UserService,
        mock_db,
        isolated_mode,
    ) -> None:
        """First user in isolated mode gets UID 50000."""
        # Mock no existing users in range
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = result

        uid = await user_service._generate_next_uid(mock_db, UIDMode.ISOLATED)

        assert uid == ISOLATED_UID_MIN, f"Expected UID {ISOLATED_UID_MIN}, got {uid}"

    @pytest.mark.asyncio
    async def test_uid_never_root_isolated(
        self,
        user_service: UserService,
        isolated_mode,
    ) -> None:
        """Isolated mode NEVER allocates root UID."""
        config = UIDSecurityConfig(mode=UIDMode.ISOLATED)

        # Attempt to validate root UID
        is_valid, _ = validate_uid_for_setuid(ROOT_UID, config)

        assert not is_valid, "Root UID should never be valid"

    @pytest.mark.asyncio
    async def test_uid_never_system_isolated(
        self,
        user_service: UserService,
        isolated_mode,
    ) -> None:
        """Isolated mode NEVER allocates system UIDs (1-999)."""
        config = UIDSecurityConfig(mode=UIDMode.ISOLATED)

        for uid in [1, 100, 500, 999]:
            is_valid, _ = validate_uid_for_setuid(uid, config)
            assert not is_valid, f"System UID {uid} should never be valid"


# =============================================================================
# Test: Direct Mode User Creation
# =============================================================================

class TestDirectModeUserCreation:
    """Test user creation in direct mode."""

    @pytest.mark.asyncio
    async def test_uid_in_direct_range(
        self,
        user_service: UserService,
        mock_db,
        direct_mode,
    ) -> None:
        """New users in direct mode get UIDs in 1000-65533 range."""
        uid = await user_service._generate_next_uid(mock_db, UIDMode.DIRECT)

        assert DIRECT_UID_MIN <= uid <= DIRECT_UID_MAX, (
            f"UID {uid} should be in direct range [{DIRECT_UID_MIN}, {DIRECT_UID_MAX}]"
        )

    @pytest.mark.asyncio
    async def test_uid_starts_at_direct_min(
        self,
        user_service: UserService,
        mock_db,
        direct_mode,
    ) -> None:
        """First user in direct mode gets UID 1000."""
        # Mock no existing users in range
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = result

        uid = await user_service._generate_next_uid(mock_db, UIDMode.DIRECT)

        assert uid == DIRECT_UID_MIN, f"Expected UID {DIRECT_UID_MIN}, got {uid}"

    @pytest.mark.asyncio
    async def test_uid_never_root_direct(
        self,
        user_service: UserService,
        direct_mode,
    ) -> None:
        """Direct mode NEVER allocates root UID."""
        config = UIDSecurityConfig(mode=UIDMode.DIRECT)

        is_valid, _ = validate_uid_for_setuid(ROOT_UID, config)

        assert not is_valid, "Root UID should never be valid in direct mode"

    @pytest.mark.asyncio
    async def test_uid_never_system_direct(
        self,
        user_service: UserService,
        direct_mode,
    ) -> None:
        """Direct mode NEVER allocates system UIDs (1-999)."""
        config = UIDSecurityConfig(mode=UIDMode.DIRECT)

        for uid in [1, 100, 500, 999]:
            is_valid, _ = validate_uid_for_setuid(uid, config)
            assert not is_valid, f"System UID {uid} should never be valid in direct mode"


# =============================================================================
# Test: Privilege Escalation Prevention
# =============================================================================

class TestPrivilegeEscalationPrevention:
    """Test that privilege escalation to root is prevented."""

    def test_cannot_validate_root_uid_isolated(self, isolated_mode) -> None:
        """Cannot validate root UID in isolated mode."""
        is_valid, reason = validate_uid_for_setuid(ROOT_UID, isolated_mode)

        assert not is_valid
        assert "SECURITY VIOLATION" in reason

    def test_cannot_validate_root_uid_direct(self, direct_mode) -> None:
        """Cannot validate root UID in direct mode."""
        is_valid, reason = validate_uid_for_setuid(ROOT_UID, direct_mode)

        assert not is_valid
        assert "SECURITY VIOLATION" in reason

    def test_cannot_validate_api_user_uid(self, isolated_mode) -> None:
        """Cannot use API user UID for sandbox commands."""
        is_valid, reason = validate_uid_for_setuid(API_USER_UID, isolated_mode)

        assert not is_valid
        assert "API user" in reason

    def test_session_uid_enforcement(self, isolated_mode) -> None:
        """Session can only use its own UID."""
        session_uid = 50001

        # Own UID is valid
        is_valid, _ = validate_uid_for_setuid(50001, isolated_mode, session_uid=session_uid)
        assert is_valid

        # Another user's UID is blocked
        is_valid, reason = validate_uid_for_setuid(50002, isolated_mode, session_uid=session_uid)
        assert not is_valid
        assert "does not match session" in reason


# =============================================================================
# Test: Cross-User Access Prevention
# =============================================================================

class TestCrossUserAccessPrevention:
    """Test that users cannot access other users' files."""

    def test_session_uid_prevents_cross_user_access(self, isolated_mode) -> None:
        """Session UID validation prevents cross-user setuid."""
        user_a_uid = 50001
        user_b_uid = 50002

        # User A cannot use User B's UID
        is_valid, reason = validate_uid_for_setuid(
            user_b_uid, isolated_mode, session_uid=user_a_uid
        )
        assert not is_valid
        assert "does not match session" in reason

        # User B cannot use User A's UID
        is_valid, reason = validate_uid_for_setuid(
            user_a_uid, isolated_mode, session_uid=user_b_uid
        )
        assert not is_valid
        assert "does not match session" in reason

    def test_cross_user_access_blocked_direct_mode(self, direct_mode) -> None:
        """Cross-user access is blocked in direct mode too."""
        user_a_uid = 1000
        user_b_uid = 1001

        # User A cannot use User B's UID
        is_valid, reason = validate_uid_for_setuid(
            user_b_uid, direct_mode, session_uid=user_a_uid
        )
        assert not is_valid


# =============================================================================
# Test: UID Range Exhaustion
# =============================================================================

class TestUIDRangeExhaustion:
    """Test behavior when UID range is exhausted."""

    @pytest.mark.asyncio
    async def test_isolated_range_exhaustion(
        self,
        user_service: UserService,
        mock_db,
        isolated_mode,
    ) -> None:
        """Error when isolated UID range is exhausted."""
        # Mock last UID is at the maximum
        result = MagicMock()
        result.scalar_one_or_none.return_value = ISOLATED_UID_MAX
        mock_db.execute.return_value = result

        with pytest.raises(ValueError) as exc_info:
            await user_service._generate_next_uid(mock_db, UIDMode.ISOLATED)

        assert "exhausted" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_direct_range_exhaustion(
        self,
        user_service: UserService,
        mock_db,
        direct_mode,
    ) -> None:
        """Error when direct UID range is exhausted."""
        # Mock last UID is at the maximum
        result = MagicMock()
        result.scalar_one_or_none.return_value = DIRECT_UID_MAX
        mock_db.execute.return_value = result

        with pytest.raises(ValueError) as exc_info:
            await user_service._generate_next_uid(mock_db, UIDMode.DIRECT)

        assert "exhausted" in str(exc_info.value).lower()


# =============================================================================
# Test: Mode Switching
# =============================================================================

class TestModeSwitching:
    """Test behavior when switching between modes."""

    @pytest.mark.asyncio
    async def test_can_override_mode_per_user(
        self,
        user_service: UserService,
        mock_db,
        isolated_mode,
    ) -> None:
        """Can create user with different mode than global default."""
        # Global mode is isolated, but we request direct
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = result

        uid = await user_service._generate_next_uid(mock_db, UIDMode.DIRECT)

        assert uid == DIRECT_UID_MIN, (
            f"Should use direct mode minimum {DIRECT_UID_MIN}, got {uid}"
        )

    def test_legacy_uids_valid_in_isolated_mode(self, isolated_mode) -> None:
        """Legacy UIDs (2000-49999) remain valid in isolated mode."""
        # Enable legacy UID support
        isolated_mode.allow_legacy_uids = True

        legacy_uids = [2000, 10000, 30000, 49999]
        for uid in legacy_uids:
            is_valid, reason = validate_uid_for_setuid(uid, isolated_mode)
            assert is_valid, f"Legacy UID {uid} should be valid: {reason}"


# =============================================================================
# Test: Security Audit Trail
# =============================================================================

class TestSecurityAuditTrail:
    """Test that security-relevant actions are logged."""

    @pytest.mark.asyncio
    async def test_uid_generation_logged(
        self,
        user_service: UserService,
        mock_db,
        isolated_mode,
    ) -> None:
        """UID generation is logged."""
        import logging
        from io import StringIO
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = result

        # Use a custom handler to capture logs directly
        logger = logging.getLogger("src.services.user_service")
        log_capture = StringIO()
        handler = logging.StreamHandler(log_capture)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)

        try:
            await user_service._generate_next_uid(mock_db, UIDMode.ISOLATED)
            log_output = log_capture.getvalue()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        assert "Generated UID" in log_output or "50000" in log_output

    def test_blocked_uid_logged(self, isolated_mode) -> None:
        """Blocked UIDs are logged."""
        import logging
        from io import StringIO
        from src.core.uid_security import log_uid_operation

        # Use a custom handler to capture logs directly
        logger = logging.getLogger("src.core.uid_security")
        log_capture = StringIO()
        handler = logging.StreamHandler(log_capture)
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.WARNING)

        try:
            log_uid_operation("test", ROOT_UID, success=False, reason="Root blocked")
            log_output = log_capture.getvalue()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        assert "UID_SECURITY" in log_output
        assert "BLOCKED" in log_output


# =============================================================================
# Test: Configuration Validation
# =============================================================================

class TestConfigurationValidation:
    """Test UID security configuration validation."""

    def test_isolated_config_has_correct_ranges(self, isolated_mode) -> None:
        """Isolated config has correct UID ranges."""
        assert isolated_mode.isolated_uid_min == ISOLATED_UID_MIN
        assert isolated_mode.isolated_uid_max == ISOLATED_UID_MAX
        assert isolated_mode.system_uid_max == SYSTEM_UID_MAX

    def test_direct_config_has_correct_ranges(self, direct_mode) -> None:
        """Direct config has correct UID ranges."""
        assert direct_mode.direct_uid_min == DIRECT_UID_MIN
        assert direct_mode.direct_uid_max == DIRECT_UID_MAX
        assert direct_mode.system_uid_max == SYSTEM_UID_MAX

    def test_root_always_in_blocked_uids(self) -> None:
        """Root UID is always in blocked_uids set."""
        isolated = UIDSecurityConfig(mode=UIDMode.ISOLATED)
        direct = UIDSecurityConfig(mode=UIDMode.DIRECT)

        assert ROOT_UID in isolated.blocked_uids
        assert ROOT_UID in direct.blocked_uids


# =============================================================================
# Test: Bwrap Privilege Dropping via --uid/--gid Flags
# =============================================================================

class TestBwrapPrivilegeDropping:
    """Test that bwrap handles privilege dropping via --uid/--gid flags.

    The sandbox now uses 'sudo bwrap --uid <uid> --gid <gid>' instead of
    preexec_fn with os.setuid()/os.setgid(). This is because:
    1. The API runs as ag3ntum_api (UID 45045), not root
    2. Non-root processes cannot call os.setuid() without CAP_SETUID
    3. Docker capabilities don't transfer to non-root processes
    4. sudo bwrap CAN switch UIDs via its --uid/--gid flags
    """

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        """Create temporary workspace for tests."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace

    def test_bwrap_command_includes_uid_flag(self, workspace: Path) -> None:
        """Bwrap command includes --uid flag with correct value."""
        from src.core.sandbox import SandboxConfig, SandboxMount, SandboxExecutor

        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = SandboxExecutor(config, linux_uid=50000, linux_gid=50000)
        cmd = executor.build_bwrap_command(["echo", "test"], allow_network=False)

        assert "--uid" in cmd
        uid_idx = cmd.index("--uid")
        assert cmd[uid_idx + 1] == "50000"

    def test_bwrap_command_includes_gid_flag(self, workspace: Path) -> None:
        """Bwrap command includes --gid flag with correct value."""
        from src.core.sandbox import SandboxConfig, SandboxMount, SandboxExecutor

        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = SandboxExecutor(config, linux_uid=50000, linux_gid=50000)
        cmd = executor.build_bwrap_command(["echo", "test"], allow_network=False)

        assert "--gid" in cmd
        gid_idx = cmd.index("--gid")
        assert cmd[gid_idx + 1] == "50000"

    def test_bwrap_command_uid_gid_before_separator(self, workspace: Path) -> None:
        """UID/GID flags appear before -- separator (part of bwrap args, not command)."""
        from src.core.sandbox import SandboxConfig, SandboxMount, SandboxExecutor

        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = SandboxExecutor(config, linux_uid=2000, linux_gid=2000)
        cmd = executor.build_bwrap_command(["echo", "test"], allow_network=False)

        separator_idx = cmd.index("--")
        uid_idx = cmd.index("--uid")
        gid_idx = cmd.index("--gid")

        assert uid_idx < separator_idx
        assert gid_idx < separator_idx

    def test_bwrap_no_uid_gid_when_not_configured(self, workspace: Path) -> None:
        """Bwrap command omits --uid/--gid when not configured."""
        from src.core.sandbox import SandboxConfig, SandboxMount, SandboxExecutor

        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        # No linux_uid/linux_gid provided
        executor = SandboxExecutor(config)
        cmd = executor.build_bwrap_command(["echo", "test"], allow_network=False)

        assert "--uid" not in cmd
        assert "--gid" not in cmd

    def test_sudo_bwrap_path_configuration(self, workspace: Path) -> None:
        """Sandbox can be configured with 'sudo bwrap' path."""
        from src.core.sandbox import SandboxConfig, SandboxMount, SandboxExecutor

        config = SandboxConfig(
            bwrap_path="sudo bwrap",
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = SandboxExecutor(config, linux_uid=50000, linux_gid=50000)
        cmd = executor.build_bwrap_command(["echo", "test"], allow_network=False)

        # Command should start with "sudo" then "bwrap"
        assert cmd[0] == "sudo"
        assert cmd[1] == "bwrap"

    def test_uid_gid_validated_before_building_command(self, workspace: Path, isolated_mode) -> None:
        """UID/GID should be from valid ranges before being used in bwrap command."""
        from src.core.sandbox import SandboxConfig, SandboxMount, SandboxExecutor

        # Valid UID in isolated range
        valid_uid = 50000
        is_valid, _ = validate_uid_for_setuid(valid_uid, isolated_mode)
        assert is_valid, "UID 50000 should be valid in isolated mode"

        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = SandboxExecutor(config, linux_uid=valid_uid, linux_gid=valid_uid)
        cmd = executor.build_bwrap_command(["echo", "test"], allow_network=False)

        # UID should be in the command
        assert str(valid_uid) in cmd

    def test_root_uid_blocked_by_validation(self, isolated_mode) -> None:
        """Root UID (0) is blocked by validation, preventing bwrap --uid 0."""
        is_valid, reason = validate_uid_for_setuid(ROOT_UID, isolated_mode)

        assert not is_valid
        assert "SECURITY VIOLATION" in reason

    def test_system_uid_blocked_by_validation(self, isolated_mode) -> None:
        """System UIDs (1-999) are blocked by validation."""
        for uid in [1, 100, 500, 999]:
            is_valid, reason = validate_uid_for_setuid(uid, isolated_mode)
            assert not is_valid, f"System UID {uid} should be blocked"
