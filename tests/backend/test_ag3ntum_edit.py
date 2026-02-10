"""
Tests for Ag3ntumEdit tool.

Tests the Edit tool functionality:
- Path validation and security
- Search and replace (single and all occurrences)
- File ownership (chown to sandbox user)
- Error handling (file not found, string not found, multiple matches)
- Edge cases (empty strings, special characters, large files)
"""
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from tools.ag3ntum.ag3ntum_edit.tool import (
    create_edit_tool,
    _edit_impl,
    AG3NTUM_EDIT_TOOL,
)


class TestEditToolConstants:
    """Tests for tool constants."""

    def test_tool_name_constant(self):
        """Test that tool name constant is correct."""
        assert AG3NTUM_EDIT_TOOL == "mcp__ag3ntum__Edit"

    def test_create_edit_tool_returns_tool(self):
        """Test that create_edit_tool returns something."""
        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator'):
            tool = create_edit_tool("test-session")
            assert tool is not None


class TestEditToolBasicEditing:
    """Tests for basic search/replace functionality."""

    @pytest.fixture
    def mock_validator(self, tmp_path):
        """Create a mock path validator."""
        validator = MagicMock()
        validated_result = MagicMock()
        validated_result.normalized = tmp_path / "test.txt"
        validated_result.is_readonly = False
        validator.validate_path.return_value = validated_result
        return validator

    @pytest.mark.asyncio
    async def test_simple_replacement(self, tmp_path, mock_validator):
        """Test simple single replacement."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello World")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_edit.tool.chown_to_session_user'):

            result = await _edit_impl({
                "file_path": "test.txt",
                "old_string": "World",
                "new_string": "Python"
            }, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            assert test_file.read_text() == "Hello Python"
            assert "Edited" in result["content"][0]["text"]
            assert "1" in result["content"][0]["text"]  # 1 replacement

    @pytest.mark.asyncio
    async def test_replace_all_occurrences(self, tmp_path, mock_validator):
        """Test replacing all occurrences."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("foo bar foo baz foo")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_edit.tool.chown_to_session_user'):

            result = await _edit_impl({
                "file_path": "test.txt",
                "old_string": "foo",
                "new_string": "qux",
                "replace_all": True
            }, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            assert test_file.read_text() == "qux bar qux baz qux"
            assert "3" in result["content"][0]["text"]  # 3 replacements

    @pytest.mark.asyncio
    async def test_multiline_replacement(self, tmp_path, mock_validator):
        """Test replacing multiline content."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("def old_func():\n    pass\n")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_edit.tool.chown_to_session_user'):

            result = await _edit_impl({
                "file_path": "test.txt",
                "old_string": "def old_func():\n    pass",
                "new_string": "def new_func():\n    return True"
            }, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            assert test_file.read_text() == "def new_func():\n    return True\n"

    @pytest.mark.asyncio
    async def test_replace_with_empty_string(self, tmp_path, mock_validator):
        """Test replacing with empty string (deletion)."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello World Goodbye")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_edit.tool.chown_to_session_user'):

            result = await _edit_impl({
                "file_path": "test.txt",
                "old_string": " World",
                "new_string": ""
            }, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            assert test_file.read_text() == "Hello Goodbye"


class TestEditToolFileOwnership:
    """Tests for file ownership (chown to sandbox user)."""

    @pytest.fixture
    def mock_validator(self, tmp_path):
        """Create a mock path validator."""
        validator = MagicMock()
        validated_result = MagicMock()
        validated_result.normalized = tmp_path / "test.txt"
        validated_result.is_readonly = False
        validator.validate_path.return_value = validated_result
        return validator

    @pytest.mark.asyncio
    async def test_chown_called_after_edit(self, tmp_path, mock_validator):
        """Test that chown_to_session_user is called after successful edit."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("old content")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_edit.tool.chown_to_session_user') as mock_chown:

            result = await _edit_impl({
                "file_path": "test.txt",
                "old_string": "old",
                "new_string": "new"
            }, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            mock_chown.assert_called_once_with(test_file, "test-session")


class TestEditToolPathValidation:
    """Tests for path validation and security."""

    @pytest.fixture
    def mock_validator(self, tmp_path):
        """Create a mock path validator."""
        validator = MagicMock()
        validated_result = MagicMock()
        validated_result.normalized = tmp_path / "test.txt"
        validated_result.is_readonly = False
        validator.validate_path.return_value = validated_result
        return validator

    @pytest.mark.asyncio
    async def test_missing_file_path(self):
        """Test that missing file_path returns error."""
        result = await _edit_impl({
            "file_path": "",
            "old_string": "foo",
            "new_string": "bar"
        }, session_id="test-session")

        assert result.get("is_error") is True
        assert "file_path is required" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_missing_old_string(self):
        """Test that missing old_string returns error."""
        result = await _edit_impl({
            "file_path": "test.txt",
            "old_string": "",
            "new_string": "bar"
        }, session_id="test-session")

        assert result.get("is_error") is True
        assert "old_string is required" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, mock_validator):
        """Test that path traversal is blocked."""
        from src.core.path_validator import PathValidationError

        mock_validator.validate_path.side_effect = PathValidationError(
            "Path traversal detected",
            path="/workspace/../../../etc/passwd",
            reason="PATH_TRAVERSAL"
        )

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator):
            result = await _edit_impl({
                "file_path": "/workspace/../../../etc/passwd",
                "old_string": "root",
                "new_string": "hacked"
            }, session_id="test-session")

            assert result.get("is_error") is True
            assert "validation failed" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_session_not_configured(self):
        """Test error when PathValidator is not configured for session."""
        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator',
                   side_effect=RuntimeError("PathValidator not configured")):

            result = await _edit_impl({
                "file_path": "test.txt",
                "old_string": "foo",
                "new_string": "bar"
            }, session_id="unknown-session")

            assert result.get("is_error") is True
            assert "Internal error" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_edit_validates_with_edit_operation(self, tmp_path, mock_validator):
        """Test that path validation uses 'edit' operation type."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_edit.tool.chown_to_session_user'):

            await _edit_impl({
                "file_path": "test.txt",
                "old_string": "content",
                "new_string": "updated"
            }, session_id="test-session")

            mock_validator.validate_path.assert_called_once_with("test.txt", operation="edit")


class TestEditToolErrorHandling:
    """Tests for error handling."""

    @pytest.fixture
    def mock_validator(self, tmp_path):
        """Create a mock path validator."""
        validator = MagicMock()
        validated_result = MagicMock()
        validated_result.normalized = tmp_path / "test.txt"
        validated_result.is_readonly = False
        validator.validate_path.return_value = validated_result
        return validator

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path, mock_validator):
        """Test editing a non-existent file."""
        missing_file = tmp_path / "missing.txt"
        mock_validator.validate_path.return_value.normalized = missing_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator):
            result = await _edit_impl({
                "file_path": "missing.txt",
                "old_string": "foo",
                "new_string": "bar"
            }, session_id="test-session")

            assert result.get("is_error") is True
            assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_edit_directory_rejected(self, tmp_path, mock_validator):
        """Test that editing a directory returns error."""
        mock_validator.validate_path.return_value.normalized = tmp_path

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator):
            result = await _edit_impl({
                "file_path": "somedir",
                "old_string": "foo",
                "new_string": "bar"
            }, session_id="test-session")

            assert result.get("is_error") is True
            assert "directory" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_string_not_found(self, tmp_path, mock_validator):
        """Test error when old_string is not found in file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello World")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator):
            result = await _edit_impl({
                "file_path": "test.txt",
                "old_string": "nonexistent string",
                "new_string": "replacement"
            }, session_id="test-session")

            assert result.get("is_error") is True
            assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_multiple_occurrences_without_replace_all(self, tmp_path, mock_validator):
        """Test error when multiple occurrences found without replace_all."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("foo bar foo baz foo")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator):
            result = await _edit_impl({
                "file_path": "test.txt",
                "old_string": "foo",
                "new_string": "qux"
            }, session_id="test-session")

            assert result.get("is_error") is True
            assert "3 occurrences" in result["content"][0]["text"]
            assert "replace_all" in result["content"][0]["text"].lower()
            # File should not have been modified
            assert test_file.read_text() == "foo bar foo baz foo"

    @pytest.mark.asyncio
    async def test_write_io_error(self, tmp_path, mock_validator):
        """Test handling of OS errors during write."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("old content")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator), \
             patch.object(Path, 'write_text', side_effect=OSError("Disk full")):

            result = await _edit_impl({
                "file_path": "test.txt",
                "old_string": "old",
                "new_string": "new"
            }, session_id="test-session")

            assert result.get("is_error") is True
            assert "failed to write" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_read_io_error(self, tmp_path, mock_validator):
        """Test handling of OS errors during file read."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator), \
             patch.object(Path, 'read_text', side_effect=OSError("Read error")):

            result = await _edit_impl({
                "file_path": "test.txt",
                "old_string": "content",
                "new_string": "new"
            }, session_id="test-session")

            assert result.get("is_error") is True
            assert "failed to read" in result["content"][0]["text"].lower()


class TestEditToolEdgeCases:
    """Edge case tests for the Edit tool."""

    @pytest.fixture
    def mock_validator(self, tmp_path):
        """Create a mock path validator."""
        validator = MagicMock()
        validated_result = MagicMock()
        validated_result.normalized = tmp_path / "test.txt"
        validated_result.is_readonly = False
        validator.validate_path.return_value = validated_result
        return validator

    @pytest.mark.asyncio
    async def test_unicode_replacement(self, tmp_path, mock_validator):
        """Test replacing unicode content."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello 世界")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_edit.tool.chown_to_session_user'):

            result = await _edit_impl({
                "file_path": "test.txt",
                "old_string": "世界",
                "new_string": "World"
            }, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            assert test_file.read_text() == "Hello World"

    @pytest.mark.asyncio
    async def test_whitespace_sensitive_matching(self, tmp_path, mock_validator):
        """Test that whitespace is matched exactly (tabs vs spaces)."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("\tindented\n\tTabbed")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_edit.tool.chown_to_session_user'):

            # Tab-indented content should not match space-indented search
            result = await _edit_impl({
                "file_path": "test.txt",
                "old_string": "    indented",  # 4 spaces instead of tab
                "new_string": "replaced"
            }, session_id="test-session")

            assert result.get("is_error") is True
            assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_long_strings_truncated_in_display(self, tmp_path, mock_validator):
        """Test that long old/new strings are truncated in display."""
        test_file = tmp_path / "test.txt"
        long_old = "x" * 300
        long_new = "y" * 300
        test_file.write_text(long_old)
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_edit.tool.chown_to_session_user'):

            result = await _edit_impl({
                "file_path": "test.txt",
                "old_string": long_old,
                "new_string": long_new
            }, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            text = result["content"][0]["text"]
            assert "..." in text  # Truncation indicator

    @pytest.mark.asyncio
    async def test_sequential_edits(self, tmp_path, mock_validator):
        """Test multiple sequential edits to same file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("alpha beta gamma")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_edit.tool.chown_to_session_user'):

            # First edit
            result1 = await _edit_impl({
                "file_path": "test.txt",
                "old_string": "alpha",
                "new_string": "one"
            }, session_id="test-session")
            assert "is_error" not in result1 or not result1["is_error"]
            assert test_file.read_text() == "one beta gamma"

            # Second edit
            result2 = await _edit_impl({
                "file_path": "test.txt",
                "old_string": "beta",
                "new_string": "two"
            }, session_id="test-session")
            assert "is_error" not in result2 or not result2["is_error"]
            assert test_file.read_text() == "one two gamma"

    @pytest.mark.asyncio
    async def test_replace_all_default_false(self, tmp_path, mock_validator):
        """Test that replace_all defaults to False."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("unique_string rest of content")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_edit.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_edit.tool.chown_to_session_user'):

            # Only one occurrence - should work without replace_all
            result = await _edit_impl({
                "file_path": "test.txt",
                "old_string": "unique_string",
                "new_string": "replaced"
            }, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            assert test_file.read_text() == "replaced rest of content"
