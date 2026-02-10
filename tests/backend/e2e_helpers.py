"""
Shared helpers for E2E agent tests.

Provides environment checks, agent task execution, and response parsing
used by test_mount_e2e.py and test_e2e_agent_tasks.py.
"""
import json
import os
import socket
from pathlib import Path

import httpx

API_BASE_URL = "http://127.0.0.1:40080"
API_V1_URL = f"{API_BASE_URL}/api/v1"


def is_docker_environment() -> bool:
    """Check if we're running inside Docker."""
    return Path("/.dockerenv").exists() or os.environ.get("AG3NTUM_IN_DOCKER") == "1"


def api_accessible() -> bool:
    """Check if API is accessible."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('127.0.0.1', 40080))
        sock.close()
        return result == 0
    except Exception:
        return False


async def run_agent_task(
    token: str,
    task: str,
    timeout: int = 120,
) -> dict:
    """
    Run an agent task and wait for completion.

    Returns dict with session_id, status, events, tool_calls, final_message, error.
    """
    result = {
        "session_id": None,
        "status": "unknown",
        "events": [],
        "tool_calls": [],
        "final_message": "",
        "error": None,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{API_V1_URL}/sessions/run",
            headers={"Authorization": f"Bearer {token}"},
            json={"task": task},
        )

        if response.status_code not in (200, 201):
            result["error"] = f"Failed to start task: {response.status_code} - {response.text}"
            result["status"] = "failed"
            return result

        data = response.json()
        result["session_id"] = data.get("session_id")
        print(f"    Session: {result['session_id']}")

    # Stream events
    current_tool = None

    async with httpx.AsyncClient(timeout=float(timeout)) as client:
        try:
            async with client.stream(
                "GET",
                f"{API_V1_URL}/sessions/{result['session_id']}/events",
                params={"token": token},
            ) as response:
                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue

                    if line.startswith("data: "):
                        try:
                            event = json.loads(line[6:])
                            result["events"].append(event)

                            event_type = event.get("type")
                            event_data = event.get("data", {})

                            if event_type == "tool_start":
                                current_tool = {
                                    "name": event_data.get("tool_name", "unknown"),
                                    "input": event_data.get("tool_input", {}),
                                }

                            elif event_type == "tool_complete":
                                if current_tool:
                                    current_tool["result"] = event_data.get("result", "")
                                    current_tool["is_error"] = event_data.get("is_error", False)
                                    result["tool_calls"].append(current_tool)
                                    current_tool = None

                            elif event_type == "message":
                                if not event_data.get("is_partial"):
                                    text = event_data.get("full_text") or event_data.get("text", "")
                                    if text:
                                        result["final_message"] = text

                            elif event_type == "error":
                                result["error"] = event_data.get("message", str(event_data))
                                result["status"] = "error"

                            elif event_type in ("agent_complete", "cancelled"):
                                result["status"] = event_type
                                break

                        except json.JSONDecodeError:
                            pass

        except httpx.ReadTimeout:
            result["status"] = "timeout"
            result["error"] = f"Timeout after {timeout}s"

    tools_used = [t["name"] for t in result["tool_calls"]]
    print(f"    Status: {result['status']}, Tools: {tools_used}")
    return result


def _extract_text(value) -> str:
    """Extract text from a value that may be a string, list of content blocks, or other."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        texts = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif isinstance(item, str):
                texts.append(item)
        return "\n".join(texts) if texts else str(value)
    return str(value) if value else ""


def get_response_text(result: dict) -> str:
    """Extract all readable text from agent result.

    Combines final message text and tool call results so callers can
    search for content regardless of where the agent placed it.
    """
    parts = []

    msg = result.get("final_message")
    if msg:
        parts.append(_extract_text(msg))

    for tool in result.get("tool_calls", []):
        if tool.get("result"):
            parts.append(_extract_text(tool["result"]))

    return "\n".join(parts)


def find_tool_result(result: dict, tool_name: str) -> str | None:
    """Find result from a specific tool call."""
    for tool in result.get("tool_calls", []):
        if tool_name in tool.get("name", ""):
            return tool.get("result", "")
    return None
