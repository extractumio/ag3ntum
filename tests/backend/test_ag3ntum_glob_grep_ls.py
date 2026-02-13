"""
Tests for Ag3ntumGlob, Ag3ntumGrep, and Ag3ntumLS tools.

Tests shared MCP tool functionality:
- Path validation and security
- Pattern matching and search
- Error handling
- Edge cases
"""
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.ag3ntum.ag3ntum_glob.tool import (
    create_glob_tool,
    _glob_impl,
    AG3NTUM_GLOB_TOOL,
    MAX_RESULTS as GLOB_MAX_RESULTS,
)
from tools.ag3ntum.ag3ntum_grep.tool import (
    create_grep_tool,
    _grep_impl,
    AG3NTUM_GREP_TOOL,
    MAX_RESULTS as GREP_MAX_RESULTS,
    DEFAULT_CONTEXT_LINES,
)
from tools.ag3ntum.ag3ntum_ls.tool import (
    create_ls_tool,
    _ls_impl,
    AG3NTUM_LS_TOOL,
    MAX_ENTRIES as LS_MAX_ENTRIES,
    _format_size,
)
from src.core.path_validator import PathValidationError


# ============================================================================
# Glob Tool Tests
# ============================================================================

class TestGlobToolConstants:
    """Tests for Glob tool constants."""

    def test_tool_name(self):
        assert AG3NTUM_GLOB_TOOL == "mcp__ag3ntum__Glob"

    def test_max_results(self):
        assert GLOB_MAX_RESULTS == 10000


class TestGlobToolBasic:
    """Tests for basic Glob tool functionality."""

    @pytest.fixture
    def mock_validator(self, tmp_path):
        validator = MagicMock()
        validated_result = MagicMock()
        validated_result.normalized = tmp_path
        validator.validate_path.return_value = validated_result
        validator.workspace = tmp_path
        def _display_path(p, ws=tmp_path):
            try:
                return str(p.relative_to(ws))
            except ValueError:
                return str(p.resolve().relative_to(ws.resolve()))
        validator.docker_to_display_path = _display_path
        return validator

    @pytest.mark.asyncio
    async def test_glob_finds_files(self, tmp_path, mock_validator):
        """Test finding files with glob pattern."""
        (tmp_path / "file1.py").write_text("# python")
        (tmp_path / "file2.py").write_text("# python")
        (tmp_path / "file3.txt").write_text("text")

        with patch('tools.ag3ntum.ag3ntum_glob.tool.get_path_validator', return_value=mock_validator):
            result = await _glob_impl({"pattern": "*.py"}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            text = result["content"][0]["text"]
            assert "2 files" in text
            assert "file1.py" in text
            assert "file2.py" in text

    @pytest.mark.asyncio
    async def test_glob_recursive(self, tmp_path, mock_validator):
        """Test recursive glob pattern."""
        subdir = tmp_path / "src"
        subdir.mkdir()
        (subdir / "main.py").write_text("# main")
        (tmp_path / "setup.py").write_text("# setup")

        with patch('tools.ag3ntum.ag3ntum_glob.tool.get_path_validator', return_value=mock_validator):
            result = await _glob_impl({"pattern": "**/*.py"}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            text = result["content"][0]["text"]
            assert "main.py" in text
            assert "setup.py" in text

    @pytest.mark.asyncio
    async def test_glob_no_matches(self, tmp_path, mock_validator):
        """Test glob with no matches."""
        (tmp_path / "file.txt").write_text("text")

        with patch('tools.ag3ntum.ag3ntum_glob.tool.get_path_validator', return_value=mock_validator):
            result = await _glob_impl({"pattern": "*.py"}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            assert "No files found" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_glob_empty_pattern(self):
        """Test empty pattern returns error."""
        result = await _glob_impl({"pattern": ""}, session_id="test-session")

        assert result.get("is_error") is True
        assert "required" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_glob_path_validation_error(self, mock_validator):
        """Test path traversal blocked."""
        from src.core.path_validator import PathValidationError
        mock_validator.validate_path.side_effect = PathValidationError(
            "Blocked", path="/etc", reason="BLOCKED_PATH"
        )

        with patch('tools.ag3ntum.ag3ntum_glob.tool.get_path_validator', return_value=mock_validator):
            result = await _glob_impl({"pattern": "*.txt", "path": "/etc"}, session_id="test-session")

            assert result.get("is_error") is True
            assert "validation failed" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_glob_directory_not_found(self, tmp_path, mock_validator):
        """Test non-existent directory."""
        missing = tmp_path / "nonexistent"
        mock_validator.validate_path.return_value.normalized = missing

        with patch('tools.ag3ntum.ag3ntum_glob.tool.get_path_validator', return_value=mock_validator):
            result = await _glob_impl({"pattern": "*.py", "path": "nonexistent"}, session_id="test-session")

            assert result.get("is_error") is True
            assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_glob_not_a_directory(self, tmp_path, mock_validator):
        """Test error when path is a file, not directory."""
        file_path = tmp_path / "file.txt"
        file_path.write_text("content")
        mock_validator.validate_path.return_value.normalized = file_path

        with patch('tools.ag3ntum.ag3ntum_glob.tool.get_path_validator', return_value=mock_validator):
            result = await _glob_impl({"pattern": "*.py", "path": "file.txt"}, session_id="test-session")

            assert result.get("is_error") is True
            assert "not a directory" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_glob_session_not_configured(self):
        """Test error when session is not configured."""
        with patch('tools.ag3ntum.ag3ntum_glob.tool.get_path_validator',
                   side_effect=RuntimeError("not configured")):
            result = await _glob_impl({"pattern": "*.py"}, session_id="unknown")

            assert result.get("is_error") is True
            assert "Internal error" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_glob_only_returns_files(self, tmp_path, mock_validator):
        """Test that glob only returns files, not directories."""
        (tmp_path / "subdir").mkdir()
        (tmp_path / "file.py").write_text("# python")

        with patch('tools.ag3ntum.ag3ntum_glob.tool.get_path_validator', return_value=mock_validator):
            result = await _glob_impl({"pattern": "*"}, session_id="test-session")

            text = result["content"][0]["text"]
            assert "1 files" in text
            assert "file.py" in text


# ============================================================================
# Grep Tool Tests
# ============================================================================

class TestGrepToolConstants:
    """Tests for Grep tool constants."""

    def test_tool_name(self):
        assert AG3NTUM_GREP_TOOL == "mcp__ag3ntum__Grep"

    def test_max_results(self):
        assert GREP_MAX_RESULTS == 1000

    def test_default_context_lines(self):
        assert DEFAULT_CONTEXT_LINES == 3


class TestGrepToolBasic:
    """Tests for basic Grep tool functionality."""

    @pytest.fixture
    def mock_validator(self, tmp_path):
        validator = MagicMock()
        validated_result = MagicMock()
        validated_result.normalized = tmp_path
        validator.validate_path.return_value = validated_result
        validator.workspace = tmp_path
        def _display_path(p, ws=tmp_path):
            try:
                return str(p.relative_to(ws))
            except ValueError:
                return str(p.resolve().relative_to(ws.resolve()))
        validator.docker_to_display_path = _display_path
        return validator

    @pytest.mark.asyncio
    async def test_grep_finds_pattern(self, tmp_path, mock_validator):
        """Test finding a pattern in files."""
        (tmp_path / "test.py").write_text("def main():\n    print('hello')\n    return 0\n")

        with patch('tools.ag3ntum.ag3ntum_grep.tool.get_path_validator', return_value=mock_validator):
            result = await _grep_impl({"pattern": "def main"}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            text = result["content"][0]["text"]
            assert "1 match" in text
            assert "def main" in text

    @pytest.mark.asyncio
    async def test_grep_case_insensitive(self, tmp_path, mock_validator):
        """Test case-insensitive search."""
        (tmp_path / "test.txt").write_text("Hello World\nhello world\nHELLO WORLD\n")

        with patch('tools.ag3ntum.ag3ntum_grep.tool.get_path_validator', return_value=mock_validator):
            result = await _grep_impl({
                "pattern": "hello",
                "ignore_case": True
            }, session_id="test-session")

            text = result["content"][0]["text"]
            assert "3 match" in text

    @pytest.mark.asyncio
    async def test_grep_no_matches(self, tmp_path, mock_validator):
        """Test grep with no matches."""
        (tmp_path / "test.txt").write_text("some content\n")

        with patch('tools.ag3ntum.ag3ntum_grep.tool.get_path_validator', return_value=mock_validator):
            result = await _grep_impl({"pattern": "nonexistent"}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            assert "No matches" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_grep_empty_pattern(self):
        """Test empty pattern returns error."""
        result = await _grep_impl({"pattern": ""}, session_id="test-session")

        assert result.get("is_error") is True
        assert "required" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_grep_invalid_regex(self):
        """Test invalid regex pattern returns error."""
        with patch('tools.ag3ntum.ag3ntum_grep.tool.get_path_validator'):
            result = await _grep_impl({"pattern": "[invalid regex"}, session_id="test-session")

            assert result.get("is_error") is True
            assert "regex" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_grep_in_single_file(self, tmp_path, mock_validator):
        """Test searching within a single file."""
        test_file = tmp_path / "test.py"
        test_file.write_text("line1\ntarget line\nline3\n")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_grep.tool.get_path_validator', return_value=mock_validator):
            result = await _grep_impl({"pattern": "target", "path": "test.py"}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            assert "target line" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_grep_with_include_filter(self, tmp_path, mock_validator):
        """Test include filter to limit file types."""
        (tmp_path / "test.py").write_text("hello python\n")
        (tmp_path / "test.txt").write_text("hello text\n")

        with patch('tools.ag3ntum.ag3ntum_grep.tool.get_path_validator', return_value=mock_validator):
            result = await _grep_impl({
                "pattern": "hello",
                "include": "*.py"
            }, session_id="test-session")

            text = result["content"][0]["text"]
            assert "1 match" in text
            assert "test.py" in text

    @pytest.mark.asyncio
    async def test_grep_path_not_found(self, tmp_path, mock_validator):
        """Test grep on non-existent path."""
        missing = tmp_path / "nonexistent"
        mock_validator.validate_path.return_value.normalized = missing

        with patch('tools.ag3ntum.ag3ntum_grep.tool.get_path_validator', return_value=mock_validator):
            result = await _grep_impl({"pattern": "test", "path": "nonexistent"}, session_id="test-session")

            assert result.get("is_error") is True
            assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_grep_context_lines(self, tmp_path, mock_validator):
        """Test context lines around matches."""
        content = "\n".join(f"line{i}" for i in range(1, 11))
        (tmp_path / "test.txt").write_text(content)

        with patch('tools.ag3ntum.ag3ntum_grep.tool.get_path_validator', return_value=mock_validator):
            result = await _grep_impl({
                "pattern": "line5",
                "context": 2
            }, session_id="test-session")

            text = result["content"][0]["text"]
            # Should include context lines around line5
            assert "line3" in text  # 2 lines before
            assert "line7" in text  # 2 lines after

    @pytest.mark.asyncio
    async def test_grep_session_not_configured(self):
        """Test error when session not configured."""
        with patch('tools.ag3ntum.ag3ntum_grep.tool.get_path_validator',
                   side_effect=RuntimeError("not configured")):
            result = await _grep_impl({"pattern": "test"}, session_id="unknown")

            assert result.get("is_error") is True


# ============================================================================
# LS Tool Tests
# ============================================================================

class TestLSToolConstants:
    """Tests for LS tool constants."""

    def test_tool_name(self):
        assert AG3NTUM_LS_TOOL == "mcp__ag3ntum__LS"

    def test_max_entries(self):
        assert LS_MAX_ENTRIES == 1000


class TestFormatSize:
    """Tests for _format_size helper."""

    def test_bytes(self):
        assert _format_size(500) == "500 B"

    def test_kilobytes(self):
        result = _format_size(2048)
        assert "KB" in result
        assert "2.0" in result

    def test_megabytes(self):
        result = _format_size(5 * 1024 * 1024)
        assert "MB" in result
        assert "5.0" in result

    def test_gigabytes(self):
        result = _format_size(2 * 1024 * 1024 * 1024)
        assert "GB" in result
        assert "2.0" in result

    def test_zero_bytes(self):
        assert _format_size(0) == "0 B"


class TestLSToolBasic:
    """Tests for basic LS tool functionality."""

    @pytest.fixture
    def mock_validator(self, tmp_path):
        validator = MagicMock()
        validated_result = MagicMock()
        validated_result.normalized = tmp_path
        validator.validate_path.return_value = validated_result
        validator.workspace = tmp_path
        def _display_path(p, ws=tmp_path):
            try:
                return str(p.relative_to(ws))
            except ValueError:
                return str(p.resolve().relative_to(ws.resolve()))
        validator.docker_to_display_path = _display_path
        return validator

    @pytest.mark.asyncio
    async def test_ls_lists_files(self, tmp_path, mock_validator):
        """Test listing directory contents."""
        (tmp_path / "file1.txt").write_text("content")
        (tmp_path / "file2.py").write_text("# python")
        (tmp_path / "subdir").mkdir()

        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator', return_value=mock_validator):
            result = await _ls_impl({"path": "."}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            text = result["content"][0]["text"]
            assert "file1.txt" in text
            assert "file2.py" in text
            assert "subdir" in text

    @pytest.mark.asyncio
    async def test_ls_hides_hidden_files(self, tmp_path, mock_validator):
        """Test that hidden files are excluded by default."""
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible.txt").write_text("content")

        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator', return_value=mock_validator):
            result = await _ls_impl({"path": "."}, session_id="test-session")

            text = result["content"][0]["text"]
            assert ".hidden" not in text
            assert "visible.txt" in text

    @pytest.mark.asyncio
    async def test_ls_shows_hidden_files(self, tmp_path, mock_validator):
        """Test showing hidden files when requested."""
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible.txt").write_text("content")

        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator', return_value=mock_validator):
            result = await _ls_impl({"path": ".", "include_hidden": True}, session_id="test-session")

            text = result["content"][0]["text"]
            assert ".hidden" in text
            assert "visible.txt" in text

    @pytest.mark.asyncio
    async def test_ls_recursive(self, tmp_path, mock_validator):
        """Test recursive directory listing."""
        subdir = tmp_path / "src"
        subdir.mkdir()
        (subdir / "main.py").write_text("# main")
        (tmp_path / "setup.py").write_text("# setup")

        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator', return_value=mock_validator):
            result = await _ls_impl({"path": ".", "recursive": True}, session_id="test-session")

            text = result["content"][0]["text"]
            assert "main.py" in text
            assert "setup.py" in text

    @pytest.mark.asyncio
    async def test_ls_empty_directory(self, tmp_path, mock_validator):
        """Test listing empty directory."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        mock_validator.validate_path.return_value.normalized = empty_dir

        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator', return_value=mock_validator):
            result = await _ls_impl({"path": "empty"}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            assert "empty" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_ls_directory_not_found(self, tmp_path, mock_validator):
        """Test non-existent directory."""
        missing = tmp_path / "missing"
        mock_validator.validate_path.return_value.normalized = missing

        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator', return_value=mock_validator):
            result = await _ls_impl({"path": "missing"}, session_id="test-session")

            assert result.get("is_error") is True
            assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_ls_not_a_directory(self, tmp_path, mock_validator):
        """Test error when path is a file."""
        file_path = tmp_path / "file.txt"
        file_path.write_text("content")
        mock_validator.validate_path.return_value.normalized = file_path

        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator', return_value=mock_validator):
            result = await _ls_impl({"path": "file.txt"}, session_id="test-session")

            assert result.get("is_error") is True
            assert "not a directory" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_ls_path_validation_error(self, mock_validator):
        """Test path traversal blocked."""
        from src.core.path_validator import PathValidationError
        mock_validator.validate_path.side_effect = PathValidationError(
            "Blocked", path="/etc", reason="BLOCKED_PATH"
        )

        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator', return_value=mock_validator):
            result = await _ls_impl({"path": "/etc"}, session_id="test-session")

            assert result.get("is_error") is True
            assert "validation failed" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_ls_session_not_configured(self):
        """Test error when session not configured."""
        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator',
                   side_effect=RuntimeError("not configured")):
            result = await _ls_impl({"path": "."}, session_id="unknown")

            assert result.get("is_error") is True

    @pytest.mark.asyncio
    async def test_ls_shows_file_sizes(self, tmp_path, mock_validator):
        """Test that file sizes are displayed."""
        (tmp_path / "small.txt").write_text("x" * 100)

        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator', return_value=mock_validator):
            result = await _ls_impl({"path": "."}, session_id="test-session")

            text = result["content"][0]["text"]
            assert "100 B" in text

    @pytest.mark.asyncio
    async def test_ls_directories_before_files(self, tmp_path, mock_validator):
        """Test that directories are listed before files."""
        (tmp_path / "z_file.txt").write_text("content")
        (tmp_path / "a_dir").mkdir()

        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator', return_value=mock_validator):
            result = await _ls_impl({"path": "."}, session_id="test-session")

            text = result["content"][0]["text"]
            # Directory icon should appear before file icon
            dir_pos = text.find("a_dir")
            file_pos = text.find("z_file.txt")
            assert dir_pos < file_pos


class TestLSBrokenSymlinks:
    """Tests for LS tool handling of broken symlinks.

    Reproduces the bug where workspace/persistent is a symlink to /persistent
    (sandbox-internal path). MCP tools run outside bwrap where /persistent
    doesn't exist, causing ENOENT when LS traverses the workspace.
    """

    @pytest.fixture
    def mock_validator(self, tmp_path):
        validator = MagicMock()
        validated_result = MagicMock()
        validated_result.normalized = tmp_path
        validator.validate_path.return_value = validated_result
        validator.workspace = tmp_path
        def _display_path(p, ws=tmp_path):
            try:
                return str(p.relative_to(ws))
            except ValueError:
                return str(p.resolve().relative_to(ws.resolve()))
        validator.docker_to_display_path = _display_path
        return validator

    @pytest.mark.asyncio
    async def test_ls_skips_broken_symlink(self, tmp_path, mock_validator):
        """LS should skip broken symlinks without crashing."""
        (tmp_path / "real_file.txt").write_text("content")
        # Create broken symlink (like workspace/persistent -> /persistent)
        (tmp_path / "broken_link").symlink_to("/nonexistent_target_path")

        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator', return_value=mock_validator):
            result = await _ls_impl({"path": "."}, session_id="test-session")

        assert "is_error" not in result or not result["is_error"]
        text = result["content"][0]["text"]
        assert "real_file.txt" in text
        assert "broken_link" not in text

    @pytest.mark.asyncio
    async def test_ls_recursive_skips_broken_symlink(self, tmp_path, mock_validator):
        """Recursive LS should skip broken symlinks without crashing."""
        subdir = tmp_path / "src"
        subdir.mkdir()
        (subdir / "main.py").write_text("# main")
        # Simulates workspace/persistent -> /persistent (broken in container)
        (tmp_path / "persistent").symlink_to("/persistent")

        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator', return_value=mock_validator):
            result = await _ls_impl({"path": ".", "recursive": True}, session_id="test-session")

        assert "is_error" not in result or not result["is_error"]
        text = result["content"][0]["text"]
        assert "main.py" in text
        assert "persistent" not in text

    @pytest.mark.asyncio
    async def test_ls_mixed_valid_and_broken_symlinks(self, tmp_path, mock_validator):
        """LS should list valid symlinks but skip broken ones."""
        real_target = tmp_path / "real_dir"
        real_target.mkdir()
        (real_target / "file.txt").write_text("content")
        # Valid symlink
        (tmp_path / "valid_link").symlink_to(real_target)
        # Broken symlink
        (tmp_path / "broken_link").symlink_to("/does_not_exist")

        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator', return_value=mock_validator):
            result = await _ls_impl({"path": "."}, session_id="test-session")

        text = result["content"][0]["text"]
        assert "valid_link" in text
        assert "broken_link" not in text


class TestLSVirtualRootListing:
    """Tests for LS virtual root listing (LS /)."""

    @pytest.fixture
    def mock_validator_with_mounts(self, tmp_path):
        """Create a mock validator with mount data for virtual root tests."""
        validator = MagicMock()
        validator.workspace = tmp_path
        validator.persistent = tmp_path / "persistent"
        validator.global_skills = Path("/skills")
        validator.user_skills = Path("/user-skills")
        validator.original_path_mounts_ro = {"/var/log": Path("/mounts/paths/_var_log")}
        validator.original_path_mounts_rw = {}

        # Set up get_sandbox_root_entries to return realistic entries
        validator.get_sandbox_root_entries.return_value = [
            ("/workspace", "rw", "Session workspace (working directory)"),
            ("/persistent", "rw", "Persistent storage (cross-session)"),
            ("/venv", "ro", "Python virtual environment"),
            ("/skills", "ro", "Global skills"),
            ("/user-skills", "ro", "User skills"),
            ("/var/log", "ro", "Mounted from host (read-only)"),
        ]

        return validator

    @pytest.mark.asyncio
    async def test_ls_root_returns_virtual_listing(self, mock_validator_with_mounts):
        """LS / should return a virtual sandbox root listing."""
        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator',
                   return_value=mock_validator_with_mounts):
            result = await _ls_impl({"path": "/"}, session_id="test-session")

        assert "is_error" not in result or not result["is_error"]
        text = result["content"][0]["text"]
        assert "sandbox root" in text
        assert "/workspace/" in text
        assert "/persistent/" in text
        assert "/venv/" in text
        assert "/skills/" in text
        assert "/var/log/" in text

    @pytest.mark.asyncio
    async def test_ls_root_slash_dot_returns_virtual_listing(self, mock_validator_with_mounts):
        """LS /. should also return a virtual sandbox root listing."""
        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator',
                   return_value=mock_validator_with_mounts):
            result = await _ls_impl({"path": "/."}, session_id="test-session")

        assert "is_error" not in result or not result["is_error"]
        text = result["content"][0]["text"]
        assert "sandbox root" in text

    @pytest.mark.asyncio
    async def test_ls_root_shows_access_modes(self, mock_validator_with_mounts):
        """LS / should show access modes (ro/rw) for each entry."""
        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator',
                   return_value=mock_validator_with_mounts):
            result = await _ls_impl({"path": "/"}, session_id="test-session")

        text = result["content"][0]["text"]
        # workspace should be rw
        assert "[rw]" in text
        # skills should be ro
        assert "[ro]" in text


class TestLSVirtualIntermediatePaths:
    """Tests for LS on intermediate mount-parent paths (e.g., /var when /var/log is mounted)."""

    @pytest.fixture
    def mock_validator_with_intermediate(self, tmp_path):
        """Create a mock validator where /var/log is a mount but /var is not."""
        validator = MagicMock()
        validator.workspace = tmp_path
        # /var/log is a mount, but /var is not
        validator.original_path_mounts_ro = {"/var/log": Path("/mounts/paths/_var_log")}
        validator.original_path_mounts_rw = {}
        validator._find_original_path_mount.return_value = None

        validator.find_virtual_children.return_value = [
            ("log", "ro", "Contains mount: /var/log"),
        ]

        return validator

    @pytest.mark.asyncio
    async def test_ls_intermediate_path_shows_children(self, mock_validator_with_intermediate):
        """LS /var should show virtual children when /var/log is mounted."""
        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator',
                   return_value=mock_validator_with_intermediate):
            result = await _ls_impl({"path": "/var"}, session_id="test-session")

        assert "is_error" not in result or not result["is_error"]
        text = result["content"][0]["text"]
        assert "log/" in text
        assert "virtual" in text.lower()

    @pytest.fixture
    def mock_validator_no_children(self, tmp_path):
        """Create a mock validator where no mounts exist under /foo."""
        validator = MagicMock()
        validator.workspace = tmp_path
        validator.original_path_mounts_ro = {}
        validator.original_path_mounts_rw = {}
        validator._find_original_path_mount.return_value = None
        validator.find_virtual_children.return_value = None

        # Set up validate_path to raise PathValidationError
        validator.validate_path.side_effect = PathValidationError(
            "Path outside allowed directories: /foo",
            path="/foo",
            reason="Path must be within workspace, skills, or external mount directories",
        )
        return validator

    @pytest.mark.asyncio
    async def test_ls_unrecognized_absolute_path_blocked(self, mock_validator_no_children):
        """LS /foo (not a mount, no children) should return an error."""
        with patch('tools.ag3ntum.ag3ntum_ls.tool.get_path_validator',
                   return_value=mock_validator_no_children):
            result = await _ls_impl({"path": "/foo"}, session_id="test-session")

        assert result.get("is_error", False)
        text = result["content"][0]["text"]
        assert "Error" in text


class TestPathValidatorSandboxView:
    """Tests for PathValidator.get_sandbox_root_entries() and find_virtual_children()."""

    @pytest.fixture
    def validator(self, tmp_path):
        """Create a real PathValidator with mount data."""
        from src.core.path_validator import Ag3ntumPathValidator, PathValidatorConfig

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        persistent = tmp_path / "persistent"
        persistent.mkdir()

        config = PathValidatorConfig(
            workspace_path=workspace,
            global_skills_path=Path("/skills"),
            user_skills_path=Path("/user-skills"),
            persistent_path=persistent,
            original_path_mounts_ro={"/var/log": Path("/mounts/paths/_var_log")},
            original_path_mounts_rw={"/data/output": Path("/mounts/paths/_data_output")},
        )
        return Ag3ntumPathValidator(config)

    def test_sandbox_root_entries_contains_workspace(self, validator):
        entries = validator.get_sandbox_root_entries()
        paths = [e[0] for e in entries]
        assert "/workspace" in paths

    def test_sandbox_root_entries_contains_persistent(self, validator):
        entries = validator.get_sandbox_root_entries()
        paths = [e[0] for e in entries]
        assert "/persistent" in paths

    def test_sandbox_root_entries_contains_venv(self, validator):
        entries = validator.get_sandbox_root_entries()
        paths = [e[0] for e in entries]
        assert "/venv" in paths

    def test_sandbox_root_entries_contains_skills(self, validator):
        entries = validator.get_sandbox_root_entries()
        paths = [e[0] for e in entries]
        assert "/skills" in paths
        assert "/user-skills" in paths

    def test_sandbox_root_entries_contains_original_mounts(self, validator):
        entries = validator.get_sandbox_root_entries()
        paths = [e[0] for e in entries]
        assert "/var/log" in paths
        assert "/data/output" in paths

    def test_sandbox_root_entries_access_modes(self, validator):
        entries = validator.get_sandbox_root_entries()
        entries_dict = {e[0]: e[1] for e in entries}
        assert entries_dict["/workspace"] == "rw"
        assert entries_dict["/var/log"] == "ro"
        assert entries_dict["/data/output"] == "rw"
        assert entries_dict["/venv"] == "ro"

    def test_find_virtual_children_returns_child(self, validator):
        children = validator.find_virtual_children("/var")
        assert children is not None
        child_names = [c[0] for c in children]
        assert "log" in child_names

    def test_find_virtual_children_returns_none_for_unknown(self, validator):
        children = validator.find_virtual_children("/unknown")
        assert children is None

    def test_find_virtual_children_multiple_mounts_under_parent(self, tmp_path):
        """Test with multiple mounts under the same parent."""
        from src.core.path_validator import Ag3ntumPathValidator, PathValidatorConfig

        workspace = tmp_path / "workspace"
        workspace.mkdir()

        config = PathValidatorConfig(
            workspace_path=workspace,
            original_path_mounts_ro={
                "/var/log": Path("/mounts/a"),
                "/var/cache": Path("/mounts/b"),
            },
        )
        v = Ag3ntumPathValidator(config)
        children = v.find_virtual_children("/var")
        assert children is not None
        child_names = sorted([c[0] for c in children])
        assert "cache" in child_names
        assert "log" in child_names


class TestGlobBrokenSymlinks:
    """Tests for Glob tool handling of broken symlinks."""

    @pytest.fixture
    def mock_validator(self, tmp_path):
        validator = MagicMock()
        validated_result = MagicMock()
        validated_result.normalized = tmp_path
        validator.validate_path.return_value = validated_result
        validator.workspace = tmp_path
        def _display_path(p, ws=tmp_path):
            try:
                return str(p.relative_to(ws))
            except ValueError:
                return str(p.resolve().relative_to(ws.resolve()))
        validator.docker_to_display_path = _display_path
        return validator

    @pytest.mark.asyncio
    async def test_glob_skips_broken_symlink(self, tmp_path, mock_validator):
        """Glob should not return broken symlinks as matches."""
        (tmp_path / "real.py").write_text("# real")
        (tmp_path / "broken.py").symlink_to("/nonexistent/file.py")

        with patch('tools.ag3ntum.ag3ntum_glob.tool.get_path_validator', return_value=mock_validator):
            result = await _glob_impl({"pattern": "*.py"}, session_id="test-session")

        assert "is_error" not in result or not result["is_error"]
        text = result["content"][0]["text"]
        assert "real.py" in text
        assert "broken.py" not in text


class TestGrepBrokenSymlinks:
    """Tests for Grep tool handling of broken symlinks."""

    @pytest.fixture
    def mock_validator(self, tmp_path):
        validator = MagicMock()
        validated_result = MagicMock()
        validated_result.normalized = tmp_path
        validator.validate_path.return_value = validated_result
        validator.workspace = tmp_path
        def _display_path(p, ws=tmp_path):
            try:
                return str(p.relative_to(ws))
            except ValueError:
                return str(p.resolve().relative_to(ws.resolve()))
        validator.docker_to_display_path = _display_path
        return validator

    @pytest.mark.asyncio
    async def test_grep_skips_broken_symlink(self, tmp_path, mock_validator):
        """Grep should skip broken symlinks without crashing."""
        (tmp_path / "real.py").write_text("hello world\n")
        (tmp_path / "broken.py").symlink_to("/nonexistent/file.py")

        with patch('tools.ag3ntum.ag3ntum_grep.tool.get_path_validator', return_value=mock_validator):
            result = await _grep_impl({"pattern": "hello"}, session_id="test-session")

        assert "is_error" not in result or not result["is_error"]
        text = result["content"][0]["text"]
        assert "hello world" in text
