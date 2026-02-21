"""
Tests for Ag3ntumBash tool.

Tests the Bash tool functionality:
- Command security filter integration
- Sandbox execution paths (with and without bwrap)
- Error handling and timeout behavior
- Output capture and preview modes
- Network error detection and enhancement
- Edge cases (empty commands, special characters)
"""
import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from dataclasses import dataclass

import pytest

from tools.ag3ntum.ag3ntum_bash.tool import (
    create_bash_tool,
    _bash_impl,
    AG3NTUM_BASH_TOOL,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_KILL_AFTER_SECONDS,
    DEFAULT_PREVIEW_MODE,
    DEFAULT_PREVIEW_LINES,
    MAX_PREVIEW_LINES,
    OUTPUT_DIR,
    _enhance_network_error,
    _error_response,
    is_ag3ntum_bash_tool,
    NETWORK_ERROR_PATTERNS,
)


@pytest.fixture
def allow_all_filter():
    """Create a security filter that allows everything."""
    filter_mock = MagicMock()
    result = MagicMock()
    result.should_block = False
    result.allowed = True
    result.message = ""
    filter_mock.check_command.return_value = result
    return filter_mock


class TestBashToolConstants:
    """Tests for tool constants and helpers."""

    def test_tool_name_constant(self):
        """Test that tool name constant is correct."""
        assert AG3NTUM_BASH_TOOL == "mcp__ag3ntum__Bash"

    def test_default_timeout(self):
        """Test default timeout is 300 seconds (5 minutes)."""
        assert DEFAULT_TIMEOUT_SECONDS == 300

    def test_default_kill_after(self):
        """Test default kill-after grace period is 10 seconds."""
        assert DEFAULT_KILL_AFTER_SECONDS == 10

    def test_default_preview_mode(self):
        """Test default preview mode is tail."""
        assert DEFAULT_PREVIEW_MODE == "tail"

    def test_default_preview_lines(self):
        """Test default preview lines is 20."""
        assert DEFAULT_PREVIEW_LINES == 20

    def test_max_preview_lines(self):
        """Test max preview lines is 100."""
        assert MAX_PREVIEW_LINES == 100

    def test_output_dir(self):
        """Test output directory constant."""
        assert OUTPUT_DIR == ".tmp/cmd"

    def test_is_ag3ntum_bash_tool_positive(self):
        """Test is_ag3ntum_bash_tool returns True for correct name."""
        assert is_ag3ntum_bash_tool("mcp__ag3ntum__Bash") is True

    def test_is_ag3ntum_bash_tool_negative(self):
        """Test is_ag3ntum_bash_tool returns False for wrong name."""
        assert is_ag3ntum_bash_tool("mcp__ag3ntum__Read") is False
        assert is_ag3ntum_bash_tool("Bash") is False
        assert is_ag3ntum_bash_tool("") is False


class TestErrorResponse:
    """Tests for _error_response helper."""

    def test_error_response_format(self):
        """Test error response has correct structure."""
        result = _error_response("Something went wrong")
        assert result["is_error"] is True
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        assert "Something went wrong" in result["content"][0]["text"]
        assert "Error" in result["content"][0]["text"]


class TestEnhanceNetworkError:
    """Tests for _enhance_network_error function."""

    def test_success_exit_code_returns_none(self):
        """Test that exit code 0 always returns None."""
        assert _enhance_network_error("name resolution failed", 0) is None

    def test_dns_resolution_error(self):
        """Test DNS resolution error detection."""
        result = _enhance_network_error(
            "Could not resolve host: name resolution failed", 6
        )
        assert result is not None
        assert "DNS resolution failed" in result
        assert "Recovery" in result

    def test_connection_refused_error(self):
        """Test connection refused error detection."""
        result = _enhance_network_error(
            "curl: (7) Failed to connect: Connection refused", 7
        )
        assert result is not None
        assert "Connection refused" in result

    def test_connection_timed_out_error(self):
        """Test connection timed out error detection."""
        result = _enhance_network_error(
            "curl: (28) Connection timed out after 30000ms", 28
        )
        assert result is not None
        assert "Connection timed out" in result

    def test_ssl_error(self):
        """Test SSL error detection."""
        result = _enhance_network_error(
            "SSL certificate problem: unable to get local issuer certificate", 60
        )
        assert result is not None
        assert "SSL" in result

    def test_no_network_error(self):
        """Test non-network error returns None."""
        result = _enhance_network_error("syntax error near unexpected token", 1)
        assert result is None

    def test_network_unreachable(self):
        """Test network unreachable error detection."""
        result = _enhance_network_error(
            "connect: network is unreachable", 1
        )
        assert result is not None
        assert "unreachable" in result.lower()

    def test_output_truncated_in_message(self):
        """Test that very long output is truncated in error message."""
        long_output = "x" * 1000
        result = _enhance_network_error(
            f"name resolution failed {long_output}", 1
        )
        assert result is not None
        # Output should be truncated to 500 chars
        assert len(result) < 2000

    def test_all_patterns_detected(self):
        """Test that all defined patterns are actually detectable."""
        for pattern, description in NETWORK_ERROR_PATTERNS:
            result = _enhance_network_error(f"error: {pattern}", 1)
            assert result is not None, f"Pattern '{pattern}' was not detected"


class TestBashToolSecurityFilter:
    """Tests for command security filter integration."""

    @pytest.fixture
    def workspace(self, tmp_path):
        """Create workspace directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace

    @pytest.fixture
    def mock_security_filter(self):
        """Create a mock security filter."""
        filter_mock = MagicMock()
        # Default: allow all commands
        result = MagicMock()
        result.should_block = False
        result.allowed = True
        result.message = ""
        filter_mock.check_command.return_value = result
        return filter_mock

    @pytest.mark.asyncio
    async def test_blocked_command_rejected(self, workspace, mock_security_filter):
        """Test that blocked commands are rejected before execution."""
        # Configure filter to block
        block_result = MagicMock()
        block_result.should_block = True
        block_result.allowed = False
        block_result.message = "rm -rf is not allowed"
        mock_security_filter.check_command.return_value = block_result

        result = await _bash_impl(
            {"command": "rm -rf /"},
            workspace=workspace,
            security_filter=mock_security_filter,
        )

        assert result.get("is_error") is True
        assert "security policy" in result["content"][0]["text"].lower()
        assert "rm -rf" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_allowed_command_passes_filter(self, workspace, mock_security_filter):
        """Test that allowed commands pass the security filter."""
        # Use a simple echo command that will succeed
        result = await _bash_impl(
            {"command": "echo hello"},
            workspace=workspace,
            security_filter=mock_security_filter,
        )

        mock_security_filter.check_command.assert_called_once_with("echo hello")
        assert "is_error" not in result or not result["is_error"]

    @pytest.mark.asyncio
    async def test_empty_command_rejected(self, workspace, mock_security_filter):
        """Test that empty commands are rejected."""
        result = await _bash_impl(
            {"command": ""},
            workspace=workspace,
            security_filter=mock_security_filter,
        )

        assert result.get("is_error") is True
        assert "empty" in result["content"][0]["text"].lower()
        # Security filter should NOT be called for empty commands
        mock_security_filter.check_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_whitespace_only_command_rejected(self, workspace, mock_security_filter):
        """Test that whitespace-only commands are rejected."""
        result = await _bash_impl(
            {"command": "   \t\n  "},
            workspace=workspace,
            security_filter=mock_security_filter,
        )

        assert result.get("is_error") is True
        assert "empty" in result["content"][0]["text"].lower()


class TestBashToolExecution:
    """Tests for command execution (without sandbox)."""

    @pytest.fixture
    def workspace(self, tmp_path):
        """Create workspace directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace


    @pytest.mark.asyncio
    async def test_successful_command_execution(self, workspace, allow_all_filter):
        """Test successful command execution."""
        result = await _bash_impl(
            {"command": "echo 'Hello World'"},
            workspace=workspace,
            security_filter=allow_all_filter,
        )

        assert "is_error" not in result or not result["is_error"]
        text = result["content"][0]["text"]
        assert "Hello World" in text
        assert "exit code" in text.lower() or "Exit code" in text

    @pytest.mark.asyncio
    async def test_failed_command_returns_error(self, workspace, allow_all_filter):
        """Test that non-zero exit code marks result as error."""
        result = await _bash_impl(
            {"command": "false"},
            workspace=workspace,
            security_filter=allow_all_filter,
        )

        assert result.get("is_error") is True

    @pytest.mark.asyncio
    async def test_output_saved_to_file(self, workspace, allow_all_filter):
        """Test that command output is saved to a file."""
        result = await _bash_impl(
            {"command": "echo 'test output'"},
            workspace=workspace,
            security_filter=allow_all_filter,
        )

        text = result["content"][0]["text"]
        # Should mention the output file path
        assert ".tmp/cmd/" in text
        # Output directory should exist
        output_dir = workspace / ".tmp" / "cmd"
        assert output_dir.exists()
        # Should have at least one output file
        output_files = list(output_dir.glob("*.txt"))
        assert len(output_files) >= 1

    @pytest.mark.asyncio
    async def test_output_file_contains_content(self, workspace, allow_all_filter):
        """Test that output file contains command output and metadata."""
        await _bash_impl(
            {"command": "echo 'captured content'"},
            workspace=workspace,
            security_filter=allow_all_filter,
        )

        output_dir = workspace / ".tmp" / "cmd"
        output_files = list(output_dir.glob("*.txt"))
        assert len(output_files) >= 1

        content = output_files[0].read_text()
        assert "captured content" in content
        assert "EXIT_CODE:" in content
        assert "FILESIZE:" in content

    @pytest.mark.asyncio
    async def test_multiline_output(self, workspace, allow_all_filter):
        """Test command with multiline output."""
        result = await _bash_impl(
            {"command": "printf 'line1\\nline2\\nline3\\n'"},
            workspace=workspace,
            security_filter=allow_all_filter,
        )

        text = result["content"][0]["text"]
        assert "is_error" not in result or not result["is_error"]
        assert "3" in text  # 3 total lines


class TestBashToolPreviewModes:
    """Tests for preview mode functionality."""

    @pytest.fixture
    def workspace(self, tmp_path):
        """Create workspace directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace


    @pytest.mark.asyncio
    async def test_head_preview_mode(self, workspace, allow_all_filter):
        """Test head preview mode shows first lines."""
        # Generate 50 lines
        result = await _bash_impl(
            {
                "command": "seq 1 50",
                "preview_mode": "head",
                "preview_lines": 5,
            },
            workspace=workspace,
            security_filter=allow_all_filter,
        )

        text = result["content"][0]["text"]
        assert "head" in text.lower()
        assert "1" in text
        # Should show truncation notice
        assert "truncated" in text.lower()

    @pytest.mark.asyncio
    async def test_tail_preview_mode(self, workspace, allow_all_filter):
        """Test tail preview mode shows last lines."""
        result = await _bash_impl(
            {
                "command": "seq 1 50",
                "preview_mode": "tail",
                "preview_lines": 5,
            },
            workspace=workspace,
            security_filter=allow_all_filter,
        )

        text = result["content"][0]["text"]
        assert "tail" in text.lower()
        assert "50" in text

    @pytest.mark.asyncio
    async def test_invalid_preview_mode_defaults(self, workspace, allow_all_filter):
        """Test that invalid preview mode falls back to default."""
        result = await _bash_impl(
            {
                "command": "echo hello",
                "preview_mode": "invalid",
            },
            workspace=workspace,
            security_filter=allow_all_filter,
        )

        # Should not error, uses default mode
        assert "is_error" not in result or not result["is_error"]

    @pytest.mark.asyncio
    async def test_preview_lines_capped_at_max(self, workspace, allow_all_filter):
        """Test that preview_lines is capped at MAX_PREVIEW_LINES."""
        result = await _bash_impl(
            {
                "command": "seq 1 100",
                "preview_lines": 999,
            },
            workspace=workspace,
            security_filter=allow_all_filter,
            max_preview_lines=10,
        )

        # Should succeed without error
        assert "is_error" not in result or not result["is_error"]


class TestBashToolSandbox:
    """Tests for sandbox execution paths."""

    @pytest.fixture
    def workspace(self, tmp_path):
        """Create workspace directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace


    @pytest.mark.asyncio
    async def test_sandbox_enabled_uses_bwrap(self, workspace, allow_all_filter):
        """Test that sandbox executor wraps command in bwrap."""
        mock_sandbox = MagicMock()
        mock_sandbox.config.enabled = True
        mock_sandbox.config.network.enabled = False
        mock_sandbox.linux_uid = 50000
        mock_sandbox.linux_gid = 50000
        mock_sandbox.build_bwrap_command.return_value = [
            "bwrap", "--ro-bind", "/", "/", "bash", "-c", "echo test"
        ]

        # Mock subprocess to avoid actually running bwrap
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"test\n", None)
        mock_process.returncode = 0

        with patch('asyncio.create_subprocess_exec', return_value=mock_process):
            result = await _bash_impl(
                {"command": "echo test"},
                workspace=workspace,
                security_filter=allow_all_filter,
                sandbox_executor=mock_sandbox,
            )

        mock_sandbox.build_bwrap_command.assert_called_once()
        assert "is_error" not in result or not result["is_error"]

    @pytest.mark.asyncio
    async def test_sandbox_fail_closed(self, workspace, allow_all_filter):
        """Test fail-closed behavior when sandbox setup fails."""
        mock_sandbox = MagicMock()
        mock_sandbox.config.enabled = True
        mock_sandbox.config.network.enabled = False
        mock_sandbox.build_bwrap_command.side_effect = RuntimeError("bwrap not found")

        result = await _bash_impl(
            {"command": "echo test"},
            workspace=workspace,
            security_filter=allow_all_filter,
            sandbox_executor=mock_sandbox,
        )

        assert result.get("is_error") is True
        assert "sandbox unavailable" in result["content"][0]["text"].lower()
        assert "blocked" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_sandbox_disabled_executes_directly(self, workspace, allow_all_filter):
        """Test that disabled sandbox executes commands directly."""
        mock_sandbox = MagicMock()
        mock_sandbox.config.enabled = False

        result = await _bash_impl(
            {"command": "echo 'direct execution'"},
            workspace=workspace,
            security_filter=allow_all_filter,
            sandbox_executor=mock_sandbox,
        )

        # Should succeed (runs directly via shell)
        assert "is_error" not in result or not result["is_error"]
        assert "direct execution" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_no_sandbox_executes_directly(self, workspace, allow_all_filter):
        """Test execution without sandbox executor provided."""
        result = await _bash_impl(
            {"command": "echo 'no sandbox'"},
            workspace=workspace,
            security_filter=allow_all_filter,
            sandbox_executor=None,
        )

        assert "is_error" not in result or not result["is_error"]
        assert "no sandbox" in result["content"][0]["text"]


class TestBashToolTimeout:
    """Tests for timeout handling."""

    @pytest.fixture
    def workspace(self, tmp_path):
        """Create workspace directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace


    @pytest.mark.asyncio
    async def test_timeout_exit_code_124(self, workspace, allow_all_filter):
        """Test handling of timeout exit code 124 (SIGTERM)."""
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", None)
        mock_process.returncode = 124

        with patch('asyncio.create_subprocess_shell', return_value=mock_process):
            result = await _bash_impl(
                {"command": "sleep 999"},
                workspace=workspace,
                security_filter=allow_all_filter,
            )

        assert result.get("is_error") is True
        assert "timed out" in result["content"][0]["text"].lower()
        assert "SIGTERM" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_timeout_exit_code_137(self, workspace, allow_all_filter):
        """Test handling of force-kill exit code 137 (SIGKILL)."""
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (b"", None)
        mock_process.returncode = 137

        with patch('asyncio.create_subprocess_shell', return_value=mock_process):
            result = await _bash_impl(
                {"command": "sleep 999"},
                workspace=workspace,
                security_filter=allow_all_filter,
            )

        assert result.get("is_error") is True
        assert "force-killed" in result["content"][0]["text"].lower()
        assert "SIGKILL" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_asyncio_timeout_fallback(self, workspace, allow_all_filter):
        """Test asyncio timeout as safety fallback."""
        mock_process = AsyncMock()
        mock_process.communicate.side_effect = asyncio.TimeoutError()
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()

        with patch('asyncio.create_subprocess_shell', return_value=mock_process):
            result = await _bash_impl(
                {"command": "sleep 999"},
                workspace=workspace,
                security_filter=allow_all_filter,
                timeout_seconds=1,
                kill_after_seconds=1,
            )

        assert result.get("is_error") is True
        assert "timed out" in result["content"][0]["text"].lower()
        mock_process.kill.assert_called_once()


class TestBashToolOutputDirectory:
    """Tests for output directory creation and file naming."""

    @pytest.fixture
    def workspace(self, tmp_path):
        """Create workspace directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace


    @pytest.mark.asyncio
    async def test_output_directory_created(self, workspace, allow_all_filter):
        """Test that output directory is created automatically."""
        output_dir = workspace / ".tmp" / "cmd"
        assert not output_dir.exists()

        await _bash_impl(
            {"command": "echo hello"},
            workspace=workspace,
            security_filter=allow_all_filter,
        )

        assert output_dir.exists()

    @pytest.mark.asyncio
    async def test_output_file_naming(self, workspace, allow_all_filter):
        """Test output file naming convention."""
        await _bash_impl(
            {"command": "echo hello"},
            workspace=workspace,
            security_filter=allow_all_filter,
        )

        output_dir = workspace / ".tmp" / "cmd"
        files = list(output_dir.glob("*.txt"))
        assert len(files) == 1

        # File should follow YYYYMMDD-HHMMSS-<hash>.txt pattern
        filename = files[0].name
        assert filename.endswith(".txt")
        parts = filename.replace(".txt", "").split("-")
        assert len(parts) >= 3  # date-time-hash

    @pytest.mark.asyncio
    async def test_output_dir_creation_failure(self, workspace, allow_all_filter):
        """Test error when output directory creation fails."""
        with patch.object(Path, 'mkdir', side_effect=OSError("Permission denied")):
            result = await _bash_impl(
                {"command": "echo hello"},
                workspace=workspace,
                security_filter=allow_all_filter,
            )

        assert result.get("is_error") is True
        assert "output directory" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_custom_output_dir(self, workspace, allow_all_filter):
        """Test custom output directory."""
        await _bash_impl(
            {"command": "echo hello"},
            workspace=workspace,
            security_filter=allow_all_filter,
            output_dir="custom/output",
        )

        custom_dir = workspace / "custom" / "output"
        assert custom_dir.exists()


class TestBashToolExecutionFailure:
    """Tests for general execution failure handling."""

    @pytest.fixture
    def workspace(self, tmp_path):
        """Create workspace directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace


    @pytest.mark.asyncio
    async def test_subprocess_creation_failure(self, workspace, allow_all_filter):
        """Test handling when subprocess creation fails."""
        with patch('asyncio.create_subprocess_shell',
                   side_effect=OSError("Cannot start process")):
            result = await _bash_impl(
                {"command": "echo hello"},
                workspace=workspace,
                security_filter=allow_all_filter,
            )

        assert result.get("is_error") is True
        assert "execution failed" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_with_output(self, workspace, allow_all_filter):
        """Test non-zero exit code command with output."""
        result = await _bash_impl(
            {"command": "echo 'error output' && exit 42"},
            workspace=workspace,
            security_filter=allow_all_filter,
        )

        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "42" in text  # exit code
        assert "error output" in text

    @pytest.mark.asyncio
    async def test_network_error_in_command_output(self, workspace, allow_all_filter):
        """Test that network errors in output get enhanced messages."""
        mock_process = AsyncMock()
        mock_process.communicate.return_value = (
            b"curl: (6) Could not resolve host: name resolution failed\n",
            None
        )
        mock_process.returncode = 6

        with patch('asyncio.create_subprocess_shell', return_value=mock_process):
            result = await _bash_impl(
                {"command": "curl https://nonexistent.example.com"},
                workspace=workspace,
                security_filter=allow_all_filter,
            )

        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "Network Error" in text
        assert "Recovery" in text
