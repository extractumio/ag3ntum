"""
Tests for Files API path normalization and security validation.

These tests ensure that sandbox-format paths from agent messages
(e.g., /workspace/external/persistent/file.txt) are correctly
normalized before being processed by the file API.
"""
import pytest
from pathlib import Path
import tempfile
import shutil

# Import the functions we're testing
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.api.routes.files import (
    normalize_path_for_mount_check,
    validate_path_security,
    get_mount_info,
)
from src.services.mount_service import resolve_file_path_for_external_mount


class TestNormalizePathForMountCheck:
    """Tests for normalize_path_for_mount_check function."""

    def test_relative_path_unchanged(self):
        """Relative paths without workspace prefix should be unchanged."""
        assert normalize_path_for_mount_check("file.txt") == "file.txt"
        assert normalize_path_for_mount_check("subdir/file.txt") == "subdir/file.txt"

    def test_sandbox_format_workspace_prefix_stripped(self):
        """Sandbox format /workspace/... should have prefix stripped."""
        assert normalize_path_for_mount_check("/workspace/file.txt") == "file.txt"
        assert normalize_path_for_mount_check("/workspace/subdir/file.txt") == "subdir/file.txt"

    def test_workspace_prefix_without_leading_slash(self):
        """workspace/... without leading slash should be stripped."""
        assert normalize_path_for_mount_check("workspace/file.txt") == "file.txt"
        assert normalize_path_for_mount_check("workspace/subdir/file.txt") == "subdir/file.txt"

    def test_external_paths_normalized(self):
        """External mount paths in sandbox format should be normalized."""
        # These are the common cases from agent messages
        assert normalize_path_for_mount_check("/workspace/external/persistent/image.png") == "external/persistent/image.png"
        assert normalize_path_for_mount_check("/workspace/external/ro/downloads/file.txt") == "external/ro/downloads/file.txt"
        assert normalize_path_for_mount_check("/workspace/external/rw/data/output.csv") == "external/rw/data/output.csv"

    def test_just_workspace_returns_empty(self):
        """Just 'workspace' or '/workspace' should return empty string."""
        assert normalize_path_for_mount_check("workspace") == ""
        assert normalize_path_for_mount_check("/workspace") == ""

    def test_multiple_leading_slashes_stripped(self):
        """Multiple leading slashes should all be stripped."""
        assert normalize_path_for_mount_check("///workspace/file.txt") == "file.txt"
        assert normalize_path_for_mount_check("//file.txt") == "file.txt"

    def test_backslash_normalized(self):
        """Backslashes should be converted to forward slashes."""
        assert normalize_path_for_mount_check("\\workspace\\file.txt") == "file.txt"
        assert normalize_path_for_mount_check("workspace\\subdir\\file.txt") == "subdir/file.txt"

    def test_external_root_normalized(self):
        """Just 'external' should be preserved after normalization."""
        assert normalize_path_for_mount_check("external") == "external"
        assert normalize_path_for_mount_check("/workspace/external") == "external"


class TestGetMountInfo:
    """Tests for get_mount_info function with sandbox-format paths."""

    def test_regular_file_not_external(self):
        """Regular workspace files should not be marked as external."""
        is_external, is_readonly, mount_type = get_mount_info("file.txt")
        assert is_external is False
        assert mount_type is None

    def test_sandbox_format_regular_file(self):
        """Sandbox format regular files should not be marked as external."""
        is_external, is_readonly, mount_type = get_mount_info("/workspace/file.txt")
        assert is_external is False
        assert mount_type is None

    def test_sandbox_format_persistent_mount(self):
        """Sandbox format persistent paths should be detected."""
        is_external, is_readonly, mount_type = get_mount_info("/workspace/external/persistent/image.png")
        assert is_external is True
        assert is_readonly is False
        assert mount_type == "persistent"

    def test_sandbox_format_ro_mount(self):
        """Sandbox format read-only mount paths should be detected."""
        is_external, is_readonly, mount_type = get_mount_info("/workspace/external/ro/downloads/file.txt")
        assert is_external is True
        assert is_readonly is True
        assert mount_type == "ro"

    def test_sandbox_format_rw_mount(self):
        """Sandbox format read-write mount paths should be detected."""
        is_external, is_readonly, mount_type = get_mount_info("/workspace/external/rw/data/output.csv")
        assert is_external is True
        assert is_readonly is False
        assert mount_type == "rw"

    def test_sandbox_format_user_ro_mount(self):
        """Sandbox format user read-only mount paths should be detected."""
        is_external, is_readonly, mount_type = get_mount_info("/workspace/external/user-ro/mydata/file.txt")
        assert is_external is True
        assert is_readonly is True
        assert mount_type == "user-ro"

    def test_sandbox_format_user_rw_mount(self):
        """Sandbox format user read-write mount paths should be detected."""
        is_external, is_readonly, mount_type = get_mount_info("/workspace/external/user-rw/mydata/file.txt")
        assert is_external is True
        assert is_readonly is False
        assert mount_type == "user-rw"

    def test_relative_external_paths(self):
        """Relative external paths should also be detected correctly."""
        is_external, is_readonly, mount_type = get_mount_info("external/persistent/file.txt")
        assert is_external is True
        assert mount_type == "persistent"


class TestValidatePathSecurity:
    """Tests for validate_path_security with sandbox-format paths."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        temp_dir = Path(tempfile.mkdtemp(prefix="test_workspace_"))
        workspace = temp_dir / "workspace"
        workspace.mkdir()
        # Create some test files
        (workspace / "test.txt").write_text("test content")
        (workspace / "subdir").mkdir()
        (workspace / "subdir" / "nested.txt").write_text("nested content")
        yield workspace
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_relative_path(self, temp_workspace):
        """Relative paths should work correctly."""
        result = validate_path_security("test.txt", temp_workspace)
        # Use resolve() on both sides to handle macOS /var vs /private/var symlink
        assert result.resolve() == (temp_workspace / "test.txt").resolve()

    def test_sandbox_format_path(self, temp_workspace):
        """Sandbox format paths should have /workspace/ prefix stripped."""
        result = validate_path_security("/workspace/test.txt", temp_workspace)
        assert result.resolve() == (temp_workspace / "test.txt").resolve()

    def test_sandbox_format_nested_path(self, temp_workspace):
        """Sandbox format nested paths should work."""
        result = validate_path_security("/workspace/subdir/nested.txt", temp_workspace)
        assert result.resolve() == (temp_workspace / "subdir" / "nested.txt").resolve()

    def test_workspace_prefix_without_slash(self, temp_workspace):
        """Paths starting with workspace/ (no leading /) should work."""
        result = validate_path_security("workspace/test.txt", temp_workspace)
        assert result.resolve() == (temp_workspace / "test.txt").resolve()

    def test_path_traversal_blocked(self, temp_workspace):
        """Path traversal attempts should be blocked."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            validate_path_security("../../../etc/passwd", temp_workspace)
        # Check for either "path traversal" or "traversal" in the error message
        assert "traversal" in exc_info.value.detail.lower()

    def test_sandbox_format_path_traversal_blocked(self, temp_workspace):
        """Path traversal in sandbox format should be blocked."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            validate_path_security("/workspace/../../../etc/passwd", temp_workspace)
        assert "traversal" in exc_info.value.detail.lower()


class TestResolveFilePathForExternalMount:
    """Tests for resolve_file_path_for_external_mount with sandbox-format paths."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        temp_dir = Path(tempfile.mkdtemp(prefix="test_workspace_"))
        workspace = temp_dir / "workspace"
        workspace.mkdir()
        # Create external directory structure
        external = workspace / "external"
        external.mkdir()
        (external / "persistent").mkdir()
        (external / "ro").mkdir()
        yield workspace
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_relative_path(self, temp_workspace):
        """Relative paths should work correctly."""
        result, is_external = resolve_file_path_for_external_mount(
            temp_workspace, "file.txt"
        )
        assert result == temp_workspace / "file.txt"
        assert is_external is False

    def test_sandbox_format_regular_path(self, temp_workspace):
        """Sandbox format regular paths should work."""
        result, is_external = resolve_file_path_for_external_mount(
            temp_workspace, "/workspace/file.txt"
        )
        assert result == temp_workspace / "file.txt"
        assert is_external is False

    def test_sandbox_format_external_path(self, temp_workspace):
        """Sandbox format external paths should be detected."""
        result, is_external = resolve_file_path_for_external_mount(
            temp_workspace, "/workspace/external/persistent/image.png"
        )
        assert is_external is True
        # The path should be correctly constructed
        assert "external/persistent/image.png" in str(result)

    def test_relative_external_path(self, temp_workspace):
        """Relative external paths should work."""
        result, is_external = resolve_file_path_for_external_mount(
            temp_workspace, "external/persistent/image.png"
        )
        assert is_external is True

    def test_workspace_prefix_stripped(self, temp_workspace):
        """The workspace prefix should be correctly stripped."""
        # Should NOT create workspace/workspace/file.txt
        result, is_external = resolve_file_path_for_external_mount(
            temp_workspace, "workspace/file.txt"
        )
        # After fix, this should resolve to workspace/file.txt, not workspace/workspace/file.txt
        assert result == temp_workspace / "file.txt"
        assert "workspace/workspace" not in str(result)


class TestPathNormalizationRegression:
    """
    Regression tests for the sandbox path normalization bug.

    The original bug: When clicking "View in File Explorer" on a file,
    the path came from agent messages in sandbox format (e.g., /workspace/tests/file.py).
    The validation code only stripped the leading '/', leaving 'workspace/tests/file.py'.
    This was then appended to workspace_root, creating an invalid double-workspace path.

    These tests verify the fix works correctly.
    """

    def test_double_workspace_path_not_created(self):
        """Ensure paths like /workspace/file.txt don't create workspace/workspace/file.txt."""
        normalized = normalize_path_for_mount_check("/workspace/tests/backend/conftest.py")
        assert normalized == "tests/backend/conftest.py"
        assert not normalized.startswith("workspace/")

    def test_agent_message_external_path_normalized(self):
        """Paths from agent messages like /workspace/external/... should be normalized."""
        # This is the exact format that caused the original bug
        path = "/workspace/external/persistent/screenshot.png"
        normalized = normalize_path_for_mount_check(path)

        assert normalized == "external/persistent/screenshot.png"
        assert not normalized.startswith("workspace/")

        # Verify it's correctly detected as external
        is_external, _, mount_type = get_mount_info(path)
        assert is_external is True
        assert mount_type == "persistent"

    def test_various_sandbox_path_formats(self):
        """Test various sandbox path formats that might come from agent messages."""
        test_cases = [
            # (input, expected_normalized)
            ("/workspace/file.txt", "file.txt"),
            ("/workspace/subdir/file.txt", "subdir/file.txt"),
            ("workspace/file.txt", "file.txt"),
            ("/workspace/external/ro/name/file.txt", "external/ro/name/file.txt"),
            ("./file.txt", "./file.txt"),  # Current dir prefix preserved
            ("file.txt", "file.txt"),  # Plain relative path unchanged
        ]

        for input_path, expected in test_cases:
            result = normalize_path_for_mount_check(input_path)
            assert result == expected, f"Failed for input '{input_path}': expected '{expected}', got '{result}'"
