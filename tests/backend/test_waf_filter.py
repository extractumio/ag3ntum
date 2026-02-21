"""
Unit tests for WAF (Web Application Firewall) Filter.

Tests cover:
- Text content truncation
- File size validation
- Request body size validation
- Body-level size enforcement (actual bytes, not just Content-Length)
- Request data filtering (nested dicts, lists)
- Pydantic model filtering
- Size info utilities
- Size formatting
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.api.waf_filter import (
    MAX_TEXT_CONTENT_LENGTH,
    MAX_FILE_UPLOAD_SIZE,
    MAX_FILES_PER_UPLOAD,
    MAX_REQUEST_BODY_SIZE,
    MAX_TOTAL_UPLOAD_SIZE,
    BLOCKED_EXTENSIONS,
    ALLOWED_EXTENSIONS,
    truncate_text_content,
    validate_file_size,
    validate_file_count,
    validate_total_upload_size,
    validate_file_extension,
    validate_request_body_size,
    validate_request_size,
    filter_request_data,
    filter_pydantic_model,
    get_text_size_info,
    format_size,
)


class TestTruncateTextContent:
    """Test text content truncation."""

    def test_none_returns_none(self) -> None:
        """None input returns None."""
        assert truncate_text_content(None) is None

    def test_short_text_unchanged(self) -> None:
        """Short text is returned unchanged."""
        text = "Hello, world!"
        assert truncate_text_content(text) == text

    def test_exact_limit_unchanged(self) -> None:
        """Text at exact limit is unchanged."""
        text = "a" * MAX_TEXT_CONTENT_LENGTH
        assert truncate_text_content(text) == text
        assert len(truncate_text_content(text)) == MAX_TEXT_CONTENT_LENGTH

    def test_long_text_truncated(self) -> None:
        """Text exceeding limit is truncated."""
        text = "a" * (MAX_TEXT_CONTENT_LENGTH + 100)
        result = truncate_text_content(text)
        assert len(result) == MAX_TEXT_CONTENT_LENGTH

    def test_non_string_returned_as_is(self) -> None:
        """Non-string values are returned unchanged."""
        assert truncate_text_content(123) == 123  # type: ignore
        assert truncate_text_content([1, 2, 3]) == [1, 2, 3]  # type: ignore

    def test_field_name_in_logging(self) -> None:
        """Field name is included in log message."""
        from unittest.mock import patch

        with patch("src.api.waf_filter.logger") as mock_logger:
            long_text = "x" * (MAX_TEXT_CONTENT_LENGTH + 1)
            truncate_text_content(long_text, field_name="task")

        mock_logger.warning.assert_called_once()
        log_msg = mock_logger.warning.call_args[0][0]
        assert "task" in log_msg, (
            f"Expected 'task' field name in log output, got: {log_msg}"
        )


class TestValidateFileSize:
    """Test file upload size validation."""

    def test_small_file_passes(self) -> None:
        """Small file passes validation."""
        # Should not raise
        validate_file_size(1024)  # 1KB

    def test_exact_limit_passes(self) -> None:
        """File at exact limit passes."""
        validate_file_size(MAX_FILE_UPLOAD_SIZE)

    def test_oversized_file_raises_413(self) -> None:
        """Oversized file raises HTTP 413."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_size(MAX_FILE_UPLOAD_SIZE + 1)

        assert exc_info.value.status_code == 413
        assert "exceeds" in exc_info.value.detail.lower()

    def test_zero_size_passes(self) -> None:
        """Zero size file passes."""
        validate_file_size(0)


class TestValidateRequestBodySize:
    """Test request body size validation."""

    def test_normal_request_passes(self) -> None:
        """Normal size request passes."""
        validate_request_body_size(1024 * 1024)  # 1MB

    def test_exact_limit_passes(self) -> None:
        """Request at exact limit passes."""
        validate_request_body_size(MAX_REQUEST_BODY_SIZE)

    def test_oversized_request_raises_413(self) -> None:
        """Oversized request raises HTTP 413."""
        with pytest.raises(HTTPException) as exc_info:
            validate_request_body_size(MAX_REQUEST_BODY_SIZE + 1)

        assert exc_info.value.status_code == 413


class TestFilterRequestData:
    """Test request data filtering."""

    def test_empty_dict_unchanged(self) -> None:
        """Empty dict is returned unchanged."""
        assert filter_request_data({}) == {}

    def test_non_dict_returned_as_is(self) -> None:
        """Non-dict values are returned as-is."""
        assert filter_request_data("string") == "string"  # type: ignore
        assert filter_request_data(123) == 123  # type: ignore

    def test_short_text_fields_unchanged(self) -> None:
        """Short text fields are unchanged."""
        data = {
            "task": "Do something",
            "prompt": "Please help",
            "message": "Hello",
        }
        result = filter_request_data(data)
        assert result == data

    def test_long_task_truncated(self) -> None:
        """Long 'task' field is truncated."""
        long_task = "x" * (MAX_TEXT_CONTENT_LENGTH + 100)
        data = {"task": long_task}
        result = filter_request_data(data)

        assert len(result["task"]) == MAX_TEXT_CONTENT_LENGTH

    def test_long_prompt_truncated(self) -> None:
        """Long 'prompt' field is truncated."""
        long_prompt = "y" * (MAX_TEXT_CONTENT_LENGTH + 50)
        data = {"prompt": long_prompt}
        result = filter_request_data(data)

        assert len(result["prompt"]) == MAX_TEXT_CONTENT_LENGTH

    def test_long_message_truncated(self) -> None:
        """Long 'message' field is truncated."""
        long_message = "z" * (MAX_TEXT_CONTENT_LENGTH + 1)
        data = {"message": long_message}
        result = filter_request_data(data)

        assert len(result["message"]) == MAX_TEXT_CONTENT_LENGTH

    def test_long_content_truncated(self) -> None:
        """Long 'content' field is truncated."""
        data = {"content": "a" * (MAX_TEXT_CONTENT_LENGTH + 10)}
        result = filter_request_data(data)
        assert len(result["content"]) == MAX_TEXT_CONTENT_LENGTH

    def test_long_text_truncated(self) -> None:
        """Long 'text' field is truncated."""
        data = {"text": "b" * (MAX_TEXT_CONTENT_LENGTH + 10)}
        result = filter_request_data(data)
        assert len(result["text"]) == MAX_TEXT_CONTENT_LENGTH

    def test_long_description_truncated(self) -> None:
        """Long 'description' field is truncated."""
        data = {"description": "c" * (MAX_TEXT_CONTENT_LENGTH + 10)}
        result = filter_request_data(data)
        assert len(result["description"]) == MAX_TEXT_CONTENT_LENGTH

    def test_long_output_truncated(self) -> None:
        """Long 'output' field is truncated."""
        data = {"output": "d" * (MAX_TEXT_CONTENT_LENGTH + 10)}
        result = filter_request_data(data)
        assert len(result["output"]) == MAX_TEXT_CONTENT_LENGTH

    def test_long_error_truncated(self) -> None:
        """Long 'error' field is truncated."""
        data = {"error": "e" * (MAX_TEXT_CONTENT_LENGTH + 10)}
        result = filter_request_data(data)
        assert len(result["error"]) == MAX_TEXT_CONTENT_LENGTH

    def test_non_text_fields_unchanged(self) -> None:
        """Non-text fields are not affected."""
        data = {
            "id": 12345,
            "count": 100,
            "enabled": True,
            "custom_field": "x" * (MAX_TEXT_CONTENT_LENGTH + 100),
        }
        result = filter_request_data(data)

        assert result["id"] == 12345
        assert result["count"] == 100
        assert result["enabled"] is True
        # custom_field is not in TEXT_FIELDS, so unchanged
        assert len(result["custom_field"]) > MAX_TEXT_CONTENT_LENGTH

    def test_nested_dict_filtered(self) -> None:
        """Nested dictionaries are filtered recursively."""
        data = {
            "outer": "short",
            "nested": {
                "task": "x" * (MAX_TEXT_CONTENT_LENGTH + 10),
            },
        }
        result = filter_request_data(data)

        assert len(result["nested"]["task"]) == MAX_TEXT_CONTENT_LENGTH

    def test_deeply_nested_filtered(self) -> None:
        """Deeply nested structures are filtered."""
        data = {
            "level1": {
                "level2": {
                    "level3": {
                        "prompt": "y" * (MAX_TEXT_CONTENT_LENGTH + 10),
                    }
                }
            }
        }
        result = filter_request_data(data)

        assert len(result["level1"]["level2"]["level3"]["prompt"]) == MAX_TEXT_CONTENT_LENGTH

    def test_list_of_dicts_filtered(self) -> None:
        """Lists of dicts are filtered."""
        data = {
            "items": [
                {"message": "short"},
                {"message": "z" * (MAX_TEXT_CONTENT_LENGTH + 10)},
            ]
        }
        result = filter_request_data(data)

        assert result["items"][0]["message"] == "short"
        assert len(result["items"][1]["message"]) == MAX_TEXT_CONTENT_LENGTH

    def test_list_of_non_dicts_unchanged(self) -> None:
        """Lists of non-dict items are unchanged."""
        data = {
            "tags": ["tag1", "tag2", "very_long_tag" * 1000],
        }
        result = filter_request_data(data)

        # Non-dict items in list are not filtered
        assert result["tags"] == data["tags"]

    def test_original_data_not_mutated(self) -> None:
        """Original data is not mutated."""
        original = {"task": "x" * (MAX_TEXT_CONTENT_LENGTH + 10)}
        original_task_len = len(original["task"])

        filter_request_data(original)

        # Original should still have the long task
        assert len(original["task"]) == original_task_len


class TestFilterPydanticModel:
    """Test Pydantic model filtering."""

    def test_model_filtered_and_recreated(self) -> None:
        """Model is filtered and a new instance is created."""
        from pydantic import BaseModel

        class TestModel(BaseModel):
            task: str
            count: int

        model = TestModel(
            task="x" * (MAX_TEXT_CONTENT_LENGTH + 10),
            count=5,
        )

        result = filter_pydantic_model(model)

        assert len(result.task) == MAX_TEXT_CONTENT_LENGTH
        assert result.count == 5
        # Should be a new instance
        assert result is not model


class TestGetTextSizeInfo:
    """Test text size info utility."""

    def test_none_text_info(self) -> None:
        """None text returns zero lengths."""
        info = get_text_size_info(None)
        assert info["length"] == 0
        assert info["size_bytes"] == 0
        assert info["truncated"] is False
        assert info["limit"] == MAX_TEXT_CONTENT_LENGTH

    def test_short_text_info(self) -> None:
        """Short text shows correct info."""
        info = get_text_size_info("hello")
        assert info["length"] == 5
        assert info["size_bytes"] == 5
        assert info["truncated"] is False

    def test_long_text_marked_truncated(self) -> None:
        """Long text is marked as truncated."""
        long_text = "x" * (MAX_TEXT_CONTENT_LENGTH + 1)
        info = get_text_size_info(long_text)

        assert info["truncated"] is True
        assert info["length"] == MAX_TEXT_CONTENT_LENGTH + 1

    def test_utf8_byte_count(self) -> None:
        """UTF-8 byte count handles multibyte chars."""
        # Unicode snowman is 3 bytes in UTF-8
        text = "☃" * 10
        info = get_text_size_info(text)

        assert info["length"] == 10
        assert info["size_bytes"] == 30  # 3 bytes each


class TestFormatSize:
    """Test size formatting utility."""

    def test_bytes_format(self) -> None:
        """Small sizes shown in bytes."""
        assert format_size(100) == "100B"
        assert format_size(1023) == "1023B"

    def test_kilobytes_format(self) -> None:
        """Kilobyte sizes formatted correctly."""
        assert format_size(1024) == "1.0KB"
        assert format_size(1536) == "1.5KB"
        assert format_size(1024 * 500) == "500.0KB"

    def test_megabytes_format(self) -> None:
        """Megabyte sizes formatted correctly."""
        assert format_size(1024 * 1024) == "1.0MB"
        assert format_size(1024 * 1024 * 10) == "10.0MB"
        assert format_size(int(1024 * 1024 * 1.5)) == "1.5MB"

    def test_zero_bytes(self) -> None:
        """Zero bytes formatted."""
        assert format_size(0) == "0B"


class TestConstants:
    """Test WAF filter constants are reasonable."""

    def test_text_limit_is_5mb(self) -> None:
        """Text content limit is 5MB."""
        assert MAX_TEXT_CONTENT_LENGTH == 5 * 1024 * 1024

    def test_file_limit_is_10mb(self) -> None:
        """File upload limit is 10MB."""
        assert MAX_FILE_UPLOAD_SIZE == 10 * 1024 * 1024

    def test_request_limit_is_60mb(self) -> None:
        """Request body limit is 60MB (allows for large prompts and base64 overhead)."""
        assert MAX_REQUEST_BODY_SIZE == 60 * 1024 * 1024

    def test_request_larger_than_file_limit(self) -> None:
        """Request limit is larger than file limit (for base64)."""
        assert MAX_REQUEST_BODY_SIZE > MAX_FILE_UPLOAD_SIZE


class TestValidateRequestSizeBodyEnforcement:
    """Test body-level size enforcement in validate_request_size.

    The middleware must measure actual bytes, not just trust Content-Length.
    """

    def _make_request(self, content_length: str | None = None) -> MagicMock:
        """Create a mock Request with a controllable _receive callable."""
        request = MagicMock()
        request.headers = {}
        if content_length is not None:
            request.headers["content-length"] = content_length
        # _receive will be replaced by validate_request_size
        request._receive = AsyncMock(return_value={
            "type": "http.request",
            "body": b"",
            "more_body": False,
        })
        return request

    @pytest.mark.asyncio
    async def test_spoofed_content_length_caught(self) -> None:
        """Body exceeding limit is caught even with small Content-Length header."""
        request = self._make_request(content_length="100")

        await validate_request_size(request)

        # Now simulate the wrapped receive returning a huge body
        oversized_body = b"x" * (MAX_REQUEST_BODY_SIZE + 1)
        original_receive = AsyncMock(return_value={
            "type": "http.request",
            "body": oversized_body,
        })
        # Replace the original receive that was captured by the wrapper
        # We need to call the wrapper which was installed on request._receive
        wrapped_receive = request._receive

        # Patch the closure's captured original_receive
        # The easiest way is to build a new request and manually test
        request2 = self._make_request(content_length="100")
        request2._receive = AsyncMock(return_value={
            "type": "http.request",
            "body": oversized_body,
        })
        await validate_request_size(request2)

        with pytest.raises(HTTPException) as exc_info:
            await request2._receive()

        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_normal_body_passes(self) -> None:
        """Normal-sized body passes through the wrapper."""
        request = self._make_request(content_length="1024")
        small_body = b"x" * 1024
        request._receive = AsyncMock(return_value={
            "type": "http.request",
            "body": small_body,
        })

        await validate_request_size(request)

        # The wrapped receive should return the message
        result = await request._receive()
        assert result["body"] == small_body

    @pytest.mark.asyncio
    async def test_content_length_header_precheck(self) -> None:
        """Oversized Content-Length is caught in pre-check (before body read)."""
        oversized = str(MAX_REQUEST_BODY_SIZE + 1)
        request = self._make_request(content_length=oversized)

        with pytest.raises(HTTPException) as exc_info:
            await validate_request_size(request)

        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_missing_content_length_still_enforced(self) -> None:
        """Missing Content-Length header still gets body-level enforcement."""
        request = self._make_request(content_length=None)
        oversized_body = b"x" * (MAX_REQUEST_BODY_SIZE + 1)
        request._receive = AsyncMock(return_value={
            "type": "http.request",
            "body": oversized_body,
        })

        await validate_request_size(request)

        # Body wrapper should catch it on read
        with pytest.raises(HTTPException) as exc_info:
            await request._receive()

        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_chunked_body_accumulated(self) -> None:
        """Body sent in multiple chunks is accumulated for size checking."""
        request = self._make_request(content_length=None)
        chunk_size = MAX_REQUEST_BODY_SIZE // 2

        # First chunk - under limit
        request._receive = AsyncMock(return_value={
            "type": "http.request",
            "body": b"x" * chunk_size,
            "more_body": True,
        })
        await validate_request_size(request)

        wrapped_receive = request._receive

        # First call succeeds (under limit)
        result = await wrapped_receive()
        assert result["type"] == "http.request"

        # Now set up a second chunk that pushes over the limit
        # We need to modify the original receive mock to return a second chunk
        # Since the wrapper captured the original, we need a different approach
        # Let's create a counter-based mock
        call_count = 0
        async def chunked_receive():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"type": "http.request", "body": b"x" * chunk_size, "more_body": True}
            else:
                return {"type": "http.request", "body": b"x" * (chunk_size + 2), "more_body": False}

        # Re-build with a fresh request
        request2 = MagicMock()
        request2.headers = {}
        request2._receive = chunked_receive

        await validate_request_size(request2)
        wrapped = request2._receive

        # First chunk OK
        await wrapped()
        # Second chunk pushes over - should raise
        with pytest.raises(HTTPException) as exc_info:
            await wrapped()

        assert exc_info.value.status_code == 413


# =============================================================================
# Test: File Extension Validation
# =============================================================================

class TestFileExtensionValidation:
    """Test validate_file_extension() with various filename patterns."""

    def test_blocked_exe_extension(self) -> None:
        """Files with .exe extension are blocked."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_extension("malware.exe")
        assert exc_info.value.status_code == 400
        assert ".exe" in exc_info.value.detail

    def test_blocked_dll_extension(self) -> None:
        """Files with .dll extension are blocked."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_extension("library.dll")
        assert exc_info.value.status_code == 400

    def test_blocked_sh_extension(self) -> None:
        """Files with .sh extension are blocked."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_extension("script.sh")
        assert exc_info.value.status_code == 400

    def test_blocked_bat_extension(self) -> None:
        """Files with .bat extension are blocked."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_extension("run.bat")
        assert exc_info.value.status_code == 400

    def test_blocked_so_extension(self) -> None:
        """Files with .so extension are blocked."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_extension("libcrypto.so")
        assert exc_info.value.status_code == 400

    def test_double_extension_bypass_blocked(self) -> None:
        """Double extension attack (malware.txt.exe) is blocked.

        WAF uses rsplit('.', 1) to get the last extension, so .exe is detected.
        """
        with pytest.raises(HTTPException) as exc_info:
            validate_file_extension("malware.txt.exe")
        assert exc_info.value.status_code == 400

    def test_case_insensitive_exe(self) -> None:
        """Uppercase .EXE extension is blocked (case-insensitive check)."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_extension("malware.EXE")
        assert exc_info.value.status_code == 400

    def test_mixed_case_exe(self) -> None:
        """Mixed-case .Exe extension is blocked."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_extension("malware.Exe")
        assert exc_info.value.status_code == 400

    def test_no_extension_passes(self) -> None:
        """File without extension passes (no dot in filename)."""
        # Should not raise
        validate_file_extension("Makefile")

    def test_dot_only_filename(self) -> None:
        """Filename that is just a dot has empty extension - passes default blocklist."""
        # "." has no extension part after rsplit(".", 1) -> ext = ""
        # Empty string is not in BLOCKED_EXTENSIONS
        validate_file_extension(".")

    def test_allowed_txt_extension(self) -> None:
        """Safe .txt extension passes."""
        validate_file_extension("readme.txt")

    def test_allowed_py_extension(self) -> None:
        """Safe .py extension passes."""
        validate_file_extension("main.py")

    def test_allowed_json_extension(self) -> None:
        """Safe .json extension passes."""
        validate_file_extension("config.json")

    def test_custom_blocked_set(self) -> None:
        """Custom blocked set overrides defaults."""
        # .py is not in default blocklist but we can custom-block it
        with pytest.raises(HTTPException):
            validate_file_extension("script.py", blocked={".py"})

    def test_custom_allowed_set_restricts(self) -> None:
        """When allowed set is non-empty, only those extensions pass."""
        # .txt is allowed, .py is not
        validate_file_extension("data.txt", blocked=set(), allowed={".txt"})
        with pytest.raises(HTTPException):
            validate_file_extension("main.py", blocked=set(), allowed={".txt"})

    def test_all_default_blocked_extensions(self) -> None:
        """Every extension in BLOCKED_EXTENSIONS is actually blocked."""
        for ext in BLOCKED_EXTENSIONS:
            with pytest.raises(HTTPException):
                validate_file_extension(f"file{ext}")


# =============================================================================
# Test: File Count Validation
# =============================================================================

class TestFileCountValidation:
    """Test validate_file_count() for upload file limits."""

    def test_count_within_limit(self) -> None:
        """File count within default limit passes."""
        validate_file_count(1)
        validate_file_count(MAX_FILES_PER_UPLOAD)

    def test_count_exceeding_limit_raises_400(self) -> None:
        """File count exceeding limit raises HTTP 400."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_count(MAX_FILES_PER_UPLOAD + 1)
        assert exc_info.value.status_code == 400
        assert "Too many files" in exc_info.value.detail

    def test_zero_files_passes(self) -> None:
        """Zero files passes validation."""
        validate_file_count(0)

    def test_custom_max_files(self) -> None:
        """Custom max_files parameter is respected."""
        validate_file_count(5, max_files=5)
        with pytest.raises(HTTPException):
            validate_file_count(6, max_files=5)

    def test_exactly_at_limit(self) -> None:
        """Exactly at the limit passes."""
        validate_file_count(MAX_FILES_PER_UPLOAD)

    def test_one_over_limit(self) -> None:
        """One over the limit is rejected."""
        with pytest.raises(HTTPException):
            validate_file_count(MAX_FILES_PER_UPLOAD + 1)


# =============================================================================
# Test: Total Upload Size Validation
# =============================================================================

class TestTotalUploadSizeValidation:
    """Test validate_total_upload_size() for aggregate upload limits."""

    def test_size_within_limit(self) -> None:
        """Total size within limit passes."""
        validate_total_upload_size(1024)
        validate_total_upload_size(MAX_TOTAL_UPLOAD_SIZE)

    def test_size_exceeding_limit_raises_413(self) -> None:
        """Total size exceeding limit raises HTTP 413."""
        with pytest.raises(HTTPException) as exc_info:
            validate_total_upload_size(MAX_TOTAL_UPLOAD_SIZE + 1)
        assert exc_info.value.status_code == 413
        assert "exceeds" in exc_info.value.detail.lower()

    def test_zero_size_passes(self) -> None:
        """Zero total size passes."""
        validate_total_upload_size(0)

    def test_custom_max_total(self) -> None:
        """Custom max_total parameter is respected."""
        validate_total_upload_size(100, max_total=100)
        with pytest.raises(HTTPException):
            validate_total_upload_size(101, max_total=100)

    def test_exactly_at_limit(self) -> None:
        """Exactly at the limit passes."""
        validate_total_upload_size(MAX_TOTAL_UPLOAD_SIZE)


# =============================================================================
# Test: Injection Prevention (WAF is size-only, not content-filtering)
# =============================================================================

class TestInjectionPrevention:
    """Verify WAF behavior with malicious content payloads.

    IMPORTANT: The WAF module (waf_filter.py) is a SIZE-ONLY filter.
    It does NOT perform content-based filtering (SQL injection, XSS, etc.).
    Content filtering is delegated to other security layers:
    - Layer 3: Ag3ntum Tools (path_validator.py) for file ops
    - Layer 4: Command Filter (command_security.py) for bash commands
    - Layer 6: Prompts (security.md) for LLM behavior

    These tests confirm that malicious content WITHIN size limits passes
    through the WAF, since the WAF's job is only to prevent DoS via
    oversized payloads, not to sanitize content.
    """

    def test_sql_injection_within_size_passes(self) -> None:
        """SQL injection payloads pass WAF if within size limits.

        WAF does not do content filtering - that's handled by other layers.
        """
        data = {"task": "'; DROP TABLE users; --"}
        result = filter_request_data(data)
        assert result["task"] == "'; DROP TABLE users; --"

    def test_xss_payload_within_size_passes(self) -> None:
        """XSS payloads pass WAF if within size limits."""
        data = {"message": '<script>alert("XSS")</script>'}
        result = filter_request_data(data)
        assert "<script>" in result["message"]

    def test_crlf_injection_within_size_passes(self) -> None:
        """CRLF injection payloads pass WAF if within size limits."""
        data = {"content": "header\r\nX-Injected: true\r\n\r\nmalicious body"}
        result = filter_request_data(data)
        assert "\r\n" in result["content"]

    def test_path_traversal_string_within_size_passes(self) -> None:
        """Path traversal strings pass WAF (blocked by PathValidator layer)."""
        data = {"text": "../../../etc/passwd"}
        result = filter_request_data(data)
        assert result["text"] == "../../../etc/passwd"

    def test_command_injection_string_within_size_passes(self) -> None:
        """Command injection strings pass WAF (blocked by CommandSecurity layer)."""
        data = {"description": "; rm -rf / ; echo pwned"}
        result = filter_request_data(data)
        assert "rm -rf" in result["description"]
