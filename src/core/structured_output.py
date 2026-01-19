"""
Helpers for parsing structured response headers emitted by agents.
"""
from __future__ import annotations

from typing import Dict, Tuple

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
            # Normalize error field to filter out placeholder values
            if key == "error":
                value = normalize_error_value(value)
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


def parse_structured_output(text: str) -> Tuple[Dict[str, str], str]:
    """
    Parse a structured header block from a message.

    Expected format (at start OR end of message):
    ---
    status: COMPLETE|FAILED|PARTIAL
    error: <empty or description>
    ---
    <body>

    Or:
    <body>
    ---
    status: COMPLETE|FAILED|PARTIAL
    error: <empty or description>
    ---

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

    # Try to find header at the END of the message
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

    return {}, text
