"""
Tests for trace_processor sanitization and processing functions.

Critical security tests for:
- Filename sanitization (path traversal, XSS prevention)
- MIME type validation
- Extension sanitization
- System reminder stripping
- Tool name sanitization
"""
import pytest

from src.core.trace_processor import (
    _sanitize_filename,
    _sanitize_mime_type,
    _sanitize_extension,
    _sanitize_size_formatted,
    strip_system_reminders,
    sanitize_tool_names_in_text,
)


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
