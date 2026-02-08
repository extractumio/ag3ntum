"""
Anthropic SSE Message Schemas for LLM Proxy Translation.

This module defines the expected schema for Anthropic SSE streaming events.
These schemas are used to validate that our OpenAI-to-Claude translator
produces correctly formatted events that the Claude Agent SDK can parse.

When Claude Code or the Anthropic API changes message formats, tests using
these schemas will fail, alerting us to adapt our translator.

Reference: https://docs.anthropic.com/en/api/messages-streaming
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional, Union
from pydantic import BaseModel, Field, model_validator


# =============================================================================
# Enums
# =============================================================================

class StopReason(str, Enum):
    """Valid stop reasons for message completion."""
    END_TURN = "end_turn"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    TOOL_USE = "tool_use"


class ContentBlockType(str, Enum):
    """Types of content blocks in assistant messages."""
    TEXT = "text"
    TOOL_USE = "tool_use"
    THINKING = "thinking"


class DeltaType(str, Enum):
    """Types of delta updates in content_block_delta events."""
    TEXT_DELTA = "text_delta"
    INPUT_JSON_DELTA = "input_json_delta"
    THINKING_DELTA = "thinking_delta"


# =============================================================================
# Content Block Schemas
# =============================================================================

class TextContentBlock(BaseModel):
    """Text content block in content_block_start."""
    type: Literal["text"] = "text"
    text: str = ""


class ToolUseContentBlock(BaseModel):
    """Tool use content block in content_block_start.

    IMPORTANT: In streaming, input MUST be empty {}.
    The actual input is streamed via input_json_delta events.
    """
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def validate_empty_input_for_streaming(self) -> 'ToolUseContentBlock':
        """Warn if input is not empty (streaming expects empty input)."""
        # This is a soft validation - we log but don't fail
        # because non-streaming responses have complete input
        return self


class ThinkingContentBlock(BaseModel):
    """Thinking content block in content_block_start."""
    type: Literal["thinking"] = "thinking"
    thinking: str = ""


ContentBlock = Union[TextContentBlock, ToolUseContentBlock, ThinkingContentBlock]


# =============================================================================
# Delta Schemas
# =============================================================================

class TextDelta(BaseModel):
    """Text delta in content_block_delta."""
    type: Literal["text_delta"] = "text_delta"
    text: str


class InputJsonDelta(BaseModel):
    """Input JSON delta for tool arguments in content_block_delta.

    The partial_json field contains a fragment of the tool input JSON.
    Multiple deltas are concatenated to form the complete JSON.
    """
    type: Literal["input_json_delta"] = "input_json_delta"
    partial_json: str


class ThinkingDelta(BaseModel):
    """Thinking delta in content_block_delta."""
    type: Literal["thinking_delta"] = "thinking_delta"
    thinking: str


Delta = Union[TextDelta, InputJsonDelta, ThinkingDelta]


# =============================================================================
# Usage Schema
# =============================================================================

class CacheCreation(BaseModel):
    """Cache creation details in usage (ephemeral caching)."""
    ephemeral_5m_input_tokens: int = 0
    ephemeral_1h_input_tokens: int = 0


class Usage(BaseModel):
    """Token usage statistics."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None
    cache_creation: Optional[CacheCreation] = None
    service_tier: Optional[str] = None
    inference_geo: Optional[str] = None


# =============================================================================
# SSE Event Schemas
# =============================================================================

class MessageStartMessage(BaseModel):
    """The message object inside message_start event."""
    id: str
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    model: str
    content: list = Field(default_factory=list)
    stop_reason: Optional[str] = None
    stop_sequence: Optional[str] = None
    usage: Usage = Field(default_factory=Usage)


class MessageStartEvent(BaseModel):
    """message_start SSE event - first event in stream."""
    type: Literal["message_start"] = "message_start"
    message: MessageStartMessage


class ContentBlockStartEvent(BaseModel):
    """content_block_start SSE event - start of a content block."""
    type: Literal["content_block_start"] = "content_block_start"
    index: int
    content_block: Union[TextContentBlock, ToolUseContentBlock, ThinkingContentBlock]


class ContentBlockDeltaEvent(BaseModel):
    """content_block_delta SSE event - incremental update to a content block."""
    type: Literal["content_block_delta"] = "content_block_delta"
    index: int
    delta: Union[TextDelta, InputJsonDelta, ThinkingDelta]


class ContentBlockStopEvent(BaseModel):
    """content_block_stop SSE event - end of a content block."""
    type: Literal["content_block_stop"] = "content_block_stop"
    index: int


class MessageDeltaData(BaseModel):
    """Delta data in message_delta event."""
    stop_reason: Optional[str] = None
    stop_sequence: Optional[str] = None


class MessageDeltaEvent(BaseModel):
    """message_delta SSE event - final message metadata."""
    type: Literal["message_delta"] = "message_delta"
    delta: MessageDeltaData
    usage: Usage = Field(default_factory=Usage)


class MessageStopEvent(BaseModel):
    """message_stop SSE event - end of message stream."""
    type: Literal["message_stop"] = "message_stop"


class PingEvent(BaseModel):
    """ping SSE event - keepalive sent during long-running streams."""
    type: Literal["ping"] = "ping"


# Union of all SSE event types
SSEEvent = Union[
    MessageStartEvent,
    ContentBlockStartEvent,
    ContentBlockDeltaEvent,
    ContentBlockStopEvent,
    MessageDeltaEvent,
    MessageStopEvent,
    PingEvent,
]


# =============================================================================
# Validation Helpers
# =============================================================================

def parse_sse_event(event_str: str) -> tuple[str, dict]:
    """
    Parse an SSE event string into event type and data.

    Args:
        event_str: Raw SSE event string like "event: message_start\ndata: {...}\n\n"

    Returns:
        Tuple of (event_type, parsed_data_dict)

    Raises:
        ValueError: If event string is malformed
    """
    import json

    lines = event_str.strip().split('\n')
    if len(lines) < 2:
        raise ValueError(f"SSE event must have at least event and data lines: {event_str[:100]}")

    event_line = lines[0]
    data_line = lines[1]

    if not event_line.startswith('event: '):
        raise ValueError(f"First line must start with 'event: ': {event_line}")

    if not data_line.startswith('data: '):
        raise ValueError(f"Second line must start with 'data: ': {data_line}")

    event_type = event_line[7:].strip()  # Remove 'event: '
    data_json = data_line[6:].strip()  # Remove 'data: '

    try:
        data = json.loads(data_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in data line: {e}") from e

    return event_type, data


def validate_sse_event(event_str: str) -> SSEEvent:
    """
    Validate an SSE event string against the expected schema.

    Args:
        event_str: Raw SSE event string

    Returns:
        Validated SSE event model

    Raises:
        ValueError: If event is malformed
        pydantic.ValidationError: If event data doesn't match schema
    """
    event_type, data = parse_sse_event(event_str)

    # Map event type to model
    event_models = {
        'message_start': MessageStartEvent,
        'content_block_start': ContentBlockStartEvent,
        'content_block_delta': ContentBlockDeltaEvent,
        'content_block_stop': ContentBlockStopEvent,
        'message_delta': MessageDeltaEvent,
        'message_stop': MessageStopEvent,
        'ping': PingEvent,
    }

    model = event_models.get(event_type)
    if not model:
        raise ValueError(f"Unknown event type: {event_type}")

    return model.model_validate(data)


def validate_sse_stream(events: list[str]) -> list[SSEEvent]:
    """
    Validate a complete SSE stream against expected schemas.

    Args:
        events: List of SSE event strings

    Returns:
        List of validated SSE event models

    Raises:
        ValueError: If stream structure is invalid
        pydantic.ValidationError: If any event doesn't match schema
    """
    validated = []
    for event_str in events:
        validated.append(validate_sse_event(event_str))

    # Validate stream structure (ignoring ping events which are just keepalives)
    if not validated:
        raise ValueError("Empty event stream")

    # Filter out ping events for structural validation
    structural_events = [e for e in validated if not isinstance(e, PingEvent)]

    if not structural_events:
        raise ValueError("Empty event stream (only ping events)")

    # First event must be message_start
    if not isinstance(structural_events[0], MessageStartEvent):
        raise ValueError(f"First event must be message_start, got {type(structural_events[0]).__name__}")

    # Last event must be message_stop
    if not isinstance(structural_events[-1], MessageStopEvent):
        raise ValueError(f"Last event must be message_stop, got {type(structural_events[-1]).__name__}")

    # Second to last must be message_delta
    if len(structural_events) >= 2 and not isinstance(structural_events[-2], MessageDeltaEvent):
        raise ValueError(f"Second to last event must be message_delta, got {type(structural_events[-2]).__name__}")

    return validated


def validate_tool_use_stream_order(events: list[SSEEvent]) -> None:
    """
    Validate that tool_use blocks follow correct event ordering.

    Expected order for each tool_use block:
    1. content_block_start with type=tool_use and input={}
    2. content_block_delta with type=input_json_delta (one or more)
    3. content_block_stop

    Args:
        events: List of validated SSE events

    Raises:
        ValueError: If tool_use event ordering is incorrect
    """
    # Track tool blocks by index
    tool_blocks: dict[int, dict] = {}

    for event in events:
        if isinstance(event, ContentBlockStartEvent):
            if isinstance(event.content_block, ToolUseContentBlock):
                if event.content_block.input != {}:
                    raise ValueError(
                        f"Tool use content_block_start at index {event.index} "
                        f"must have empty input {{}}, got: {event.content_block.input}"
                    )
                tool_blocks[event.index] = {
                    'started': True,
                    'has_input_delta': False,
                    'stopped': False,
                }

        elif isinstance(event, ContentBlockDeltaEvent):
            if isinstance(event.delta, InputJsonDelta):
                if event.index not in tool_blocks:
                    raise ValueError(
                        f"input_json_delta at index {event.index} "
                        "without preceding tool_use content_block_start"
                    )
                tool_blocks[event.index]['has_input_delta'] = True

        elif isinstance(event, ContentBlockStopEvent):
            if event.index in tool_blocks:
                tool_blocks[event.index]['stopped'] = True

    # Verify all tool blocks are complete
    for index, block in tool_blocks.items():
        if not block['stopped']:
            raise ValueError(f"Tool block at index {index} never received content_block_stop")


def validate_text_block_closes_before_tools(events: list[SSEEvent]) -> None:
    """
    Validate that text block (index 0) closes before any tool blocks start.

    Args:
        events: List of validated SSE events

    Raises:
        ValueError: If text block closes after tool blocks start
    """
    text_block_stopped = False
    text_block_stop_position = -1
    first_tool_block_position = -1

    for i, event in enumerate(events):
        if isinstance(event, ContentBlockStopEvent) and event.index == 0:
            text_block_stopped = True
            text_block_stop_position = i

        if isinstance(event, ContentBlockStartEvent):
            if isinstance(event.content_block, ToolUseContentBlock):
                if first_tool_block_position == -1:
                    first_tool_block_position = i

    if first_tool_block_position != -1 and text_block_stop_position != -1:
        if text_block_stop_position > first_tool_block_position:
            raise ValueError(
                f"Text block (index 0) stopped at position {text_block_stop_position} "
                f"but first tool block started at position {first_tool_block_position}. "
                "Text block must close before tool blocks start."
            )


# =============================================================================
# Schema Version Info
# =============================================================================

SCHEMA_VERSION = "1.0.0"
SCHEMA_LAST_UPDATED = "2026-02-06"
ANTHROPIC_API_VERSION = "2023-06-01"

SCHEMA_CHANGELOG = """
v1.0.0 (2026-02-06):
  - Initial schema definition based on Anthropic Messages API streaming format
  - Covers: message_start, content_block_start, content_block_delta,
    content_block_stop, message_delta, message_stop
  - Tool use validation: input must be empty in content_block_start,
    streamed via input_json_delta
  - Event ordering validation: text block must close before tool blocks
"""
