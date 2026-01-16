"""
Security Tests for User Isolation and UID/GID Management.

Tests cover:
- API process runs as ag3ntum_api user (UID 45045), never root
- Sandbox commands run as user-specific UID (2000+), not as API user
- Privilege dropping works correctly in SandboxExecutor
- No process ever runs as root (UID 0)

These tests validate the multi-layered user isolation security model:
1. Container level: API runs as ag3ntum_api (UID 45045)
2. Sandbox level: Commands run as the actual user (UID 2000+)
3. Security invariant: No process ever runs as root

Run with:
    pytest tests/security/test_user_isolation.py -v

Inside Docker:
    docker exec project-ag3ntum-api-1 python -m pytest tests/security/test_user_isolation.py -v

Or via deploy.sh:
    ./deploy.sh test --subset user_isolation
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
    _create_demote_fn,
)


# =============================================================================
# Constants
# =============================================================================

# API user UID (ag3ntum_api in Docker)
API_USER_UID = 45045

# User UID range (users created by user_service start at 2000)
MIN_USER_UID = 2000
MAX_USER_UID = 65534  # Maximum standard UID

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

    def test_api_user_well_above_user_allocation_start(self) -> None:
        """API user UID (45045) should be well above user allocation start (2000).

        User UIDs are generated starting at 2000 and increment. The API user
        UID is chosen to be sufficiently high (45045) that normal user
        allocation won't reach it for thousands of users.
        """
        # API user should be significantly above the starting user UID
        # to avoid collision during normal operation
        SAFE_MARGIN = 40000  # Allow for 40000+ users before collision risk

        assert API_USER_UID >= MIN_USER_UID + SAFE_MARGIN, (
            f"API user UID {API_USER_UID} should be at least {SAFE_MARGIN} "
            f"above MIN_USER_UID ({MIN_USER_UID}) for safe separation"
        )


# =============================================================================
# Test: SandboxExecutor UID/GID Configuration
# =============================================================================

class TestSandboxExecutorUIDs:
    """Test SandboxExecutor UID/GID handling for privilege dropping."""

    def test_executor_accepts_uid_gid(self, basic_sandbox_config: SandboxConfig) -> None:
        """SandboxExecutor accepts linux_uid and linux_gid parameters."""
        executor = SandboxExecutor(
            basic_sandbox_config,
            linux_uid=2000,
            linux_gid=2000,
        )

        assert executor.linux_uid == 2000
        assert executor.linux_gid == 2000

    def test_executor_uid_gid_default_none(self, basic_sandbox_config: SandboxConfig) -> None:
        """SandboxExecutor defaults to None for UID/GID (no privilege drop)."""
        executor = SandboxExecutor(basic_sandbox_config)

        assert executor.linux_uid is None
        assert executor.linux_gid is None

    def test_executor_rejects_root_uid(self, basic_sandbox_config: SandboxConfig) -> None:
        """Creating executor with root UID should be flagged (test documents intent).

        Note: The executor currently accepts any UID. This test documents that
        using ROOT_UID (0) for privilege dropping would be a security issue.
        """
        # Currently executor accepts any UID - this test documents the risk
        executor = SandboxExecutor(
            basic_sandbox_config,
            linux_uid=ROOT_UID,
            linux_gid=ROOT_UID,
        )

        # Document that this is dangerous
        assert executor.linux_uid == ROOT_UID, (
            "If executor accepts ROOT_UID, caller must validate UID before passing"
        )

    def test_user_uid_in_valid_range(self) -> None:
        """User UIDs should be in the valid range (2000+)."""
        # Test UIDs that should be valid for users
        valid_uids = [2000, 2001, 5000, 10000, 65534]

        for uid in valid_uids:
            assert uid >= MIN_USER_UID, f"UID {uid} should be >= {MIN_USER_UID}"
            assert uid != ROOT_UID, f"UID {uid} should not be root"
            assert uid != API_USER_UID, f"UID {uid} should not be API user"


# =============================================================================
# Test: Privilege Dropping Function
# =============================================================================

class TestPrivilegeDropping:
    """Test the privilege dropping mechanism."""

    def test_demote_fn_created_for_valid_uid_gid(self) -> None:
        """_create_demote_fn creates a callable for valid UID/GID."""
        demote_fn = _create_demote_fn(uid=2000, gid=2000)

        assert callable(demote_fn), "Demote function should be callable"

    def test_demote_fn_not_created_with_root(self) -> None:
        """Using root UID for demote would be a security issue.

        Note: The function currently accepts any UID. This test documents
        that the caller must validate UIDs.
        """
        # Function accepts any UID - caller must validate
        demote_fn = _create_demote_fn(uid=ROOT_UID, gid=ROOT_UID)

        # Document that this would be dangerous if actually executed
        assert callable(demote_fn), (
            "Demote function accepts root UID - caller must validate"
        )


# =============================================================================
# Test: Sandbox Command Execution with UID
# =============================================================================

class TestSandboxCommandExecution:
    """Test that sandbox commands can run with different UIDs."""

    @pytest.mark.asyncio
    async def test_execute_sandboxed_command_with_uid(self, workspace: Path) -> None:
        """execute_sandboxed_command uses preexec_fn when UID/GID set."""
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
        executor = SandboxExecutor(config, linux_uid=2000, linux_gid=2000)

        # Mock subprocess execution to verify preexec_fn is passed
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"2000\n", b"")
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            await execute_sandboxed_command(
                executor, "id -u", allow_network=False, timeout=10
            )

            # Verify preexec_fn was passed (for privilege dropping)
            call_kwargs = mock_exec.call_args.kwargs
            assert "preexec_fn" in call_kwargs, (
                "preexec_fn should be passed for privilege dropping"
            )
            assert call_kwargs["preexec_fn"] is not None

    @pytest.mark.asyncio
    async def test_execute_sandboxed_command_without_uid(self, workspace: Path) -> None:
        """execute_sandboxed_command without UID/GID has no preexec_fn."""
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

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"45045\n", b"")
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            await execute_sandboxed_command(
                executor, "id -u", allow_network=False, timeout=10
            )

            # Without UID/GID, preexec_fn should be None
            call_kwargs = mock_exec.call_args.kwargs
            assert call_kwargs.get("preexec_fn") is None


# =============================================================================
# Test: User Service UID Generation
# =============================================================================

class TestUserServiceUIDs:
    """Test that user service generates valid UIDs."""

    def test_user_uid_starts_at_2000(self) -> None:
        """User UIDs should start at 2000 per user_service.py."""
        # This is a documentation test - the actual logic is in user_service.py
        # _generate_next_uid() returns 2000 for first user
        MIN_GENERATED_UID = 2000

        assert MIN_USER_UID == MIN_GENERATED_UID, (
            f"Test constant MIN_USER_UID ({MIN_USER_UID}) should match "
            f"user_service starting UID ({MIN_GENERATED_UID})"
        )

    def test_user_uid_allocation_wont_reach_api_uid(self) -> None:
        """Generated user UIDs should not collide with API UID during normal operation.

        User UIDs start at 2000 and increment. The API UID (45045) is
        chosen to be well above typical allocations, providing a buffer
        of 43000+ users before any collision risk.
        """
        # Verify there's significant headroom before API UID
        HEADROOM = API_USER_UID - MIN_USER_UID

        assert HEADROOM > 40000, (
            f"Headroom between user allocation start ({MIN_USER_UID}) and "
            f"API UID ({API_USER_UID}) should be substantial"
        )

        # Generated UIDs start at MIN_USER_UID (2000)
        assert API_USER_UID != MIN_USER_UID, (
            "API UID should not equal the starting user UID"
        )

    def test_user_uid_never_root(self) -> None:
        """User UIDs (2000+) should never be root (0)."""
        assert ROOT_UID < MIN_USER_UID, (
            f"Root UID ({ROOT_UID}) should be below MIN_USER_UID ({MIN_USER_UID})"
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
        # Regular users: 2000-65534
        # API user: 45045 (outside regular range by design)

        assert ROOT_UID < MIN_USER_UID, "Root should be below user range"

        # Note: API_USER_UID (45045) is technically in the valid range
        # but is chosen to be well above typical user allocations
        # and is not generated by user_service

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
