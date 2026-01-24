"""
Security Tests for UID Mapping Modes and Privilege Escalation Prevention.

This test suite validates the hybrid UID security model with two modes:

Mode A: Isolated Range (Default)
    - UIDs allocated from 50000-60000 (new) or 2000-49999 (legacy)
    - Tests verify UIDs don't escape the isolated range
    - Multi-tenant safe: container UIDs don't map to real host users

Mode B: Direct Host Mapping (Opt-in)
    - UIDs from 1000-65533 map to host system UIDs
    - Tests verify root (0) and system accounts (1-999) are blocked
    - Single-tenant/dev: simpler file permissions

Security Invariants (tested in BOTH modes):
    1. UID 0 (root) is NEVER allowed - kernel-level block via seccomp
    2. System UIDs (1-999) are blocked
    3. Each session can only setuid to its own authenticated UID
    4. Cross-user file access is prevented

Run with:
    pytest tests/security/test_uid_security.py -v

Inside Docker:
    docker exec project-ag3ntum-api-1 python -m pytest tests/security/test_uid_security.py -v

Or via run.sh:
    ./run.sh test --subset uid_security
"""
import os
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch, AsyncMock
import tempfile

import pytest

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.uid_security import (
    UIDMode,
    UIDSecurityConfig,
    UIDSecurityError,
    get_uid_security_config,
    set_uid_security_config,
    validate_uid_for_setuid,
    validate_gid_for_setgid,
    check_setuid_capability,
    generate_seccomp_profile,
)
from src.core.sandbox import (
    create_demote_fn,
    UIDValidationError,
)


# =============================================================================
# Constants
# =============================================================================

# Root UID (must ALWAYS be blocked)
ROOT_UID = 0

# System UID range (must be blocked)
SYSTEM_UID_MAX = 999

# API user UID (should never be used for sandboxed commands)
API_USER_UID = 45045

# Isolated mode range
ISOLATED_UID_MIN = 50000
ISOLATED_UID_MAX = 60000

# Legacy range (still valid in isolated mode)
LEGACY_UID_MIN = 2000
LEGACY_UID_MAX = 49999

# Direct mode range
DIRECT_UID_MIN = 1000
DIRECT_UID_MAX = 65533


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def isolated_config() -> UIDSecurityConfig:
    """Create isolated mode configuration."""
    return UIDSecurityConfig(mode=UIDMode.ISOLATED)


@pytest.fixture
def direct_config() -> UIDSecurityConfig:
    """Create direct mode configuration."""
    return UIDSecurityConfig(mode=UIDMode.DIRECT)


@pytest.fixture(autouse=True)
def reset_global_config():
    """Reset global config after each test."""
    yield
    # Reset to default
    set_uid_security_config(UIDSecurityConfig(mode=UIDMode.ISOLATED))


# =============================================================================
# Test: UID Validation - Root Always Blocked
# =============================================================================

class TestRootUIDAlwaysBlocked:
    """Test that UID 0 (root) is ALWAYS blocked regardless of mode.

    This is the most critical security invariant - root must NEVER be allowed.
    """

    def test_root_blocked_in_isolated_mode(self, isolated_config: UIDSecurityConfig) -> None:
        """Root UID is blocked in isolated mode."""
        is_valid, reason = validate_uid_for_setuid(ROOT_UID, isolated_config)

        assert not is_valid, "Root UID should be blocked"
        assert "SECURITY VIOLATION" in reason
        assert "UID 0" in reason or "root" in reason.lower()

    def test_root_blocked_in_direct_mode(self, direct_config: UIDSecurityConfig) -> None:
        """Root UID is blocked in direct mode."""
        is_valid, reason = validate_uid_for_setuid(ROOT_UID, direct_config)

        assert not is_valid, "Root UID should be blocked"
        assert "SECURITY VIOLATION" in reason
        assert "UID 0" in reason or "root" in reason.lower()

    def test_root_gid_blocked_in_isolated_mode(self, isolated_config: UIDSecurityConfig) -> None:
        """Root GID is blocked in isolated mode."""
        is_valid, reason = validate_gid_for_setgid(ROOT_UID, isolated_config)

        assert not is_valid, "Root GID should be blocked"
        assert "SECURITY VIOLATION" in reason

    def test_root_gid_blocked_in_direct_mode(self, direct_config: UIDSecurityConfig) -> None:
        """Root GID is blocked in direct mode."""
        is_valid, reason = validate_gid_for_setgid(ROOT_UID, direct_config)

        assert not is_valid, "Root GID should be blocked"
        assert "SECURITY VIOLATION" in reason

    def test_demote_fn_rejects_root(self) -> None:
        """create_demote_fn raises error for root UID."""
        with pytest.raises(UIDValidationError) as exc_info:
            create_demote_fn(ROOT_UID, ROOT_UID)

        assert "SECURITY VIOLATION" in str(exc_info.value) or "UID 0" in str(exc_info.value)


# =============================================================================
# Test: System UIDs Always Blocked
# =============================================================================

class TestSystemUIDsBlocked:
    """Test that system UIDs (1-999) are blocked in both modes."""

    @pytest.mark.parametrize("uid", [1, 10, 100, 500, 999])
    def test_system_uids_blocked_isolated(self, isolated_config: UIDSecurityConfig, uid: int) -> None:
        """System UIDs are blocked in isolated mode."""
        is_valid, reason = validate_uid_for_setuid(uid, isolated_config)

        assert not is_valid, f"System UID {uid} should be blocked"
        assert "system" in reason.lower() or str(SYSTEM_UID_MAX) in reason

    @pytest.mark.parametrize("uid", [1, 10, 100, 500, 999])
    def test_system_uids_blocked_direct(self, direct_config: UIDSecurityConfig, uid: int) -> None:
        """System UIDs are blocked in direct mode."""
        is_valid, reason = validate_uid_for_setuid(uid, direct_config)

        assert not is_valid, f"System UID {uid} should be blocked"
        assert "system" in reason.lower() or str(SYSTEM_UID_MAX) in reason

    @pytest.mark.parametrize("gid", [1, 10, 100, 500, 999])
    def test_system_gids_blocked_isolated(self, isolated_config: UIDSecurityConfig, gid: int) -> None:
        """System GIDs are blocked in isolated mode."""
        is_valid, reason = validate_gid_for_setgid(gid, isolated_config)

        assert not is_valid, f"System GID {gid} should be blocked"

    @pytest.mark.parametrize("gid", [1, 10, 100, 500, 999])
    def test_system_gids_blocked_direct(self, direct_config: UIDSecurityConfig, gid: int) -> None:
        """System GIDs are blocked in direct mode."""
        is_valid, reason = validate_gid_for_setgid(gid, direct_config)

        assert not is_valid, f"System GID {gid} should be blocked"


# =============================================================================
# Test: Isolated Mode UID Range
# =============================================================================

class TestIsolatedModeRange:
    """Test isolated mode UID range enforcement."""

    def test_isolated_range_boundaries(self, isolated_config: UIDSecurityConfig) -> None:
        """Verify isolated mode uses correct range."""
        min_uid, max_uid = isolated_config.get_uid_range()

        assert min_uid == ISOLATED_UID_MIN
        assert max_uid == ISOLATED_UID_MAX

    @pytest.mark.parametrize("uid", [50000, 50001, 55000, 59999, 60000])
    def test_valid_isolated_uids(self, isolated_config: UIDSecurityConfig, uid: int) -> None:
        """UIDs in isolated range (50000-60000) are valid."""
        is_valid, reason = validate_uid_for_setuid(uid, isolated_config)

        assert is_valid, f"UID {uid} should be valid in isolated mode: {reason}"

    @pytest.mark.parametrize("uid", [2000, 10000, 30000, 49999])
    def test_legacy_uids_valid_in_isolated(self, isolated_config: UIDSecurityConfig, uid: int) -> None:
        """Legacy UIDs (2000-49999) are still valid in isolated mode."""
        is_valid, reason = validate_uid_for_setuid(uid, isolated_config)

        assert is_valid, f"Legacy UID {uid} should be valid: {reason}"

    @pytest.mark.parametrize("uid", [60001, 65000, 65534, 100000])
    def test_uids_above_isolated_range_blocked(self, isolated_config: UIDSecurityConfig, uid: int) -> None:
        """UIDs above isolated range are blocked."""
        # Disable legacy UIDs to test strict isolated range
        isolated_config.allow_legacy_uids = False

        is_valid, reason = validate_uid_for_setuid(uid, isolated_config)

        assert not is_valid, f"UID {uid} should be blocked above isolated range"

    def test_api_user_uid_blocked(self, isolated_config: UIDSecurityConfig) -> None:
        """API user UID (45045) is blocked for sandbox commands."""
        is_valid, reason = validate_uid_for_setuid(API_USER_UID, isolated_config)

        assert not is_valid, "API user UID should be blocked for sandbox"
        assert "API user" in reason


# =============================================================================
# Test: Direct Mode UID Range
# =============================================================================

class TestDirectModeRange:
    """Test direct mode UID range enforcement."""

    def test_direct_range_boundaries(self, direct_config: UIDSecurityConfig) -> None:
        """Verify direct mode uses correct range."""
        min_uid, max_uid = direct_config.get_uid_range()

        assert min_uid == DIRECT_UID_MIN
        assert max_uid == DIRECT_UID_MAX

    @pytest.mark.parametrize("uid", [1000, 1001, 5000, 50000, 65533])
    def test_valid_direct_uids(self, direct_config: UIDSecurityConfig, uid: int) -> None:
        """UIDs in direct range (1000-65533) are valid."""
        is_valid, reason = validate_uid_for_setuid(uid, direct_config)

        assert is_valid, f"UID {uid} should be valid in direct mode: {reason}"

    @pytest.mark.parametrize("uid", [65534, 65535, 100000])
    def test_uids_above_direct_range_blocked(self, direct_config: UIDSecurityConfig, uid: int) -> None:
        """UIDs above direct range are blocked."""
        is_valid, reason = validate_uid_for_setuid(uid, direct_config)

        assert not is_valid, f"UID {uid} should be blocked above direct range"


# =============================================================================
# Test: Session UID Principle of Least Privilege
# =============================================================================

class TestSessionUIDLeastPrivilege:
    """Test that sessions can only use their own authenticated UID."""

    def test_session_uid_must_match(self, isolated_config: UIDSecurityConfig) -> None:
        """When session_uid is provided, target UID must match."""
        session_uid = 50001

        # Same UID should be valid
        is_valid, _ = validate_uid_for_setuid(50001, isolated_config, session_uid=session_uid)
        assert is_valid

        # Different UID should be blocked
        is_valid, reason = validate_uid_for_setuid(50002, isolated_config, session_uid=session_uid)
        assert not is_valid
        assert "does not match session" in reason

    def test_session_uid_enforcement_direct_mode(self, direct_config: UIDSecurityConfig) -> None:
        """Session UID enforcement works in direct mode."""
        session_uid = 1000

        # Same UID should be valid
        is_valid, _ = validate_uid_for_setuid(1000, direct_config, session_uid=session_uid)
        assert is_valid

        # Different UID should be blocked (even if in valid range)
        is_valid, reason = validate_uid_for_setuid(1001, direct_config, session_uid=session_uid)
        assert not is_valid
        assert "does not match session" in reason

    def test_demote_fn_validates_session_uid(self) -> None:
        """create_demote_fn validates session_uid if provided."""
        # Should succeed - matching UIDs
        demote = create_demote_fn(50001, 50001, session_uid=50001)
        assert callable(demote)

        # Should fail - mismatched UIDs
        with pytest.raises(UIDValidationError) as exc_info:
            create_demote_fn(50002, 50002, session_uid=50001)

        assert "does not match session" in str(exc_info.value)


# =============================================================================
# Test: Seccomp Profile Generation
# =============================================================================

class TestSeccompProfileGeneration:
    """Test seccomp profile generation for both modes."""

    def test_isolated_profile_blocks_root(self) -> None:
        """Isolated seccomp profile blocks root UID."""
        profile = generate_seccomp_profile(UIDMode.ISOLATED)

        # Find the rule that blocks UID 0
        root_block_rules = [
            rule for rule in profile["syscalls"]
            if "setuid" in str(rule.get("names", [])) and
               any(arg.get("value") == 0 for arg in rule.get("args", []))
        ]

        assert len(root_block_rules) > 0, "Profile should block root UID"

    def test_direct_profile_blocks_root(self) -> None:
        """Direct seccomp profile blocks root UID."""
        profile = generate_seccomp_profile(UIDMode.DIRECT)

        # Find the rule that blocks UID 0
        root_block_rules = [
            rule for rule in profile["syscalls"]
            if "setuid" in str(rule.get("names", [])) and
               any(arg.get("value") == 0 for arg in rule.get("args", []))
        ]

        assert len(root_block_rules) > 0, "Profile should block root UID"

    def test_isolated_profile_blocks_system_accounts(self) -> None:
        """Isolated seccomp profile blocks system accounts."""
        profile = generate_seccomp_profile(UIDMode.ISOLATED)

        # Find rules that block system UIDs
        system_block_rules = [
            rule for rule in profile["syscalls"]
            if any(arg.get("value") == 999 and arg.get("op") == "SCMP_CMP_LE"
                   for arg in rule.get("args", []))
        ]

        assert len(system_block_rules) > 0, "Profile should block system accounts"

    def test_profile_writes_to_file(self) -> None:
        """Profile can be written to file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = Path(f.name)

        try:
            profile = generate_seccomp_profile(UIDMode.ISOLATED, output_path=temp_path)

            assert temp_path.exists()

            import json
            with open(temp_path) as f:
                written_profile = json.load(f)

            assert written_profile["defaultAction"] == "SCMP_ACT_ALLOW"
            assert len(written_profile["syscalls"]) > 0
        finally:
            temp_path.unlink(missing_ok=True)


# =============================================================================
# Test: Configuration Management
# =============================================================================

class TestConfigurationManagement:
    """Test UID security configuration management."""

    def test_default_mode_is_isolated(self) -> None:
        """Default mode should be isolated."""
        config = UIDSecurityConfig()
        assert config.mode == UIDMode.ISOLATED

    def test_config_from_environment_isolated(self) -> None:
        """Config loads isolated mode from environment."""
        with patch.dict(os.environ, {"AG3NTUM_UID_MODE": "isolated"}):
            from src.core.uid_security import _load_uid_security_config
            config = _load_uid_security_config()
            assert config.mode == UIDMode.ISOLATED

    def test_config_from_environment_direct(self) -> None:
        """Config loads direct mode from environment."""
        with patch.dict(os.environ, {"AG3NTUM_UID_MODE": "direct"}):
            from src.core.uid_security import _load_uid_security_config
            config = _load_uid_security_config()
            assert config.mode == UIDMode.DIRECT

    def test_set_global_config(self) -> None:
        """Global config can be set programmatically."""
        new_config = UIDSecurityConfig(mode=UIDMode.DIRECT)
        set_uid_security_config(new_config)

        current = get_uid_security_config()
        assert current.mode == UIDMode.DIRECT

    def test_config_serialization(self, isolated_config: UIDSecurityConfig) -> None:
        """Config can be serialized to dict."""
        config_dict = isolated_config.to_dict()

        assert config_dict["mode"] == "isolated"
        assert config_dict["isolated_uid_min"] == ISOLATED_UID_MIN
        assert config_dict["isolated_uid_max"] == ISOLATED_UID_MAX
        assert 0 in config_dict["blocked_uids"]


# =============================================================================
# Test: Capability Checking
# =============================================================================

class TestCapabilityChecking:
    """Test CAP_SETUID/CAP_SETGID capability detection."""

    def test_capability_check_returns_tuple(self) -> None:
        """Capability check returns (has_cap, reason) tuple."""
        result = check_setuid_capability()

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)

    def test_capability_check_on_non_linux(self) -> None:
        """Capability check handles non-Linux systems gracefully."""
        with patch("platform.system", return_value="Darwin"):
            has_cap, reason = check_setuid_capability()

            assert not has_cap
            assert "Linux" in reason


# =============================================================================
# Test: Integration with create_demote_fn
# =============================================================================

class TestDemoteFunctionIntegration:
    """Test create_demote_fn integration with UID security."""

    def test_demote_fn_validates_by_default(self) -> None:
        """create_demote_fn validates UID by default."""
        # Valid UID should work
        demote = create_demote_fn(50001, 50001)
        assert callable(demote)

    def test_demote_fn_blocks_root(self) -> None:
        """create_demote_fn blocks root UID."""
        with pytest.raises(UIDValidationError):
            create_demote_fn(0, 0)

    def test_demote_fn_blocks_system_uids(self) -> None:
        """create_demote_fn blocks system UIDs."""
        with pytest.raises(UIDValidationError):
            create_demote_fn(100, 100)

    def test_demote_fn_validation_can_be_disabled(self) -> None:
        """Validation can be disabled for testing."""
        # This should NOT raise even for invalid UIDs
        # ONLY for testing purposes
        demote = create_demote_fn(0, 0, validate=False)
        assert callable(demote)

    def test_demote_fn_with_session_uid_validation(self) -> None:
        """create_demote_fn validates session UID when provided."""
        # Matching session UID - should work
        demote = create_demote_fn(50001, 50001, session_uid=50001)
        assert callable(demote)

        # Mismatched session UID - should fail
        with pytest.raises(UIDValidationError) as exc_info:
            create_demote_fn(50002, 50002, session_uid=50001)

        assert "session" in str(exc_info.value).lower()


# =============================================================================
# Test: Edge Cases and Boundary Conditions
# =============================================================================

class TestEdgeCasesAndBoundaries:
    """Test edge cases and boundary conditions."""

    def test_boundary_uid_999(self, isolated_config: UIDSecurityConfig) -> None:
        """UID 999 (last system UID) is blocked."""
        is_valid, _ = validate_uid_for_setuid(999, isolated_config)
        assert not is_valid

    def test_boundary_uid_1000(self, direct_config: UIDSecurityConfig) -> None:
        """UID 1000 (first normal user) is valid in direct mode."""
        is_valid, _ = validate_uid_for_setuid(1000, direct_config)
        assert is_valid

    def test_boundary_uid_2000(self, isolated_config: UIDSecurityConfig) -> None:
        """UID 2000 (legacy minimum) is valid in isolated mode."""
        is_valid, _ = validate_uid_for_setuid(2000, isolated_config)
        assert is_valid

    def test_boundary_uid_50000(self, isolated_config: UIDSecurityConfig) -> None:
        """UID 50000 (new isolated minimum) is valid."""
        is_valid, _ = validate_uid_for_setuid(50000, isolated_config)
        assert is_valid

    def test_boundary_uid_60000(self, isolated_config: UIDSecurityConfig) -> None:
        """UID 60000 (isolated maximum) is valid."""
        is_valid, _ = validate_uid_for_setuid(60000, isolated_config)
        assert is_valid

    def test_boundary_uid_65533(self, direct_config: UIDSecurityConfig) -> None:
        """UID 65533 (direct maximum) is valid."""
        is_valid, _ = validate_uid_for_setuid(65533, direct_config)
        assert is_valid

    def test_boundary_uid_65534(self, direct_config: UIDSecurityConfig) -> None:
        """UID 65534 (nobody) is blocked in direct mode."""
        is_valid, _ = validate_uid_for_setuid(65534, direct_config)
        assert not is_valid

    def test_negative_uid_blocked(self, isolated_config: UIDSecurityConfig) -> None:
        """Negative UIDs are blocked."""
        is_valid, _ = validate_uid_for_setuid(-1, isolated_config)
        assert not is_valid


# =============================================================================
# Test: Audit Logging
# =============================================================================

class TestAuditLogging:
    """Test UID security audit logging."""

    def test_log_uid_operation_success(self) -> None:
        """Successful UID operations are logged."""
        import logging
        from io import StringIO
        from src.core.uid_security import log_uid_operation

        # Use a custom handler to capture logs directly
        logger = logging.getLogger("src.core.uid_security")
        log_capture = StringIO()
        handler = logging.StreamHandler(log_capture)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.INFO)

        try:
            log_uid_operation("test_op", 50001, session_uid=50001, success=True)
            log_output = log_capture.getvalue()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        assert "UID_SECURITY" in log_output
        assert "SUCCESS" in log_output
        assert "50001" in log_output

    def test_log_uid_operation_failure(self) -> None:
        """Failed UID operations are logged with reason."""
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
            log_uid_operation(
                "test_op", 0, session_uid=50001,
                success=False, reason="Root blocked"
            )
            log_output = log_capture.getvalue()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

        assert "UID_SECURITY" in log_output
        assert "BLOCKED" in log_output
        assert "Root blocked" in log_output
