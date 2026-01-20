"""
Tests for Ag3ntumWrite tool.

Tests the enhanced Write tool functionality:
- Path validation and security
- Writability checks (fail fast)
- Overwrite protection (requires explicit flag)
- File creation verification
- Display path normalization
"""
import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

# Import the functions to test
from tools.ag3ntum.ag3ntum_write.tool import (
    _is_path_writable,
    _verify_file_written,
    _write_impl,
    create_write_tool,
    AG3NTUM_WRITE_TOOL,
)


class TestIsPathWritable:
    """Tests for _is_path_writable function."""

    def test_existing_writable_file(self, tmp_path):
        """Test with existing writable file."""
        test_file = tmp_path / "writable.txt"
        test_file.write_text("content")

        is_writable, reason = _is_path_writable(test_file)
        assert is_writable is True
        assert reason == ""

    def test_existing_readonly_file(self, tmp_path):
        """Test with existing read-only file."""
        test_file = tmp_path / "readonly.txt"
        test_file.write_text("content")
        # Remove write permission
        os.chmod(test_file, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

        try:
            is_writable, reason = _is_path_writable(test_file)
            assert is_writable is False
            assert "read-only" in reason.lower() or "permission" in reason.lower()
        finally:
            # Restore permissions for cleanup
            os.chmod(test_file, stat.S_IRUSR | stat.S_IWUSR)

    def test_path_is_directory(self, tmp_path):
        """Test that directories are rejected."""
        is_writable, reason = _is_path_writable(tmp_path)
        assert is_writable is False
        assert "directory" in reason.lower()

    def test_new_file_in_writable_directory(self, tmp_path):
        """Test new file in writable directory."""
        new_file = tmp_path / "new_file.txt"
        assert not new_file.exists()

        is_writable, reason = _is_path_writable(new_file)
        assert is_writable is True
        assert reason == ""

    def test_new_file_in_readonly_directory(self, tmp_path):
        """Test new file in read-only directory."""
        readonly_dir = tmp_path / "readonly_dir"
        readonly_dir.mkdir()
        os.chmod(readonly_dir, stat.S_IRUSR | stat.S_IXUSR)

        new_file = readonly_dir / "new_file.txt"

        try:
            is_writable, reason = _is_path_writable(new_file)
            assert is_writable is False
            assert "not writable" in reason.lower() or "permission" in reason.lower()
        finally:
            # Restore permissions for cleanup
            os.chmod(readonly_dir, stat.S_IRWXU)

    def test_nested_new_directory_path(self, tmp_path):
        """Test path with multiple non-existent parent directories."""
        deep_path = tmp_path / "a" / "b" / "c" / "file.txt"
        assert not (tmp_path / "a").exists()

        is_writable, reason = _is_path_writable(deep_path)
        assert is_writable is True
        assert reason == ""


class TestVerifyFileWritten:
    """Tests for _verify_file_written function."""

    def test_file_exists_correct_size(self, tmp_path):
        """Test verification passes for correctly written file."""
        test_file = tmp_path / "test.txt"
        content = "Hello, World!"
        test_file.write_text(content)

        success, error = _verify_file_written(test_file, content)
        assert success is True
        assert error == ""

    def test_file_does_not_exist(self, tmp_path):
        """Test verification fails for missing file."""
        missing_file = tmp_path / "missing.txt"

        success, error = _verify_file_written(missing_file, "content")
        assert success is False
        assert "not created" in error.lower()

    def test_file_size_mismatch(self, tmp_path):
        """Test verification fails for size mismatch."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("short")

        success, error = _verify_file_written(test_file, "much longer content")
        assert success is False
        assert "size mismatch" in error.lower()

    def test_unicode_content(self, tmp_path):
        """Test verification works with unicode content."""
        test_file = tmp_path / "unicode.txt"
        content = "Hello, 世界! 🌍"
        test_file.write_text(content, encoding="utf-8")

        success, error = _verify_file_written(test_file, content)
        assert success is True
        assert error == ""


class TestWriteTool:
    """Integration tests for the Write tool using _write_impl."""

    @pytest.fixture
    def mock_validator(self, tmp_path):
        """Create a mock path validator."""
        validator = MagicMock()
        validated_result = MagicMock()
        validated_result.normalized = tmp_path / "test.txt"
        validated_result.is_readonly = False
        validator.validate_path.return_value = validated_result
        return validator

    @pytest.fixture
    def mock_resolver(self):
        """Create a mock sandbox path resolver."""
        resolver = MagicMock()
        resolver.normalize.return_value = "/workspace/test.txt"
        return resolver

    @pytest.mark.asyncio
    async def test_create_new_file(self, tmp_path, mock_validator, mock_resolver):
        """Test creating a new file."""
        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

            result = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content="Hello"
            )

            assert "isError" not in result or not result["isError"]
            assert "Created file" in result["content"][0]["text"]
            assert "5 bytes" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_overwrite_without_flag(self, tmp_path, mock_validator, mock_resolver):
        """Test that overwriting without flag is rejected."""
        # Create existing file
        existing_file = tmp_path / "test.txt"
        existing_file.write_text("existing content")
        mock_validator.validate_path.return_value.normalized = existing_file

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

            result = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content="new content"
            )

            assert result.get("isError") is True
            assert "already exists" in result["content"][0]["text"]
            assert "overwrite_existing=true" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_overwrite_with_flag(self, tmp_path, mock_validator, mock_resolver):
        """Test that overwriting with flag works."""
        # Create existing file
        existing_file = tmp_path / "test.txt"
        existing_file.write_text("existing content")
        mock_validator.validate_path.return_value.normalized = existing_file

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

            result = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content="new content",
                overwrite_existing=True
            )

            assert "isError" not in result or not result["isError"]
            assert "Overwrote file" in result["content"][0]["text"]
            assert existing_file.read_text() == "new content"

    @pytest.mark.asyncio
    async def test_overwrite_flag_various_values(self, tmp_path, mock_validator, mock_resolver):
        """Test various truthy/falsy values for overwrite flag."""
        existing_file = tmp_path / "test.txt"
        mock_validator.validate_path.return_value.normalized = existing_file

        # Test values that should be treated as False
        false_values = [None, False, 0, "0", "false", "False", ""]

        for value in false_values:
            existing_file.write_text("existing")

            with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
                 patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
                 patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

                result = await _write_impl(
                    session_id="test-session",
                    file_path="test.txt",
                    content="new",
                    overwrite_existing=value
                )

                assert result.get("isError") is True, f"Value {value!r} should be treated as False"

        # Test values that should be treated as True
        true_values = [True, 1, "1", "true", "True", "yes", "YES"]

        for value in true_values:
            existing_file.write_text("existing")

            with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
                 patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
                 patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

                result = await _write_impl(
                    session_id="test-session",
                    file_path="test.txt",
                    content="new",
                    overwrite_existing=value
                )

                assert "isError" not in result or not result["isError"], \
                    f"Value {value!r} should be treated as True"

    @pytest.mark.asyncio
    async def test_readonly_file_rejected(self, tmp_path, mock_validator, mock_resolver):
        """Test that writing to read-only file is rejected."""
        readonly_file = tmp_path / "readonly.txt"
        readonly_file.write_text("content")
        os.chmod(readonly_file, stat.S_IRUSR)
        mock_validator.validate_path.return_value.normalized = readonly_file

        try:
            with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
                 patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
                 patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

                result = await _write_impl(
                    session_id="test-session",
                    file_path="readonly.txt",
                    content="new content",
                    overwrite_existing=True
                )

                assert result.get("isError") is True
                error_text = result["content"][0]["text"].lower()
                assert "cannot write" in error_text or "read-only" in error_text or "permission" in error_text
        finally:
            os.chmod(readonly_file, stat.S_IRWXU)

    @pytest.mark.asyncio
    async def test_missing_file_path(self, mock_validator, mock_resolver):
        """Test that missing file_path returns error."""
        result = await _write_impl(
            session_id="test-session",
            file_path="",
            content="Hello"
        )

        assert result.get("isError") is True
        assert "file_path is required" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_path_validation_error(self, mock_validator, mock_resolver):
        """Test handling of path validation errors."""
        from src.core.path_validator import PathValidationError

        mock_validator.validate_path.side_effect = PathValidationError(
            "Invalid path",
            path="/workspace/../../../etc/passwd",
            reason="PATH_TRAVERSAL"
        )

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator):
            result = await _write_impl(
                session_id="test-session",
                file_path="/workspace/../../../etc/passwd",
                content="hack"
            )

            assert result.get("isError") is True
            assert "validation failed" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_creates_parent_directories(self, tmp_path, mock_validator, mock_resolver):
        """Test that parent directories are created."""
        nested_file = tmp_path / "a" / "b" / "c" / "file.txt"
        mock_validator.validate_path.return_value.normalized = nested_file

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

            result = await _write_impl(
                session_id="test-session",
                file_path="a/b/c/file.txt",
                content="Hello"
            )

            assert "isError" not in result or not result["isError"]
            assert nested_file.exists()
            assert nested_file.read_text() == "Hello"

    @pytest.mark.asyncio
    async def test_display_path_in_result(self, tmp_path, mock_validator, mock_resolver):
        """Test that display path (sandbox format) is shown in result."""
        test_file = tmp_path / "test.txt"
        mock_validator.validate_path.return_value.normalized = test_file
        mock_resolver.normalize.return_value = "/workspace/test.txt"

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

            result = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content="Hello"
            )

            # Should use sandbox path format in result
            assert "/workspace/test.txt" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_content_with_secrets_redacted(self, tmp_path, mock_validator, mock_resolver):
        """Test that secrets in content are redacted."""
        test_file = tmp_path / "test.txt"
        mock_validator.validate_path.return_value.normalized = test_file

        # Mock the scanner
        mock_scan_result = MagicMock()
        mock_scan_result.has_secrets = True
        mock_scan_result.redacted_text = "API_KEY=****REDACTED****"
        mock_scan_result.secret_count = 1
        mock_scan_result.secret_types = {"api_key"}

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=True), \
             patch('tools.ag3ntum.ag3ntum_write.tool.scan_and_redact', return_value=mock_scan_result):

            result = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content="API_KEY=sk-1234567890abcdef"
            )

            assert "isError" not in result or not result["isError"]
            assert "Security Notice" in result["content"][0]["text"]
            assert "redacted" in result["content"][0]["text"].lower()
            # Verify redacted content was written
            assert "REDACTED" in test_file.read_text()


    @pytest.mark.asyncio
    async def test_empty_content(self, tmp_path, mock_validator, mock_resolver):
        """Test writing empty content creates empty file."""
        test_file = tmp_path / "empty.txt"
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

            result = await _write_impl(
                session_id="test-session",
                file_path="empty.txt",
                content=""
            )

            assert "isError" not in result or not result["isError"]
            assert test_file.exists()
            assert test_file.read_text() == ""
            assert "0 bytes" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_missing_content_parameter(self, tmp_path, mock_validator, mock_resolver):
        """Test that missing content defaults to empty string."""
        test_file = tmp_path / "test.txt"
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

            # Simulating missing content (would be empty string from args.get)
            result = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content=""  # This is what args.get("content", "") returns when missing
            )

            assert "isError" not in result or not result["isError"]
            assert test_file.exists()

    @pytest.mark.asyncio
    async def test_unicode_content_write(self, tmp_path, mock_validator, mock_resolver):
        """Test writing unicode content including emojis."""
        test_file = tmp_path / "unicode.txt"
        mock_validator.validate_path.return_value.normalized = test_file
        content = "Hello 世界! 🌍🎉 Привет мир! مرحبا"

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

            result = await _write_impl(
                session_id="test-session",
                file_path="unicode.txt",
                content=content
            )

            assert "isError" not in result or not result["isError"]
            assert test_file.read_text(encoding="utf-8") == content

    @pytest.mark.asyncio
    async def test_multiline_content(self, tmp_path, mock_validator, mock_resolver):
        """Test writing multiline content and line count."""
        test_file = tmp_path / "multiline.txt"
        mock_validator.validate_path.return_value.normalized = test_file
        content = "line1\nline2\nline3\nline4\nline5"

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

            result = await _write_impl(
                session_id="test-session",
                file_path="multiline.txt",
                content=content
            )

            assert "isError" not in result or not result["isError"]
            # Verify line count in result
            assert "5" in result["content"][0]["text"]  # 5 lines

    @pytest.mark.asyncio
    async def test_session_not_configured(self, mock_resolver):
        """Test error when PathValidator is not configured for session."""
        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator',
                   side_effect=RuntimeError("PathValidator not configured")):

            result = await _write_impl(
                session_id="unknown-session",
                file_path="test.txt",
                content="Hello"
            )

            assert result.get("isError") is True
            assert "not properly configured" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_resolver_not_available(self, tmp_path, mock_validator):
        """Test fallback when resolver returns None."""
        test_file = tmp_path / "test.txt"
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=None), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

            result = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content="Hello"
            )

            # Should still succeed, just use real path as display path
            assert "isError" not in result or not result["isError"]
            assert test_file.exists()

    @pytest.mark.asyncio
    async def test_resolver_raises_exception(self, tmp_path, mock_validator):
        """Test fallback when resolver raises exception."""
        test_file = tmp_path / "test.txt"
        mock_validator.validate_path.return_value.normalized = test_file

        mock_resolver = MagicMock()
        mock_resolver.normalize.side_effect = Exception("Resolver error")

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

            result = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content="Hello"
            )

            # Should still succeed with fallback display path
            assert "isError" not in result or not result["isError"]
            assert test_file.exists()

    @pytest.mark.asyncio
    async def test_write_io_error(self, tmp_path, mock_validator, mock_resolver):
        """Test handling of OS errors during write."""
        test_file = tmp_path / "test.txt"
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False), \
             patch.object(Path, 'write_text', side_effect=OSError("Disk full")):

            result = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content="Hello"
            )

            assert result.get("isError") is True
            assert "failed to write" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_write_permission_error(self, tmp_path, mock_validator, mock_resolver):
        """Test handling of permission errors during write."""
        test_file = tmp_path / "test.txt"
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False), \
             patch.object(Path, 'write_text', side_effect=PermissionError("Permission denied")):

            result = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content="Hello"
            )

            assert result.get("isError") is True
            assert "permission denied" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_mkdir_permission_error(self, tmp_path, mock_validator, mock_resolver):
        """Test handling of permission errors during directory creation."""
        nested_file = tmp_path / "a" / "b" / "file.txt"
        mock_validator.validate_path.return_value.normalized = nested_file

        # Mock mkdir to raise PermissionError
        original_mkdir = Path.mkdir
        def mock_mkdir(self, *args, **kwargs):
            raise PermissionError("Cannot create directory")

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False), \
             patch.object(Path, 'mkdir', mock_mkdir):

            result = await _write_impl(
                session_id="test-session",
                file_path="a/b/file.txt",
                content="Hello"
            )

            assert result.get("isError") is True
            assert "permission denied" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_verification_failure(self, tmp_path, mock_validator, mock_resolver):
        """Test handling when file verification fails after write."""
        test_file = tmp_path / "test.txt"
        mock_validator.validate_path.return_value.normalized = test_file

        # Mock _verify_file_written to return failure
        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False), \
             patch('tools.ag3ntum.ag3ntum_write.tool._verify_file_written',
                   return_value=(False, "File disappeared after write")):

            result = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content="Hello"
            )

            assert result.get("isError") is True
            assert "verification failed" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_scanner_exception(self, tmp_path, mock_validator, mock_resolver):
        """Test that scanner exceptions are handled gracefully."""
        test_file = tmp_path / "test.txt"
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=True), \
             patch('tools.ag3ntum.ag3ntum_write.tool.scan_and_redact',
                   side_effect=Exception("Scanner failed")):

            result = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content="API_KEY=secret"
            )

            # Should still succeed - scanner failure is logged but not fatal
            assert "isError" not in result or not result["isError"]
            assert test_file.exists()
            # Original content should be written when scanner fails
            assert test_file.read_text() == "API_KEY=secret"

    @pytest.mark.asyncio
    async def test_content_without_secrets(self, tmp_path, mock_validator, mock_resolver):
        """Test writing content that has no secrets (scanner enabled but no redaction)."""
        test_file = tmp_path / "test.txt"
        mock_validator.validate_path.return_value.normalized = test_file

        # Mock the scanner to return no secrets
        mock_scan_result = MagicMock()
        mock_scan_result.has_secrets = False
        mock_scan_result.redacted_text = "Normal content without secrets"

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=True), \
             patch('tools.ag3ntum.ag3ntum_write.tool.scan_and_redact', return_value=mock_scan_result):

            result = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content="Normal content without secrets"
            )

            assert "isError" not in result or not result["isError"]
            # No security notice when no secrets found
            assert "Security Notice" not in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_sequential_overwrites(self, tmp_path, mock_validator, mock_resolver):
        """Test multiple sequential writes with overwrite flag."""
        test_file = tmp_path / "test.txt"
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

            # First write - create
            result1 = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content="version 1"
            )
            assert "Created file" in result1["content"][0]["text"]
            assert test_file.read_text() == "version 1"

            # Second write - must use overwrite flag
            result2 = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content="version 2",
                overwrite_existing=True
            )
            assert "Overwrote file" in result2["content"][0]["text"]
            assert test_file.read_text() == "version 2"

            # Third write - again with overwrite
            result3 = await _write_impl(
                session_id="test-session",
                file_path="test.txt",
                content="version 3",
                overwrite_existing=True
            )
            assert "Overwrote file" in result3["content"][0]["text"]
            assert test_file.read_text() == "version 3"

    @pytest.mark.asyncio
    async def test_special_characters_in_path(self, tmp_path, mock_validator, mock_resolver):
        """Test writing to file with special characters in name."""
        test_file = tmp_path / "file with spaces & special-chars_123.txt"
        mock_validator.validate_path.return_value.normalized = test_file

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

            result = await _write_impl(
                session_id="test-session",
                file_path="file with spaces & special-chars_123.txt",
                content="Hello"
            )

            assert "isError" not in result or not result["isError"]
            assert test_file.exists()
            assert test_file.read_text() == "Hello"

    @pytest.mark.asyncio
    async def test_large_content(self, tmp_path, mock_validator, mock_resolver):
        """Test writing large content."""
        test_file = tmp_path / "large.txt"
        mock_validator.validate_path.return_value.normalized = test_file
        # 1MB of content
        content = "x" * (1024 * 1024)

        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator', return_value=mock_validator), \
             patch('tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session', return_value=mock_resolver), \
             patch('tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled', return_value=False):

            result = await _write_impl(
                session_id="test-session",
                file_path="large.txt",
                content=content
            )

            assert "isError" not in result or not result["isError"]
            assert test_file.exists()
            assert test_file.stat().st_size == 1024 * 1024
            assert "1048576 bytes" in result["content"][0]["text"]


class TestIsPathWritableEdgeCases:
    """Additional edge case tests for _is_path_writable."""

    def test_symlink_to_writable_file(self, tmp_path):
        """Test symlink pointing to writable file."""
        real_file = tmp_path / "real.txt"
        real_file.write_text("content")
        symlink = tmp_path / "link.txt"
        symlink.symlink_to(real_file)

        is_writable, reason = _is_path_writable(symlink)
        assert is_writable is True
        assert reason == ""

    def test_symlink_to_readonly_file(self, tmp_path):
        """Test symlink pointing to read-only file."""
        real_file = tmp_path / "real.txt"
        real_file.write_text("content")
        os.chmod(real_file, stat.S_IRUSR)
        symlink = tmp_path / "link.txt"
        symlink.symlink_to(real_file)

        try:
            is_writable, reason = _is_path_writable(symlink)
            assert is_writable is False
        finally:
            os.chmod(real_file, stat.S_IRWXU)

    def test_broken_symlink(self, tmp_path):
        """Test broken symlink (pointing to non-existent file)."""
        symlink = tmp_path / "broken_link.txt"
        symlink.symlink_to(tmp_path / "nonexistent.txt")

        # Broken symlink - path.exists() returns False
        # Should check if parent is writable (new file case)
        is_writable, reason = _is_path_writable(symlink)
        assert is_writable is True  # Parent dir is writable


class TestVerifyFileWrittenEdgeCases:
    """Additional edge case tests for _verify_file_written."""

    def test_empty_file_verification(self, tmp_path):
        """Test verification of empty file."""
        test_file = tmp_path / "empty.txt"
        test_file.write_text("")

        success, error = _verify_file_written(test_file, "")
        assert success is True
        assert error == ""

    def test_very_large_file_verification(self, tmp_path):
        """Test verification of large file."""
        test_file = tmp_path / "large.txt"
        content = "x" * (10 * 1024 * 1024)  # 10MB
        test_file.write_text(content)

        success, error = _verify_file_written(test_file, content)
        assert success is True
        assert error == ""


class TestToolConstants:
    """Tests for tool constants."""

    def test_tool_name_constant(self):
        """Test that tool name constant is correct."""
        assert AG3NTUM_WRITE_TOOL == "mcp__ag3ntum__Write"

    def test_create_write_tool_returns_tool(self):
        """Test that create_write_tool returns something."""
        with patch('tools.ag3ntum.ag3ntum_write.tool.get_path_validator'):
            tool = create_write_tool("test-session")
            # The decorator wraps it in SdkMcpTool, so just verify it's not None
            assert tool is not None
