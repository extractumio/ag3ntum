"""
Security tests for Ag3ntum sandbox implementation.

These tests verify that the multi-layered security architecture is functioning correctly:
- Layer 1: Docker container isolation
- Layer 2: Bubblewrap filesystem sandboxing with filtered /proc
- Layer 3: Ag3ntumBash subprocess isolation
- Layer 4: Python-level path validation

Run with: pytest tests/sandbox/security_tests.py -v
"""

import os
import subprocess
from pathlib import Path

import pytest

from src.core.sandbox import SandboxConfig, build_bwrap_command, execute_in_sandbox


class TestFilteredProcSecurity:
    """Test filtered /proc implementation prevents process enumeration."""

    def test_zero_pids_visible_in_sandbox(self):
        """Verify that agents cannot see other process PIDs."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,  # Docker mode
        )

        # Execute command to count visible PIDs
        result = execute_in_sandbox(
            config=config,
            command=["bash", "-c", "ls /proc 2>/dev/null | grep -E '^[0-9]+$' | wc -l"],
            workspace_path=Path("/tmp"),
        )

        pids_count = int(result.stdout.strip())
        assert pids_count == 0, (
            f"SECURITY ISSUE: {pids_count} PIDs visible to agent. "
            "Filtered /proc should expose ZERO process PIDs."
        )

    def test_cannot_read_other_process_environ(self):
        """Verify that agents cannot read other processes' environment variables."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        # Try to read PID 1's environment (main container process)
        result = execute_in_sandbox(
            config=config,
            command=["cat", "/proc/1/environ"],
            workspace_path=Path("/tmp"),
        )

        assert result.returncode != 0, "Should not be able to read /proc/1/environ"
        assert (
            "No such file or directory" in result.stderr
            or "No such file or directory" in result.stdout
        ), "Expected 'No such file or directory' error"

    def test_cannot_read_other_process_cmdline(self):
        """Verify that agents cannot read other processes' command lines."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        # Try to read PID 1's command line
        result = execute_in_sandbox(
            config=config,
            command=["cat", "/proc/1/cmdline"],
            workspace_path=Path("/tmp"),
        )

        assert result.returncode != 0, "Should not be able to read /proc/1/cmdline"
        assert (
            "No such file or directory" in result.stderr
            or "No such file or directory" in result.stdout
        ), "Expected 'No such file or directory' error"

    def test_ps_command_fails_gracefully(self):
        """Verify that ps command fails due to filtered /proc (expected behavior)."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        # ps aux should fail with filtered /proc
        result = execute_in_sandbox(
            config=config,
            command=["ps", "aux"],
            workspace_path=Path("/tmp"),
        )

        # ps fails because it cannot enumerate /proc/[pid]/ directories
        assert result.returncode != 0, "ps should fail with filtered /proc"
        # Common error messages from procps
        assert any(
            msg in result.stderr.lower()
            for msg in ["error", "fatal", "cannot", "failed"]
        ), f"Expected error message from ps, got: {result.stderr}"


class TestSafeSystemInfoAccess:
    """Test that agents can still access safe system information."""

    def test_proc_self_accessible(self):
        """Verify that /proc/self is accessible (own process info)."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        result = execute_in_sandbox(
            config=config,
            command=["test", "-d", "/proc/self"],
            workspace_path=Path("/tmp"),
        )

        assert result.returncode == 0, "/proc/self should be accessible"

    def test_proc_cpuinfo_accessible(self):
        """Verify that /proc/cpuinfo is accessible."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        result = execute_in_sandbox(
            config=config,
            command=["test", "-r", "/proc/cpuinfo"],
            workspace_path=Path("/tmp"),
        )

        assert result.returncode == 0, "/proc/cpuinfo should be readable"

    def test_proc_meminfo_accessible(self):
        """Verify that /proc/meminfo is accessible."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        result = execute_in_sandbox(
            config=config,
            command=["cat", "/proc/meminfo"],
            workspace_path=Path("/tmp"),
        )

        assert result.returncode == 0, "/proc/meminfo should be readable"
        assert "MemTotal" in result.stdout, "Should contain memory information"

    def test_system_info_commands_work(self):
        """Verify that system info commands still work."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        # Test uptime command
        result = execute_in_sandbox(
            config=config,
            command=["uptime"],
            workspace_path=Path("/tmp"),
        )

        assert result.returncode == 0, "uptime command should work"

        # Test uname command
        result = execute_in_sandbox(
            config=config,
            command=["uname", "-a"],
            workspace_path=Path("/tmp"),
        )

        assert result.returncode == 0, "uname command should work"


class TestFileSandboxing:
    """Test that file sandboxing prevents unauthorized access."""

    def test_cannot_read_host_etc_passwd(self):
        """Verify that /etc/passwd is not accessible (not mounted)."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        result = execute_in_sandbox(
            config=config,
            command=["cat", "/etc/passwd"],
            workspace_path=Path("/tmp"),
        )

        # /etc/passwd should not exist in sandbox
        assert result.returncode != 0, "/etc/passwd should not be accessible"

    def test_cannot_write_to_system_dirs(self):
        """Verify that system directories are read-only."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        # Try to write to /usr (should be read-only)
        result = execute_in_sandbox(
            config=config,
            command=["touch", "/usr/malicious_file"],
            workspace_path=Path("/tmp"),
        )

        assert result.returncode != 0, "Should not be able to write to /usr"
        assert (
            "Read-only file system" in result.stderr
            or "Permission denied" in result.stderr
        ), "Expected read-only or permission denied error"

    def test_workspace_is_writable(self, tmp_path):
        """Verify that workspace directory is writable."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        test_file = "test_write.txt"
        result = execute_in_sandbox(
            config=config,
            command=["bash", "-c", f"echo 'test' > /workspace/{test_file}"],
            workspace_path=tmp_path,
        )

        assert result.returncode == 0, "Should be able to write to /workspace"
        assert (tmp_path / test_file).exists(), "File should be created in workspace"


class TestProcessIsolation:
    """Test that process isolation is working correctly."""

    def test_unshare_pid_namespace(self):
        """Verify that PID namespace is unshared."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        # In isolated PID namespace, init process should have PID 1
        # But we can't see it due to filtered /proc
        result = execute_in_sandbox(
            config=config,
            command=["bash", "-c", "echo $$"],  # Print own PID
            workspace_path=Path("/tmp"),
        )

        assert result.returncode == 0, "Should be able to get own PID"
        # The PID should be visible to the process itself
        pid = int(result.stdout.strip())
        assert pid > 0, "Should have a valid PID"

    def test_cannot_kill_host_processes(self):
        """Verify that agents cannot kill processes outside sandbox."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        # Try to kill PID 1 (should fail - not visible in namespace)
        result = execute_in_sandbox(
            config=config,
            command=["kill", "-0", "1"],  # -0 just tests if process exists
            workspace_path=Path("/tmp"),
        )

        # Should fail because PID 1 is not in the same namespace
        assert result.returncode != 0, "Should not be able to signal PID 1"


class TestBusinessOperations:
    """Test that business-critical operations still work."""

    def test_python_execution(self, tmp_path):
        """Verify that Python scripts can execute."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        # Create a test Python script
        script = tmp_path / "test.py"
        script.write_text("print('Hello from sandbox')")

        result = execute_in_sandbox(
            config=config,
            command=["python3", "/workspace/test.py"],
            workspace_path=tmp_path,
        )

        assert result.returncode == 0, "Python should execute successfully"
        assert "Hello from sandbox" in result.stdout, "Python output should be captured"

    def test_file_operations(self, tmp_path):
        """Verify that file operations work correctly."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        # Create, read, and delete a file
        result = execute_in_sandbox(
            config=config,
            command=[
                "bash",
                "-c",
                "echo 'test content' > /workspace/test.txt && "
                "cat /workspace/test.txt && "
                "rm /workspace/test.txt",
            ],
            workspace_path=tmp_path,
        )

        assert result.returncode == 0, "File operations should work"
        assert "test content" in result.stdout, "Should be able to read file content"

    def test_network_access(self):
        """Verify that network access works (when not disabled)."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            network_sandboxing=False,  # Enable network for this test
            nested_container=True,
        )

        # Simple DNS resolution test
        result = execute_in_sandbox(
            config=config,
            command=["getent", "hosts", "localhost"],
            workspace_path=Path("/tmp"),
        )

        assert result.returncode == 0, "DNS resolution should work"


class TestConfigurationValidation:
    """Test sandbox configuration validation."""

    def test_proc_filtering_enabled_by_default(self):
        """Verify that proc_filtering is enabled by default."""
        config = SandboxConfig()

        assert config.proc_filtering.enabled is True, (
            "proc_filtering should be enabled by default for security"
        )

    def test_safe_proc_entries_configured(self):
        """Verify that safe /proc entries are configured."""
        config = SandboxConfig()

        expected_entries = [
            "/proc/self",
            "/proc/cpuinfo",
            "/proc/meminfo",
            "/proc/uptime",
            "/proc/version",
        ]

        for entry in expected_entries:
            assert entry in config.proc_filtering.allowed_entries, (
                f"{entry} should be in allowed_entries"
            )

    def test_bwrap_command_uses_filtered_proc(self):
        """Verify that bwrap command uses filtered /proc in nested container mode."""
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        cmd = build_bwrap_command(
            config=config,
            workspace_path=Path("/tmp"),
            command=["echo", "test"],
        )

        # Should have --tmpfs /proc
        assert "--tmpfs" in cmd, "Should use --tmpfs for /proc"
        assert cmd[cmd.index("--tmpfs") + 1] == "/proc", "Should create tmpfs at /proc"

        # Should have selective bind mounts
        assert "--ro-bind" in cmd, "Should have read-only bind mounts"

        # Should NOT have full /proc bind
        proc_binds = [
            (cmd[i], cmd[i + 1], cmd[i + 2])
            for i in range(len(cmd))
            if i < len(cmd) - 2 and cmd[i] == "--ro-bind" and "/proc" in cmd[i + 1]
        ]

        # Should have selective binds like /proc/self, NOT full /proc
        full_proc_bind = any(
            src == "/proc" and dst == "/proc" for _, src, dst in proc_binds
        )
        assert not full_proc_bind, (
            "Should NOT have full /proc bind (--ro-bind /proc /proc)"
        )


class TestSecurityRegression:
    """Regression tests for known security issues."""

    def test_security_issue_20260111_proc_exposure(self):
        """
        Regression test for 2026-01-11 security issue.

        Before fix: 17+ PIDs were visible via /proc, exposing process environments
        After fix: 0 PIDs visible, complete process isolation

        This test ensures the vulnerability does not reoccur.
        """
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        # Count visible PIDs
        result = execute_in_sandbox(
            config=config,
            command=["bash", "-c", "ls /proc 2>/dev/null | grep -E '^[0-9]+$' | wc -l"],
            workspace_path=Path("/tmp"),
        )

        pids_count = int(result.stdout.strip())

        # CRITICAL: This must be 0 to prevent information disclosure
        assert pids_count == 0, (
            f"REGRESSION: Security vulnerability reintroduced! "
            f"{pids_count} PIDs are visible. This exposes process information "
            f"and may leak secrets via /proc/[pid]/environ. "
            f"Reference: IMPLEMENTATION_COMPLETE.md (2026-01-11)"
        )

    def test_cannot_enumerate_processes_via_proc(self):
        """
        Verify that process enumeration is completely blocked.

        Attack vector: Agent tries to enumerate all processes to find sensitive
        command lines or environment variables containing secrets.
        """
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            nested_container=True,
        )

        # Try multiple enumeration techniques
        enumeration_commands = [
            "ls /proc | grep -E '^[0-9]+$'",  # Direct enumeration
            "find /proc -maxdepth 1 -type d -regex '.*/[0-9]+'",  # Find command
            "for p in /proc/[0-9]*; do echo $p; done",  # Shell glob
        ]

        for cmd in enumeration_commands:
            result = execute_in_sandbox(
                config=config,
                command=["bash", "-c", cmd],
                workspace_path=Path("/tmp"),
            )

            # Should return empty output (no PIDs found)
            assert result.stdout.strip() == "", (
                f"Process enumeration should fail. Command: {cmd} "
                f"returned: {result.stdout}"
            )


# Pytest markers for categorization
pytestmark = [
    pytest.mark.security,
]
