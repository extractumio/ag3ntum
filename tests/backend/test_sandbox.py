"""
Unit tests for Sandbox configuration and SandboxExecutor.

Tests cover:
- SandboxMount configuration and resolution
- SandboxConfig model validation
- SandboxExecutor bwrap command building
- Filtered /proc configuration
- Network isolation configuration
- Environment variable handling
- Mount source validation (fail-closed security)
- Placeholder resolution in paths
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip marker for tests requiring Linux-specific features (bwrap, /lib, etc.)
requires_linux = pytest.mark.skipif(
    sys.platform != "linux",
    reason="Test requires Linux-specific features (bwrap sandbox)"
)

from src.core.sandbox import (
    SandboxConfig,
    SandboxMount,
    SandboxExecutor,
    SandboxMountError,
    SandboxNetworkConfig,
    ProcFilteringConfig,
    execute_sandboxed_command,
)


class TestSandboxMount:
    """Test SandboxMount configuration."""

    def test_default_mode_is_readonly(self) -> None:
        """Default mount mode is read-only."""
        mount = SandboxMount(source="/src", target="/dest")
        assert mount.mode == "ro"

    def test_explicit_readonly_mode(self) -> None:
        """Explicit 'ro' mode is accepted."""
        mount = SandboxMount(source="/src", target="/dest", mode="ro")
        assert mount.mode == "ro"

    def test_readwrite_mode(self) -> None:
        """'rw' mode is accepted."""
        mount = SandboxMount(source="/src", target="/dest", mode="rw")
        assert mount.mode == "rw"

    def test_mode_case_insensitive(self) -> None:
        """Mode is case-insensitive."""
        mount = SandboxMount(source="/src", target="/dest", mode="RW")
        assert mount.mode == "rw"

    def test_invalid_mode_raises(self) -> None:
        """Invalid mount mode raises ValueError."""
        with pytest.raises(ValueError, match="must be 'ro' or 'rw'"):
            SandboxMount(source="/src", target="/dest", mode="invalid")

    def test_resolve_placeholders(self) -> None:
        """Placeholder resolution in source and target."""
        mount = SandboxMount(
            source="/users/{username}/workspace",
            target="/workspace",
        )
        resolved = mount.resolve({"username": "alice"})
        assert resolved.source == "/users/alice/workspace"
        assert resolved.target == "/workspace"

    def test_resolve_multiple_placeholders(self) -> None:
        """Multiple placeholders are resolved."""
        mount = SandboxMount(
            source="{base}/{session}/workspace",
            target="{target_base}/workspace",
        )
        resolved = mount.resolve({
            "base": "/users/alice",
            "session": "sess_123",
            "target_base": "/home",
        })
        assert resolved.source == "/users/alice/sess_123/workspace"
        assert resolved.target == "/home/workspace"


class TestSandboxNetworkConfig:
    """Test network configuration."""

    def test_default_network_disabled(self) -> None:
        """Network is disabled by default."""
        config = SandboxNetworkConfig()
        assert config.enabled is False

    def test_allowed_domains_normalized(self) -> None:
        """Domain names are normalized to lowercase."""
        config = SandboxNetworkConfig(
            enabled=True,
            allowed_domains=["EXAMPLE.COM", "  api.github.com  "]
        )
        assert config.allowed_domains == ["example.com", "api.github.com"]

    def test_empty_domains_list(self) -> None:
        """Empty or None domains becomes empty list."""
        config = SandboxNetworkConfig(enabled=True, allowed_domains=None)
        assert config.allowed_domains == []

    def test_localhost_disabled_by_default(self) -> None:
        """Localhost access is disabled by default."""
        config = SandboxNetworkConfig()
        assert config.allow_localhost is False


class TestProcFilteringConfig:
    """Test /proc filtering configuration."""

    def test_filtering_enabled_by_default(self) -> None:
        """Proc filtering is enabled by default."""
        config = ProcFilteringConfig()
        assert config.enabled is True

    def test_default_allowed_entries(self) -> None:
        """Default allowed entries include safe /proc paths."""
        config = ProcFilteringConfig()
        assert "/proc/self" in config.allowed_entries
        assert "/proc/cpuinfo" in config.allowed_entries
        assert "/proc/meminfo" in config.allowed_entries

    def test_custom_allowed_entries(self) -> None:
        """Custom allowed entries can be specified."""
        config = ProcFilteringConfig(
            enabled=True,
            allowed_entries=["/proc/self", "/proc/version"]
        )
        assert len(config.allowed_entries) == 2


class TestSandboxConfig:
    """Test complete sandbox configuration."""

    def test_default_config(self) -> None:
        """Default config has sandboxing enabled."""
        config = SandboxConfig()
        assert config.enabled is True
        assert config.file_sandboxing is True
        assert config.network_sandboxing is True

    def test_default_bwrap_path(self) -> None:
        """Default bwrap path is 'bwrap'.

        Note: In production (permissions.yaml), bwrap_path is set to 'sudo bwrap'
        to allow privilege dropping via bwrap --uid/--gid flags. The default here
        is for unit testing without sudo.
        """
        config = SandboxConfig()
        assert config.bwrap_path == "bwrap"

    def test_custom_bwrap_path_sudo(self) -> None:
        """Custom bwrap path 'sudo bwrap' is accepted for privilege dropping."""
        config = SandboxConfig(bwrap_path="sudo bwrap")
        assert config.bwrap_path == "sudo bwrap"

    def test_resolve_config_placeholders(self) -> None:
        """Config resolution applies to all mounts."""
        config = SandboxConfig(
            static_mounts={
                "bin": SandboxMount(source="/usr/bin", target="/usr/bin"),
            },
            session_mounts={
                "workspace": SandboxMount(
                    source="/users/{user}/workspace",
                    target="/workspace",
                    mode="rw",
                ),
            },
            writable_paths=["/users/{user}/output"],
        )

        resolved = config.resolve({"user": "bob"})

        assert resolved.session_mounts["workspace"].source == "/users/bob/workspace"
        assert resolved.writable_paths[0] == "/users/bob/output"


@requires_linux
class TestSandboxExecutor:
    """Test SandboxExecutor command building.

    These tests require Linux-specific features:
    - bwrap (bubblewrap) sandbox utility
    - Linux filesystem paths (/lib, /usr/lib, etc.)
    """

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        """Create temporary workspace."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace

    @pytest.fixture
    def basic_config(self, workspace: Path) -> SandboxConfig:
        """Create basic sandbox config with real paths."""
        return SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            static_mounts={
                "bin": SandboxMount(source="/usr/bin", target="/usr/bin"),
                "lib": SandboxMount(source="/lib", target="/lib"),
            },
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )

    def test_build_bwrap_command_includes_bwrap(
        self, basic_config: SandboxConfig
    ) -> None:
        """Built command includes bwrap (possibly prefixed with sudo)."""
        executor = SandboxExecutor(basic_config)
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)
        # With default bwrap_path="bwrap", command starts with bwrap
        assert cmd[0] == "bwrap"

    def test_build_bwrap_command_with_sudo_prefix(
        self, workspace: Path
    ) -> None:
        """Built command with sudo bwrap starts with sudo."""
        config = SandboxConfig(
            bwrap_path="sudo bwrap",
            static_mounts={
                "bin": SandboxMount(source="/usr/bin", target="/usr/bin"),
                "lib": SandboxMount(source="/lib", target="/lib"),
            },
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = SandboxExecutor(config)
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)
        # With bwrap_path="sudo bwrap", command starts with "sudo"
        assert cmd[0] == "sudo"
        assert cmd[1] == "bwrap"

    def test_build_bwrap_includes_die_with_parent(
        self, basic_config: SandboxConfig
    ) -> None:
        """Built command includes --die-with-parent."""
        executor = SandboxExecutor(basic_config)
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)
        assert "--die-with-parent" in cmd

    def test_build_bwrap_includes_new_session(
        self, basic_config: SandboxConfig
    ) -> None:
        """Built command includes --new-session."""
        executor = SandboxExecutor(basic_config)
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)
        assert "--new-session" in cmd

    def test_nested_container_uses_specific_unshare(
        self, basic_config: SandboxConfig
    ) -> None:
        """Nested container mode uses --unshare-pid, not --unshare-all."""
        executor = SandboxExecutor(basic_config)
        cmd = executor.build_bwrap_command(
            ["echo", "hello"],
            allow_network=False,
            nested_container=True
        )
        assert "--unshare-pid" in cmd
        assert "--unshare-all" not in cmd

    def test_nested_container_adds_unshare_user_when_uid_set(
        self, basic_config: SandboxConfig
    ) -> None:
        """Nested container with UID adds --unshare-user (required by bwrap for --uid)."""
        executor = SandboxExecutor(basic_config, linux_uid=50000, linux_gid=50000)
        cmd = executor.build_bwrap_command(
            ["echo", "hello"],
            allow_network=False,
            nested_container=True
        )
        assert "--unshare-user" in cmd
        assert "--uid" in cmd
        assert "--gid" in cmd

    def test_nested_container_no_unshare_user_without_uid(
        self, basic_config: SandboxConfig
    ) -> None:
        """Nested container without UID does not add --unshare-user."""
        executor = SandboxExecutor(basic_config)  # No linux_uid
        cmd = executor.build_bwrap_command(
            ["echo", "hello"],
            allow_network=False,
            nested_container=True
        )
        assert "--unshare-user" not in cmd

    def test_non_nested_uses_unshare_all(
        self, basic_config: SandboxConfig
    ) -> None:
        """Non-nested mode uses --unshare-all."""
        executor = SandboxExecutor(basic_config)
        cmd = executor.build_bwrap_command(
            ["echo", "hello"],
            allow_network=False,
            nested_container=False
        )
        assert "--unshare-all" in cmd

    def test_command_appended_at_end(
        self, basic_config: SandboxConfig
    ) -> None:
        """User command is appended after -- separator."""
        executor = SandboxExecutor(basic_config)
        cmd = executor.build_bwrap_command(["echo", "hello", "world"], allow_network=False)

        # Find -- separator
        separator_idx = cmd.index("--")
        assert cmd[separator_idx + 1:] == ["echo", "hello", "world"]

    def test_clearenv_when_configured(
        self, basic_config: SandboxConfig
    ) -> None:
        """--clearenv is included when configured."""
        basic_config.environment.clear_env = True
        executor = SandboxExecutor(basic_config)
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)
        assert "--clearenv" in cmd

    def test_home_env_set(self, basic_config: SandboxConfig) -> None:
        """HOME environment variable is set."""
        executor = SandboxExecutor(basic_config)
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)

        # Find --setenv HOME
        for i, arg in enumerate(cmd):
            if arg == "--setenv" and i + 1 < len(cmd) and cmd[i + 1] == "HOME":
                assert cmd[i + 2] == "/workspace"
                return
        pytest.fail("--setenv HOME not found")

    def test_chdir_to_home(self, basic_config: SandboxConfig) -> None:
        """Working directory is set to HOME."""
        executor = SandboxExecutor(basic_config)
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)
        assert "--chdir" in cmd

    def test_wrap_shell_command(self, basic_config: SandboxConfig) -> None:
        """wrap_shell_command wraps in bash -lc."""
        executor = SandboxExecutor(basic_config)
        wrapped = executor.wrap_shell_command("echo hello", allow_network=False)

        # Should be a properly escaped string
        assert "bwrap" in wrapped
        assert "bash" in wrapped
        assert "echo hello" in wrapped

    def test_bwrap_includes_uid_gid_flags_when_set(
        self, basic_config: SandboxConfig
    ) -> None:
        """Built command includes --uid and --gid flags when executor has UID/GID."""
        executor = SandboxExecutor(basic_config, linux_uid=50000, linux_gid=50000)
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)

        # Check --uid flag
        assert "--uid" in cmd
        uid_idx = cmd.index("--uid")
        assert cmd[uid_idx + 1] == "50000"

        # Check --gid flag
        assert "--gid" in cmd
        gid_idx = cmd.index("--gid")
        assert cmd[gid_idx + 1] == "50000"

    def test_bwrap_no_uid_gid_flags_when_not_set(
        self, basic_config: SandboxConfig
    ) -> None:
        """Built command does not include --uid/--gid when not set."""
        executor = SandboxExecutor(basic_config)  # No linux_uid/linux_gid
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)

        assert "--uid" not in cmd
        assert "--gid" not in cmd

    def test_bwrap_uid_gid_flags_before_command_separator(
        self, basic_config: SandboxConfig
    ) -> None:
        """UID/GID flags appear before -- separator (before command)."""
        executor = SandboxExecutor(basic_config, linux_uid=2000, linux_gid=2000)
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)

        separator_idx = cmd.index("--")
        uid_idx = cmd.index("--uid")
        gid_idx = cmd.index("--gid")

        assert uid_idx < separator_idx, "--uid should appear before --"
        assert gid_idx < separator_idx, "--gid should appear before --"


class TestMountValidation:
    """Test mount source validation (fail-closed security)."""

    def test_missing_static_mount_raises(self, tmp_path: Path) -> None:
        """Missing static mount source raises SandboxMountError."""
        config = SandboxConfig(
            static_mounts={
                "nonexistent": SandboxMount(
                    source="/nonexistent/path/that/does/not/exist",
                    target="/target"
                ),
            },
        )
        executor = SandboxExecutor(config)

        with pytest.raises(SandboxMountError, match="does not exist"):
            executor.build_bwrap_command(["echo", "hello"], allow_network=False)

    def test_missing_session_mount_raises(self, tmp_path: Path) -> None:
        """Missing session mount source raises SandboxMountError."""
        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source="/nonexistent/workspace",
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = SandboxExecutor(config)

        with pytest.raises(SandboxMountError, match="does not exist"):
            executor.build_bwrap_command(["echo", "hello"], allow_network=False)

    def test_missing_dynamic_mount_raises(self, tmp_path: Path) -> None:
        """Missing dynamic mount source raises SandboxMountError."""
        config = SandboxConfig(
            dynamic_mounts=[
                SandboxMount(
                    source="/nonexistent/dynamic",
                    target="/dynamic",
                ),
            ],
        )
        executor = SandboxExecutor(config)

        with pytest.raises(SandboxMountError, match="does not exist"):
            executor.build_bwrap_command(["echo", "hello"], allow_network=False)

    def test_validate_mount_sources(self, tmp_path: Path) -> None:
        """validate_mount_sources returns list of missing paths."""
        real_path = tmp_path / "real"
        real_path.mkdir()

        config = SandboxConfig(
            static_mounts={
                "real": SandboxMount(source=str(real_path), target="/real"),
                "fake": SandboxMount(source="/nonexistent", target="/fake"),
            },
        )
        executor = SandboxExecutor(config)

        missing = executor.validate_mount_sources()
        assert "/nonexistent" in missing
        assert str(real_path) not in missing


class TestFilteredProc:
    """Test filtered /proc mount configuration."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace

    def test_filtered_proc_creates_tmpfs(self, workspace: Path) -> None:
        """Filtered /proc mode creates tmpfs at /proc."""
        config = SandboxConfig(
            proc_filtering=ProcFilteringConfig(enabled=True),
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = SandboxExecutor(config)
        cmd = executor.build_bwrap_command(
            ["echo", "hello"],
            allow_network=False,
            nested_container=True
        )

        # Should have --tmpfs /proc
        for i, arg in enumerate(cmd):
            if arg == "--tmpfs" and i + 1 < len(cmd) and cmd[i + 1] == "/proc":
                return
        pytest.fail("--tmpfs /proc not found")

    def test_filtered_proc_mounts_allowed_entries(self, workspace: Path) -> None:
        """Filtered /proc mode mounts allowed entries as ro-bind."""
        config = SandboxConfig(
            proc_filtering=ProcFilteringConfig(
                enabled=True,
                allowed_entries=["/proc/cpuinfo"]  # This should exist
            ),
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        executor = SandboxExecutor(config)
        cmd = executor.build_bwrap_command(
            ["echo", "hello"],
            allow_network=False,
            nested_container=True
        )

        # Should have --ro-bind /proc/cpuinfo /proc/cpuinfo
        cmd_str = " ".join(cmd)
        if Path("/proc/cpuinfo").exists():
            assert "--ro-bind /proc/cpuinfo /proc/cpuinfo" in cmd_str


class TestExecuteSandboxedCommand:
    """Test the async execute_sandboxed_command function."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace

    @pytest.mark.asyncio
    async def test_execute_returns_tuple(self, workspace: Path) -> None:
        """execute_sandboxed_command returns (exit_code, stdout, stderr)."""
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

        # Mock the subprocess execution
        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"hello\n", b"")
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            exit_code, stdout, stderr = await execute_sandboxed_command(
                executor, "echo hello", allow_network=False, timeout=10
            )

            assert exit_code == 0
            assert stdout == "hello\n"
            assert stderr == ""

    @pytest.mark.asyncio
    async def test_execute_timeout_returns_124(self, workspace: Path) -> None:
        """Timeout returns exit code 124."""
        import asyncio

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

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate.side_effect = asyncio.TimeoutError()
            mock_process.kill = MagicMock()
            mock_exec.return_value = mock_process

            exit_code, stdout, stderr = await execute_sandboxed_command(
                executor, "sleep 100", allow_network=False, timeout=1
            )

            assert exit_code == 124
            assert "timed out" in stderr.lower()

    @pytest.mark.asyncio
    async def test_execute_with_uid_gid(self, workspace: Path) -> None:
        """Execution with UID/GID includes --uid/--gid flags in bwrap command."""
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

        with patch('asyncio.create_subprocess_exec') as mock_exec:
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"ok\n", b"")
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            await execute_sandboxed_command(
                executor, "whoami", allow_network=False, timeout=10
            )

            # Verify the bwrap command includes --uid and --gid flags
            # (privilege dropping is now handled by bwrap, not preexec_fn)
            call_args = mock_exec.call_args.args
            cmd_list = list(call_args)

            # Check --uid flag is present with correct value
            assert "--uid" in cmd_list
            uid_idx = cmd_list.index("--uid")
            assert cmd_list[uid_idx + 1] == "2000"

            # Check --gid flag is present with correct value
            assert "--gid" in cmd_list
            gid_idx = cmd_list.index("--gid")
            assert cmd_list[gid_idx + 1] == "2000"


class TestOptionalMounts:
    """Test optional mount handling (skip missing mounts when optional=True)."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace

    def test_optional_mount_skipped_when_missing(self, workspace: Path) -> None:
        """Optional mount that doesn't exist is silently skipped."""
        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
                "persistent": SandboxMount(
                    source="/nonexistent/persistent/path",
                    target="/workspace/external/persistent",
                    mode="rw",
                    optional=True,
                ),
            },
        )
        executor = SandboxExecutor(config)

        # Should NOT raise - optional mount should be skipped
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)

        # Verify bwrap command was built successfully
        assert cmd[0] == "bwrap"
        assert "echo" in cmd
        assert "hello" in cmd

        # Verify the optional mount is NOT in the command
        cmd_str = " ".join(cmd)
        assert "/nonexistent/persistent/path" not in cmd_str

    def test_required_mount_still_raises_when_missing(self, workspace: Path) -> None:
        """Non-optional (required) mount that doesn't exist still raises error."""
        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
                "required_mount": SandboxMount(
                    source="/nonexistent/required/path",
                    target="/workspace/required",
                    mode="ro",
                    optional=False,  # Explicitly required
                ),
            },
        )
        executor = SandboxExecutor(config)

        # SHOULD raise for required mount
        with pytest.raises(SandboxMountError, match="does not exist"):
            executor.build_bwrap_command(["echo", "hello"], allow_network=False)

    def test_optional_mount_included_when_exists(self, workspace: Path, tmp_path: Path) -> None:
        """Optional mount that exists IS included in the command."""
        persistent = tmp_path / "persistent"
        persistent.mkdir()

        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
                "persistent": SandboxMount(
                    source=str(persistent),
                    target="/workspace/external/persistent",
                    mode="rw",
                    optional=True,
                ),
            },
        )
        executor = SandboxExecutor(config)

        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)
        cmd_str = " ".join(cmd)

        # When the path exists, it SHOULD be included
        assert str(persistent) in cmd_str
        assert "/workspace/external/persistent" in cmd_str

    def test_validate_mount_sources_excludes_optional_missing(
        self, workspace: Path, tmp_path: Path
    ) -> None:
        """validate_mount_sources excludes optional mounts from missing list."""
        real_path = tmp_path / "real"
        real_path.mkdir()

        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
            dynamic_mounts=[
                SandboxMount(
                    source="/nonexistent/optional",
                    target="/optional",
                    mode="ro",
                    optional=True,
                ),
                SandboxMount(
                    source="/nonexistent/required",
                    target="/required",
                    mode="ro",
                    optional=False,
                ),
            ],
        )
        executor = SandboxExecutor(config)

        missing = executor.validate_mount_sources()

        # Optional mount should NOT be in missing list
        assert "/nonexistent/optional" not in missing
        # Required mount SHOULD be in missing list
        assert "/nonexistent/required" in missing


class TestWorkspaceAndMountAccess:
    """Test file access validation for workspace, RO/RW mounts, and persistent storage."""

    @pytest.fixture
    def mount_structure(self, tmp_path: Path) -> dict[str, Path]:
        """Create realistic mount structure for testing."""
        # Main workspace
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "project.py").write_text("# Main project file")
        (workspace / "subdir").mkdir()
        (workspace / "subdir" / "nested.txt").write_text("nested content")

        # External mounts structure
        external = workspace / "external"
        external.mkdir()

        # Read-only mount
        ro_base = tmp_path / "mounts" / "ro"
        ro_mount = ro_base / "docs"
        ro_mount.mkdir(parents=True)
        (ro_mount / "readme.md").write_text("# Documentation")
        (ro_mount / "subdir").mkdir()
        (ro_mount / "subdir" / "guide.txt").write_text("guide content")

        # Symlink for ro mount inside workspace
        (external / "ro").mkdir()
        (external / "ro" / "docs").symlink_to(ro_mount)

        # Read-write mount
        rw_base = tmp_path / "mounts" / "rw"
        rw_mount = rw_base / "projects"
        rw_mount.mkdir(parents=True)
        (rw_mount / "editable.py").write_text("# Editable file")

        # Symlink for rw mount inside workspace
        (external / "rw").mkdir()
        (external / "rw" / "projects").symlink_to(rw_mount)

        # Persistent storage
        persistent = tmp_path / "users" / "testuser" / "ag3ntum" / "persistent"
        persistent.mkdir(parents=True)
        (persistent / "cache.json").write_text('{"cached": true}')

        # Symlink for persistent inside workspace
        (external / "persistent").symlink_to(persistent)

        return {
            "workspace": workspace,
            "ro_base": ro_base,
            "rw_base": rw_base,
            "persistent": persistent,
            "external": external,
            "root": tmp_path,
        }

    @pytest.fixture
    def validator(self, mount_structure: dict[str, Path]):
        """Create path validator with mount configuration."""
        from src.core.path_validator import Ag3ntumPathValidator, PathValidatorConfig

        config = PathValidatorConfig(
            workspace_path=mount_structure["workspace"],
            external_ro_base=mount_structure["ro_base"],
            external_rw_base=mount_structure["rw_base"],
            persistent_path=mount_structure["persistent"],
        )
        return Ag3ntumPathValidator(config)

    def test_read_workspace_file_allowed(
        self, validator, mount_structure: dict[str, Path]
    ) -> None:
        """Agent can read files in main workspace."""
        result = validator.validate_path("/workspace/project.py", "read")
        assert result.normalized.exists()
        assert result.is_readonly is False  # Workspace is writable

    def test_write_workspace_file_allowed(
        self, validator, mount_structure: dict[str, Path]
    ) -> None:
        """Agent can write files in main workspace."""
        result = validator.validate_path("/workspace/new_file.py", "write")
        assert result.is_readonly is False

    def test_read_nested_workspace_file_allowed(
        self, validator, mount_structure: dict[str, Path]
    ) -> None:
        """Agent can read files in nested workspace directories."""
        result = validator.validate_path("/workspace/subdir/nested.txt", "read")
        assert result.normalized.exists()

    def test_read_ro_mount_root_file_allowed(
        self, validator, mount_structure: dict[str, Path]
    ) -> None:
        """Agent can read files at root of RO mount."""
        result = validator.validate_path(
            "/workspace/external/ro/docs/readme.md", "read"
        )
        assert result.normalized.exists()
        assert result.is_readonly is True

    def test_read_ro_mount_nested_file_allowed(
        self, validator, mount_structure: dict[str, Path]
    ) -> None:
        """Agent can read files in nested directories of RO mount."""
        result = validator.validate_path(
            "/workspace/external/ro/docs/subdir/guide.txt", "read"
        )
        assert result.normalized.exists()
        assert result.is_readonly is True

    def test_write_ro_mount_blocked(self, validator) -> None:
        """Agent cannot write to RO mount."""
        from src.core.path_validator import PathValidationError

        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_path(
                "/workspace/external/ro/docs/new_file.txt", "write"
            )
        assert "read-only" in str(exc_info.value).lower()

    def test_read_rw_mount_allowed(
        self, validator, mount_structure: dict[str, Path]
    ) -> None:
        """Agent can read files in RW mount."""
        result = validator.validate_path(
            "/workspace/external/rw/projects/editable.py", "read"
        )
        assert result.normalized.exists()
        assert result.is_readonly is False

    def test_write_rw_mount_allowed(
        self, validator, mount_structure: dict[str, Path]
    ) -> None:
        """Agent can write files in RW mount."""
        result = validator.validate_path(
            "/workspace/external/rw/projects/new_file.py", "write"
        )
        assert result.is_readonly is False

    def test_read_persistent_storage_allowed(
        self, validator, mount_structure: dict[str, Path]
    ) -> None:
        """Agent can read from persistent storage."""
        result = validator.validate_path(
            "/workspace/external/persistent/cache.json", "read"
        )
        assert result.normalized.exists()
        assert result.is_readonly is False

    def test_write_persistent_storage_allowed(
        self, validator, mount_structure: dict[str, Path]
    ) -> None:
        """Agent can write to persistent storage."""
        result = validator.validate_path(
            "/workspace/external/persistent/new_cache.json", "write"
        )
        assert result.is_readonly is False

    def test_path_traversal_outside_workspace_blocked(self, validator) -> None:
        """Path traversal outside workspace is blocked."""
        from src.core.path_validator import PathValidationError

        with pytest.raises(PathValidationError):
            validator.validate_path("/workspace/../etc/passwd", "read")

    def test_path_traversal_inside_ro_mount_blocked(self, validator) -> None:
        """Path traversal from RO mount to escape is blocked."""
        from src.core.path_validator import PathValidationError

        with pytest.raises(PathValidationError):
            validator.validate_path(
                "/workspace/external/ro/docs/../../../etc/passwd", "read"
            )
