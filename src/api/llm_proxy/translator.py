"""Translation helpers for Claude-compatible requests."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from .config import ModelMapping, ProviderConfig

logger = logging.getLogger(__name__)


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


def _map_openai_usage(openai_usage: dict[str, Any]) -> dict[str, int]:
    """Map OpenAI usage format to Claude/Anthropic usage format."""
    usage: dict[str, int] = {
        "input_tokens": openai_usage.get("prompt_tokens", 0),
        "output_tokens": openai_usage.get("completion_tokens", 0),
    }
    # Extract prompt caching stats (OpenAI prompt_tokens_details.cached_tokens)
    prompt_details = openai_usage.get("prompt_tokens_details") or {}
    cached = prompt_details.get("cached_tokens", 0)
    if cached > 0:
        usage["cache_read_input_tokens"] = cached
    return usage


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

    # Map OpenAI stop reasons to Claude format
    finish_reason = choice.get("finish_reason")
    stop_reason_map = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "end_turn",
    }
    stop_reason = stop_reason_map.get(finish_reason, "end_turn")

    usage = _map_openai_usage(payload.get("usage", {}))

    return {
        "id": payload.get("id", "proxy-response"),
        "type": "message",
        "role": "assistant",
        "model": model_name,
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage,
    }


async def stream_openai_to_claude(
    response: httpx.Response,
    model_name: str,
) -> AsyncIterator[str]:
    message_id = "proxy-stream"
    # OpenAI includes full usage in the final streaming chunk
    # (when stream_options.include_usage is set)
    last_usage: dict[str, Any] = {}

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
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    }
    yield f"event: message_start\ndata: {json.dumps(message_start)}\n\n"

    content_block_start = {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "text", "text": ""},
    }
    yield f"event: content_block_start\ndata: {json.dumps(content_block_start)}\n\n"

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

        # Capture usage from final chunk (OpenAI sends it with stream_options.include_usage)
        chunk_usage = chunk.get("usage")
        if chunk_usage:
            last_usage = chunk_usage

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
            yield f"event: content_block_delta\ndata: {json.dumps(delta_event)}\n\n"

        # Process tool calls from streaming delta
        delta_tool_calls = delta.get("tool_calls", []) or []
        if delta_tool_calls:
            logger.debug("Received tool_calls delta: %s", delta_tool_calls)
        for tool_call in delta_tool_calls:
            index = tool_call.get("index", 0)
            existing = tool_calls.setdefault(index, {"id": None, "name": None, "arguments": ""})
            existing["id"] = tool_call.get("id") or existing["id"]
            function = tool_call.get("function", {})
            existing["name"] = function.get("name") or existing["name"]
            args_chunk = function.get("arguments", "")
            if args_chunk:
                logger.debug("Tool %s: appending arguments chunk (%d chars): %s...",
                            existing["name"], len(args_chunk), args_chunk[:100])
            existing["arguments"] += args_chunk

    # Close the text block (index 0) before emitting tool blocks
    # This ensures proper event ordering: text block must close before tool blocks start
    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"

    if tool_calls:
        logger.info("Processing %d accumulated tool calls from OpenAI stream", len(tool_calls))
        for idx, tool_call in tool_calls.items():
            tool_name = tool_call.get("name")
            tool_args_str = tool_call.get("arguments") or "{}"
            logger.info(
                "Tool call [%d]: name=%s, accumulated_args_length=%d, raw_args=%s",
                idx, tool_name, len(tool_args_str),
                tool_args_str[:500] if len(tool_args_str) <= 500 else tool_args_str[:500] + "..."
            )
            try:
                tool_input = json.loads(tool_args_str)
            except json.JSONDecodeError:
                logger.warning(
                    "Invalid JSON in tool arguments for %s: %s",
                    tool_name,
                    tool_args_str[:100],
                )
                tool_input = {}

            # Log tool call details for debugging
            logger.info(
                "Tool call parsed: name=%s, parsed_keys=%s, input_preview=%s",
                tool_name,
                list(tool_input.keys()) if tool_input else "empty",
                str(tool_input)[:200] if tool_input else "{}"
            )
            if not tool_input:
                logger.warning(
                    "Empty tool arguments for %s - model may not understand schema",
                    tool_name,
                )

            # Anthropic streaming format: content_block_start has empty input,
            # then input is streamed via input_json_delta in content_block_delta events
            tool_event = {
                "type": "content_block_start",
                "index": idx + 1,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_call.get("id"),
                    "name": tool_name,
                    "input": {},  # Empty - input comes via delta events
                },
            }
            yield f"event: content_block_start\ndata: {json.dumps(tool_event)}\n\n"

            # Stream the tool input as input_json_delta
            # We send the complete JSON as a single delta (could chunk for larger inputs)
            if tool_input:
                input_json = json.dumps(tool_input)
                input_delta = {
                    "type": "content_block_delta",
                    "index": idx + 1,
                    "delta": {"type": "input_json_delta", "partial_json": input_json},
                }
                yield f"event: content_block_delta\ndata: {json.dumps(input_delta)}\n\n"

            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': idx + 1})}\n\n"

    stop_reason = "tool_use" if tool_calls else "end_turn"
    final_usage = _map_openai_usage(last_usage)

    message_delta = {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": final_usage,
    }
    yield f"event: message_delta\ndata: {json.dumps(message_delta)}\n\n"

    yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
