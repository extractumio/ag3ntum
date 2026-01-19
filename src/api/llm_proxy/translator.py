"""Translation helpers for Claude-compatible requests."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from .config import ModelMapping, ProviderConfig


@dataclass
class ProxyTarget:
    provider: ProviderConfig
    model: ModelMapping
    api_key: str


def map_claude_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    openai_tools: list[dict[str, Any]] = []
    for tool in tools:
        function = {
            "name": tool.get("name"),
            "description": tool.get("description"),
            "parameters": tool.get("input_schema", {}),
        }
        openai_tools.append({"type": "function", "function": function})
    return openai_tools


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    text_parts = [block.get("text", "") for block in content if block.get("type") == "text"]
    return "\n".join(part for part in text_parts if part)


def _tool_use_from_block(block: dict[str, Any]) -> dict[str, Any] | None:
    if block.get("type") != "tool_use":
        return None
    tool_id = block.get("id")
    name = block.get("name")
    tool_input = block.get("input", {})
    return {
        "id": tool_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(tool_input)},
    }


def _tool_result_message(block: dict[str, Any]) -> dict[str, Any] | None:
    if block.get("type") != "tool_result":
        return None
    return {
        "role": "tool",
        "tool_call_id": block.get("tool_use_id"),
        "content": block.get("content", ""),
    }


def claude_to_openai_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    system = payload.get("system")
    if system:
        messages.append({"role": "system", "content": system})

    for message in payload.get("messages", []):
        role = message.get("role")
        content = message.get("content")
        if role in {"user", "assistant"}:
            tool_calls: list[dict[str, Any]] = []
            if isinstance(content, list):
                for block in content:
                    tool_call = _tool_use_from_block(block)
                    if tool_call:
                        tool_calls.append(tool_call)
            msg: dict[str, Any] = {"role": role, "content": _extract_text(content)}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            messages.append(msg)
        if isinstance(content, list):
            for block in content:
                tool_result = _tool_result_message(block)
                if tool_result:
                    messages.append(tool_result)

    return messages


def openai_to_claude_response(
    payload: dict[str, Any],
    model_name: str,
) -> dict[str, Any]:
    choice = payload.get("choices", [{}])[0]
    message = choice.get("message", {})
    content_blocks: list[dict[str, Any]] = []

    content_text = message.get("content")
    if content_text:
        content_blocks.append({"type": "text", "text": content_text})

    tool_calls = message.get("tool_calls", [])
    for tool_call in tool_calls:
        function = tool_call.get("function", {})
        args = function.get("arguments")
        tool_input = json.loads(args) if args else {}
        content_blocks.append(
            {
                "type": "tool_use",
                "id": tool_call.get("id"),
                "name": function.get("name"),
                "input": tool_input,
            }
        )

    return {
        "id": payload.get("id", "proxy-response"),
        "type": "message",
        "role": "assistant",
        "model": model_name,
        "content": content_blocks,
        "stop_reason": choice.get("finish_reason"),
    }


async def stream_openai_to_claude(
    response: httpx.Response,
    model_name: str,
) -> AsyncIterator[str]:
    message_id = "proxy-stream"
    message_start = {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": model_name,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
        },
    }
    yield f"data: {json.dumps(message_start)}\n\n"

    content_block_start = {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    }
    yield f"data: {json.dumps(content_block_start)}\n\n"

    tool_calls: dict[int, dict[str, Any]] = {}

    async for line in response.aiter_lines():
        if not line or not line.startswith("data: "):
            continue
        data = line.removeprefix("data: ").strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = chunk.get("choices", [])
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        content = delta.get("content")
        if content:
            delta_event = {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": content},
            }
            yield f"data: {json.dumps(delta_event)}\n\n"
        for tool_call in delta.get("tool_calls", []) or []:
            index = tool_call.get("index", 0)
            existing = tool_calls.setdefault(index, {"id": None, "name": None, "arguments": ""})
            existing["id"] = tool_call.get("id") or existing["id"]
            function = tool_call.get("function", {})
            existing["name"] = function.get("name") or existing["name"]
            existing["arguments"] += function.get("arguments", "")

    if tool_calls:
        for idx, tool_call in tool_calls.items():
            tool_event = {
                "type": "content_block_start",
                "index": idx + 1,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_call.get("id"),
                    "name": tool_call.get("name"),
                    "input": json.loads(tool_call.get("arguments") or "{}"),
                },
            }
            yield f"data: {json.dumps(tool_event)}\n\n"
            yield f"data: {json.dumps({'type': 'content_block_stop', 'index': idx + 1})}\n\n"

    yield f"data: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
    yield f"data: {json.dumps({'type': 'message_stop'})}\n\n"
