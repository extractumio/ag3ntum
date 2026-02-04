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


class TestPathDisplayTransformation:
    """Tests for path display transformation in TraceProcessor.

    The TraceProcessor transforms internal mount paths to host paths
    for user-friendly display. This converts paths like:
    - ./external/ro/global_var_log/syslog -> /var/log/syslog
    - var-log/auth.log -> /var/log/auth.log (dynamic mount)
    """

    @pytest.fixture
    def trace_processor(self) -> TraceProcessor:
        """Create a TraceProcessor with a NullTracer for testing."""
        return TraceProcessor(NullTracer())

    @pytest.fixture
    def trace_processor_with_mapping(self) -> TraceProcessor:
        """Create a TraceProcessor with path display mapping configured."""
        mapping = {
            "external/ro/global_var_log": "/var/log",
            "external/rw/product_docs": "/Users/greg/PRODUCT",
            "external/user-ro/all_documents": "/Users/greg/Documents",
            "var-log": "/var/log",  # dynamic mount alias
        }
        return TraceProcessor(NullTracer(), path_display_mapping=mapping)

    @pytest.mark.unit
    def test_no_mapping_text_unchanged(self, trace_processor: TraceProcessor) -> None:
        """Text passes through unchanged when no mapping is configured."""
        text = "Reading ./external/ro/global_var_log/syslog"
        result = trace_processor._transform_paths_for_display(text)
        assert result == text

    @pytest.mark.unit
    def test_empty_text_returns_empty(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """Empty text returns empty string."""
        assert trace_processor_with_mapping._transform_paths_for_display("") == ""

    @pytest.mark.unit
    def test_external_ro_path_transformed(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """External RO mount path is transformed to host path."""
        text = "Reading ./external/ro/global_var_log/syslog"
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == "Reading /var/log/syslog"

    @pytest.mark.unit
    def test_external_ro_path_without_dot_prefix(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """External path without ./ prefix is also transformed."""
        text = "Found in external/ro/global_var_log/auth.log"
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == "Found in /var/log/auth.log"

    @pytest.mark.unit
    def test_external_rw_path_transformed(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """External RW mount path is transformed to host path."""
        text = "Writing to ./external/rw/product_docs/readme.md"
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == "Writing to /Users/greg/PRODUCT/readme.md"

    @pytest.mark.unit
    def test_user_ro_path_transformed(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """User RO mount path is transformed to host path."""
        text = "Accessing ./external/user-ro/all_documents/notes.txt"
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == "Accessing /Users/greg/Documents/notes.txt"

    @pytest.mark.unit
    def test_dynamic_mount_alias_transformed(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """Dynamic mount alias is transformed to host path."""
        text = "Reading ./var-log/syslog"
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == "Reading /var/log/syslog"

    @pytest.mark.unit
    def test_mount_path_only_no_subpath(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """Mount path without subpath is transformed."""
        text = "Listing ./external/ro/global_var_log"
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == "Listing /var/log"

    @pytest.mark.unit
    def test_multiple_paths_in_same_text(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """Multiple paths in same text are all transformed."""
        text = "Comparing ./external/ro/global_var_log/syslog with ./external/rw/product_docs/log.txt"
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == "Comparing /var/log/syslog with /Users/greg/PRODUCT/log.txt"

    @pytest.mark.unit
    def test_nested_subpath_preserved(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """Deeply nested subpaths are preserved after transformation."""
        text = "Found ./external/ro/global_var_log/nginx/access.log"
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == "Found /var/log/nginx/access.log"

    @pytest.mark.unit
    def test_path_in_quotes_transformed(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """Paths in quotes are transformed (boundary at quote)."""
        text = 'Opening "./external/ro/global_var_log/syslog" for reading'
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == 'Opening "/var/log/syslog" for reading'

    @pytest.mark.unit
    def test_path_in_backticks_transformed(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """Paths in backticks are transformed."""
        text = "Check `./external/ro/global_var_log/auth.log` for details"
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == "Check `/var/log/auth.log` for details"

    @pytest.mark.unit
    def test_unmatched_path_unchanged(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """Paths not in mapping are unchanged."""
        text = "Reading ./external/ro/unknown_mount/file.txt"
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == text

    @pytest.mark.unit
    def test_workspace_path_unchanged(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """Regular workspace paths are unchanged."""
        text = "Created ./output/results.json"
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == text

    @pytest.mark.unit
    def test_set_path_display_mapping_updates_processor(self, trace_processor: TraceProcessor) -> None:
        """set_path_display_mapping updates the processor's mapping."""
        # Initially no mapping
        text = "Reading ./external/ro/global_var_log/syslog"
        assert trace_processor._transform_paths_for_display(text) == text

        # Set mapping
        trace_processor.set_path_display_mapping({
            "external/ro/global_var_log": "/var/log"
        })

        # Now transformation should work
        result = trace_processor._transform_paths_for_display(text)
        assert result == "Reading /var/log/syslog"

    @pytest.mark.unit
    def test_sanitize_text_includes_path_transformation(
        self, trace_processor_with_mapping: TraceProcessor
    ) -> None:
        """_sanitize_text applies both global sanitization and path transformation."""
        # Text with MCP tool name and path
        text = "mcp__ag3ntum__Read opened ./external/ro/global_var_log/syslog"
        result = trace_processor_with_mapping._sanitize_text(text)

        # Tool name should be sanitized
        assert "mcp__ag3ntum__" not in result
        assert "Read" in result

        # Path should be transformed
        assert "/var/log/syslog" in result
        assert "external/ro/global_var_log" not in result

    @pytest.mark.unit
    def test_sanitize_text_strips_reminders_and_transforms_paths(
        self, trace_processor_with_mapping: TraceProcessor
    ) -> None:
        """_sanitize_text strips system reminders and transforms paths."""
        text = """Found file at ./external/ro/global_var_log/syslog
<system-reminder>Internal note</system-reminder>
Contents shown below."""
        result = trace_processor_with_mapping._sanitize_text(text)

        # Path transformed
        assert "/var/log/syslog" in result
        # System reminder stripped
        assert "<system-reminder>" not in result
        assert "Internal note" not in result

    @pytest.mark.unit
    def test_longest_prefix_matched_first(self) -> None:
        """Longer prefixes are matched before shorter ones (avoids partial matches)."""
        # Mapping with overlapping prefixes
        mapping = {
            "external/ro": "/short",
            "external/ro/global_var_log": "/var/log",
        }
        processor = TraceProcessor(NullTracer(), path_display_mapping=mapping)

        text = "./external/ro/global_var_log/syslog"
        result = processor._transform_paths_for_display(text)

        # Should match the longer, more specific prefix
        assert result == "/var/log/syslog"
        assert "/short/global_var_log" not in result

    @pytest.mark.unit
    def test_path_at_end_of_line(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """Path at end of line (no trailing character) is transformed."""
        text = "File location: ./external/ro/global_var_log/syslog"
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == "File location: /var/log/syslog"

    @pytest.mark.unit
    def test_path_followed_by_comma(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """Path followed by comma is transformed correctly."""
        text = "Files: ./external/ro/global_var_log/a.log, ./external/ro/global_var_log/b.log"
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == "Files: /var/log/a.log, /var/log/b.log"

    @pytest.mark.unit
    def test_path_followed_by_parenthesis(self, trace_processor_with_mapping: TraceProcessor) -> None:
        """Path followed by closing parenthesis is transformed."""
        text = "See log (./external/ro/global_var_log/syslog) for details"
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == "See log (/var/log/syslog) for details"

    @pytest.mark.unit
    def test_path_with_special_characters_in_filename(
        self, trace_processor_with_mapping: TraceProcessor
    ) -> None:
        """Paths with special characters in filename are handled."""
        text = "Reading ./external/ro/global_var_log/my-app_2024.log"
        result = trace_processor_with_mapping._transform_paths_for_display(text)
        assert result == "Reading /var/log/my-app_2024.log"
