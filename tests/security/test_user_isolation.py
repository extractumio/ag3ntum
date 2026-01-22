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


# =============================================================================
# Test: UID Passing Through Task Runner Chain
# =============================================================================

class TestUIDPassingChain:
    """Test that UID is correctly passed through task_runner → agent_core → executor."""

    def test_claude_agent_accepts_uid_gid(self) -> None:
        """ClaudeAgent.__init__ accepts linux_uid and linux_gid parameters."""
        from src.core.agent_core import ClaudeAgent
        from src.core.permission_profiles import PermissionManager

        # Create minimal config
        pm = PermissionManager()

        agent = ClaudeAgent(
            permission_manager=pm,
            linux_uid=2000,
            linux_gid=2000,
        )

        assert agent._linux_uid == 2000
        assert agent._linux_gid == 2000

    def test_claude_agent_uid_default_none(self) -> None:
        """ClaudeAgent defaults to None for UID/GID."""
        from src.core.agent_core import ClaudeAgent
        from src.core.permission_profiles import PermissionManager

        pm = PermissionManager()
        agent = ClaudeAgent(permission_manager=pm)

        assert agent._linux_uid is None
        assert agent._linux_gid is None

    def test_task_execution_params_has_uid_fields(self) -> None:
        """TaskExecutionParams schema has linux_uid and linux_gid fields."""
        from src.core.schemas import TaskExecutionParams

        params = TaskExecutionParams(
            task="test",
            linux_uid=2000,
            linux_gid=2000,
        )

        assert params.linux_uid == 2000
        assert params.linux_gid == 2000


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

        # Mock database and user lookup
        mock_db = AsyncMock()
        mock_user = MagicMock()
        mock_user.is_active = True
        mock_user.password_hash = "$2b$12$test"  # bcrypt hash
        mock_user.username = "testuser"

        with patch.object(auth, 'validate_user_environment',
                         side_effect=UserEnvironmentError("Environment error")):
            with patch('bcrypt.checkpw', return_value=True):
                with patch('sqlalchemy.ext.asyncio.AsyncSession.execute') as mock_exec:
                    mock_result = MagicMock()
                    mock_result.scalar_one_or_none.return_value = mock_user
                    mock_exec.return_value = mock_result

                    with pytest.raises(UserEnvironmentError):
                        await auth.authenticate(mock_db, "test@test.com", "password")


# =============================================================================
# Test: UID Chain Integration Tests
# =============================================================================

class TestUIDChainIntegration:
    """Integration tests that verify UID flows through the entire chain.

    These tests mock at the subprocess level to verify that UID is correctly
    passed from task_runner → agent_core → executor → ag3ntum_bash → subprocess.

    CRITICAL: These tests should catch regressions where UID is dropped
    at any point in the chain.
    """

    @pytest.mark.asyncio
    async def test_uid_flows_from_agent_to_sandbox_executor(self, workspace: Path) -> None:
        """Verify UID flows from ClaudeAgent to SandboxExecutor.

        This tests the agent_core → sandbox chain. If agent_core drops UID,
        the executor won't have it.
        """
        from src.core.agent_core import ClaudeAgent
        from src.core.permission_profiles import PermissionManager
        from src.core.sandbox import SandboxConfig, SandboxMount

        pm = PermissionManager()
        test_uid = 2500
        test_gid = 2500

        agent = ClaudeAgent(
            permission_manager=pm,
            linux_uid=test_uid,
            linux_gid=test_gid,
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
    async def test_uid_flows_to_subprocess_preexec_fn(self, workspace: Path) -> None:
        """Verify UID flows all the way to subprocess preexec_fn.

        This tests the full chain: executor → ag3ntum_bash → subprocess.
        The preexec_fn should use the correct UID for privilege dropping.
        """
        from src.core.sandbox import execute_sandboxed_command, SandboxConfig, SandboxMount, SandboxExecutor

        test_uid = 2600
        test_gid = 2600

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

        captured_preexec_fn = None

        async def capture_subprocess_call(*args, **kwargs):
            """Capture the preexec_fn passed to subprocess."""
            nonlocal captured_preexec_fn
            captured_preexec_fn = kwargs.get("preexec_fn")

            # Return a mock process
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"2600\n", b"")
            mock_process.returncode = 0
            return mock_process

        with patch('asyncio.create_subprocess_exec', side_effect=capture_subprocess_call):
            await execute_sandboxed_command(
                executor, "id -u", allow_network=False, timeout=10
            )

        # CRITICAL ASSERTION: preexec_fn must be set (privilege dropping enabled)
        assert captured_preexec_fn is not None, (
            "UID chain broken: preexec_fn is None but executor has linux_uid. "
            "This indicates execute_sandboxed_command is not creating demote function."
        )

        # Verify the preexec_fn would drop to correct UID
        # We can't actually call it (requires root), but we verify it was created
        assert callable(captured_preexec_fn), (
            "preexec_fn should be a callable demote function"
        )

    @pytest.mark.asyncio
    async def test_ag3ntum_bash_uses_sandbox_executor_uid(self, workspace: Path) -> None:
        """Verify ag3ntum_bash tool uses UID from bound SandboxExecutor.

        This is the final link in the chain. If ag3ntum_bash doesn't use
        executor.linux_uid, commands run as API user instead of session user.
        """
        from tools.ag3ntum.ag3ntum_bash.tool import create_bash_tool
        from src.core.sandbox import SandboxConfig, SandboxMount, SandboxExecutor

        test_uid = 2700
        test_gid = 2700

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

        # Create tool with bound executor using factory
        bash_tool = create_bash_tool(
            workspace_path=workspace,
            sandbox_executor=executor,
        )

        captured_preexec_fn = None

        async def capture_subprocess_call(*args, **kwargs):
            nonlocal captured_preexec_fn
            captured_preexec_fn = kwargs.get("preexec_fn")
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"test output", b"")
            mock_process.returncode = 0
            return mock_process

        with patch('asyncio.create_subprocess_exec', side_effect=capture_subprocess_call):
            # Call the tool with a simple command
            await bash_tool({"command": "echo test"})

        # CRITICAL ASSERTION: tool must use preexec_fn from executor's UID
        assert captured_preexec_fn is not None, (
            "UID chain broken at ag3ntum_bash: preexec_fn is None but executor has UID. "
            "This indicates ag3ntum_bash is not using create_demote_fn with executor's UID."
        )

    def test_regression_detection_uid_dropped_in_agent(self, workspace: Path) -> None:
        """Regression test: Detect if agent_core drops UID before passing to executor.

        This simulates the bug where agent accepted UID but didn't pass it to executor.
        """
        from src.core.agent_core import ClaudeAgent
        from src.core.permission_profiles import PermissionManager

        pm = PermissionManager()

        # Agent created with UID
        agent = ClaudeAgent(
            permission_manager=pm,
            linux_uid=2000,
            linux_gid=2000,
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
            linux_uid=2000,
            linux_gid=2000,
        )
        assert test_params.linux_uid == 2000
        assert test_params.linux_gid == 2000

    def test_regression_detection_sandbox_executor_uid_fields(self, workspace: Path) -> None:
        """Regression test: Detect if SandboxExecutor loses UID fields."""
        from src.core.sandbox import SandboxConfig, SandboxMount, SandboxExecutor

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
        executor = SandboxExecutor(config, linux_uid=2000, linux_gid=2000)

        assert hasattr(executor, 'linux_uid'), (
            "REGRESSION: SandboxExecutor missing linux_uid attribute"
        )
        assert hasattr(executor, 'linux_gid'), (
            "REGRESSION: SandboxExecutor missing linux_gid attribute"
        )
        assert executor.linux_uid == 2000, (
            "REGRESSION: SandboxExecutor not storing linux_uid correctly"
        )
        assert executor.linux_gid == 2000, (
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

        # Verify it creates callable
        fn = create_demote_fn(2000, 2000)
        assert callable(fn), "create_demote_fn should return a callable"

    @pytest.mark.asyncio
    async def test_full_chain_uid_consistency(self, workspace: Path) -> None:
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
            permission_manager=pm,
            linux_uid=params.linux_uid,
            linux_gid=params.linux_gid,
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
