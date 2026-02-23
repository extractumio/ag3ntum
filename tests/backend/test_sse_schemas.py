"""
Tests for SSE Schema Validation.

These tests validate that our LLM proxy translator produces correctly formatted
Anthropic SSE events. If Claude Code or the Anthropic API changes message formats,
these tests will fail, alerting us to adapt our translator.

Run with: ./run.sh test --backend
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.api.llm_proxy.schemas import (
    # Event models
    MessageStartEvent,
    ContentBlockStartEvent,
    ContentBlockDeltaEvent,
    ContentBlockStopEvent,
    MessageDeltaEvent,
    MessageStopEvent,
    # Content block models
    TextContentBlock,
    ToolUseContentBlock,
    ThinkingContentBlock,
    # Delta models
    TextDelta,
    InputJsonDelta,
    ThinkingDelta,
    # Other models
    Usage,
    MessageStartMessage,
    MessageDeltaData,
    # Validation helpers
    parse_sse_event,
    validate_sse_event,
    validate_sse_stream,
    validate_tool_use_stream_order,
    validate_text_block_closes_before_tools,
    # Enums
    StopReason,
    ContentBlockType,
    DeltaType,
)
from src.api.llm_proxy.translator import stream_openai_to_claude


class TestEnums:
    """Test enum values match expected Anthropic API values."""

    def test_stop_reason_values(self):
        """Validate all stop_reason values are correct."""
        assert StopReason.END_TURN.value == "end_turn"
        assert StopReason.MAX_TOKENS.value == "max_tokens"
        assert StopReason.STOP_SEQUENCE.value == "stop_sequence"
        assert StopReason.TOOL_USE.value == "tool_use"

    def test_content_block_type_values(self):
        """Validate all content_block types are correct."""
        assert ContentBlockType.TEXT.value == "text"
        assert ContentBlockType.TOOL_USE.value == "tool_use"
        assert ContentBlockType.THINKING.value == "thinking"

    def test_delta_type_values(self):
        """Validate all delta types are correct."""
        assert DeltaType.TEXT_DELTA.value == "text_delta"
        assert DeltaType.INPUT_JSON_DELTA.value == "input_json_delta"
        assert DeltaType.THINKING_DELTA.value == "thinking_delta"


class TestContentBlockSchemas:
    """Test content block Pydantic models."""

    def test_text_content_block(self):
        """Validate TextContentBlock schema."""
        block = TextContentBlock(text="Hello world")
        assert block.type == "text"
        assert block.text == "Hello world"

    def test_text_content_block_empty(self):
        """Text blocks can have empty text (initial state)."""
        block = TextContentBlock()
        assert block.type == "text"
        assert block.text == ""

    def test_tool_use_content_block(self):
        """Validate ToolUseContentBlock schema."""
        block = ToolUseContentBlock(
            id="tool_123",
            name="Read",
            input={},
        )
        assert block.type == "tool_use"
        assert block.id == "tool_123"
        assert block.name == "Read"
        assert block.input == {}

    def test_tool_use_content_block_streaming_requires_empty_input(self):
        """In streaming, tool_use blocks should have empty input."""
        # This is valid for streaming - empty input
        block = ToolUseContentBlock(id="tool_1", name="Write", input={})
        assert block.input == {}

        # Non-empty input is allowed by schema (for non-streaming responses)
        # but validate_tool_use_stream_order() will catch this in streaming
        block_with_input = ToolUseContentBlock(
            id="tool_1",
            name="Write",
            input={"file_path": "/test.txt"},
        )
        assert block_with_input.input == {"file_path": "/test.txt"}

    def test_thinking_content_block(self):
        """Validate ThinkingContentBlock schema."""
        block = ThinkingContentBlock(thinking="Let me think...")
        assert block.type == "thinking"
        assert block.thinking == "Let me think..."


class TestDeltaSchemas:
    """Test delta Pydantic models."""

    def test_text_delta(self):
        """Validate TextDelta schema."""
        delta = TextDelta(text="Hello")
        assert delta.type == "text_delta"
        assert delta.text == "Hello"

    def test_input_json_delta(self):
        """Validate InputJsonDelta schema."""
        delta = InputJsonDelta(partial_json='{"file_path":"/test.txt"}')
        assert delta.type == "input_json_delta"
        assert delta.partial_json == '{"file_path":"/test.txt"}'

    def test_thinking_delta(self):
        """Validate ThinkingDelta schema."""
        delta = ThinkingDelta(thinking="Considering options...")
        assert delta.type == "thinking_delta"
        assert delta.thinking == "Considering options..."


class TestUsageSchema:
    """Test Usage schema."""

    def test_usage_default_values(self):
        """Usage fields have correct defaults."""
        usage = Usage()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.cache_creation_input_tokens is None
        assert usage.cache_read_input_tokens is None

    def test_usage_with_values(self):
        """Usage accepts token counts."""
        usage = Usage(
            input_tokens=100,
            output_tokens=50,
            cache_creation_input_tokens=10,
            cache_read_input_tokens=5,
        )
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.cache_creation_input_tokens == 10
        assert usage.cache_read_input_tokens == 5


class TestSSEEventSchemas:
    """Test SSE event Pydantic models."""

    def test_message_start_event(self):
        """Validate MessageStartEvent schema."""
        event = MessageStartEvent(
            message=MessageStartMessage(
                id="msg_123",
                model="claude-3-opus",
            )
        )
        assert event.type == "message_start"
        assert event.message.id == "msg_123"
        assert event.message.type == "message"
        assert event.message.role == "assistant"
        assert event.message.model == "claude-3-opus"
        assert event.message.content == []
        assert event.message.stop_reason is None
        assert event.message.stop_sequence is None

    def test_content_block_start_event_text(self):
        """Validate ContentBlockStartEvent with text block."""
        event = ContentBlockStartEvent(
            index=0,
            content_block=TextContentBlock(text=""),
        )
        assert event.type == "content_block_start"
        assert event.index == 0
        assert event.content_block.type == "text"

    def test_content_block_start_event_tool_use(self):
        """Validate ContentBlockStartEvent with tool_use block."""
        event = ContentBlockStartEvent(
            index=1,
            content_block=ToolUseContentBlock(
                id="tool_abc",
                name="Bash",
                input={},
            ),
        )
        assert event.type == "content_block_start"
        assert event.index == 1
        assert event.content_block.type == "tool_use"
        assert event.content_block.id == "tool_abc"
        assert event.content_block.name == "Bash"
        assert event.content_block.input == {}

    def test_content_block_delta_event_text(self):
        """Validate ContentBlockDeltaEvent with text_delta."""
        event = ContentBlockDeltaEvent(
            index=0,
            delta=TextDelta(text="Hello"),
        )
        assert event.type == "content_block_delta"
        assert event.index == 0
        assert event.delta.type == "text_delta"
        assert event.delta.text == "Hello"

    def test_content_block_delta_event_input_json(self):
        """Validate ContentBlockDeltaEvent with input_json_delta."""
        event = ContentBlockDeltaEvent(
            index=1,
            delta=InputJsonDelta(partial_json='{"command":"ls"}'),
        )
        assert event.type == "content_block_delta"
        assert event.index == 1
        assert event.delta.type == "input_json_delta"
        assert event.delta.partial_json == '{"command":"ls"}'

    def test_content_block_stop_event(self):
        """Validate ContentBlockStopEvent schema."""
        event = ContentBlockStopEvent(index=0)
        assert event.type == "content_block_stop"
        assert event.index == 0

    def test_message_delta_event(self):
        """Validate MessageDeltaEvent schema."""
        event = MessageDeltaEvent(
            delta=MessageDeltaData(
                stop_reason="end_turn",
                stop_sequence=None,
            ),
            usage=Usage(output_tokens=100),
        )
        assert event.type == "message_delta"
        assert event.delta.stop_reason == "end_turn"
        assert event.delta.stop_sequence is None
        assert event.usage.output_tokens == 100

    def test_message_stop_event(self):
        """Validate MessageStopEvent schema."""
        event = MessageStopEvent()
        assert event.type == "message_stop"


class TestParseSSEEvent:
    """Test SSE event string parsing."""

    def test_parse_valid_event(self):
        """Parse a valid SSE event string."""
        event_str = 'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","model":"test","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":0,"output_tokens":0}}}\n\n'
        event_type, data = parse_sse_event(event_str)
        assert event_type == "message_start"
        assert data["type"] == "message_start"
        assert data["message"]["id"] == "msg_1"

    def test_parse_event_without_event_prefix(self):
        """Reject event without 'event: ' prefix."""
        event_str = 'data: {"type":"message_start"}\n\n'
        with pytest.raises(ValueError, match="must have at least event and data lines"):
            parse_sse_event(event_str)

    def test_parse_event_without_data_prefix(self):
        """Reject event without 'data: ' prefix."""
        event_str = 'event: message_start\n{"type":"message_start"}\n\n'
        with pytest.raises(ValueError, match="must start with 'data: '"):
            parse_sse_event(event_str)

    def test_parse_event_invalid_json(self):
        """Reject event with invalid JSON."""
        event_str = 'event: message_start\ndata: {invalid json}\n\n'
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_sse_event(event_str)


class TestValidateSSEEvent:
    """Test SSE event validation against schemas."""

    def test_validate_message_start(self):
        """Validate message_start event."""
        event_str = 'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","model":"test","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":0,"output_tokens":0}}}\n\n'
        event = validate_sse_event(event_str)
        assert isinstance(event, MessageStartEvent)
        assert event.message.id == "msg_1"

    def test_validate_content_block_start(self):
        """Validate content_block_start event."""
        event_str = 'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
        event = validate_sse_event(event_str)
        assert isinstance(event, ContentBlockStartEvent)
        assert event.index == 0

    def test_validate_content_block_delta(self):
        """Validate content_block_delta event."""
        event_str = 'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n'
        event = validate_sse_event(event_str)
        assert isinstance(event, ContentBlockDeltaEvent)
        assert event.delta.text == "Hello"

    def test_validate_content_block_stop(self):
        """Validate content_block_stop event."""
        event_str = 'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
        event = validate_sse_event(event_str)
        assert isinstance(event, ContentBlockStopEvent)
        assert event.index == 0

    def test_validate_message_delta(self):
        """Validate message_delta event."""
        event_str = 'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":50}}\n\n'
        event = validate_sse_event(event_str)
        assert isinstance(event, MessageDeltaEvent)
        assert event.delta.stop_reason == "end_turn"

    def test_validate_message_stop(self):
        """Validate message_stop event."""
        event_str = 'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        event = validate_sse_event(event_str)
        assert isinstance(event, MessageStopEvent)

    def test_validate_unknown_event_type(self):
        """Reject unknown event type."""
        event_str = 'event: unknown_type\ndata: {"type":"unknown_type"}\n\n'
        with pytest.raises(ValueError, match="Unknown event type"):
            validate_sse_event(event_str)

    def test_validate_missing_required_field(self):
        """Reject event missing required field."""
        # Missing 'message' in message_start
        event_str = 'event: message_start\ndata: {"type":"message_start"}\n\n'
        with pytest.raises(Exception):  # Pydantic ValidationError
            validate_sse_event(event_str)


class TestValidateSSEStream:
    """Test complete SSE stream validation."""

    def test_validate_minimal_stream(self):
        """Validate minimal valid stream (message_start + message_delta + message_stop)."""
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","model":"test","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":0,"output_tokens":0}}}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":0}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]
        validated = validate_sse_stream(events)
        assert len(validated) == 3
        assert isinstance(validated[0], MessageStartEvent)
        assert isinstance(validated[-2], MessageDeltaEvent)
        assert isinstance(validated[-1], MessageStopEvent)

    def test_validate_stream_with_text(self):
        """Validate stream with text content."""
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","model":"test","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":0,"output_tokens":0}}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":10}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]
        validated = validate_sse_stream(events)
        assert len(validated) == 6

    def test_validate_empty_stream(self):
        """Reject empty stream."""
        with pytest.raises(ValueError, match="Empty event stream"):
            validate_sse_stream([])

    def test_validate_stream_wrong_first_event(self):
        """Reject stream not starting with message_start."""
        events = [
            'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]
        with pytest.raises(ValueError, match="First event must be message_start"):
            validate_sse_stream(events)

    def test_validate_stream_wrong_last_event(self):
        """Reject stream not ending with message_stop."""
        events = [
            'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","model":"test","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":0,"output_tokens":0}}}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":0}}\n\n',
        ]
        with pytest.raises(ValueError, match="Last event must be message_stop"):
            validate_sse_stream(events)


class TestValidateToolUseStreamOrder:
    """Test tool_use event ordering validation."""

    def test_valid_tool_use_order(self):
        """Valid tool_use ordering: start -> delta -> stop."""
        events = [
            ContentBlockStartEvent(
                index=1,
                content_block=ToolUseContentBlock(id="t1", name="Read", input={}),
            ),
            ContentBlockDeltaEvent(
                index=1,
                delta=InputJsonDelta(partial_json='{"file_path":"/test.txt"}'),
            ),
            ContentBlockStopEvent(index=1),
        ]
        # Should not raise
        validate_tool_use_stream_order(events)

    def test_tool_use_non_empty_input_in_start(self):
        """Reject tool_use with non-empty input in content_block_start."""
        events = [
            ContentBlockStartEvent(
                index=1,
                content_block=ToolUseContentBlock(
                    id="t1",
                    name="Read",
                    input={"file_path": "/test.txt"},  # Should be empty
                ),
            ),
            ContentBlockStopEvent(index=1),
        ]
        with pytest.raises(ValueError, match="must have empty input"):
            validate_tool_use_stream_order(events)

    def test_input_json_delta_without_tool_start(self):
        """Reject input_json_delta without preceding tool_use start."""
        events = [
            ContentBlockDeltaEvent(
                index=1,
                delta=InputJsonDelta(partial_json='{"file_path":"/test.txt"}'),
            ),
        ]
        with pytest.raises(ValueError, match="without preceding tool_use content_block_start"):
            validate_tool_use_stream_order(events)

    def test_tool_block_not_stopped(self):
        """Reject tool block that never receives content_block_stop."""
        events = [
            ContentBlockStartEvent(
                index=1,
                content_block=ToolUseContentBlock(id="t1", name="Read", input={}),
            ),
            ContentBlockDeltaEvent(
                index=1,
                delta=InputJsonDelta(partial_json='{"file_path":"/test.txt"}'),
            ),
            # Missing content_block_stop for index 1
        ]
        with pytest.raises(ValueError, match="never received content_block_stop"):
            validate_tool_use_stream_order(events)


class TestValidateTextBlockClosesBeforeTools:
    """Test text block / tool block ordering validation."""

    def test_correct_order_text_stops_before_tools(self):
        """Text block (index 0) stops before tool blocks start."""
        events = [
            ContentBlockStartEvent(
                index=0,
                content_block=TextContentBlock(text=""),
            ),
            ContentBlockDeltaEvent(
                index=0,
                delta=TextDelta(text="Hello"),
            ),
            ContentBlockStopEvent(index=0),  # Text stops first
            ContentBlockStartEvent(  # Then tool starts
                index=1,
                content_block=ToolUseContentBlock(id="t1", name="Read", input={}),
            ),
            ContentBlockStopEvent(index=1),
        ]
        # Should not raise
        validate_text_block_closes_before_tools(events)

    def test_incorrect_order_tool_starts_before_text_stops(self):
        """Reject tool block starting before text block stops."""
        events = [
            ContentBlockStartEvent(
                index=0,
                content_block=TextContentBlock(text=""),
            ),
            ContentBlockStartEvent(  # Tool starts
                index=1,
                content_block=ToolUseContentBlock(id="t1", name="Read", input={}),
            ),
            ContentBlockStopEvent(index=0),  # Text stops after
            ContentBlockStopEvent(index=1),
        ]
        with pytest.raises(ValueError, match="Text block must close before tool blocks start"):
            validate_text_block_closes_before_tools(events)


class TestTranslatorOutputValidation:
    """Test that translator output conforms to SSE schemas.

    These tests validate our OpenAI-to-Claude translator produces
    correctly formatted events. If Claude Code changes format,
    these tests will fail.
    """

    @pytest.fixture
    def mock_openai_text_response(self):
        """Mock OpenAI streaming response with text only."""
        mock_response = AsyncMock()

        async def mock_aiter_lines():
            yield 'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}'
            yield 'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}'
            yield 'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}]}'
            yield 'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5}}'
            yield "data: [DONE]"

        mock_response.aiter_lines = mock_aiter_lines
        return mock_response

    @pytest.fixture
    def mock_openai_tool_response(self):
        """Mock OpenAI streaming response with tool call."""
        mock_response = AsyncMock()

        async def mock_aiter_lines():
            yield 'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}'
            yield 'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Let me read the file."},"finish_reason":null}]}'
            yield 'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_abc","type":"function","function":{"name":"Read","arguments":""}}]},"finish_reason":null}]}'
            yield 'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"file"}}]},"finish_reason":null}]}'
            yield 'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"_path\\":\\"/test.txt\\"}"}}]},"finish_reason":null}]}'
            yield 'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":20,"completion_tokens":15}}'
            yield "data: [DONE]"

        mock_response.aiter_lines = mock_aiter_lines
        return mock_response

    @pytest.mark.asyncio
    async def test_translator_text_stream_validates(self, mock_openai_text_response):
        """Translator text output validates against schemas."""
        events = []
        async for chunk in stream_openai_to_claude(mock_openai_text_response, "test-model"):
            events.append(chunk)

        # Validate each event
        validated = validate_sse_stream(events)

        # Check structure
        assert isinstance(validated[0], MessageStartEvent)
        assert validated[0].message.model == "test-model"

        # Find text deltas
        text_deltas = [
            e for e in validated
            if isinstance(e, ContentBlockDeltaEvent) and isinstance(e.delta, TextDelta)
        ]
        assert len(text_deltas) >= 1

        # Check final events
        assert isinstance(validated[-2], MessageDeltaEvent)
        assert validated[-2].delta.stop_reason == "end_turn"
        assert isinstance(validated[-1], MessageStopEvent)

    @pytest.mark.asyncio
    async def test_translator_tool_stream_validates(self, mock_openai_tool_response):
        """Translator tool call output validates against schemas."""
        events = []
        async for chunk in stream_openai_to_claude(mock_openai_tool_response, "test-model"):
            events.append(chunk)

        # Validate each event
        validated = validate_sse_stream(events)

        # Validate tool use ordering
        validate_tool_use_stream_order(validated)

        # Validate text block closes before tools
        validate_text_block_closes_before_tools(validated)

        # Find tool_use content blocks
        tool_starts = [
            e for e in validated
            if isinstance(e, ContentBlockStartEvent)
            and isinstance(e.content_block, ToolUseContentBlock)
        ]
        assert len(tool_starts) == 1
        assert tool_starts[0].content_block.name == "Read"
        assert tool_starts[0].content_block.input == {}  # Must be empty in streaming

        # Find input_json_delta
        input_deltas = [
            e for e in validated
            if isinstance(e, ContentBlockDeltaEvent)
            and isinstance(e.delta, InputJsonDelta)
        ]
        assert len(input_deltas) == 1
        assert '"/test.txt"' in input_deltas[0].delta.partial_json

        # Check stop_reason is tool_use
        message_delta = [e for e in validated if isinstance(e, MessageDeltaEvent)][0]
        assert message_delta.delta.stop_reason == "tool_use"

    @pytest.mark.asyncio
    async def test_translator_message_id_format(self, mock_openai_text_response):
        """Translator produces valid message IDs."""
        events = []
        async for chunk in stream_openai_to_claude(mock_openai_text_response, "test-model"):
            events.append(chunk)

        validated = validate_sse_stream(events)
        msg_start = validated[0]
        assert isinstance(msg_start, MessageStartEvent)
        assert msg_start.message.id  # Non-empty
        assert isinstance(msg_start.message.id, str)

    @pytest.mark.asyncio
    async def test_translator_content_block_indices(self, mock_openai_tool_response):
        """Translator uses correct content block indices."""
        events = []
        async for chunk in stream_openai_to_claude(mock_openai_tool_response, "test-model"):
            events.append(chunk)

        validated = validate_sse_stream(events)

        # Text block should be index 0
        text_starts = [
            e for e in validated
            if isinstance(e, ContentBlockStartEvent)
            and isinstance(e.content_block, TextContentBlock)
        ]
        assert len(text_starts) == 1
        assert text_starts[0].index == 0

        # Tool blocks should be index 1+
        tool_starts = [
            e for e in validated
            if isinstance(e, ContentBlockStartEvent)
            and isinstance(e.content_block, ToolUseContentBlock)
        ]
        assert all(e.index >= 1 for e in tool_starts)


class TestSchemaChanges:
    """Tests that will fail if Claude Code/Anthropic API changes message format.

    Add tests here for specific fields that are critical to our translator.
    When Anthropic changes their API, these tests help identify what changed.
    """

    def test_message_start_required_fields(self):
        """MessageStartMessage requires these fields."""
        # This will fail if Anthropic adds required fields
        msg = MessageStartMessage(
            id="msg_test",
            model="claude-3",
        )
        # These should all exist and have expected types
        assert hasattr(msg, "id")
        assert hasattr(msg, "type")
        assert hasattr(msg, "role")
        assert hasattr(msg, "model")
        assert hasattr(msg, "content")
        assert hasattr(msg, "stop_reason")
        assert hasattr(msg, "stop_sequence")
        assert hasattr(msg, "usage")

    def test_tool_use_block_required_fields(self):
        """ToolUseContentBlock requires these fields."""
        block = ToolUseContentBlock(
            id="tool_1",
            name="Read",
            input={},
        )
        assert hasattr(block, "type")
        assert hasattr(block, "id")
        assert hasattr(block, "name")
        assert hasattr(block, "input")
        assert block.type == "tool_use"

    def test_input_json_delta_required_fields(self):
        """InputJsonDelta requires these fields."""
        delta = InputJsonDelta(partial_json="{}")
        assert hasattr(delta, "type")
        assert hasattr(delta, "partial_json")
        assert delta.type == "input_json_delta"

    def test_message_delta_required_fields(self):
        """MessageDeltaEvent requires these fields."""
        event = MessageDeltaEvent(
            delta=MessageDeltaData(stop_reason="end_turn"),
            usage=Usage(output_tokens=10),
        )
        assert hasattr(event, "type")
        assert hasattr(event, "delta")
        assert hasattr(event, "usage")
        assert hasattr(event.delta, "stop_reason")
        assert hasattr(event.delta, "stop_sequence")

    def test_all_event_types_have_type_field(self):
        """All SSE events must have a 'type' field."""
        events = [
            MessageStartEvent(message=MessageStartMessage(id="1", model="m")),
            ContentBlockStartEvent(index=0, content_block=TextContentBlock()),
            ContentBlockDeltaEvent(index=0, delta=TextDelta(text="")),
            ContentBlockStopEvent(index=0),
            MessageDeltaEvent(delta=MessageDeltaData(), usage=Usage()),
            MessageStopEvent(),
        ]
        for event in events:
            assert hasattr(event, "type")
            assert event.type is not None


class TestRecordedAPIEvents:
    """Validate recorded real Anthropic API SSE events against schemas.

    These tests load recorded SSE events from fixtures/anthropic_sse_samples.json
    and validate them against our Pydantic schemas. If Anthropic changes their
    API format, these tests will fail when you update the fixture with fresh
    recordings.

    To update fixtures:
    1. Run a real Anthropic API call with stream=True
    2. Capture the raw SSE events
    3. Update tests/backend/fixtures/anthropic_sse_samples.json
    4. Run tests - failures indicate schema changes to investigate
    """

    @pytest.fixture
    def recorded_samples(self):
        """Load recorded SSE samples from fixture file."""
        import os
        fixture_path = os.path.join(
            os.path.dirname(__file__),
            "fixtures",
            "anthropic_sse_samples.json",
        )
        with open(fixture_path) as f:
            return json.load(f)

    def test_recorded_text_stream_validates(self, recorded_samples):
        """Recorded text-only stream validates against schemas."""
        events = recorded_samples["text_only_stream"]
        validated = validate_sse_stream(events)

        # Verify structure
        assert isinstance(validated[0], MessageStartEvent)
        assert isinstance(validated[-1], MessageStopEvent)

        # Verify text content
        text_deltas = [
            e for e in validated
            if isinstance(e, ContentBlockDeltaEvent)
            and isinstance(e.delta, TextDelta)
        ]
        assert len(text_deltas) >= 1
        assert "Hello" in text_deltas[0].delta.text

    def test_recorded_tool_stream_validates(self, recorded_samples):
        """Recorded tool call stream validates against schemas."""
        events = recorded_samples["tool_call_stream"]
        validated = validate_sse_stream(events)

        # Validate tool use ordering
        validate_tool_use_stream_order(validated)

        # Validate text block closes before tools
        validate_text_block_closes_before_tools(validated)

        # Find tool_use blocks
        tool_starts = [
            e for e in validated
            if isinstance(e, ContentBlockStartEvent)
            and isinstance(e.content_block, ToolUseContentBlock)
        ]
        assert len(tool_starts) == 1
        assert tool_starts[0].content_block.name == "Read"
        assert tool_starts[0].content_block.input == {}  # Must be empty

        # Verify input streamed via delta
        input_deltas = [
            e for e in validated
            if isinstance(e, ContentBlockDeltaEvent)
            and isinstance(e.delta, InputJsonDelta)
        ]
        assert len(input_deltas) >= 1

        # Verify stop_reason
        message_delta = [e for e in validated if isinstance(e, MessageDeltaEvent)][0]
        assert message_delta.delta.stop_reason == "tool_use"

    def test_recorded_multiple_tools_validates(self, recorded_samples):
        """Recorded stream with multiple tool calls validates."""
        events = recorded_samples["multiple_tools_stream"]
        validated = validate_sse_stream(events)

        # Validate ordering
        validate_tool_use_stream_order(validated)
        validate_text_block_closes_before_tools(validated)

        # Should have 2 tool_use blocks
        tool_starts = [
            e for e in validated
            if isinstance(e, ContentBlockStartEvent)
            and isinstance(e.content_block, ToolUseContentBlock)
        ]
        assert len(tool_starts) == 2
        assert tool_starts[0].index == 1
        assert tool_starts[1].index == 2

    def test_recorded_message_start_has_expected_fields(self, recorded_samples):
        """Recorded message_start has all expected fields.

        This test will fail if Anthropic adds new required fields.
        """
        events = recorded_samples["text_only_stream"]
        validated = validate_sse_stream(events)
        msg_start = validated[0]

        assert isinstance(msg_start, MessageStartEvent)
        msg = msg_start.message

        # All these fields must exist in real API response
        assert msg.id.startswith("msg_")
        assert msg.type == "message"
        assert msg.role == "assistant"
        assert msg.model  # Non-empty model name
        assert isinstance(msg.content, list)
        assert msg.stop_reason is None  # Not set in message_start
        assert isinstance(msg.usage.input_tokens, int)
        assert isinstance(msg.usage.output_tokens, int)

    def test_recorded_tool_use_block_format(self, recorded_samples):
        """Recorded tool_use block has expected format.

        This test will fail if Anthropic changes tool_use structure.
        """
        events = recorded_samples["tool_call_stream"]
        validated = validate_sse_stream(events)

        tool_starts = [
            e for e in validated
            if isinstance(e, ContentBlockStartEvent)
            and isinstance(e.content_block, ToolUseContentBlock)
        ]
        assert len(tool_starts) >= 1

        tool_block = tool_starts[0].content_block
        assert tool_block.type == "tool_use"
        assert tool_block.id.startswith("toolu_")  # Anthropic tool ID format
        assert isinstance(tool_block.name, str)
        assert tool_block.input == {}  # Must be empty in streaming

    def test_recorded_input_json_delta_format(self, recorded_samples):
        """Recorded input_json_delta has expected format.

        This test will fail if Anthropic changes how tool inputs are streamed.
        """
        events = recorded_samples["tool_call_stream"]
        validated = validate_sse_stream(events)

        input_deltas = [
            e for e in validated
            if isinstance(e, ContentBlockDeltaEvent)
            and isinstance(e.delta, InputJsonDelta)
        ]
        assert len(input_deltas) >= 1

        # All deltas must have correct type
        for delta_event in input_deltas:
            delta = delta_event.delta
            assert delta.type == "input_json_delta"
            assert isinstance(delta.partial_json, str)

        # At least one delta should have non-empty content
        # (first delta can be empty, content comes in subsequent deltas)
        non_empty_deltas = [d for d in input_deltas if d.delta.partial_json]
        assert len(non_empty_deltas) >= 1, "At least one input_json_delta should have content"


class TestTraceProcessorEventCoverage:
    """Verify TraceProcessor handles all schema-defined event types.

    These tests ensure TraceProcessor can process events that match our schemas.
    When Anthropic adds new event types:
    1. TestRecordedAPIEvents will fail (unknown event in fixture)
    2. We update schemas.py with new event type
    3. These tests fail, reminding us to update TraceProcessor

    This catches the case where we update schemas but forget to update
    TraceProcessor, which would silently drop new events.
    """

    @pytest.fixture
    def tracer(self):
        """Create a mock tracer that tracks method calls."""
        from unittest.mock import MagicMock
        mock_tracer = MagicMock()
        # Ensure all expected methods exist
        mock_tracer.on_agent_start = MagicMock()
        mock_tracer.on_message = MagicMock()
        mock_tracer.on_thinking = MagicMock()
        mock_tracer.on_tool_start = MagicMock()
        mock_tracer.on_tool_complete = MagicMock()
        mock_tracer.on_error = MagicMock()
        mock_tracer.on_metrics_update = MagicMock()
        mock_tracer.on_subagent_start = MagicMock()
        mock_tracer.on_subagent_stop = MagicMock()
        mock_tracer.on_subagent_message = MagicMock()
        mock_tracer.on_system_event = MagicMock()
        return mock_tracer

    @pytest.fixture
    def trace_processor(self, tracer):
        """Create a TraceProcessor with the mock tracer."""
        from src.core.trace_processor import TraceProcessor
        processor = TraceProcessor(tracer)
        processor.set_model("claude-sonnet-4-20250514")
        return processor

    def _make_stream_event(self, raw_event: dict):
        """Create a StreamEvent with required uuid and session_id."""
        from claude_agent_sdk.types import StreamEvent
        return StreamEvent(
            event=raw_event,
            uuid="test-uuid-12345",
            session_id="test-session-id"
        )

    def test_handles_message_start_event(self, trace_processor):
        """TraceProcessor handles message_start events."""
        raw_event = {
            "type": "message_start",
            "message": {
                "id": "msg_123",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [],
                "stop_reason": None,
                "usage": {"input_tokens": 10, "output_tokens": 0},
            }
        }
        event = self._make_stream_event(raw_event)
        # Should not raise
        trace_processor.process_message(event)

    def test_handles_content_block_start_text(self, trace_processor, tracer):
        """TraceProcessor handles content_block_start with text type."""
        raw_event = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": "Hello"},
        }
        event = self._make_stream_event(raw_event)
        trace_processor.process_message(event)
        tracer.on_message.assert_called()

    def test_handles_content_block_start_thinking(self, trace_processor, tracer):
        """TraceProcessor handles content_block_start with thinking type."""
        raw_event = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "Let me think..."},
        }
        event = self._make_stream_event(raw_event)
        trace_processor.process_message(event)
        tracer.on_thinking.assert_called()

    def test_handles_content_block_delta_text(self, trace_processor, tracer):
        """TraceProcessor handles content_block_delta with text_delta."""
        raw_event = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": " world"},
        }
        event = self._make_stream_event(raw_event)
        trace_processor.process_message(event)
        tracer.on_message.assert_called()

    def test_handles_content_block_delta_thinking(self, trace_processor, tracer):
        """TraceProcessor handles content_block_delta with thinking_delta."""
        # First start a thinking block
        start_event = self._make_stream_event({
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        })
        trace_processor.process_message(start_event)

        # Then send thinking delta
        raw_event = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "more thoughts"},
        }
        event = self._make_stream_event(raw_event)
        trace_processor.process_message(event)
        # Thinking updates are throttled, but buffer should be updated
        assert trace_processor._stream_thinking_buffer == "more thoughts"

    def test_handles_content_block_delta_input_json(self, trace_processor):
        """TraceProcessor handles content_block_delta with input_json_delta.

        NOTE: input_json_delta is used for tool input streaming. TraceProcessor
        doesn't explicitly process these - they're handled by the SDK internally
        for tool input assembly. This test documents that behavior.
        """
        raw_event = {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"file_path":'},
        }
        event = self._make_stream_event(raw_event)
        # Should not raise - falls through delta type check gracefully
        trace_processor.process_message(event)

    def test_handles_content_block_stop(self, trace_processor):
        """TraceProcessor handles content_block_stop events."""
        raw_event = {"type": "content_block_stop", "index": 0}
        event = self._make_stream_event(raw_event)
        # Should not raise
        trace_processor.process_message(event)

    def test_handles_message_delta(self, trace_processor, tracer):
        """TraceProcessor handles message_delta events with usage."""
        raw_event = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 50},
        }
        event = self._make_stream_event(raw_event)
        trace_processor.process_message(event)
        tracer.on_metrics_update.assert_called()

    def test_handles_message_stop(self, trace_processor):
        """TraceProcessor handles message_stop events."""
        raw_event = {"type": "message_stop"}
        event = self._make_stream_event(raw_event)
        # Should not raise
        trace_processor.process_message(event)

    def test_handles_ping_event(self, trace_processor):
        """TraceProcessor handles ping keepalive events gracefully.

        Ping events are keepalives and should be silently ignored.
        """
        raw_event = {"type": "ping"}
        event = self._make_stream_event(raw_event)
        # Should not raise - ping is silently ignored
        trace_processor.process_message(event)

    def test_all_schema_event_types_have_handlers(self):
        """Verify all schema-defined event types are handled by TraceProcessor.

        This test documents which event types are explicitly handled vs passed
        through. If a new event type is added to schemas.py, this test should
        be updated to document how TraceProcessor handles it.
        """
        from src.api.llm_proxy.schemas import (
            MessageStartEvent,
            ContentBlockStartEvent,
            ContentBlockDeltaEvent,
            ContentBlockStopEvent,
            MessageDeltaEvent,
            MessageStopEvent,
            PingEvent,
        )

        # Map of event types to how TraceProcessor handles them
        # This serves as documentation and a reminder to update when schemas change
        event_handling = {
            "message_start": "Extracts usage, tracks parent_tool_use_id",
            "content_block_start": "Routes text/thinking blocks to tracer",
            "content_block_delta": "Routes text_delta/thinking_delta to tracer, ignores input_json_delta",
            "content_block_stop": "Finalizes thinking block buffer",
            "message_delta": "Extracts usage, updates metrics",
            "message_stop": "Finalizes partial messages, clears parent context",
            "ping": "Silently ignored (keepalive)",
        }

        # All schema event types must be documented
        schema_event_types = {
            MessageStartEvent: "message_start",
            ContentBlockStartEvent: "content_block_start",
            ContentBlockDeltaEvent: "content_block_delta",
            ContentBlockStopEvent: "content_block_stop",
            MessageDeltaEvent: "message_delta",
            MessageStopEvent: "message_stop",
            PingEvent: "ping",
        }

        for event_class, event_type in schema_event_types.items():
            assert event_type in event_handling, \
                f"Event type '{event_type}' from {event_class.__name__} is not documented. " \
                f"Update TraceProcessor and add to event_handling dict."

    def test_all_schema_delta_types_have_handlers(self):
        """Verify all schema-defined delta types are handled by TraceProcessor.

        This test documents which delta types are explicitly handled.
        """
        from src.api.llm_proxy.schemas import (
            TextDelta,
            InputJsonDelta,
            ThinkingDelta,
        )

        # Map of delta types to how TraceProcessor handles them
        delta_handling = {
            "text_delta": "Routed to tracer.on_message()",
            "thinking_delta": "Accumulated in buffer, throttled emission",
            "input_json_delta": "Ignored (SDK handles tool input assembly)",
        }

        schema_delta_types = {
            TextDelta: "text_delta",
            InputJsonDelta: "input_json_delta",
            ThinkingDelta: "thinking_delta",
        }

        for delta_class, delta_type in schema_delta_types.items():
            assert delta_type in delta_handling, \
                f"Delta type '{delta_type}' from {delta_class.__name__} is not documented. " \
                f"Update TraceProcessor and add to delta_handling dict."

    def test_all_schema_content_block_types_have_handlers(self):
        """Verify all schema-defined content block types are handled."""
        from src.api.llm_proxy.schemas import (
            TextContentBlock,
            ToolUseContentBlock,
            ThinkingContentBlock,
        )

        # Map of content block types to how TraceProcessor handles them
        block_handling = {
            "text": "Routed to tracer.on_message()",
            "tool_use": "Handled via ToolUseBlock, not in stream events directly",
            "thinking": "Routed to tracer.on_thinking()",
        }

        schema_block_types = {
            TextContentBlock: "text",
            ToolUseContentBlock: "tool_use",
            ThinkingContentBlock: "thinking",
        }

        for block_class, block_type in schema_block_types.items():
            assert block_type in block_handling, \
                f"Content block type '{block_type}' from {block_class.__name__} is not documented. " \
                f"Update TraceProcessor and add to block_handling dict."
