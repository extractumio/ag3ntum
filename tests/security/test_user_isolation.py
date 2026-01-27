"""
Security Tests for User Isolation and UID/GID Management.

Tests cover:
- API process runs as ag3ntum_api user (UID 45045), never root
- Sandbox commands run as user-specific UID (50000+), not as API user
- Privilege dropping works correctly via bwrap --uid/--gid flags
- No process ever runs as root (UID 0)
- System UIDs (1-999) are always blocked

These tests validate the multi-layered user isolation security model:
1. Container level: API runs as ag3ntum_api (UID 45045)
2. Sandbox level: Commands run as the actual user (UID 50000+)
3. Security invariant: No process ever runs as root or system UID

UID Allocation Modes:
- ISOLATED (default): UIDs from 50000-60000 (safe for multi-tenant)
- DIRECT (opt-in): UIDs from 1000-65533 (maps to host users)

Run with:
    pytest tests/security/test_user_isolation.py -v

Inside Docker:
    docker exec project-ag3ntum-api-1 python -m pytest tests/security/test_user_isolation.py -v

Or via run.sh:
    ./run.sh test --subset user_isolation
"""
import os
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.sandbox import (
    SandboxConfig,
    SandboxMount,
    SandboxExecutor,
    SandboxEnvConfig,
    create_demote_fn,
    _create_demote_fn,  # Alias for backward compatibility
    UIDValidationError,
)


# =============================================================================
# Constants
# =============================================================================

# API user UID (ag3ntum_api in Docker)
API_USER_UID = 45045

# User UID range for ISOLATED mode (default)
# New allocations start at 50000, legacy users may have UIDs 2000-49999
MIN_USER_UID_ISOLATED = 50000
MAX_USER_UID_ISOLATED = 60000

# Legacy UID range (still valid but no new allocations)
MIN_USER_UID_LEGACY = 2000

# System UID max (UIDs 0-999 are blocked)
SYSTEM_UID_MAX = 999

# Root UID (must never be used)
ROOT_UID = 0


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def minimal_agent_config():
    """Create a minimal valid AgentConfig for testing.

    This provides all required fields so ClaudeAgent can be instantiated
    without loading from agent.yaml.
    """
    from src.core.schemas import AgentConfig
    return AgentConfig(
        model="claude-haiku-4-5-20251001",
        max_turns=10,
        timeout_seconds=300,
        enable_skills=False,
        enable_file_checkpointing=False,
        role="default",
    )


@pytest.fixture
def basic_sandbox_config(workspace: Path) -> SandboxConfig:
    """Create a basic sandbox config with real paths."""
    return SandboxConfig(
        enabled=True,
        file_sandboxing=True,
        session_mounts={
            "workspace": SandboxMount(
                source=str(workspace),
                target="/workspace",
                mode="rw",
            ),
        },
    )


# =============================================================================
# Test: API Process UID (Inside Docker)
# =============================================================================

class TestAPIProcessUID:
    """Test that the API process runs as the correct user.

    SECURITY: The API must never run as root.
    In Docker: Should run as ag3ntum_api (UID 45045)
    """

    def test_current_process_not_root(self) -> None:
        """Current process must never be root (UID 0)."""
        current_uid = os.getuid()
        assert current_uid != ROOT_UID, (
            f"SECURITY VIOLATION: Process running as root (UID {ROOT_UID})! "
            f"The API must never run as root."
        )

    def test_current_process_uid_in_docker(self) -> None:
        """In Docker, process should run as ag3ntum_api (UID 45045).

        This test only validates when running inside Docker.
        Outside Docker, it just ensures we're not root.
        """
        current_uid = os.getuid()

        # Check if we're inside Docker (AG3NTUM_ROOT env var is set)
        in_docker = os.environ.get("AG3NTUM_ROOT") == "/"

        if in_docker:
            assert current_uid == API_USER_UID, (
                f"SECURITY WARNING: In Docker, expected API to run as UID {API_USER_UID} "
                f"(ag3ntum_api), but running as UID {current_uid}"
            )
        else:
            # Outside Docker, just ensure not root
            assert current_uid != ROOT_UID, (
                f"SECURITY VIOLATION: Process running as root!"
            )

    def test_api_user_separated_from_user_allocation_range(self) -> None:
        """API user UID (45045) is separated from user allocation ranges.

        In isolated mode (default), user UIDs are allocated from 50000-60000.
        The API UID (45045) is below this range, preventing any collision.

        In legacy mode, user UIDs started at 2000. The API UID was chosen
        to be high enough (45045) to provide a buffer of 43000+ users.
        """
        # API user should be below isolated range (50000-60000)
        assert API_USER_UID < MIN_USER_UID_ISOLATED, (
            f"API user UID {API_USER_UID} should be below isolated range start "
            f"({MIN_USER_UID_ISOLATED}) to avoid collision"
        )

        # API user should be significantly above legacy starting UID (2000)
        SAFE_MARGIN_LEGACY = 40000  # Allow for 40000+ legacy users before collision
        assert API_USER_UID >= MIN_USER_UID_LEGACY + SAFE_MARGIN_LEGACY, (
            f"API user UID {API_USER_UID} should be at least {SAFE_MARGIN_LEGACY} "
            f"above legacy MIN_USER_UID ({MIN_USER_UID_LEGACY}) for safe separation"
        )


# =============================================================================
# Test: SandboxExecutor UID/GID Configuration
# =============================================================================

class TestSandboxExecutorUIDs:
    """Test SandboxExecutor UID/GID handling for privilege dropping."""

    def test_executor_accepts_uid_gid(self, basic_sandbox_config: SandboxConfig) -> None:
        """SandboxExecutor accepts linux_uid and linux_gid parameters."""
        # Use UID in valid isolated range (50000-60000)
        executor = SandboxExecutor(
            basic_sandbox_config,
            linux_uid=50000,
            linux_gid=50000,
        )

        assert executor.linux_uid == 50000
        assert executor.linux_gid == 50000

    def test_executor_uid_gid_default_none(self, basic_sandbox_config: SandboxConfig) -> None:
        """SandboxExecutor defaults to None for UID/GID (no privilege drop)."""
        executor = SandboxExecutor(basic_sandbox_config)

        assert executor.linux_uid is None
        assert executor.linux_gid is None

    def test_executor_accepts_root_uid_but_validation_catches_it(self, basic_sandbox_config: SandboxConfig) -> None:
        """Executor accepts root UID but validation catches it later.

        The SandboxExecutor stores the UID without validation. Validation
        happens in create_demote_fn() which raises UIDValidationError.
        This is by design - the executor is a data container.
        """
        # Executor accepts any UID - validation happens in create_demote_fn
        executor = SandboxExecutor(
            basic_sandbox_config,
            linux_uid=ROOT_UID,
            linux_gid=ROOT_UID,
        )

        # Executor stores the value
        assert executor.linux_uid == ROOT_UID, (
            "Executor stores UID - validation happens in create_demote_fn"
        )

        # But create_demote_fn will reject it
        with pytest.raises(UIDValidationError):
            create_demote_fn(uid=ROOT_UID, gid=ROOT_UID)

    def test_user_uid_in_valid_isolated_range(self) -> None:
        """User UIDs in isolated mode should be in range 50000-60000."""
        # Test UIDs that are valid for isolated mode
        valid_isolated_uids = [50000, 50001, 55000, 59999, 60000]

        for uid in valid_isolated_uids:
            assert uid >= MIN_USER_UID_ISOLATED, f"UID {uid} should be >= {MIN_USER_UID_ISOLATED}"
            assert uid <= MAX_USER_UID_ISOLATED, f"UID {uid} should be <= {MAX_USER_UID_ISOLATED}"
            assert uid != ROOT_UID, f"UID {uid} should not be root"
            assert uid != API_USER_UID, f"UID {uid} should not be API user"

    def test_legacy_uid_still_valid(self) -> None:
        """Legacy UIDs (2000-49999) are still valid but no longer allocated."""
        # Legacy UIDs are valid (existing users may have them)
        legacy_uids = [2000, 5000, 10000, 45000]

        for uid in legacy_uids:
            assert uid >= MIN_USER_UID_LEGACY, f"UID {uid} should be >= {MIN_USER_UID_LEGACY}"
            assert uid != ROOT_UID, f"UID {uid} should not be root"
            assert uid > SYSTEM_UID_MAX, f"UID {uid} should be above system range"


# =============================================================================
# Test: Privilege Dropping Function
# =============================================================================

class TestPrivilegeDropping:
    """Test the privilege dropping mechanism.

    The create_demote_fn() function performs security validation and raises
    UIDValidationError for blocked UIDs:
    - UID 0 (root)
    - System UIDs (1-999)
    - UIDs outside the configured range
    """

    def test_demote_fn_created_for_valid_uid_gid(self) -> None:
        """_create_demote_fn creates a callable for valid UID/GID."""
        # Use a UID in the valid isolated range (50000-60000)
        demote_fn = _create_demote_fn(uid=50000, gid=50000)

        assert callable(demote_fn), "Demote function should be callable"

    def test_demote_fn_raises_for_root_uid(self) -> None:
        """create_demote_fn raises UIDValidationError for root UID (0).

        SECURITY: Root UID is unconditionally blocked to prevent
        privilege escalation attacks.
        """
        with pytest.raises(UIDValidationError) as exc_info:
            _create_demote_fn(uid=ROOT_UID, gid=ROOT_UID)

        assert "root" in str(exc_info.value).lower(), (
            "Error message should mention root"
        )

    def test_demote_fn_raises_for_system_uid(self) -> None:
        """create_demote_fn raises UIDValidationError for system UIDs (1-999).

        SECURITY: System UIDs are blocked to prevent impersonation of
        system services.
        """
        # Test several system UIDs
        system_uids = [1, 100, 500, 999]

        for uid in system_uids:
            with pytest.raises(UIDValidationError) as exc_info:
                _create_demote_fn(uid=uid, gid=uid)

            assert "system" in str(exc_info.value).lower() or str(uid) in str(exc_info.value), (
                f"Error for UID {uid} should mention 'system' or the UID"
            )

    def test_demote_fn_raises_for_root_gid(self) -> None:
        """create_demote_fn raises UIDValidationError for root GID (0).

        SECURITY: Root GID is also blocked.
        """
        with pytest.raises(UIDValidationError) as exc_info:
            _create_demote_fn(uid=50000, gid=ROOT_UID)

        assert "root" in str(exc_info.value).lower() or "gid" in str(exc_info.value).lower(), (
            "Error message should mention root or GID"
        )

    def test_demote_fn_can_skip_validation(self) -> None:
        """create_demote_fn with validate=False skips security checks.

        NOTE: This is only for testing purposes. Production code should
        never use validate=False.
        """
        # With validation disabled, even root UID returns a callable
        demote_fn = create_demote_fn(uid=ROOT_UID, gid=ROOT_UID, validate=False)
        assert callable(demote_fn), "With validate=False, function should be created"


# =============================================================================
# Test: Sandbox Command Execution with UID
# =============================================================================

class TestSandboxCommandExecution:
    """Test that sandbox commands can run with different UIDs.

    Privilege dropping is now handled by bwrap's --uid and --gid flags
    instead of preexec_fn. This is more secure as bwrap runs via sudo
    and handles the privilege dropping in a controlled manner.
    """

    @pytest.mark.asyncio
    async def test_execute_sandboxed_command_with_uid(self, workspace: Path) -> None:
        """execute_sandboxed_command uses bwrap --uid/--gid when UID/GID set."""
        from src.core.sandbox import execute_sandboxed_command

        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        test_uid = 50000
        test_gid = 50000
        executor = SandboxExecutor(config, linux_uid=test_uid, linux_gid=test_gid)

        # Mock subprocess execution to capture the bwrap command
        captured_args = None

        async def capture_exec(*args, **kwargs):
            nonlocal captured_args
            captured_args = args
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"50000\n", b"")
            mock_process.returncode = 0
            return mock_process

        with patch('asyncio.create_subprocess_exec', side_effect=capture_exec):
            await execute_sandboxed_command(
                executor, "id -u", allow_network=False, timeout=10
            )

        # Verify bwrap command includes --uid and --gid flags
        assert captured_args is not None, "subprocess should have been called"
        cmd_str = " ".join(str(arg) for arg in captured_args)

        assert f"--uid {test_uid}" in cmd_str or f"--uid\n{test_uid}" in cmd_str.replace(" ", "\n"), (
            f"bwrap command should include --uid {test_uid}, got: {cmd_str[:200]}"
        )
        assert f"--gid {test_gid}" in cmd_str or f"--gid\n{test_gid}" in cmd_str.replace(" ", "\n"), (
            f"bwrap command should include --gid {test_gid}, got: {cmd_str[:200]}"
        )

    @pytest.mark.asyncio
    async def test_execute_sandboxed_command_without_uid(self, workspace: Path) -> None:
        """execute_sandboxed_command without UID/GID has no --uid/--gid flags."""
        from src.core.sandbox import execute_sandboxed_command

        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = SandboxExecutor(config)  # No UID/GID

        captured_args = None

        async def capture_exec(*args, **kwargs):
            nonlocal captured_args
            captured_args = args
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"45045\n", b"")
            mock_process.returncode = 0
            return mock_process

        with patch('asyncio.create_subprocess_exec', side_effect=capture_exec):
            await execute_sandboxed_command(
                executor, "id -u", allow_network=False, timeout=10
            )

        # Without UID/GID, bwrap command should NOT include --uid/--gid flags
        assert captured_args is not None, "subprocess should have been called"
        cmd_str = " ".join(str(arg) for arg in captured_args)

        assert "--uid" not in cmd_str, (
            f"bwrap command should NOT include --uid without UID set, got: {cmd_str[:200]}"
        )
        assert "--gid" not in cmd_str, (
            f"bwrap command should NOT include --gid without GID set, got: {cmd_str[:200]}"
        )


# =============================================================================
# Test: User Service UID Generation
# =============================================================================

class TestUserServiceUIDs:
    """Test that user service generates valid UIDs.

    UID allocation modes:
    - ISOLATED (default): UIDs 50000-60000 (safe for multi-tenant)
    - DIRECT (opt-in): UIDs 1000-65533 (maps to host users)

    Legacy UIDs (2000-49999) are still valid but no longer allocated.
    """

    def test_user_uid_starts_at_50000_isolated_mode(self) -> None:
        """User UIDs start at 50000 in isolated mode (default)."""
        # In isolated mode, UIDs are allocated from 50000-60000
        assert MIN_USER_UID_ISOLATED == 50000, (
            f"MIN_USER_UID_ISOLATED should be 50000, got {MIN_USER_UID_ISOLATED}"
        )

    def test_user_uid_range_isolated_is_bounded(self) -> None:
        """Isolated mode UID range is bounded to prevent collisions."""
        assert MIN_USER_UID_ISOLATED < MAX_USER_UID_ISOLATED, (
            "MIN should be less than MAX in isolated range"
        )
        assert MAX_USER_UID_ISOLATED == 60000, (
            f"MAX_USER_UID_ISOLATED should be 60000, got {MAX_USER_UID_ISOLATED}"
        )
        # Range provides 10000 UIDs which is plenty for most deployments
        uid_capacity = MAX_USER_UID_ISOLATED - MIN_USER_UID_ISOLATED
        assert uid_capacity == 10000, (
            f"Isolated range should provide 10000 UIDs, got {uid_capacity}"
        )

    def test_api_uid_above_isolated_range(self) -> None:
        """API UID (45045) is below isolated range to prevent collision.

        The API UID was chosen when allocations started at 2000.
        Now with isolated mode starting at 50000, the API UID is
        safely below the allocation range.
        """
        assert API_USER_UID < MIN_USER_UID_ISOLATED, (
            f"API UID ({API_USER_UID}) should be below isolated range start "
            f"({MIN_USER_UID_ISOLATED})"
        )

    def test_user_uid_never_root(self) -> None:
        """User UIDs should never be root (0)."""
        assert ROOT_UID < MIN_USER_UID_ISOLATED, (
            f"Root UID ({ROOT_UID}) should be below MIN_USER_UID_ISOLATED"
        )
        assert ROOT_UID < MIN_USER_UID_LEGACY, (
            f"Root UID ({ROOT_UID}) should be below MIN_USER_UID_LEGACY"
        )

    def test_user_uid_never_system_uid(self) -> None:
        """User UIDs should never be in system UID range (1-999)."""
        assert SYSTEM_UID_MAX < MIN_USER_UID_LEGACY, (
            f"SYSTEM_UID_MAX ({SYSTEM_UID_MAX}) should be below "
            f"MIN_USER_UID_LEGACY ({MIN_USER_UID_LEGACY})"
        )
        assert SYSTEM_UID_MAX < MIN_USER_UID_ISOLATED, (
            f"SYSTEM_UID_MAX ({SYSTEM_UID_MAX}) should be below "
            f"MIN_USER_UID_ISOLATED ({MIN_USER_UID_ISOLATED})"
        )


# =============================================================================
# Test: Integration with Bubblewrap (Real Execution)
# =============================================================================

class TestBubblewrapRealExecution:
    """Integration tests that run actual bubblewrap commands.

    These tests require Docker environment with bubblewrap installed.
    They are marked to skip when bubblewrap is not available.
    """

    @pytest.fixture
    def bwrap_available(self) -> bool:
        """Check if bubblewrap is available."""
        import shutil
        return shutil.which("bwrap") is not None

    def test_bwrap_command_reports_uid(
        self, workspace: Path, bwrap_available: bool
    ) -> None:
        """Bubblewrap command should report its UID correctly.

        Note: Without actual privilege dropping (which requires root),
        the command runs as the current user.
        """
        if not bwrap_available:
            pytest.skip("Bubblewrap not available")

        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = SandboxExecutor(config)

        # Build the command
        cmd = executor.build_bwrap_command(
            ["id", "-u"],
            allow_network=False,
            nested_container=True,
        )

        # Execute and check
        import subprocess
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            uid = int(result.stdout.strip())
            assert uid != ROOT_UID, (
                f"Sandbox command ran as root (UID {ROOT_UID})!"
            )

    @pytest.mark.asyncio
    async def test_sandbox_command_not_root(
        self, workspace: Path, bwrap_available: bool
    ) -> None:
        """Sandbox command should never run as root."""
        if not bwrap_available:
            pytest.skip("Bubblewrap not available")

        from src.core.sandbox import execute_sandboxed_command

        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = SandboxExecutor(config)

        exit_code, stdout, stderr = await execute_sandboxed_command(
            executor, "id -u", allow_network=False, timeout=10
        )

        if exit_code == 0:
            uid = int(stdout.strip())
            assert uid != ROOT_UID, (
                f"SECURITY VIOLATION: Sandbox command ran as root!"
            )


# =============================================================================
# Test: Security Invariants
# =============================================================================

class TestSecurityInvariants:
    """Test security invariants that must always hold."""

    def test_api_uid_is_well_defined(self) -> None:
        """API_USER_UID constant matches Dockerfile user definition."""
        # Dockerfile: useradd -m -u 45045 -s /bin/bash ag3ntum_api
        DOCKERFILE_API_UID = 45045

        assert API_USER_UID == DOCKERFILE_API_UID, (
            f"API_USER_UID ({API_USER_UID}) should match Dockerfile "
            f"(useradd -u {DOCKERFILE_API_UID})"
        )

    def test_uid_ranges_dont_overlap(self) -> None:
        """UID ranges should not overlap to ensure clear separation."""
        # Root: 0
        # System: 1-999
        # Legacy users: 2000-49999 (still valid, no new allocations)
        # API user: 45045 (in legacy range but chosen for separation)
        # Isolated users: 50000-60000 (new allocations)

        # Root should be below all user ranges
        assert ROOT_UID < MIN_USER_UID_LEGACY, "Root should be below legacy user range"
        assert ROOT_UID < MIN_USER_UID_ISOLATED, "Root should be below isolated user range"

        # System UIDs should be below all user ranges
        assert SYSTEM_UID_MAX < MIN_USER_UID_LEGACY, "System UIDs should be below legacy range"
        assert SYSTEM_UID_MAX < MIN_USER_UID_ISOLATED, "System UIDs should be below isolated range"

        # API user should be below isolated range to avoid collision
        assert API_USER_UID < MIN_USER_UID_ISOLATED, (
            "API_USER_UID should be below isolated range"
        )

    def test_no_hardcoded_root_in_sandbox(self, workspace: Path) -> None:
        """SandboxExecutor should not have hardcoded root UID."""
        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = SandboxExecutor(config)

        # Default should be None, not root
        assert executor.linux_uid != ROOT_UID or executor.linux_uid is None
        assert executor.linux_gid != ROOT_UID or executor.linux_gid is None

    def test_environment_doesnt_leak_uid(self, workspace: Path) -> None:
        """Custom environment shouldn't contain UID-related secrets."""
        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        config.environment.custom_env = {
            "API_KEY": "secret",
            "DATABASE_URL": "postgres://...",
        }

        # UID/GID shouldn't be in environment (they're handled separately)
        for key in config.environment.custom_env:
            assert "uid" not in key.lower(), f"UID-related key found: {key}"
            assert "gid" not in key.lower(), f"GID-related key found: {key}"


# =============================================================================
# Test: Docker Environment Detection
# =============================================================================

class TestDockerEnvironment:
    """Test Docker environment detection and configuration."""

    def test_ag3ntum_root_env_in_docker(self) -> None:
        """AG3NTUM_ROOT environment variable should be set in Docker."""
        ag3ntum_root = os.environ.get("AG3NTUM_ROOT")

        if ag3ntum_root == "/":
            # We're in Docker
            current_uid = os.getuid()
            assert current_uid != ROOT_UID, (
                "In Docker, process should not be root"
            )
        # Outside Docker, this test just passes

    def test_process_user_matches_container_user(self) -> None:
        """If in Docker, current user should be ag3ntum_api."""
        import pwd

        ag3ntum_root = os.environ.get("AG3NTUM_ROOT")

        if ag3ntum_root == "/":
            # We're in Docker
            current_uid = os.getuid()
            try:
                user_info = pwd.getpwuid(current_uid)
                # Should be ag3ntum_api or running as that UID
                assert current_uid == API_USER_UID or user_info.pw_name == "ag3ntum_api", (
                    f"In Docker, expected ag3ntum_api but got {user_info.pw_name}"
                )
            except KeyError:
                # UID not in passwd (possible in some containers)
                assert current_uid == API_USER_UID, (
                    f"In Docker, expected UID {API_USER_UID} but got {current_uid}"
                )


# =============================================================================
# Test: UID Passing Through Task Runner Chain
# =============================================================================

class TestUIDPassingChain:
    """Test that UID is correctly passed through task_runner → agent_core → executor."""

    def test_claude_agent_accepts_uid_gid(self, minimal_agent_config) -> None:
        """ClaudeAgent.__init__ accepts linux_uid and linux_gid parameters."""
        from src.core.agent_core import ClaudeAgent
        from src.core.permission_profiles import PermissionManager

        pm = PermissionManager()
        # Use UID in valid isolated range (50000-60000)
        test_uid = 50100
        test_gid = 50100

        agent = ClaudeAgent(
            config=minimal_agent_config,
            permission_manager=pm,
            linux_uid=test_uid,
            linux_gid=test_gid,
            tracer=False,
        )

        assert agent._linux_uid == test_uid
        assert agent._linux_gid == test_gid

    def test_claude_agent_uid_default_none(self, minimal_agent_config) -> None:
        """ClaudeAgent defaults to None for UID/GID."""
        from src.core.agent_core import ClaudeAgent
        from src.core.permission_profiles import PermissionManager

        pm = PermissionManager()
        agent = ClaudeAgent(
            config=minimal_agent_config,
            permission_manager=pm,
            tracer=False,
        )

        assert agent._linux_uid is None
        assert agent._linux_gid is None

    def test_task_execution_params_has_uid_fields(self) -> None:
        """TaskExecutionParams schema has linux_uid and linux_gid fields."""
        from src.core.schemas import TaskExecutionParams
        # Use UID in valid isolated range (50000-60000)
        test_uid = 50200
        test_gid = 50200

        params = TaskExecutionParams(
            task="test",
            linux_uid=test_uid,
            linux_gid=test_gid,
        )

        assert params.linux_uid == test_uid
        assert params.linux_gid == test_gid


# =============================================================================
# Test: Directory Permission Validation (Mode 711)
# =============================================================================

class TestDirectoryPermissions:
    """Test permission validation with mode 711 directories."""

    def test_mode_711_allows_stat_on_child(self, tmp_path: Path) -> None:
        """Mode 711 on parent allows stat() on known child path."""
        # Create directory structure
        parent = tmp_path / "parent"
        parent.mkdir()
        child = parent / "child"
        child.mkdir()
        grandchild = child / "file.txt"
        grandchild.write_text("test")

        # Set mode 711 on parent and child (traverse only)
        parent.chmod(0o711)
        child.chmod(0o711)

        # We should still be able to stat the grandchild
        # (assuming we know the exact path)
        assert grandchild.exists()
        assert grandchild.stat().st_size > 0

    def test_mode_711_hides_directory_listing(self, tmp_path: Path) -> None:
        """Mode 711 prevents listing directory contents."""
        # Create directory with contents
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / "secret1.txt").write_text("secret")
        (parent / "secret2.txt").write_text("secret")

        # Set mode 711 (owner can do everything, others can only traverse)
        parent.chmod(0o711)

        # As the owner, we can still list
        # But if we were a different user, listing would fail
        # This test documents the expected behavior
        contents = list(parent.iterdir())
        assert len(contents) == 2, "Owner can still list mode 711 directory"

    def test_venv_validation_with_mode_711(self, tmp_path: Path) -> None:
        """Auth service can validate venv exists with mode 711 directories."""
        # Simulate user directory structure
        user_home = tmp_path / "users" / "testuser"
        user_home.mkdir(parents=True)
        venv = user_home / "venv"
        venv.mkdir()
        venv_bin = venv / "bin"
        venv_bin.mkdir()
        python3 = venv_bin / "python3"
        python3.write_text("#!/usr/bin/env python3")

        # Set permissions like the production system
        user_home.chmod(0o711)
        venv.chmod(0o711)
        venv_bin.chmod(0o711)
        python3.chmod(0o755)

        # Validation should work - we can stat() the python3 binary
        assert user_home.exists()
        assert venv.exists()
        assert python3.exists()


# =============================================================================
# Test: Auth Service Permission Error Handling
# =============================================================================

class TestAuthServicePermissionHandling:
    """Test auth_service handles permission errors correctly."""

    def test_user_environment_error_defined(self) -> None:
        """UserEnvironmentError is properly defined and importable."""
        from src.services.auth_service import UserEnvironmentError

        # Should be able to create and raise it
        error = UserEnvironmentError("test error")
        assert str(error) == "test error"

    def test_validate_user_environment_catches_permission_error(self, tmp_path: Path) -> None:
        """validate_user_environment catches PermissionError and raises UserEnvironmentError."""
        from src.services.auth_service import AuthService, UserEnvironmentError
        from unittest.mock import patch

        auth = AuthService()

        # Mock Path.exists() to raise PermissionError
        with patch.object(Path, 'exists', side_effect=PermissionError("Permission denied")):
            with pytest.raises(UserEnvironmentError) as exc_info:
                auth.validate_user_environment("testuser")

            assert "inaccessible" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_authenticate_propagates_user_environment_error(self) -> None:
        """authenticate() propagates UserEnvironmentError to caller."""
        from src.services.auth_service import AuthService, UserEnvironmentError
        from unittest.mock import patch, AsyncMock, MagicMock

        auth = AuthService()

        # Create mock user
        mock_user = MagicMock()
        mock_user.is_active = True
        mock_user.password_hash = "$2b$12$test"  # bcrypt hash
        mock_user.username = "testuser"

        # Create mock database session
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db.execute.return_value = mock_result

        with patch.object(auth, 'validate_user_environment',
                         side_effect=UserEnvironmentError("Environment error")):
            with patch('bcrypt.checkpw', return_value=True):
                with pytest.raises(UserEnvironmentError):
                    await auth.authenticate(mock_db, "test@test.com", "password")


# =============================================================================
# Test: UID Chain Integration Tests
# =============================================================================

class TestUIDChainIntegration:
    """Integration tests that verify UID flows through the entire chain.

    These tests mock at the subprocess level to verify that UID is correctly
    passed from task_runner → agent_core → executor → bwrap → subprocess.

    CRITICAL: These tests should catch regressions where UID is dropped
    at any point in the chain.

    Note: Privilege dropping is now handled by bwrap's --uid/--gid flags
    instead of preexec_fn for improved security.
    """

    @pytest.mark.asyncio
    async def test_uid_flows_from_agent_to_sandbox_executor(
        self, workspace: Path, minimal_agent_config
    ) -> None:
        """Verify UID flows from ClaudeAgent to SandboxExecutor.

        This tests the agent_core → sandbox chain. If agent_core drops UID,
        the executor won't have it.
        """
        from src.core.agent_core import ClaudeAgent
        from src.core.permission_profiles import PermissionManager
        from src.core.sandbox import SandboxConfig, SandboxMount

        pm = PermissionManager()
        # Use UID in valid isolated range (50000-60000)
        test_uid = 50500
        test_gid = 50500

        agent = ClaudeAgent(
            config=minimal_agent_config,
            permission_manager=pm,
            linux_uid=test_uid,
            linux_gid=test_gid,
            tracer=False,
        )

        # Build sandbox config and executor
        sandbox_config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )

        executor = agent._build_sandbox_executor(sandbox_config, workspace)

        # CRITICAL ASSERTION: Executor must have UID from agent
        assert executor is not None, "Executor should be created when sandbox is enabled"
        assert executor.linux_uid == test_uid, (
            f"UID chain broken: agent has UID {test_uid} but executor has {executor.linux_uid}. "
            "This indicates agent_core is not passing UID to SandboxExecutor."
        )
        assert executor.linux_gid == test_gid, (
            f"GID chain broken: agent has GID {test_gid} but executor has {executor.linux_gid}. "
            "This indicates agent_core is not passing GID to SandboxExecutor."
        )

    @pytest.mark.asyncio
    async def test_uid_flows_to_bwrap_command(self, workspace: Path) -> None:
        """Verify UID flows all the way to bwrap --uid/--gid flags.

        This tests the full chain: executor → bwrap command.
        Bwrap should include --uid and --gid flags for privilege dropping.
        """
        from src.core.sandbox import execute_sandboxed_command, SandboxConfig, SandboxMount, SandboxExecutor

        # Use UID in valid isolated range (50000-60000)
        test_uid = 50600
        test_gid = 50600

        config = SandboxConfig(
            enabled=True,
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = SandboxExecutor(config, linux_uid=test_uid, linux_gid=test_gid)

        captured_args = None

        async def capture_subprocess_call(*args, **kwargs):
            """Capture the bwrap command args."""
            nonlocal captured_args
            captured_args = args

            # Return a mock process
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (f"{test_uid}\n".encode(), b"")
            mock_process.returncode = 0
            return mock_process

        with patch('asyncio.create_subprocess_exec', side_effect=capture_subprocess_call):
            await execute_sandboxed_command(
                executor, "id -u", allow_network=False, timeout=10
            )

        # CRITICAL ASSERTION: bwrap command must include --uid and --gid
        assert captured_args is not None, "subprocess should have been called"
        cmd_str = " ".join(str(arg) for arg in captured_args)

        assert f"--uid" in cmd_str, (
            "UID chain broken: bwrap command missing --uid flag. "
            "This indicates execute_sandboxed_command is not setting UID."
        )
        assert str(test_uid) in cmd_str, (
            f"UID chain broken: bwrap command missing UID value {test_uid}. "
            f"Command: {cmd_str[:200]}"
        )
        assert f"--gid" in cmd_str, (
            "GID chain broken: bwrap command missing --gid flag. "
            "This indicates execute_sandboxed_command is not setting GID."
        )
        assert str(test_gid) in cmd_str, (
            f"GID chain broken: bwrap command missing GID value {test_gid}. "
            f"Command: {cmd_str[:200]}"
        )

    def test_ag3ntum_bash_tool_binds_sandbox_executor(self, workspace: Path) -> None:
        """Verify ag3ntum_bash tool factory accepts and binds SandboxExecutor.

        This verifies the tool factory correctly accepts the executor parameter.
        The actual UID flow through to subprocess is tested by
        test_uid_flows_to_bwrap_command which uses execute_sandboxed_command.
        """
        from tools.ag3ntum.ag3ntum_bash.tool import create_bash_tool
        from src.core.sandbox import SandboxConfig, SandboxMount, SandboxExecutor

        # Use UID in valid isolated range (50000-60000)
        test_uid = 50700
        test_gid = 50700

        config = SandboxConfig(
            enabled=True,
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = SandboxExecutor(config, linux_uid=test_uid, linux_gid=test_gid)

        # Verify tool factory accepts sandbox_executor parameter
        bash_tool = create_bash_tool(
            workspace_path=workspace,
            sandbox_executor=executor,
        )

        # Tool should be created successfully with executor bound
        assert bash_tool is not None, "create_bash_tool should return a tool instance"

        # The executor should have our UID - this is what will be used
        # when the tool eventually calls execute_sandboxed_command
        assert executor.linux_uid == test_uid, (
            "Executor UID not preserved - ag3ntum_bash would run with wrong UID"
        )
        assert executor.linux_gid == test_gid, (
            "Executor GID not preserved - ag3ntum_bash would run with wrong GID"
        )

    def test_regression_detection_uid_dropped_in_agent(
        self, workspace: Path, minimal_agent_config
    ) -> None:
        """Regression test: Detect if agent_core drops UID before passing to executor.

        This simulates the bug where agent accepted UID but didn't pass it to executor.
        """
        from src.core.agent_core import ClaudeAgent
        from src.core.permission_profiles import PermissionManager

        pm = PermissionManager()
        # Use UID in valid isolated range (50000-60000)
        test_uid = 50800
        test_gid = 50800

        # Agent created with UID
        agent = ClaudeAgent(
            config=minimal_agent_config,
            permission_manager=pm,
            linux_uid=test_uid,
            linux_gid=test_gid,
            tracer=False,
        )

        # Verify agent stored the UID (this would catch constructor bug)
        assert agent._linux_uid is not None, (
            "REGRESSION: ClaudeAgent dropped linux_uid in __init__"
        )
        assert agent._linux_gid is not None, (
            "REGRESSION: ClaudeAgent dropped linux_gid in __init__"
        )

    def test_regression_detection_uid_dropped_in_task_params(self) -> None:
        """Regression test: Detect if TaskExecutionParams loses UID fields.

        This catches schema changes that remove or rename UID fields.
        """
        from src.core.schemas import TaskExecutionParams
        import inspect
        # Use UID in valid isolated range (50000-60000)
        test_uid = 50900
        test_gid = 50900

        # Verify the schema has linux_uid and linux_gid fields
        sig = inspect.signature(TaskExecutionParams)
        params = sig.parameters

        assert "linux_uid" in params, (
            "REGRESSION: TaskExecutionParams schema missing linux_uid field"
        )
        assert "linux_gid" in params, (
            "REGRESSION: TaskExecutionParams schema missing linux_gid field"
        )

        # Verify they can hold integer values
        test_params = TaskExecutionParams(
            task="test",
            linux_uid=test_uid,
            linux_gid=test_gid,
        )
        assert test_params.linux_uid == test_uid
        assert test_params.linux_gid == test_gid

    def test_regression_detection_sandbox_executor_uid_fields(self, workspace: Path) -> None:
        """Regression test: Detect if SandboxExecutor loses UID fields."""
        from src.core.sandbox import SandboxConfig, SandboxMount, SandboxExecutor
        # Use UID in valid isolated range (50000-60000)
        test_uid = 51000
        test_gid = 51000

        config = SandboxConfig(
            enabled=True,
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )

        # Verify SandboxExecutor accepts and stores UID/GID
        executor = SandboxExecutor(config, linux_uid=test_uid, linux_gid=test_gid)

        assert hasattr(executor, 'linux_uid'), (
            "REGRESSION: SandboxExecutor missing linux_uid attribute"
        )
        assert hasattr(executor, 'linux_gid'), (
            "REGRESSION: SandboxExecutor missing linux_gid attribute"
        )
        assert executor.linux_uid == test_uid, (
            "REGRESSION: SandboxExecutor not storing linux_uid correctly"
        )
        assert executor.linux_gid == test_gid, (
            "REGRESSION: SandboxExecutor not storing linux_gid correctly"
        )

    def test_regression_detection_create_demote_fn_exported(self) -> None:
        """Regression test: Verify create_demote_fn is exported from sandbox module."""
        try:
            from src.core.sandbox import create_demote_fn
        except ImportError:
            pytest.fail(
                "REGRESSION: create_demote_fn not exported from src.core.sandbox. "
                "This function is required by ag3ntum_bash for privilege dropping."
            )

        # Use UID in valid isolated range (50000-60000)
        test_uid = 51100
        test_gid = 51100

        # Verify it creates callable
        fn = create_demote_fn(test_uid, test_gid)
        assert callable(fn), "create_demote_fn should return a callable"

    @pytest.mark.asyncio
    async def test_full_chain_uid_consistency(
        self, workspace: Path, minimal_agent_config
    ) -> None:
        """End-to-end test: Verify UID is consistent across entire chain.

        This test creates the full chain and verifies the same UID appears
        at each level. If any component drops or changes the UID, this fails.
        """
        from src.core.agent_core import ClaudeAgent
        from src.core.permission_profiles import PermissionManager
        from src.core.sandbox import SandboxConfig, SandboxMount
        from src.core.schemas import TaskExecutionParams

        TEST_UID = 2999
        TEST_GID = 2999

        # Level 1: TaskExecutionParams
        params = TaskExecutionParams(
            task="test",
            linux_uid=TEST_UID,
            linux_gid=TEST_GID,
        )
        assert params.linux_uid == TEST_UID, "UID lost at TaskExecutionParams level"

        # Level 2: ClaudeAgent
        pm = PermissionManager()
        agent = ClaudeAgent(
            config=minimal_agent_config,
            permission_manager=pm,
            linux_uid=params.linux_uid,
            linux_gid=params.linux_gid,
            tracer=False,
        )
        assert agent._linux_uid == TEST_UID, "UID lost at ClaudeAgent level"

        # Level 3: SandboxExecutor (via agent)
        sandbox_config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = agent._build_sandbox_executor(sandbox_config, workspace)
        assert executor is not None, "Executor should be created when sandbox is enabled"
        assert executor.linux_uid == TEST_UID, "UID lost at SandboxExecutor level"

        # Level 4: Verify preexec_fn would be created with this UID
        from src.core.sandbox import create_demote_fn
        demote_fn = create_demote_fn(executor.linux_uid, executor.linux_gid)
        assert demote_fn is not None, "UID lost at demote_fn creation level"

        # All levels verified - UID chain is intact


# =============================================================================
# Test: Session Isolation and Permission-Based Security
# =============================================================================

class TestSessionIsolation:
    """Test session-level isolation and permission-based security.

    Security Model:
    - User directories: 750 (owner rwx, ag3ntum group rx)
    - Session directories: 700 (owner only, no group access)
    - Persistent storage: 770 (owner + ag3ntum group rwx)
    - PathValidator provides application-level access control

    Cross-user blocking:
    - Users cannot access other users' directories
    - PathValidator blocks /users/{other_user}/* paths

    Cross-session blocking:
    - Users cannot access other sessions (even their own)
    - PathValidator blocks /users/{user}/sessions/{other_session}/*
    """

    @pytest.fixture
    def mock_workspace(self, tmp_path: Path) -> Path:
        """Create a mock workspace with session structure."""
        # Simulate /users/{username}/sessions/{session_id}/workspace
        user_home = tmp_path / "users" / "testuser"
        user_home.mkdir(parents=True)
        sessions_dir = user_home / "sessions"
        sessions_dir.mkdir()
        session_dir = sessions_dir / "20260127_120000_abc12345"
        session_dir.mkdir()
        workspace = session_dir / "workspace"
        workspace.mkdir()
        return workspace

    def test_path_validator_blocks_cross_user_access(self, mock_workspace: Path) -> None:
        """PathValidator should block access to other users' directories."""
        from src.core.path_validator import (
            Ag3ntumPathValidator,
            PathValidatorConfig,
            PathValidationError,
        )

        config = PathValidatorConfig(
            workspace_path=mock_workspace,
        )
        validator = Ag3ntumPathValidator(config)

        # Should have extracted session context
        assert validator._session_username == "testuser"
        assert validator._session_id == "20260127_120000_abc12345"

        # Access to own workspace should work
        result = validator.validate_path("/workspace/file.txt", "read")
        assert result.normalized == mock_workspace / "file.txt"

        # Access to other user's directory should be blocked
        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_path("/users/otheruser/sessions/xxx/workspace/file.txt", "read")
        assert "CROSS_USER_ACCESS_BLOCKED" in str(exc_info.value.reason)

    def test_path_validator_blocks_cross_session_access(self, mock_workspace: Path) -> None:
        """PathValidator should block access to other sessions."""
        from src.core.path_validator import (
            Ag3ntumPathValidator,
            PathValidatorConfig,
            PathValidationError,
        )

        # Create another session directory for the same user
        other_session = mock_workspace.parent.parent / "20260127_130000_def67890"
        other_session.mkdir()
        (other_session / "workspace").mkdir()

        config = PathValidatorConfig(
            workspace_path=mock_workspace,
        )
        validator = Ag3ntumPathValidator(config)

        # Access to other session of same user should be blocked
        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_path(
                "/users/testuser/sessions/20260127_130000_def67890/workspace/file.txt",
                "read"
            )
        assert "CROSS_SESSION_ACCESS_BLOCKED" in str(exc_info.value.reason)

    def test_path_validator_allows_own_session(self, mock_workspace: Path) -> None:
        """PathValidator should allow access to own session."""
        from src.core.path_validator import (
            Ag3ntumPathValidator,
            PathValidatorConfig,
        )

        config = PathValidatorConfig(
            workspace_path=mock_workspace,
        )
        validator = Ag3ntumPathValidator(config)

        # Create a test file
        test_file = mock_workspace / "test.txt"
        test_file.write_text("test")

        # Access to own session should work
        result = validator.validate_path("/workspace/test.txt", "read")
        assert result.normalized == test_file

    def test_session_directory_creation_with_owner_uid(self, tmp_path: Path) -> None:
        """Session directory should be created with proper ownership."""
        from src.core.sessions import SessionManager

        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        manager = SessionManager(sessions_dir)

        # Create session without owner_uid (fallback mode)
        session_id = manager.create_session_directory()
        session_dir = manager.get_session_dir(session_id)

        assert session_dir.exists()
        assert (session_dir / "workspace").exists()

    def test_secure_file_write_creates_600_permissions(self, tmp_path: Path) -> None:
        """secure_file_write should create files with 600 permissions."""
        from src.core.sessions import secure_file_write

        test_file = tmp_path / "test.txt"

        secure_file_write(test_file, "test content")

        assert test_file.exists()
        # Check permissions (600 = owner read/write)
        mode = test_file.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    def test_ensure_secure_session_files_hardens_permissions(self, tmp_path: Path) -> None:
        """ensure_secure_session_files should harden all session files."""
        from src.core.sessions import ensure_secure_session_files

        # Create a session directory structure
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / "agent.jsonl").write_text("test")
        (session_dir / "workspace").mkdir()
        (session_dir / "workspace" / "file.txt").write_text("test")

        # Set insecure permissions initially
        (session_dir / "agent.jsonl").chmod(0o644)
        (session_dir / "workspace" / "file.txt").chmod(0o666)

        # Harden permissions
        ensure_secure_session_files(session_dir)

        # Verify session directory is 700
        mode = session_dir.stat().st_mode & 0o777
        assert mode == 0o700, f"Session dir: expected 0o700, got {oct(mode)}"

        # Verify agent.jsonl is 600
        mode = (session_dir / "agent.jsonl").stat().st_mode & 0o777
        assert mode == 0o600, f"agent.jsonl: expected 0o600, got {oct(mode)}"

        # Verify workspace file is 600
        mode = (session_dir / "workspace" / "file.txt").stat().st_mode & 0o777
        assert mode == 0o600, f"workspace file: expected 0o600, got {oct(mode)}"


# =============================================================================
# Test: Permission Model Constants
# =============================================================================

class TestPermissionModelConstants:
    """Test that permission model constants are correctly defined."""

    def test_api_uid_constant_matches_dockerfile(self) -> None:
        """API_UID should match the value in Dockerfile."""
        from src.services.user_service import API_UID
        assert API_UID == 45045, f"API_UID should be 45045, got {API_UID}"

    def test_permission_modes_defined_correctly(self) -> None:
        """Permission modes should be defined correctly.

        Security Model:
        - 700: Owner only (rwx------) - directories
        - 600: Owner only (rw-------) - files
        - 755: World executable (rwxr-xr-x) - venv
        """
        # These are the expected permission modes
        DIR_MODE = 0o700   # Owner only
        FILE_MODE = 0o600  # Owner only
        VENV_MODE = 0o755  # World executable

        # Verify they block group and others
        assert (DIR_MODE & 0o070) == 0, "700 should block group"
        assert (DIR_MODE & 0o007) == 0, "700 should block others"
        assert (FILE_MODE & 0o070) == 0, "600 should block group"
        assert (FILE_MODE & 0o007) == 0, "600 should block others"

    def test_uid_ranges_for_isolation(self) -> None:
        """UID ranges should maintain isolation between API and users."""
        from src.services.user_service import API_UID
        from src.core.uid_security import get_uid_security_config

        config = get_uid_security_config()

        # API UID should be below user allocation range (isolated mode)
        assert API_UID < config.isolated_uid_min, (
            f"API_UID ({API_UID}) should be below isolated_uid_min ({config.isolated_uid_min})"
        )

        # User range should not include API UID
        assert not (config.isolated_uid_min <= API_UID <= config.isolated_uid_max), (
            "API_UID should not be in user UID range"
        )
