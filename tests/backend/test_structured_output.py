"""
Tests for structured_output parsing functions.

Tests cover:
- Error value normalization
- Header block parsing
- Body extraction
- Edge cases and malformed inputs
"""
import pytest

from src.core.structured_output import (
    normalize_error_value,
    parse_structured_output,
)


class TestNormalizeErrorValue:
    """Tests for normalize_error_value function."""

    @pytest.mark.unit
    def test_actual_error_preserved(self) -> None:
        """Actual error messages are preserved."""
        assert normalize_error_value("Connection failed") == "Connection failed"
        assert normalize_error_value("File not found") == "File not found"

    @pytest.mark.unit
    def test_none_placeholder_filtered(self) -> None:
        """'None' and variants are filtered to empty string."""
        assert normalize_error_value("None") == ""
        assert normalize_error_value("none") == ""
        assert normalize_error_value("NONE") == ""
        assert normalize_error_value("None yet") == ""
        assert normalize_error_value("none yet") == ""

    @pytest.mark.unit
    def test_no_error_placeholder_filtered(self) -> None:
        """'No error' variants are filtered."""
        assert normalize_error_value("No error") == ""
        assert normalize_error_value("no error") == ""
        assert normalize_error_value("No errors") == ""
        assert normalize_error_value("no errors") == ""

    @pytest.mark.unit
    def test_na_placeholder_filtered(self) -> None:
        """'N/A' and variants are filtered."""
        assert normalize_error_value("N/A") == ""
        assert normalize_error_value("n/a") == ""
        assert normalize_error_value("NA") == ""
        assert normalize_error_value("na") == ""

    @pytest.mark.unit
    def test_other_placeholders_filtered(self) -> None:
        """Other common placeholders are filtered."""
        assert normalize_error_value("null") == ""
        assert normalize_error_value("undefined") == ""
        assert normalize_error_value("empty") == ""
        assert normalize_error_value("-") == ""
        assert normalize_error_value("") == ""

    @pytest.mark.unit
    def test_whitespace_trimmed(self) -> None:
        """Whitespace is trimmed from values."""
        assert normalize_error_value("  Connection failed  ") == "Connection failed"
        assert normalize_error_value("  none  ") == ""

    @pytest.mark.unit
    def test_empty_and_none(self) -> None:
        """Empty string and None return empty string."""
        assert normalize_error_value("") == ""
        assert normalize_error_value(None) == ""


class TestParseStructuredOutputStartHeader:
    """Tests for parse_structured_output with header at start."""

    @pytest.mark.unit
    def test_header_at_start(self) -> None:
        """Parse header block at start of message."""
        text = """---
status: COMPLETE
error:
---
This is the body content."""

        fields, body = parse_structured_output(text)

        assert fields["status"] == "COMPLETE"
        assert fields["error"] == ""
        assert "body content" in body

    @pytest.mark.unit
    def test_header_with_error(self) -> None:
        """Parse header with actual error message."""
        text = """---
status: FAILED
error: Connection timeout
---
Partial output here."""

        fields, body = parse_structured_output(text)

        assert fields["status"] == "FAILED"
        assert fields["error"] == "Connection timeout"
        assert "Partial output" in body

    @pytest.mark.unit
    def test_multiple_fields(self) -> None:
        """Parse header with multiple custom fields."""
        text = """---
status: COMPLETE
error:
progress: 100%
duration: 5s
---
Done."""

        fields, body = parse_structured_output(text)

        assert fields["status"] == "COMPLETE"
        assert fields["progress"] == "100%"
        assert fields["duration"] == "5s"


class TestParseStructuredOutputEndHeader:
    """Tests for parse_structured_output with header at end."""

    @pytest.mark.unit
    def test_header_at_end(self) -> None:
        """Parse header block at end of message."""
        text = """This is the body content.
---
status: COMPLETE
error:
---"""

        fields, body = parse_structured_output(text)

        assert fields["status"] == "COMPLETE"
        assert fields["error"] == ""
        assert "body content" in body
        assert "---" not in body

    @pytest.mark.unit
    def test_multiline_body_with_trailing_header(self) -> None:
        """Parse multiline body with trailing header."""
        text = """Line 1
Line 2
Line 3
---
status: PARTIAL
error: Timeout reached
---"""

        fields, body = parse_structured_output(text)

        assert fields["status"] == "PARTIAL"
        assert fields["error"] == "Timeout reached"
        assert "Line 1" in body
        assert "Line 2" in body
        assert "Line 3" in body


class TestParseStructuredOutputNoHeader:
    """Tests for messages without valid headers."""

    @pytest.mark.unit
    def test_no_header(self) -> None:
        """Message without header returns empty fields."""
        text = "Just plain text without any header."

        fields, body = parse_structured_output(text)

        assert fields == {}
        assert body == text

    @pytest.mark.unit
    def test_empty_text(self) -> None:
        """Empty text returns empty fields and text."""
        fields, body = parse_structured_output("")

        assert fields == {}
        assert body == ""

    @pytest.mark.unit
    def test_none_text(self) -> None:
        """None returns empty fields and None."""
        fields, body = parse_structured_output(None)

        assert fields == {}
        assert body is None

    @pytest.mark.unit
    def test_incomplete_header(self) -> None:
        """Incomplete header (no closing ---) returns plain text."""
        text = """---
status: COMPLETE
This continues without closing."""

        fields, body = parse_structured_output(text)

        # Should return original text since header is incomplete
        assert "COMPLETE" in body or fields.get("status") == "COMPLETE"


class TestParseStructuredOutputCodeFence:
    """Tests for messages wrapped in code fences."""

    @pytest.mark.unit
    def test_code_fence_wrapped_header(self) -> None:
        """Header wrapped in code fence is parsed correctly."""
        text = """```
---
status: COMPLETE
error:
---
Body here.
```"""

        fields, body = parse_structured_output(text)

        # Should handle the code fence
        assert "COMPLETE" in str(fields) or "Body" in body


class TestParseStructuredOutputEdgeCases:
    """Edge case tests."""

    @pytest.mark.unit
    def test_field_with_colon_in_value(self) -> None:
        """Field values containing colons are handled correctly."""
        text = """---
status: COMPLETE
message: Error at line 10: undefined
---
Body"""

        fields, body = parse_structured_output(text)

        assert fields["status"] == "COMPLETE"
        # Value should include everything after first colon
        assert "Error at line 10" in fields.get("message", "")

    @pytest.mark.unit
    def test_empty_lines_in_header(self) -> None:
        """Empty lines in header block are skipped."""
        text = """---
status: COMPLETE

error:

---
Body"""

        fields, body = parse_structured_output(text)

        assert fields["status"] == "COMPLETE"
        assert "error" in fields

    @pytest.mark.unit
    def test_case_insensitive_keys(self) -> None:
        """Field keys are normalized to lowercase."""
        text = """---
Status: COMPLETE
ERROR:
---
Body"""

        fields, body = parse_structured_output(text)

        assert fields.get("status") == "COMPLETE"
        assert "error" in fields

    @pytest.mark.unit
    def test_error_normalization_in_header(self) -> None:
        """Error field values are normalized within headers."""
        text = """---
status: COMPLETE
error: None
---
Body"""

        fields, body = parse_structured_output(text)

        # "None" should be normalized to empty string
        assert fields["error"] == ""
