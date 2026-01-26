"""
Tests for trace_processor sanitization and processing functions.

Critical security tests for:
- Filename sanitization (path traversal, XSS prevention)
- MIME type validation
- Extension sanitization
- System reminder stripping
- Tool name sanitization
- Tool error tracking for session status determination
"""
import pytest
from unittest.mock import MagicMock

from src.core.trace_processor import (
    _sanitize_filename,
    _sanitize_mime_type,
    _sanitize_extension,
    _sanitize_size_formatted,
    strip_system_reminders,
    sanitize_tool_names_in_text,
    TraceProcessor,
)
from src.core.tracer import NullTracer


class TestSanitizeFilename:
    """Tests for _sanitize_filename function - security critical."""

    @pytest.mark.unit
    def test_normal_filename_unchanged(self) -> None:
        """Normal filename passes through."""
        assert _sanitize_filename("document.pdf") == "document.pdf"
        assert _sanitize_filename("my file.txt") == "my file.txt"
        assert _sanitize_filename("image_2024.png") == "image_2024.png"

    @pytest.mark.unit
    def test_empty_returns_unnamed(self) -> None:
        """Empty or None filename returns 'unnamed_file'."""
        assert _sanitize_filename("") == "unnamed_file"
        assert _sanitize_filename(None) == "unnamed_file"

    @pytest.mark.unit
    def test_path_traversal_removed(self) -> None:
        """Path traversal sequences are stripped."""
        assert "../" not in _sanitize_filename("../../../etc/passwd")
        assert "..\\" not in _sanitize_filename("..\\..\\windows\\system32")
        assert _sanitize_filename("../file.txt") == "file.txt"

    @pytest.mark.unit
    def test_null_bytes_removed(self) -> None:
        """Null bytes and control characters are stripped."""
        assert "\x00" not in _sanitize_filename("file\x00.txt")
        assert "\x1f" not in _sanitize_filename("file\x1f.txt")
        assert "\x7f" not in _sanitize_filename("file\x7f.txt")

    @pytest.mark.unit
    def test_special_characters_replaced(self) -> None:
        """Characters dangerous for display/storage are replaced."""
        result = _sanitize_filename("file<script>.txt")
        assert "<" not in result
        assert ">" not in result

        result = _sanitize_filename("file|test.txt")
        assert "|" not in result

        result = _sanitize_filename('file"name.txt')
        assert '"' not in result

    @pytest.mark.unit
    def test_leading_trailing_dots_stripped(self) -> None:
        """Leading/trailing dots and spaces are removed."""
        assert _sanitize_filename("...file.txt") == "file.txt"
        assert _sanitize_filename("file.txt...") == "file.txt"
        assert _sanitize_filename("  file.txt  ") == "file.txt"

    @pytest.mark.unit
    def test_long_filename_truncated(self) -> None:
        """Very long filenames are truncated with ellipsis."""
        long_name = "a" * 300 + ".txt"
        result = _sanitize_filename(long_name)
        assert len(result) <= 255
        assert result.endswith(".txt") or result.endswith("...")

    @pytest.mark.unit
    def test_multiple_spaces_collapsed(self) -> None:
        """Multiple consecutive spaces are collapsed to single space."""
        assert _sanitize_filename("file   name.txt") == "file name.txt"

    @pytest.mark.unit
    def test_xss_injection_attempt(self) -> None:
        """XSS injection attempts are sanitized."""
        # Script tags
        result = _sanitize_filename("<script>alert('xss')</script>.txt")
        assert "<script>" not in result
        assert ">" not in result

        # Event handlers
        result = _sanitize_filename("image.png\" onload=\"alert(1)")
        assert '"' not in result


class TestSanitizeMimeType:
    """Tests for _sanitize_mime_type function."""

    @pytest.mark.unit
    def test_valid_mime_unchanged(self) -> None:
        """Valid MIME types pass through (lowercased)."""
        assert _sanitize_mime_type("text/plain") == "text/plain"
        assert _sanitize_mime_type("application/json") == "application/json"
        assert _sanitize_mime_type("image/svg+xml") == "image/svg+xml"

    @pytest.mark.unit
    def test_empty_returns_empty(self) -> None:
        """Empty or None returns empty string."""
        assert _sanitize_mime_type("") == ""
        assert _sanitize_mime_type(None) == ""

    @pytest.mark.unit
    def test_invalid_chars_removed(self) -> None:
        """Invalid characters are stripped from MIME type."""
        result = _sanitize_mime_type("text/plain<script>")
        assert "<" not in result
        assert ">" not in result

    @pytest.mark.unit
    def test_length_limited(self) -> None:
        """Very long MIME types are truncated."""
        long_mime = "application/" + "a" * 200
        result = _sanitize_mime_type(long_mime)
        assert len(result) <= 100


class TestSanitizeExtension:
    """Tests for _sanitize_extension function."""

    @pytest.mark.unit
    def test_valid_extension(self) -> None:
        """Valid extensions pass through (lowercased)."""
        assert _sanitize_extension("pdf") == "pdf"
        assert _sanitize_extension("PDF") == "pdf"
        assert _sanitize_extension("txt") == "txt"

    @pytest.mark.unit
    def test_empty_returns_empty(self) -> None:
        """Empty or None returns empty string."""
        assert _sanitize_extension("") == ""
        assert _sanitize_extension(None) == ""

    @pytest.mark.unit
    def test_special_chars_removed(self) -> None:
        """Special characters are stripped."""
        assert _sanitize_extension("pdf.exe") == "pdfexe"
        assert _sanitize_extension("pdf<script>") == "pdfscript"

    @pytest.mark.unit
    def test_length_limited(self) -> None:
        """Very long extensions are truncated."""
        long_ext = "a" * 50
        result = _sanitize_extension(long_ext)
        assert len(result) <= 10


class TestSanitizeSizeFormatted:
    """Tests for _sanitize_size_formatted function."""

    @pytest.mark.unit
    def test_valid_sizes(self) -> None:
        """Valid size strings pass through."""
        assert _sanitize_size_formatted("1.5MB") == "1.5MB"
        assert _sanitize_size_formatted("100 KB") == "100 KB"
        assert _sanitize_size_formatted("2.3 GB") == "2.3 GB"

    @pytest.mark.unit
    def test_empty_returns_empty(self) -> None:
        """Empty returns empty string."""
        assert _sanitize_size_formatted("") == ""
        assert _sanitize_size_formatted(None) == ""

    @pytest.mark.unit
    def test_invalid_chars_removed(self) -> None:
        """Invalid characters are stripped."""
        result = _sanitize_size_formatted("1.5MB<script>")
        assert "<" not in result


class TestStripSystemReminders:
    """Tests for strip_system_reminders function."""

    @pytest.mark.unit
    def test_no_reminder_unchanged(self) -> None:
        """Text without reminders passes through unchanged."""
        text = "This is normal text without any reminders."
        assert strip_system_reminders(text) == text

    @pytest.mark.unit
    def test_single_reminder_removed(self) -> None:
        """Single system-reminder block is removed."""
        text = "Before <system-reminder>hidden content</system-reminder> After"
        result = strip_system_reminders(text)
        assert "Before" in result
        assert "After" in result
        assert "hidden content" not in result
        assert "<system-reminder>" not in result

    @pytest.mark.unit
    def test_multiline_reminder_removed(self) -> None:
        """Multiline system-reminder block is removed."""
        text = """Before
<system-reminder>
This is a multiline
reminder block
</system-reminder>
After"""
        result = strip_system_reminders(text)
        assert "Before" in result
        assert "After" in result
        assert "multiline" not in result

    @pytest.mark.unit
    def test_multiple_reminders_removed(self) -> None:
        """Multiple system-reminder blocks are all removed."""
        text = "A <system-reminder>1</system-reminder> B <system-reminder>2</system-reminder> C"
        result = strip_system_reminders(text)
        assert "A" in result
        assert "B" in result
        assert "C" in result
        assert "<system-reminder>" not in result


class TestSanitizeToolNamesInText:
    """Tests for sanitize_tool_names_in_text function."""

    @pytest.mark.unit
    def test_no_tool_names_unchanged(self) -> None:
        """Text without tool names passes through unchanged."""
        text = "This is normal text."
        assert sanitize_tool_names_in_text(text) == text

    @pytest.mark.unit
    def test_mcp_tool_name_simplified(self) -> None:
        """MCP tool names are simplified for display."""
        text = "Using mcp__ag3ntum__ReadFile to read the file."
        result = sanitize_tool_names_in_text(text)
        assert "mcp__ag3ntum__" not in result
        assert "ReadFile" in result

    @pytest.mark.unit
    def test_multiple_tool_names_simplified(self) -> None:
        """Multiple MCP tool names are all simplified."""
        text = "Used mcp__ag3ntum__ReadFile and mcp__ag3ntum__WriteFile"
        result = sanitize_tool_names_in_text(text)
        assert "mcp__ag3ntum__" not in result
        assert "ReadFile" in result
        assert "WriteFile" in result


class TestTraceProcessorToolErrorTracking:
    """Tests for TraceProcessor tool error tracking.

    The TraceProcessor tracks tool errors during execution to determine
    the final session status. If any tool returns is_error=True, the
    session should be marked as FAILED instead of COMPLETE.
    """

    @pytest.fixture
    def trace_processor(self) -> TraceProcessor:
        """Create a TraceProcessor with a NullTracer for testing."""
        return TraceProcessor(NullTracer())

    @pytest.mark.unit
    def test_initial_tool_error_count_is_zero(self, trace_processor: TraceProcessor) -> None:
        """Tool error count starts at zero."""
        assert trace_processor.tool_error_count == 0
        assert trace_processor.had_tool_errors() is False

    @pytest.mark.unit
    def test_tool_error_count_property(self, trace_processor: TraceProcessor) -> None:
        """tool_error_count property returns current count."""
        assert trace_processor.tool_error_count == 0
        trace_processor._tool_error_count = 5
        assert trace_processor.tool_error_count == 5

    @pytest.mark.unit
    def test_had_tool_errors_false_when_no_errors(self, trace_processor: TraceProcessor) -> None:
        """had_tool_errors() returns False when no errors occurred."""
        assert trace_processor.had_tool_errors() is False

    @pytest.mark.unit
    def test_had_tool_errors_true_when_errors_exist(self, trace_processor: TraceProcessor) -> None:
        """had_tool_errors() returns True when errors occurred."""
        trace_processor._tool_error_count = 1
        assert trace_processor.had_tool_errors() is True

        trace_processor._tool_error_count = 10
        assert trace_processor.had_tool_errors() is True

    @pytest.mark.unit
    def test_tool_complete_increments_error_count_on_error(self, trace_processor: TraceProcessor) -> None:
        """on_tool_complete with is_error=True increments error count."""
        # Register a pending tool call first
        trace_processor._pending_tool_calls["tool-1"] = {"name": "TestTool"}

        # Create a ToolResultBlock with is_error=True
        from claude_agent_sdk.types import ToolResultBlock
        block = ToolResultBlock(
            tool_use_id="tool-1",
            content="Error: Something went wrong",
            is_error=True,
        )

        # Process the block - this should increment error count
        trace_processor._process_content_block(block)

        assert trace_processor.tool_error_count == 1
        assert trace_processor.had_tool_errors() is True

    @pytest.mark.unit
    def test_tool_complete_no_increment_on_success(self, trace_processor: TraceProcessor) -> None:
        """on_tool_complete with is_error=False does not increment count."""
        # Register a pending tool call
        trace_processor._pending_tool_calls["tool-1"] = {"name": "TestTool"}

        # Create a ToolResultBlock with is_error=False
        from claude_agent_sdk.types import ToolResultBlock
        block = ToolResultBlock(
            tool_use_id="tool-1",
            content="Success",
            is_error=False,
        )

        trace_processor._process_content_block(block)

        assert trace_processor.tool_error_count == 0
        assert trace_processor.had_tool_errors() is False

    @pytest.mark.unit
    def test_multiple_tool_errors_accumulate(self, trace_processor: TraceProcessor) -> None:
        """Multiple tool errors accumulate in the count."""
        from claude_agent_sdk.types import ToolResultBlock

        # Process multiple error results
        for i in range(3):
            tool_id = f"tool-{i}"
            trace_processor._pending_tool_calls[tool_id] = {"name": f"TestTool{i}"}
            block = ToolResultBlock(
                tool_use_id=tool_id,
                content=f"Error {i}",
                is_error=True,
            )
            trace_processor._process_content_block(block)

        assert trace_processor.tool_error_count == 3
        assert trace_processor.had_tool_errors() is True

    @pytest.mark.unit
    def test_mixed_success_and_error_tools(self, trace_processor: TraceProcessor) -> None:
        """Error count only reflects tools that actually errored."""
        from claude_agent_sdk.types import ToolResultBlock

        # Process mix of success and error results
        results = [
            ("tool-1", "Success 1", False),
            ("tool-2", "Error 1", True),
            ("tool-3", "Success 2", False),
            ("tool-4", "Error 2", True),
            ("tool-5", "Success 3", False),
        ]

        for tool_id, content, is_error in results:
            trace_processor._pending_tool_calls[tool_id] = {"name": tool_id}
            block = ToolResultBlock(
                tool_use_id=tool_id,
                content=content,
                is_error=is_error,
            )
            trace_processor._process_content_block(block)

        # Only 2 errors
        assert trace_processor.tool_error_count == 2
        assert trace_processor.had_tool_errors() is True
