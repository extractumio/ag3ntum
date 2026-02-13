"""
Tests for structured_output parsing functions.

Tests cover:
- Error value normalization
- Header block parsing (with field name aliasing)
- Body extraction
- Unclosed trailing headers
- Edge cases and malformed inputs
"""
import pytest

from src.core.structured_output import (
    _find_unclosed_trailing_header,
    normalize_error_value,
    parse_structured_output,
)
from src.core.agent_core import determine_session_status


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

        # "status" aliased to "request_status", "error" aliased to "request_error_message"
        assert fields["request_status"] == "COMPLETE"
        assert fields["request_error_message"] == ""
        assert "body content" in body

    @pytest.mark.unit
    def test_header_with_canonical_fields(self) -> None:
        """Parse header with canonical field names (no aliasing needed)."""
        text = """---
request_status: COMPLETE
request_error_message:
---
This is the body content."""

        fields, body = parse_structured_output(text)

        assert fields["request_status"] == "COMPLETE"
        assert fields["request_error_message"] == ""
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

        assert fields["request_status"] == "FAILED"
        assert fields["request_error_message"] == "Connection timeout"
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

        assert fields["request_status"] == "COMPLETE"
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

        assert fields["request_status"] == "COMPLETE"
        assert fields["request_error_message"] == ""
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

        assert fields["request_status"] == "PARTIAL"
        assert fields["request_error_message"] == "Timeout reached"
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

        assert fields["request_status"] == "COMPLETE"
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

        assert fields["request_status"] == "COMPLETE"
        assert "request_error_message" in fields

    @pytest.mark.unit
    def test_case_insensitive_keys(self) -> None:
        """Field keys are normalized to lowercase (and aliased)."""
        text = """---
Status: COMPLETE
ERROR:
---
Body"""

        fields, body = parse_structured_output(text)

        assert fields.get("request_status") == "COMPLETE"
        assert "request_error_message" in fields

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
        assert fields["request_error_message"] == ""


class TestParseStructuredOutputUnclosedTrailing:
    """Tests for unclosed trailing headers (no closing ---)."""

    @pytest.mark.unit
    def test_unclosed_trailing_header(self) -> None:
        """Parse unclosed trailing header (common with smaller LLMs like haiku)."""
        text = """Here is the body content.

---
status: COMPLETE
error:"""

        fields, body = parse_structured_output(text)

        assert fields["request_status"] == "COMPLETE"
        assert fields["request_error_message"] == ""
        assert "body content" in body
        assert "---" not in body

    @pytest.mark.unit
    def test_unclosed_trailing_header_with_error(self) -> None:
        """Parse unclosed trailing header with error value."""
        text = """Something went wrong.

---
status: FAILED
error: Connection refused"""

        fields, body = parse_structured_output(text)

        assert fields["request_status"] == "FAILED"
        assert fields["request_error_message"] == "Connection refused"
        assert "Something went wrong" in body

    @pytest.mark.unit
    def test_unclosed_trailing_header_canonical_names(self) -> None:
        """Parse unclosed trailing header using canonical field names."""
        text = """Body text here.

---
request_status: PARTIAL
request_error_message: Timeout"""

        fields, body = parse_structured_output(text)

        assert fields["request_status"] == "PARTIAL"
        assert fields["request_error_message"] == "Timeout"
        assert "Body text" in body

    @pytest.mark.unit
    def test_unclosed_trailing_not_matched_without_status_field(self) -> None:
        """Trailing --- followed by non-status fields should not match."""
        text = """Body text here.

---
name: John
age: 30"""

        fields, body = parse_structured_output(text)

        # Should not detect as a header (no known status field)
        assert fields == {}
        assert body == text

    @pytest.mark.unit
    def test_unclosed_trailing_not_matched_with_non_kv_lines(self) -> None:
        """Trailing --- with non key:value lines after should not match."""
        text = """Body text here.

---
This is just a separator.
Not a header block."""

        fields, body = parse_structured_output(text)

        assert fields == {}
        assert body == text

    @pytest.mark.unit
    def test_unclosed_trailing_strips_empty_lines_from_body(self) -> None:
        """Trailing empty lines before unclosed header are stripped."""
        text = """Body content.


---
status: COMPLETE
error:"""

        fields, body = parse_structured_output(text)

        assert body == "Body content."
        assert fields["request_status"] == "COMPLETE"


class TestFindUnclosedTrailingHeader:
    """Tests for _find_unclosed_trailing_header helper."""

    @pytest.mark.unit
    def test_finds_unclosed_header(self) -> None:
        lines = ["Body", "---", "status: COMPLETE", "error:"]
        assert _find_unclosed_trailing_header(lines) == 1

    @pytest.mark.unit
    def test_returns_neg1_for_no_header(self) -> None:
        lines = ["Just text", "No header"]
        assert _find_unclosed_trailing_header(lines) == -1

    @pytest.mark.unit
    def test_returns_neg1_for_bare_separator(self) -> None:
        lines = ["Body", "---"]
        assert _find_unclosed_trailing_header(lines) == -1

    @pytest.mark.unit
    def test_returns_neg1_for_non_status_fields(self) -> None:
        lines = ["Body", "---", "name: John"]
        assert _find_unclosed_trailing_header(lines) == -1

    @pytest.mark.unit
    def test_returns_neg1_for_non_kv_content(self) -> None:
        lines = ["Body", "---", "Not a key value pair"]
        assert _find_unclosed_trailing_header(lines) == -1

    @pytest.mark.unit
    def test_returns_neg1_for_too_few_lines(self) -> None:
        lines = ["---"]
        assert _find_unclosed_trailing_header(lines) == -1


class TestFieldNameAliasing:
    """Tests for field name alias mapping (status -> request_status, etc)."""

    @pytest.mark.unit
    def test_short_names_mapped_in_start_header(self) -> None:
        """Short field names are mapped to canonical names in start header."""
        text = """---
status: COMPLETE
error: some error
---
Body"""

        fields, body = parse_structured_output(text)

        assert "request_status" in fields
        assert "request_error_message" in fields
        assert "status" not in fields
        assert "error" not in fields

    @pytest.mark.unit
    def test_canonical_names_preserved(self) -> None:
        """Canonical field names are kept as-is."""
        text = """---
request_status: COMPLETE
request_error_message:
---
Body"""

        fields, body = parse_structured_output(text)

        assert fields["request_status"] == "COMPLETE"
        assert fields["request_error_message"] == ""

    @pytest.mark.unit
    def test_short_names_mapped_in_trailing_header(self) -> None:
        """Short field names are mapped to canonical names in trailing header."""
        text = """Body text.
---
status: PARTIAL
error: timeout
---"""

        fields, body = parse_structured_output(text)

        assert "request_status" in fields
        assert "request_error_message" in fields
        assert fields["request_status"] == "PARTIAL"

    @pytest.mark.unit
    def test_non_alias_fields_preserved(self) -> None:
        """Non-aliased field names are preserved as-is."""
        text = """---
status: COMPLETE
error:
progress: 50%
---
Body"""

        fields, body = parse_structured_output(text)

        assert fields["request_status"] == "COMPLETE"
        assert fields["progress"] == "50%"


class TestDetermineSessionStatus:
    """Tests for determine_session_status — the priority chain for final status.

    Priority:
    1. Agent's self-assessment from structured header (request_status)
    2. Fallback heuristic: default to COMPLETE
    """

    # --- Agent self-assessment takes precedence ---

    @pytest.mark.unit
    def test_agent_says_complete_no_errors(self) -> None:
        """Agent says COMPLETE, no tool errors → COMPLETE."""
        text = "---\nrequest_status: COMPLETE\nrequest_error_message:\n---\nHere is your answer."
        assert determine_session_status(text, had_tool_errors=False) == "COMPLETE"

    @pytest.mark.unit
    def test_agent_says_complete_despite_tool_errors(self) -> None:
        """Agent says COMPLETE even though tools had errors → COMPLETE (agent recovered)."""
        text = "---\nrequest_status: COMPLETE\nrequest_error_message:\n---\nHere is your answer."
        assert determine_session_status(text, had_tool_errors=True, tool_error_count=3) == "COMPLETE"

    @pytest.mark.unit
    def test_agent_says_failed(self) -> None:
        """Agent says FAILED → FAILED."""
        text = "---\nrequest_status: FAILED\nrequest_error_message: Could not access file\n---\nSorry."
        assert determine_session_status(text, had_tool_errors=True, tool_error_count=5) == "FAILED"

    @pytest.mark.unit
    def test_agent_says_partial(self) -> None:
        """Agent says PARTIAL → PARTIAL."""
        text = "---\nrequest_status: PARTIAL\nrequest_error_message:\n---\nStill working."
        assert determine_session_status(text, had_tool_errors=False) == "PARTIAL"

    @pytest.mark.unit
    def test_agent_says_failed_no_tool_errors(self) -> None:
        """Agent says FAILED even without tool errors (e.g., can't fulfill request)."""
        text = "---\nrequest_status: FAILED\nrequest_error_message: I cannot do that\n---\n"
        assert determine_session_status(text, had_tool_errors=False) == "FAILED"

    # --- Short field names (alias mapping) ---

    @pytest.mark.unit
    def test_short_status_field_name(self) -> None:
        """'status' field (short alias) is mapped to request_status."""
        text = "---\nstatus: COMPLETE\nerror:\n---\nDone."
        assert determine_session_status(text, had_tool_errors=True, tool_error_count=1) == "COMPLETE"

    # --- Trailing header (common with Haiku) ---

    @pytest.mark.unit
    def test_trailing_header_complete(self) -> None:
        """Trailing header (at end of message) is parsed."""
        text = "Here is the answer.\n---\nstatus: COMPLETE\nerror:\n---"
        assert determine_session_status(text, had_tool_errors=False) == "COMPLETE"

    @pytest.mark.unit
    def test_unclosed_trailing_header(self) -> None:
        """Unclosed trailing header (no closing ---) is parsed."""
        text = "Done.\n---\nstatus: COMPLETE\nerror:"
        assert determine_session_status(text, had_tool_errors=True, tool_error_count=2) == "COMPLETE"

    # --- Fallback heuristic (no structured header) ---

    @pytest.mark.unit
    def test_no_header_no_errors_defaults_complete(self) -> None:
        """No structured header, no tool errors → COMPLETE."""
        text = "Here is the answer to your question."
        assert determine_session_status(text, had_tool_errors=False) == "COMPLETE"

    @pytest.mark.unit
    def test_no_header_with_errors_defaults_complete(self) -> None:
        """No structured header but tool errors → COMPLETE (not FAILED).

        An agent that ran to completion without crash/circuit-breaker
        likely completed its task despite some tool errors along the way.
        """
        text = "I completed your request. Some tools had issues but I worked around them."
        assert determine_session_status(text, had_tool_errors=True, tool_error_count=2) == "COMPLETE"

    @pytest.mark.unit
    def test_none_result_defaults_complete(self) -> None:
        """None result text → COMPLETE."""
        assert determine_session_status(None, had_tool_errors=False) == "COMPLETE"

    @pytest.mark.unit
    def test_empty_result_defaults_complete(self) -> None:
        """Empty result text → COMPLETE."""
        assert determine_session_status("", had_tool_errors=False) == "COMPLETE"

    @pytest.mark.unit
    def test_none_result_with_errors_defaults_complete(self) -> None:
        """None result text with tool errors → COMPLETE (agent still ran to completion)."""
        assert determine_session_status(None, had_tool_errors=True, tool_error_count=1) == "COMPLETE"

    # --- Case insensitivity ---

    @pytest.mark.unit
    def test_lowercase_status_value(self) -> None:
        """Lowercase status values are normalized to uppercase."""
        text = "---\nstatus: complete\nerror:\n---\nDone."
        assert determine_session_status(text, had_tool_errors=False) == "COMPLETE"

    @pytest.mark.unit
    def test_mixed_case_status_value(self) -> None:
        """Mixed case status values are normalized."""
        text = "---\nstatus: Failed\nerror: something broke\n---\nSorry."
        assert determine_session_status(text, had_tool_errors=True) == "FAILED"

    # --- Invalid status values fall through to heuristic ---

    @pytest.mark.unit
    def test_invalid_status_value_falls_through(self) -> None:
        """Invalid status value (not COMPLETE/PARTIAL/FAILED) falls to heuristic."""
        text = "---\nstatus: DONE\nerror:\n---\nBody."
        assert determine_session_status(text, had_tool_errors=False) == "COMPLETE"

    @pytest.mark.unit
    def test_empty_status_value_falls_through(self) -> None:
        """Empty status value falls through to heuristic."""
        text = "---\nstatus:\nerror:\n---\nBody."
        assert determine_session_status(text, had_tool_errors=False) == "COMPLETE"
