"""
Helpers for parsing structured response headers emitted by agents.
"""
from __future__ import annotations

from typing import Dict, Tuple

# Known status field names that indicate a valid structured header
_KNOWN_STATUS_FIELDS = frozenset({
    "status", "request_status",
    "error", "request_error_message",
})

# Mapping from short field names to canonical names expected by the codebase
_FIELD_NAME_ALIASES: Dict[str, str] = {
    "status": "request_status",
    "error": "request_error_message",
}

# Placeholder values that should be treated as empty/no error
_ERROR_PLACEHOLDERS = frozenset({
    "none",
    "none yet",
    "no error",
    "no errors",
    "n/a",
    "na",
    "null",
    "undefined",
    "empty",
    "-",
    "",
})


def normalize_error_value(value: str) -> str:
    """
    Normalize an error field value, returning empty string for placeholder values.

    This filters out common placeholder text like "None", "None yet", "No error", etc.
    that don't represent actual errors.
    """
    if not value:
        return ""
    normalized = value.strip().lower()
    if not normalized:
        return ""
    # Check exact matches against placeholders
    if normalized in _ERROR_PLACEHOLDERS:
        return ""
    # Check if it starts with common "no error" patterns
    if normalized.startswith("none yet") or normalized.startswith("no error"):
        return ""
    return value.strip()


def _parse_header_block(lines: list, start_index: int, end_index: int) -> Dict[str, str]:
    """Extract fields from a header block between start and end indices."""
    fields: Dict[str, str] = {}
    for line in lines[start_index + 1 : end_index]:
        if not line.strip() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key:
            # Normalize error fields to filter out placeholder values
            if key in ("error", "request_error_message"):
                value = normalize_error_value(value)
            # Map short field names to canonical names
            key = _FIELD_NAME_ALIASES.get(key, key)
            fields[key] = value
    return fields


def _find_trailing_header(lines: list) -> Tuple[int, int]:
    """
    Find a trailing header block at the end of lines.

    Returns (start_index, end_index) of the header, or (-1, -1) if not found.
    """
    # Search backwards for the closing ---
    end_index = -1
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == "---":
            end_index = i
            break

    if end_index == -1:
        return -1, -1

    # Search backwards from end_index for the opening ---
    start_index = -1
    for i in range(end_index - 1, -1, -1):
        if lines[i].strip() == "---":
            start_index = i
            break

    if start_index == -1:
        return -1, -1

    # Verify this looks like a valid header block (has key: value pairs)
    has_field = False
    for line in lines[start_index + 1 : end_index]:
        stripped = line.strip()
        if stripped and ":" in stripped:
            has_field = True
            break

    if not has_field:
        return -1, -1

    return start_index, end_index


def _find_unclosed_trailing_header(lines: list) -> int:
    """
    Find an unclosed trailing header block at the end of lines.

    Detects patterns like:
        ---
        status: COMPLETE
        error:
    (without a closing --- delimiter)

    Returns the start_index of the opening ---, or -1 if not found.
    """
    if len(lines) < 2:
        return -1

    # Search backwards for a --- line that starts an unclosed header
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == "---":
            # Check that ALL lines after the --- are key: value pairs (or empty)
            trailing_lines = lines[i + 1 :]
            if not trailing_lines:
                return -1  # --- at the very end with nothing after it

            has_status_field = False
            all_valid = True
            for line in trailing_lines:
                stripped = line.strip()
                if not stripped:
                    continue
                if ":" not in stripped:
                    all_valid = False
                    break
                key = stripped.split(":", 1)[0].strip().lower()
                canonical = _FIELD_NAME_ALIASES.get(key, key)
                if canonical in _KNOWN_STATUS_FIELDS:
                    has_status_field = True

            if all_valid and has_status_field:
                return i
            return -1  # Only check the last --- occurrence

    return -1


def parse_structured_output(text: str) -> Tuple[Dict[str, str], str]:
    """
    Parse a structured header block from a message.

    Expected format (at start OR end of message):
    ---
    request_status: COMPLETE|FAILED|PARTIAL
    request_error_message: <empty or description>
    ---
    <body>

    Or:
    <body>
    ---
    request_status: COMPLETE|FAILED|PARTIAL
    request_error_message: <empty or description>
    ---

    Also handles unclosed trailing headers (missing closing ---),
    common with smaller LLMs.

    Returns a tuple of (fields, body). If no valid header is present,
    fields is empty and body is the original text.
    """
    if not text:
        return {}, text

    payload = text
    if payload.startswith("```"):
        fence_end = payload.find("\n")
        if fence_end == -1:
            return {}, text
        payload = payload[fence_end + 1 :]

    lines = payload.splitlines()

    # Try to find header at the START of the message
    if len(lines) >= 3 and lines[0].strip() == "---":
        end_index = None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                end_index = i
                break

        if end_index is not None:
            fields = _parse_header_block(lines, 0, end_index)
            if fields:  # Only use if we found actual fields
                body_lines = lines[end_index + 1 :]
                if body_lines and body_lines[0].strip().startswith("```"):
                    body_lines = body_lines[1:]
                body = "\n".join(body_lines)
                if body.startswith("\n"):
                    body = body[1:]
                return fields, body

    # Try to find closed header at the END of the message
    start_index, end_index = _find_trailing_header(lines)
    if start_index != -1 and end_index != -1:
        fields = _parse_header_block(lines, start_index, end_index)
        if fields:  # Only use if we found actual fields
            # Body is everything before the trailing header
            body_lines = lines[:start_index]
            # Remove trailing empty lines from body
            while body_lines and not body_lines[-1].strip():
                body_lines.pop()
            body = "\n".join(body_lines)
            return fields, body

    # Try to find unclosed header at the END of the message (no closing ---)
    unclosed_start = _find_unclosed_trailing_header(lines)
    if unclosed_start != -1:
        # Parse fields from unclosed_start+1 to end of lines
        fields: Dict[str, str] = {}
        for line in lines[unclosed_start + 1 :]:
            stripped = line.strip()
            if not stripped or ":" not in stripped:
                continue
            key, value = stripped.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            if key:
                if key in ("error", "request_error_message"):
                    value = normalize_error_value(value)
                key = _FIELD_NAME_ALIASES.get(key, key)
                fields[key] = value
        if fields:
            body_lines = lines[:unclosed_start]
            while body_lines and not body_lines[-1].strip():
                body_lines.pop()
            body = "\n".join(body_lines)
            return fields, body

    return {}, text
