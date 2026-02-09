"""
Tests for Ag3ntumRead tool.

Tests the Read tool functionality:
- Path validation and security
- File reading with line numbers
- Offset and limit support
- Binary file detection
- Error handling (file not found, permission denied)
- Secrets redaction
- Edge cases (empty files, unicode, large files)
"""
import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.ag3ntum.ag3ntum_read.tool import (
    create_read_tool,
    _read_impl,
    AG3NTUM_READ_TOOL,
)


class TestReadToolConstants:
    """Tests for tool constants."""

    def test_tool_name_constant(self):
        """Test that tool name constant is correct."""
        assert AG3NTUM_READ_TOOL == "mcp__ag3ntum__Read"

    def test_create_read_tool_returns_tool(self):
        """Test that create_read_tool returns something."""
        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator'):
            tool = create_read_tool("test-session")
            assert tool is not None


class TestReadToolBasicReading:
    """Tests for basic file reading functionality."""

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
    async def test_read_simple_file(self, tmp_path, mock_validator):
        """Test reading a simple text file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=False):

            result = await _read_impl({"file_path": "test.txt"}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            assert "Hello, World!" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_read_multiline_file(self, tmp_path, mock_validator):
        """Test reading a multiline file with line numbers."""
        test_file = tmp_path / "multi.txt"
        test_file.write_text("line1\nline2\nline3\nline4\nline5")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=False):

            result = await _read_impl({"file_path": "multi.txt"}, session_id="test-session")

            text = result["content"][0]["text"]
            assert "is_error" not in result or not result["is_error"]
            # Check line numbers are present
            assert "1|" in text
            assert "5|" in text
            assert "line1" in text
            assert "line5" in text

    @pytest.mark.asyncio
    async def test_read_empty_file(self, tmp_path, mock_validator):
        """Test reading an empty file."""
        test_file = tmp_path / "empty.txt"
        test_file.write_text("")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=False):

            result = await _read_impl({"file_path": "empty.txt"}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]

    @pytest.mark.asyncio
    async def test_read_unicode_file(self, tmp_path, mock_validator):
        """Test reading a file with unicode content."""
        test_file = tmp_path / "unicode.txt"
        content = "Hello 世界! Привет мир! مرحبا"
        test_file.write_text(content, encoding="utf-8")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=False):

            result = await _read_impl({"file_path": "unicode.txt"}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            assert "世界" in result["content"][0]["text"]


class TestReadToolOffsetAndLimit:
    """Tests for offset and limit parameters."""

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
    async def test_read_with_offset(self, tmp_path, mock_validator):
        """Test reading with a line offset."""
        test_file = tmp_path / "test.txt"
        lines = "\n".join(f"line{i}" for i in range(1, 11))
        test_file.write_text(lines)
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=False):

            result = await _read_impl({"file_path": "test.txt", "offset": 5}, session_id="test-session")

            text = result["content"][0]["text"]
            assert "is_error" not in result or not result["is_error"]
            # Line 5 should be first
            assert "line5" in text
            # Lines 1-4 should not be present
            assert "1|" not in text.split("\n")[0]

    @pytest.mark.asyncio
    async def test_read_with_limit(self, tmp_path, mock_validator):
        """Test reading with a line limit."""
        test_file = tmp_path / "test.txt"
        lines = "\n".join(f"line{i}" for i in range(1, 11))
        test_file.write_text(lines)
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=False):

            result = await _read_impl({"file_path": "test.txt", "limit": 3}, session_id="test-session")

            text = result["content"][0]["text"]
            assert "is_error" not in result or not result["is_error"]
            # Only first 3 lines
            assert "line1" in text
            assert "line3" in text
            # Truncation notice
            assert "more lines" in text

    @pytest.mark.asyncio
    async def test_read_with_offset_and_limit(self, tmp_path, mock_validator):
        """Test reading with both offset and limit."""
        test_file = tmp_path / "test.txt"
        lines = "\n".join(f"line{i}" for i in range(1, 21))
        test_file.write_text(lines)
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=False):

            result = await _read_impl({"file_path": "test.txt", "offset": 5, "limit": 3}, session_id="test-session")

            text = result["content"][0]["text"]
            assert "is_error" not in result or not result["is_error"]
            assert "line5" in text
            assert "line7" in text

    @pytest.mark.asyncio
    async def test_read_offset_beyond_file(self, tmp_path, mock_validator):
        """Test reading with offset beyond file length returns empty."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=False):

            result = await _read_impl({"file_path": "test.txt", "offset": 100}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]


class TestReadToolPathValidation:
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
        result = await _read_impl({"file_path": ""}, session_id="test-session")

        assert result.get("is_error") is True
        assert "file_path is required" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, mock_validator):
        """Test that path traversal is blocked."""
        from src.core.path_validator import PathValidationError

        mock_validator.validate_path.side_effect = PathValidationError(
            "Path traversal detected",
            path="/workspace/../../../etc/passwd",
            reason="PATH_TRAVERSAL"
        )

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator):
            result = await _read_impl({"file_path": "/workspace/../../../etc/passwd"}, session_id="test-session")

            assert result.get("is_error") is True
            assert "validation failed" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_blocked_path_rejected(self, mock_validator):
        """Test that blocked paths (e.g., /etc/shadow) are rejected."""
        from src.core.path_validator import PathValidationError

        mock_validator.validate_path.side_effect = PathValidationError(
            "Path is blocked",
            path="/etc/shadow",
            reason="BLOCKED_PATH"
        )

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator):
            result = await _read_impl({"file_path": "/etc/shadow"}, session_id="test-session")

            assert result.get("is_error") is True
            assert "validation failed" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_session_not_configured(self):
        """Test error when PathValidator is not configured for session."""
        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator',
                   side_effect=RuntimeError("PathValidator not configured")):

            result = await _read_impl({"file_path": "test.txt"}, session_id="unknown-session")

            assert result.get("is_error") is True
            assert "Internal error" in result["content"][0]["text"]


class TestReadToolErrorHandling:
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
        """Test reading a non-existent file."""
        missing_file = tmp_path / "missing.txt"
        mock_validator.validate_path.return_value.normalized = missing_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator):
            result = await _read_impl({"file_path": "missing.txt"}, session_id="test-session")

            assert result.get("is_error") is True
            assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_read_directory_rejected(self, tmp_path, mock_validator):
        """Test that reading a directory returns error."""
        mock_validator.validate_path.return_value.normalized = tmp_path

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator):
            result = await _read_impl({"file_path": "somedir"}, session_id="test-session")

            assert result.get("is_error") is True
            assert "directory" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_binary_file_detected(self, tmp_path, mock_validator):
        """Test that binary files are detected and not displayed."""
        binary_file = tmp_path / "binary.bin"
        binary_file.write_bytes(b"\x00\x01\x02\x03\x04\x05")
        mock_validator.validate_path.return_value.normalized = binary_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator):
            result = await _read_impl({"file_path": "binary.bin"}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            assert "binary" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_read_permission_error(self, tmp_path, mock_validator):
        """Test handling of permission errors during file read."""
        test_file = tmp_path / "noperm.txt"
        test_file.write_text("content")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('builtins.open', side_effect=PermissionError("Permission denied")):

            result = await _read_impl({"file_path": "noperm.txt"}, session_id="test-session")

            assert result.get("is_error") is True
            assert "failed to read" in result["content"][0]["text"].lower()


class TestReadToolSecretsRedaction:
    """Tests for secrets redaction functionality."""

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
    async def test_secrets_redacted_in_output(self, tmp_path, mock_validator):
        """Test that secrets are redacted when scanner is enabled."""
        test_file = tmp_path / "secrets.txt"
        test_file.write_text("API_KEY=sk-1234567890abcdef")
        mock_validator.validate_path.return_value.normalized = test_file

        mock_scan_result = MagicMock()
        mock_scan_result.has_secrets = True
        mock_scan_result.redacted_text = "API_KEY=****REDACTED****"
        mock_scan_result.secret_count = 1
        mock_scan_result.secret_types = {"api_key"}

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=True), \
             patch('tools.ag3ntum.ag3ntum_read.tool.scan_and_redact', return_value=mock_scan_result):

            result = await _read_impl({"file_path": "secrets.txt"}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            text = result["content"][0]["text"]
            assert "Security Notice" in text
            assert "redacted" in text.lower()

    @pytest.mark.asyncio
    async def test_no_secrets_no_notice(self, tmp_path, mock_validator):
        """Test no security notice when no secrets found."""
        test_file = tmp_path / "clean.txt"
        test_file.write_text("Normal content without secrets")
        mock_validator.validate_path.return_value.normalized = test_file

        mock_scan_result = MagicMock()
        mock_scan_result.has_secrets = False

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=True), \
             patch('tools.ag3ntum.ag3ntum_read.tool.scan_and_redact', return_value=mock_scan_result):

            result = await _read_impl({"file_path": "clean.txt"}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            assert "Security Notice" not in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_scanner_disabled(self, tmp_path, mock_validator):
        """Test that scanner disabled means no scanning."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("API_KEY=sk-1234567890abcdef")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=False):

            result = await _read_impl({"file_path": "test.txt"}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]
            # Original content should be present (no redaction)
            assert "sk-1234567890abcdef" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_scanner_exception_handled_gracefully(self, tmp_path, mock_validator):
        """Test that scanner exceptions are handled gracefully."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("some content")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=True), \
             patch('tools.ag3ntum.ag3ntum_read.tool.scan_and_redact',
                   side_effect=Exception("Scanner crashed")):

            result = await _read_impl({"file_path": "test.txt"}, session_id="test-session")

            # Should still succeed, original content returned
            assert "is_error" not in result or not result["is_error"]
            assert "some content" in result["content"][0]["text"]


class TestReadToolEdgeCases:
    """Edge case tests for the Read tool."""

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
    async def test_large_file(self, tmp_path, mock_validator):
        """Test reading a large file."""
        test_file = tmp_path / "large.txt"
        content = "\n".join(f"line {i}" for i in range(10000))
        test_file.write_text(content)
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=False):

            result = await _read_impl({"file_path": "large.txt"}, session_id="test-session")

            assert "is_error" not in result or not result["is_error"]

    @pytest.mark.asyncio
    async def test_file_with_special_characters(self, tmp_path, mock_validator):
        """Test reading a file containing special characters."""
        test_file = tmp_path / "special.txt"
        test_file.write_text("tab\there\nnull\x00-free line\n")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator):
            result = await _read_impl({"file_path": "special.txt"}, session_id="test-session")

            # The binary check reads first 8192 bytes; \x00 triggers binary detection
            assert "binary" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_binary_detection_large_offset(self, tmp_path, mock_validator):
        """Test binary detection only checks first 8192 bytes."""
        test_file = tmp_path / "mostly_text.bin"
        # 8192 bytes of text, then null byte
        content = b"a" * 8192 + b"\x00"
        test_file.write_bytes(content)
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=False):

            result = await _read_impl({"file_path": "mostly_text.bin"}, session_id="test-session")

            # The null byte is at position 8192 which is exactly the boundary
            # The read chunk is first 8192 bytes, so null at 8192 is NOT in the chunk
            assert "is_error" not in result or not result["is_error"]

    @pytest.mark.asyncio
    async def test_read_validates_with_read_operation(self, tmp_path, mock_validator):
        """Test that path validation uses 'read' operation type."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=False):

            await _read_impl({"file_path": "test.txt"}, session_id="test-session")

            mock_validator.validate_path.assert_called_once_with("test.txt", operation="read")

    @pytest.mark.asyncio
    async def test_default_offset_is_one(self, tmp_path, mock_validator):
        """Test that default offset starts at line 1."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("first\nsecond\nthird")
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_read.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled', return_value=False):

            result = await _read_impl({"file_path": "test.txt"}, session_id="test-session")

            text = result["content"][0]["text"]
            # First line should start with line number 1
            assert text.strip().startswith("1|") or text.strip().startswith("     1|")
