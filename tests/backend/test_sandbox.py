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
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
        """Default bwrap path is 'bwrap'."""
        config = SandboxConfig()
        assert config.bwrap_path == "bwrap"

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


class TestSandboxExecutor:
    """Test SandboxExecutor command building."""

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
        """Built command starts with bwrap."""
        executor = SandboxExecutor(basic_config)
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)
        assert cmd[0] == "bwrap"

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
        """Execution with UID/GID sets preexec_fn for privilege dropping."""
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

            # Verify preexec_fn was passed
            call_kwargs = mock_exec.call_args.kwargs
            assert "preexec_fn" in call_kwargs
            assert call_kwargs["preexec_fn"] is not None
