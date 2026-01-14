"""
Helpers for parsing structured response headers emitted by agents.
"""
from __future__ import annotations

from typing import Dict, Tuple


def parse_structured_output(text: str) -> Tuple[Dict[str, str], str]:
    """
    Parse a structured header block from the start of a message.

    Expected format:
    ---
    status: COMPLETE|FAILED|PARTIAL
    error: <empty or description>
    ---
    <body>

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
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}, text

    end_index = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_index = i
            break

    if end_index is None:
        return {}, text

    fields: Dict[str, str] = {}
    for line in lines[1:end_index]:
        if not line.strip() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key:
            fields[key] = value

    body_lines = lines[end_index + 1 :]
    if body_lines and body_lines[0].strip().startswith("```"):
        body_lines = body_lines[1:]
    body = "\n".join(body_lines)
    if body.startswith("\n"):
        body = body[1:]

    return fields, body
