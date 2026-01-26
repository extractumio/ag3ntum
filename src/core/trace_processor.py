"""
Trace Processor for Claude Agent SDK.

Bridges the SDK's streaming messages and hooks to the ExecutionTracer.

Usage:
    from tracer import ExecutionTracer
    from trace_processor import TraceProcessor

    tracer = ExecutionTracer(verbose=True)
    processor = TraceProcessor(tracer)

    # Use in agent execution
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            processor.process_message(message)
"""
import re
import time
from typing import Any, Optional, Union
from claude_agent_sdk import (
    AssistantMessage,
    HookContext,
    HookMatcher,
    PostToolUseHookInput,
    PreToolUseHookInput,
    ResultMessage,
    StopHookInput,
    SystemMessage,
    UserMessage,
)
from claude_agent_sdk.types import (
    ContentBlock,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

from .tracer import TracerBase


# Pattern to match <system-reminder>...</system-reminder> blocks (including multiline)
_SYSTEM_REMINDER_PATTERN = re.compile(
    r'<system-reminder>.*?</system-reminder>',
    re.DOTALL
)

# Pattern to match mcp__ag3ntum__ToolName and capture just ToolName
_MCP_TOOL_NAME_PATTERN = re.compile(r'mcp__ag3ntum__(\w+)')

# Pattern to match <attached-files>...</attached-files> blocks (including multiline)
_ATTACHED_FILES_PATTERN = re.compile(
    r'<attached-files>(.*?)</attached-files>',
    re.DOTALL
)

# Pattern to parse individual file entries from attached-files block
# Legacy format: - filename.ext (size)
_ATTACHED_FILE_ENTRY_PATTERN = re.compile(r'-\s+(.+?)\s+\(([^)]+)\)')

# Maximum lengths for sanitization
_MAX_FILENAME_LENGTH = 255
_MAX_MIME_LENGTH = 100
_MAX_EXTENSION_LENGTH = 10


def _sanitize_filename(name: str) -> str:
    """
    Sanitize a filename to prevent security issues.

    Defends against:
    - Path traversal (../)
    - Control characters and null bytes
    - Excessively long names
    - HTML/script injection via special characters

    Args:
        name: Raw filename from user input.

    Returns:
        Sanitized filename safe for display.
    """
    if not name:
        return 'unnamed_file'

    # Remove null bytes and control characters
    sanitized = re.sub(r'[\x00-\x1f\x7f]', '', name)

    # Remove path traversal sequences
    sanitized = sanitized.replace('../', '').replace('..\\', '')

    # Remove characters that could cause issues in display or storage
    # Keep: alphanumeric, spaces, dots, dashes, underscores, parentheses
    sanitized = re.sub(r'[<>:"|?*\\]', '_', sanitized)

    # Remove leading/trailing whitespace and dots
    sanitized = sanitized.strip(' .')

    # Collapse multiple spaces
    sanitized = re.sub(r'\s+', ' ', sanitized)

    # Truncate if too long (preserve extension if possible)
    if len(sanitized) > _MAX_FILENAME_LENGTH:
        last_dot = sanitized.rfind('.')
        if last_dot > 0 and len(sanitized) - last_dot <= _MAX_EXTENSION_LENGTH + 1:
            ext = sanitized[last_dot:]
            base = sanitized[:_MAX_FILENAME_LENGTH - len(ext) - 3]
            sanitized = base + '...' + ext
        else:
            sanitized = sanitized[:_MAX_FILENAME_LENGTH - 3] + '...'

    return sanitized or 'unnamed_file'


def _sanitize_mime_type(mime: str) -> str:
    """
    Sanitize a MIME type string.

    Args:
        mime: Raw MIME type from user input.

    Returns:
        Sanitized MIME type (alphanumeric, /, -, +, . only).
    """
    if not mime:
        return ''

    # MIME types should only contain specific characters
    sanitized = re.sub(r'[^a-zA-Z0-9/\-+.]', '', mime).lower()
    return sanitized[:_MAX_MIME_LENGTH]


def _sanitize_extension(ext: str) -> str:
    """
    Sanitize a file extension.

    Args:
        ext: Raw extension from user input.

    Returns:
        Sanitized extension (alphanumeric only, lowercase).
    """
    if not ext:
        return ''

    # Extensions should only be alphanumeric
    sanitized = re.sub(r'[^a-zA-Z0-9]', '', ext).lower()
    return sanitized[:_MAX_EXTENSION_LENGTH]


def _sanitize_size_formatted(size_str: str) -> str:
    """
    Sanitize a formatted size string.

    Args:
        size_str: Raw size string (e.g., "1.5MB").

    Returns:
        Sanitized size string.
    """
    if not size_str:
        return ''

    # Size strings should only contain digits, dots, and unit letters
    sanitized = re.sub(r'[^0-9.a-zA-Z ]', '', size_str)
    return sanitized[:20]  # Reasonable max length for "999.99 GB" etc.


def strip_system_reminders(text: str) -> str:
    """
    Remove <system-reminder> blocks from text.

    These are injected by the system and should not be displayed to users.

    Args:
        text: Input text that may contain system-reminder tags.

    Returns:
        Text with all system-reminder blocks removed.
    """
    if '<system-reminder>' not in text:
        return text
    return _SYSTEM_REMINDER_PATTERN.sub('', text)


def sanitize_tool_names_in_text(text: str) -> str:
    """
    Replace internal MCP tool names with user-friendly names in text.

    Converts mcp__ag3ntum__ToolName to just ToolName for display to users.
    The actual tool IDs in messages remain unchanged - this only affects
    text content shown to end users.

    Args:
        text: Input text that may contain MCP tool name references.

    Returns:
        Text with tool names sanitized for user display.
    """
    if 'mcp__ag3ntum__' not in text:
        return text
    return _MCP_TOOL_NAME_PATTERN.sub(r'\1', text)


def transform_attached_files(text: str) -> str:
    """
    Transform <attached-files> blocks to <ag3ntum-attached-file> format.

    Converts the system-injected attached-files format into a structured
    JSON format that the frontend can render as an expandable file list widget.

    Supports two input formats:

    Legacy format:
        <attached-files>
        The user is attaching the following files (uploading to workspace):
        - filename1.txt (10.5KB)
        - filename2.pdf (1.2MB)
        </attached-files>

    New YAML format:
        <attached-files>
        files:
        - name: "filename1.txt"
          size: 10752
          size_formatted: "10.5KB"
          mime_type: "text/plain"
          extension: "txt"
          last_modified: "2024-01-15T10:30:00.000Z"
        </attached-files>

    Output format (JSON array):
        <ag3ntum-attached-file>[{"name":"filename1.txt","size":10752,...}]</ag3ntum-attached-file>

    Args:
        text: Input text that may contain attached-files blocks.

    Returns:
        Text with attached-files blocks transformed to ag3ntum-attached-file tags.
    """
    import json

    if '<attached-files>' not in text:
        return text

    def parse_yaml_files(content: str) -> list[dict[str, any]]:
        """Parse YAML-formatted file entries with security sanitization."""
        import yaml

        files = []
        try:
            # Parse YAML properly
            data = yaml.safe_load(content)
            if not isinstance(data, dict) or 'files' not in data:
                return []

            file_list = data.get('files', [])
            if not isinstance(file_list, list):
                return []

            for entry in file_list:
                if not isinstance(entry, dict):
                    continue

                # Get and sanitize name (required field)
                raw_name = entry.get('name')
                if not raw_name or not isinstance(raw_name, str):
                    continue

                file_info: dict[str, any] = {'name': _sanitize_filename(raw_name)}

                # Size (integer, capped)
                size_val = entry.get('size')
                if isinstance(size_val, (int, float)):
                    file_info['size'] = max(0, min(int(size_val), 10**15))

                # Formatted size
                size_fmt = entry.get('size_formatted')
                if isinstance(size_fmt, str):
                    file_info['size_formatted'] = _sanitize_size_formatted(size_fmt)

                # MIME type
                mime = entry.get('mime_type')
                if isinstance(mime, str):
                    sanitized_mime = _sanitize_mime_type(mime)
                    if sanitized_mime:
                        file_info['mime_type'] = sanitized_mime

                # Extension
                ext = entry.get('extension')
                if isinstance(ext, str):
                    sanitized_ext = _sanitize_extension(ext)
                    if sanitized_ext:
                        file_info['extension'] = sanitized_ext

                # Last modified (validate ISO date format)
                modified = entry.get('last_modified')
                if isinstance(modified, str):
                    if re.match(r'^\d{4}-\d{2}-\d{2}', modified) and len(modified) <= 30:
                        file_info['last_modified'] = modified

                files.append(file_info)

        except yaml.YAMLError:
            # If YAML parsing fails, return empty list
            pass

        return files

    def parse_legacy_files(content: str) -> list[dict[str, any]]:
        """Parse legacy format file entries with security sanitization."""
        files = []
        entries = _ATTACHED_FILE_ENTRY_PATTERN.findall(content)
        for filename, size in entries:
            files.append({
                'name': _sanitize_filename(filename.strip()),
                'size_formatted': _sanitize_size_formatted(size.strip())
            })
        return files

    def replace_block(match: re.Match) -> str:
        content = match.group(1)

        # Check if it's YAML format (contains "files:" or "- name:")
        if 'files:' in content or '- name:' in content:
            files = parse_yaml_files(content)
        else:
            # Fall back to legacy format
            files = parse_legacy_files(content)

        if not files:
            return ''  # Remove empty blocks

        # Output as single tag with JSON array
        json_data = json.dumps(files, separators=(',', ':'))
        return f'<ag3ntum-attached-file>{json_data}</ag3ntum-attached-file>'

    return _ATTACHED_FILES_PATTERN.sub(replace_block, text)


def sanitize_text_for_display(text: str) -> str:
    """
    Apply all text sanitization filters for user-facing display.

    Combines multiple filters:
    - Removes <system-reminder> blocks
    - Converts mcp__ag3ntum__ToolName to ToolName
    - Transforms <attached-files> to <ag3ntum-attached-file> tags

    Args:
        text: Input text from agent output.

    Returns:
        Sanitized text suitable for display to end users.
    """
    result = strip_system_reminders(text)
    result = sanitize_tool_names_in_text(result)
    result = transform_attached_files(result)
    return result


# Type alias for SDK messages
SDKMessage = Union[
    UserMessage,
    AssistantMessage,
    SystemMessage,
    ResultMessage,
    StreamEvent,
]


class TraceProcessor:
    """
    Processes Claude Agent SDK messages and dispatches to tracer.

    This class bridges the SDK's streaming message types to the
    TracerBase interface, handling message parsing and event dispatching.

    Args:
        tracer: The tracer instance to dispatch events to.
        include_user_messages: Whether to trace user messages.
    """

    def __init__(
        self,
        tracer: TracerBase,
        include_user_messages: bool = False
    ) -> None:
        self.tracer = tracer
        self.include_user_messages = include_user_messages
        self._pending_tool_calls: dict[str, dict[str, Any]] = {}
        self._initialized = False
        self._task: Optional[str] = None
        self._model: Optional[str] = None  # Model used in this session
        self._permission_denied: bool = False  # Set when permission denial interrupts
        # Cumulative token totals across all messages/API calls
        self._metrics_input_tokens = 0
        self._metrics_output_tokens = 0
        self._metrics_cache_creation_tokens = 0
        self._metrics_cache_read_tokens = 0
        self._metrics_turns = 0
        self._metrics_cost_usd: Optional[float] = None
        self._last_metrics_snapshot: Optional[tuple[int, int, int, int, int, Optional[float]]] = None
        # Current message tracking (for stream events before ResultMessage)
        self._current_msg_input_tokens = 0
        self._current_msg_output_tokens = 0
        self._current_msg_cache_creation = 0
        self._current_msg_cache_read = 0
        self._current_msg_cost: Optional[float] = None
        # Cumulative stats (set externally when resuming a session)
        self._cumulative_cost_usd: Optional[float] = None
        self._cumulative_turns: Optional[int] = None
        self._cumulative_tokens: Optional[int] = None
        self._stream_has_text = False
        # Subagent tracking: task_id -> {name, start_time, prompt}
        self._active_subagents: dict[str, dict[str, Any]] = {}
        # Current parent_tool_use_id for routing subagent messages
        self._current_parent_tool_use_id: Optional[str] = None
        # Thinking block tracking for streaming
        self._stream_thinking_active = False
        self._stream_thinking_buffer = ""
        # Throttle thinking updates to reduce UI blinking (1 second interval)
        self._thinking_last_emit_time: float = 0.0
        self._thinking_emit_interval: float = 1.0  # seconds
        # Track tool errors for session status determination
        self._tool_error_count: int = 0

    def set_task(self, task: str) -> None:
        """
        Set the task text to be displayed when agent starts.

        Args:
            task: The task description text.
        """
        self._task = task

    def set_model(self, model: str) -> None:
        """
        Set the model name for context size calculations.

        Args:
            model: The model identifier.
        """
        self._model = model

    def set_permission_denied(self, denied: bool = True) -> None:
        """
        Mark that the agent was interrupted due to permission denial.

        This affects the status displayed in the completion box.

        Args:
            denied: Whether permission was denied.
        """
        self._permission_denied = denied

    def set_cumulative_stats(
        self,
        cost_usd: Optional[float] = None,
        turns: Optional[int] = None,
        tokens: Optional[int] = None
    ) -> None:
        """
        Set cumulative statistics from previous runs (for resumed sessions).

        These values will be added to the current run's stats to show
        the total across all runs.

        Args:
            cost_usd: Previous cumulative cost in USD.
            turns: Previous cumulative turn count.
            tokens: Previous cumulative token count.
        """
        self._cumulative_cost_usd = cost_usd
        self._cumulative_turns = turns
        self._cumulative_tokens = tokens

    @property
    def tool_error_count(self) -> int:
        """Return the number of tool errors that occurred during execution."""
        return self._tool_error_count

    def had_tool_errors(self) -> bool:
        """Return True if any tool errors occurred during execution."""
        return self._tool_error_count > 0

    def finalize_orphaned_subagents(self) -> None:
        """
        Emit subagent_stop for any subagents that haven't completed.

        Call this when the agent completes to ensure all subagents are marked
        as finished (either complete or error) in the UI. This handles edge
        cases where ToolResultBlock events weren't received for some subagents.
        """
        if not self._active_subagents:
            return

        for task_id, subagent_info in list(self._active_subagents.items()):
            duration_ms = int((time.time() - subagent_info["start_time"]) * 1000)
            self.tracer.on_subagent_stop(
                task_id=task_id,
                result="(No result received - agent completed)",
                duration_ms=duration_ms,
                is_error=False  # Mark as complete, not error
            )
        self._active_subagents.clear()

    def process_message(self, message: SDKMessage) -> None:
        """
        Process a single SDK message and dispatch to tracer.

        Args:
            message: The SDK message to process.
        """
        if isinstance(message, SystemMessage):
            self._handle_system_message(message)
        elif isinstance(message, AssistantMessage):
            self._handle_assistant_message(message)
        elif isinstance(message, UserMessage):
            self._handle_user_message(message)
        elif isinstance(message, ResultMessage):
            self._handle_result_message(message)
        elif isinstance(message, StreamEvent):
            self._handle_stream_event(message)
        else:
            # Unknown message type - try to handle generically
            self._handle_unknown_message(message)

    def _handle_system_message(self, msg: SystemMessage) -> None:
        """Handle system lifecycle messages."""
        subtype = msg.subtype
        data = msg.data

        if subtype == "init":
            self._initialized = True
            # Extract skills from init data if available
            skills = data.get("skills", [])
            if not skills:
                # Skills might be empty list or not present
                skills = None

            # Extract task if available
            task = data.get("task")

            # Sanitize cwd to hide internal path structure
            # Replace full session paths with /workspace for privacy
            raw_cwd = data.get("cwd", ".")
            if "/sessions/" in raw_cwd and "/workspace" in raw_cwd:
                sanitized_cwd = "/workspace"
            else:
                sanitized_cwd = raw_cwd

            # Filter tools to hide disabled tools from init event
            tools = data.get("tools", [])

            # Filter agents list to remove Bash (we use mcp__ag3ntum__Bash instead)
            agents = data.get("agents", [])
            filtered_agents = [a for a in agents if a != "Bash"]
            # Update data for downstream consumers
            data["agents"] = filtered_agents

            self.tracer.on_agent_start(
                session_id=data.get("session_id", "unknown"),
                model=data.get("model", "unknown"),
                tools=tools,
                working_dir=sanitized_cwd,
                skills=skills,
                task=task or self._task
            )
        elif subtype in ("error", "api_error", "server_error"):
            error_msg = data.get("message", data.get("error", str(data)))
            self.tracer.on_error(str(error_msg), error_type=subtype)
        else:
            # Other system events (status changes, etc.)
            self.tracer.on_system_event(subtype, data) \
                if hasattr(self.tracer, 'on_system_event') else None

    def _handle_assistant_message(self, msg: AssistantMessage) -> None:
        """Handle assistant responses with content blocks."""
        if msg.error:
            self.tracer.on_error(
                f"Assistant error: {msg.error}",
                error_type="assistant_error"
            )
            return

        for block in msg.content:
            self._process_content_block(block)

    def _process_content_block(self, block: ContentBlock) -> None:
        """Process a single content block."""
        if isinstance(block, TextBlock):
            self.tracer.on_message(sanitize_text_for_display(block.text))

        elif isinstance(block, ThinkingBlock):
            self.tracer.on_thinking(block.thinking)

        elif isinstance(block, ToolUseBlock):
            # Store pending tool call info
            self._pending_tool_calls[block.id] = {
                "name": block.name,
                "input": block.input,
            }
            self.tracer.on_tool_start(
                tool_name=block.name,
                tool_input=block.input,
                tool_id=block.id
            )
            self._metrics_turns += 1
            self._emit_metrics_update()

            # Track Task tool invocations for subagent tracing
            if block.name == "Task":
                tool_input = block.input if isinstance(block.input, dict) else {}
                subagent_name = tool_input.get("subagent_type", "unknown")
                prompt = tool_input.get("prompt", "")
                self._active_subagents[block.id] = {
                    "name": subagent_name,
                    "start_time": time.time(),
                    "prompt": prompt,
                }
                self.tracer.on_subagent_start(
                    task_id=block.id,
                    subagent_name=subagent_name,
                    prompt=prompt
                )

        elif isinstance(block, ToolResultBlock):
            tool_id = block.tool_use_id
            tool_info = self._pending_tool_calls.pop(tool_id, {})
            tool_name = tool_info.get("name", "unknown")
            is_error = block.is_error or False

            # Track tool errors for session status determination
            if is_error:
                self._tool_error_count += 1

            self.tracer.on_tool_complete(
                tool_name=tool_name,
                tool_id=tool_id,
                result=block.content,
                duration_ms=0,  # Will be calculated by tracer
                is_error=is_error
            )

            # Handle Task tool completion for subagent tracing
            if tool_id in self._active_subagents:
                subagent_info = self._active_subagents.pop(tool_id)
                duration_ms = int((time.time() - subagent_info["start_time"]) * 1000)
                self.tracer.on_subagent_stop(
                    task_id=tool_id,
                    result=block.content,
                    duration_ms=duration_ms,
                    is_error=block.is_error or False
                )

        elif isinstance(block, dict):
            # Handle dict-style blocks (from JSON parsing)
            self._process_dict_block(block)

    def _process_dict_block(self, block: dict[str, Any]) -> None:
        """Process a dictionary-style content block."""
        if "text" in block:
            self.tracer.on_message(sanitize_text_for_display(block["text"]))
        elif "thinking" in block:
            self.tracer.on_thinking(block["thinking"])
        elif "name" in block and "input" in block:
            # Tool use
            tool_id = block.get("id", "unknown")
            tool_name = block["name"]
            tool_input = block["input"]

            # Check if this tool was already started (from streaming)
            # If so, emit tool_input_ready with the complete input instead of another tool_start
            if tool_id in self._pending_tool_calls:
                # Tool was already started during streaming - emit update with complete input
                if hasattr(self.tracer, 'on_tool_input_ready') and tool_input:
                    self.tracer.on_tool_input_ready(
                        tool_name=tool_name,
                        tool_id=tool_id,
                        tool_input=tool_input,
                    )
                # Update the stored input
                self._pending_tool_calls[tool_id]["input"] = tool_input
            else:
                # New tool - emit tool_start
                self._pending_tool_calls[tool_id] = {
                    "name": tool_name,
                    "input": tool_input,
                }
                self.tracer.on_tool_start(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_id=tool_id
                )

                # Track Task tool invocations for subagent tracing
                if tool_name == "Task":
                    input_dict = tool_input if isinstance(tool_input, dict) else {}
                    subagent_name = input_dict.get("subagent_type", "unknown")
                    prompt = input_dict.get("prompt", "")
                    self._active_subagents[tool_id] = {
                        "name": subagent_name,
                        "start_time": time.time(),
                        "prompt": prompt,
                    }
                    self.tracer.on_subagent_start(
                        task_id=tool_id,
                        subagent_name=subagent_name,
                        prompt=prompt
                    )
        elif "tool_use_id" in block:
            # Tool result
            tool_id = block["tool_use_id"]
            tool_info = self._pending_tool_calls.pop(tool_id, {})
            is_error = block.get("is_error", False)

            # Track tool errors for session status determination
            if is_error:
                self._tool_error_count += 1

            self.tracer.on_tool_complete(
                tool_name=tool_info.get("name", "unknown"),
                tool_id=tool_id,
                result=block.get("content", ""),
                duration_ms=0,
                is_error=is_error
            )

            # Handle Task tool completion for subagent tracing
            if tool_id in self._active_subagents:
                subagent_info = self._active_subagents.pop(tool_id)
                duration_ms = int((time.time() - subagent_info["start_time"]) * 1000)
                self.tracer.on_subagent_stop(
                    task_id=tool_id,
                    result=block.get("content", ""),
                    duration_ms=duration_ms,
                    is_error=block.get("is_error", False)
                )

    def _handle_user_message(self, msg: UserMessage) -> None:
        """Handle user input messages, including tool results."""
        content = msg.content
        
        if isinstance(content, str):
            if self.include_user_messages:
                self.tracer.on_message(f"[USER] {content}")
            return
        
        if isinstance(content, list):
            for block in content:
                # Handle tool result blocks (they come in UserMessage!)
                if isinstance(block, ToolResultBlock):
                    tool_id = block.tool_use_id
                    tool_info = self._pending_tool_calls.pop(tool_id, {})
                    tool_name = tool_info.get("name", "unknown")
                    is_error = block.is_error or False

                    # Track tool errors for session status determination
                    if is_error:
                        self._tool_error_count += 1

                    self.tracer.on_tool_complete(
                        tool_name=tool_name,
                        tool_id=tool_id,
                        result=block.content,
                        duration_ms=0,
                        is_error=is_error
                    )
                    
                    # Handle Task tool completion for subagent tracing
                    if tool_id in self._active_subagents:
                        subagent_info = self._active_subagents.pop(tool_id)
                        duration_ms = int((time.time() - subagent_info["start_time"]) * 1000)
                        self.tracer.on_subagent_stop(
                            task_id=tool_id,
                            result=block.content,
                            duration_ms=duration_ms,
                            is_error=block.is_error or False
                        )
                
                # Handle dict-style tool results (from JSON parsing)
                elif isinstance(block, dict) and "tool_use_id" in block:
                    tool_id = block["tool_use_id"]
                    tool_info = self._pending_tool_calls.pop(tool_id, {})
                    tool_name = tool_info.get("name", "unknown")
                    is_error = block.get("is_error", False) or False

                    # Track tool errors for session status determination
                    if is_error:
                        self._tool_error_count += 1

                    self.tracer.on_tool_complete(
                        tool_name=tool_name,
                        tool_id=tool_id,
                        result=block.get("content", ""),
                        duration_ms=0,
                        is_error=is_error
                    )
                    
                    # Handle Task tool completion for subagent tracing  
                    if tool_id in self._active_subagents:
                        subagent_info = self._active_subagents.pop(tool_id)
                        duration_ms = int((time.time() - subagent_info["start_time"]) * 1000)
                        self.tracer.on_subagent_stop(
                            task_id=tool_id,
                            result=block.get("content", ""),
                            duration_ms=duration_ms,
                            is_error=is_error
                        )
                
                elif isinstance(block, TextBlock):
                    if self.include_user_messages:
                        self.tracer.on_message(f"[USER] {block.text}")

    def _handle_result_message(self, msg: ResultMessage) -> None:
        """
        Handle final result with metrics and usage.

        This is the authoritative source for token counts for this API call.
        We accumulate these to our running totals and reset the current message
        tracking for the next API call.
        """
        if msg.usage:
            # Apply final usage with accumulation
            self._apply_usage_update(msg.usage, is_final=True)

        # Reset current message tracking for next API call
        self._current_msg_input_tokens = 0
        self._current_msg_output_tokens = 0
        self._current_msg_cache_creation = 0
        self._current_msg_cache_read = 0
        self._current_msg_cost = None

    def _handle_stream_event(self, event: StreamEvent) -> None:
        """Handle low-level stream events."""
        raw_event = event.event
        if not isinstance(raw_event, dict):
            return

        event_type = raw_event.get("type")
        usage = None

        # Check for parent_tool_use_id to identify subagent context
        parent_tool_use_id = raw_event.get("parent_tool_use_id")
        if parent_tool_use_id:
            self._current_parent_tool_use_id = parent_tool_use_id

        if event_type == "message_start":
            self._stream_has_text = False
            message = raw_event.get("message", {})
            if isinstance(message, dict):
                usage = message.get("usage")
                # Check for parent_tool_use_id in message as well
                msg_parent_id = message.get("parent_tool_use_id")
                if msg_parent_id:
                    self._current_parent_tool_use_id = msg_parent_id
        elif event_type == "message_delta":
            usage = raw_event.get("usage")
        elif event_type == "message_stop":
            usage = raw_event.get("usage")
            if self._stream_has_text:
                # Route final message to appropriate handler
                if self._current_parent_tool_use_id and \
                   self._current_parent_tool_use_id in self._active_subagents:
                    self.tracer.on_subagent_message(
                        task_id=self._current_parent_tool_use_id,
                        text="",
                        is_partial=False
                    )
                else:
                    self.tracer.on_message("", is_partial=False)
                self._stream_has_text = False
            # Clear parent context on message stop
            self._current_parent_tool_use_id = None
        else:
            usage = raw_event.get("usage")

        if event_type == "content_block_start":
            content_block = raw_event.get("content_block", {})
            if isinstance(content_block, dict):
                block_type = content_block.get("type")
                if block_type == "text":
                    text = content_block.get("text")
                    if isinstance(text, str) and text:
                        # Sanitize text for display (removes system-reminders, cleans tool names)
                        filtered_text = sanitize_text_for_display(text)
                        if filtered_text:
                            self._stream_has_text = True
                            # Route to subagent handler if in subagent context
                            if self._current_parent_tool_use_id and \
                               self._current_parent_tool_use_id in self._active_subagents:
                                self.tracer.on_subagent_message(
                                    task_id=self._current_parent_tool_use_id,
                                    text=filtered_text,
                                    is_partial=True
                                )
                            else:
                                self.tracer.on_message(filtered_text, is_partial=True)
                elif block_type == "thinking":
                    # Start of thinking block - initialize buffer
                    self._stream_thinking_active = True
                    self._stream_thinking_buffer = content_block.get("thinking", "")
                    self._thinking_last_emit_time = time.time()
                    # Emit initial thinking event to signal UI should show thinking indicator
                    if self._stream_thinking_buffer:
                        # Show last 300 chars for preview
                        preview = self._stream_thinking_buffer[-300:]
                        self.tracer.on_thinking(preview, is_partial=True)
        elif event_type == "content_block_delta":
            delta = raw_event.get("delta", {})
            if isinstance(delta, dict):
                delta_type = delta.get("type")
                if delta_type == "text_delta":
                    text = delta.get("text")
                    if isinstance(text, str) and text:
                        # Sanitize text for display (removes system-reminders, cleans tool names)
                        filtered_text = sanitize_text_for_display(text)
                        if filtered_text:
                            self._stream_has_text = True
                            # Route to subagent handler if in subagent context
                            if self._current_parent_tool_use_id and \
                               self._current_parent_tool_use_id in self._active_subagents:
                                self.tracer.on_subagent_message(
                                    task_id=self._current_parent_tool_use_id,
                                    text=filtered_text,
                                    is_partial=True
                                )
                            else:
                                self.tracer.on_message(filtered_text, is_partial=True)
                elif delta_type == "thinking_delta":
                    # Streaming thinking content - accumulate in buffer
                    thinking_text = delta.get("thinking", "")
                    if thinking_text:
                        self._stream_thinking_buffer += thinking_text
                        # Throttle emissions to once per second to reduce UI blinking
                        current_time = time.time()
                        if current_time - self._thinking_last_emit_time >= self._thinking_emit_interval:
                            self._thinking_last_emit_time = current_time
                            # Show last 300 chars as preview
                            preview = self._stream_thinking_buffer[-300:]
                            self.tracer.on_thinking(preview, is_partial=True)
        elif event_type == "content_block_stop":
            # Check if this is the end of a thinking block
            if self._stream_thinking_active:
                self._stream_thinking_active = False
                # Emit final thinking event with is_complete flag
                # The full thinking is in _stream_thinking_buffer
                self._stream_thinking_buffer = ""

        if isinstance(usage, dict):
            self._apply_usage_update(usage)

    def _apply_usage_update(self, usage: dict[str, Any], is_final: bool = False) -> None:
        """
        Apply usage update from stream events or final result.

        Stream events report per-message usage which we track to get the max
        for the current message. When is_final=True (from ResultMessage), we
        accumulate the final values to our running totals.

        Args:
            usage: Usage dict with token counts.
            is_final: If True, accumulate to totals. If False, track current message max.
        """
        def safe_int(value: Optional[int]) -> int:
            if value is None:
                return 0
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        input_tokens = safe_int(usage.get("input_tokens"))
        output_tokens = safe_int(usage.get("output_tokens"))
        cache_creation = safe_int(usage.get("cache_creation_input_tokens"))
        cache_read = safe_int(usage.get("cache_read_input_tokens"))

        if is_final:
            # Final result: accumulate to running totals
            self._metrics_input_tokens += input_tokens
            self._metrics_output_tokens += output_tokens
            self._metrics_cache_creation_tokens += cache_creation
            self._metrics_cache_read_tokens += cache_read
        else:
            # Stream event: track max for current message (will be finalized later)
            self._current_msg_input_tokens = max(
                self._current_msg_input_tokens, input_tokens
            )
            self._current_msg_output_tokens = max(
                self._current_msg_output_tokens, output_tokens
            )
            self._current_msg_cache_creation = max(
                self._current_msg_cache_creation, cache_creation
            )
            self._current_msg_cache_read = max(
                self._current_msg_cache_read, cache_read
            )

        # Update cost from API if provided
        cost_value = usage.get("total_cost_usd") or usage.get("cost_usd")
        if cost_value is not None:
            try:
                new_cost = float(cost_value)
                if is_final:
                    # Accumulate cost
                    if self._metrics_cost_usd is None:
                        self._metrics_cost_usd = new_cost
                    else:
                        self._metrics_cost_usd += new_cost
                else:
                    # Track current message cost (will be accumulated on final)
                    self._current_msg_cost = new_cost
            except (TypeError, ValueError):
                pass

        # If no API-provided cost, estimate it
        if self._metrics_cost_usd is None:
            estimated = self._estimate_cost_usd()
            if estimated is not None:
                self._metrics_cost_usd = estimated

        self._emit_metrics_update()

    def _estimate_cost_usd(self) -> Optional[float]:
        """
        Estimate cost based on token counts and model pricing.

        Pricing is per million tokens: (input_rate, output_rate)
        Cache tokens are charged at reduced rates but we simplify by
        treating them as input tokens for estimation.
        """
        if not self._model:
            return None

        # Pricing per million tokens: (input_rate, output_rate)
        # Claude 4.5 models
        pricing_map: dict[str, tuple[float, float]] = {
            # Claude 4.5 (latest)
            "claude-opus-4-5-20251101": (15.0, 75.0),
            "claude-sonnet-4-5-20250929": (3.0, 15.0),
            "claude-haiku-4-5-20251001": (0.80, 4.0),
            # Claude 4 models
            "claude-sonnet-4-20250514": (3.0, 15.0),
            "claude-opus-4-20250514": (15.0, 75.0),
            # Claude 3.x models
            "claude-3-7-sonnet-20250219": (3.0, 15.0),
            "claude-3-5-sonnet-20241022": (3.0, 15.0),
            "claude-3-5-haiku-20241022": (0.80, 4.0),
            "claude-3-opus-20240229": (15.0, 75.0),
            "claude-3-sonnet-20240229": (3.0, 15.0),
            "claude-3-haiku-20240307": (0.25, 1.25),
        }

        rates = pricing_map.get(self._model)
        if not rates:
            # Try to match by model family for unknown versions
            model_lower = self._model.lower()
            if "opus-4-5" in model_lower or "opus-4.5" in model_lower:
                rates = (15.0, 75.0)
            elif "sonnet-4-5" in model_lower or "sonnet-4.5" in model_lower:
                rates = (3.0, 15.0)
            elif "haiku-4-5" in model_lower or "haiku-4.5" in model_lower:
                rates = (0.80, 4.0)
            elif "opus" in model_lower:
                rates = (15.0, 75.0)
            elif "sonnet" in model_lower:
                rates = (3.0, 15.0)
            elif "haiku" in model_lower:
                rates = (0.80, 4.0)
            else:
                return None

        input_rate, output_rate = rates
        # Include current message tokens for real-time estimation
        total_input = (
            self._metrics_input_tokens
            + self._current_msg_input_tokens
            + self._metrics_cache_creation_tokens
            + self._current_msg_cache_creation
            + self._metrics_cache_read_tokens
            + self._current_msg_cache_read
        )
        total_output = self._metrics_output_tokens + self._current_msg_output_tokens
        return (total_input / 1_000_000) * input_rate + (
            total_output / 1_000_000
        ) * output_rate

    def _emit_metrics_update(self) -> None:
        """
        Emit current metrics to the tracer.

        Shows accumulated totals plus current message progress for real-time display.
        """
        # Include current message progress for real-time display
        display_input = self._metrics_input_tokens + self._current_msg_input_tokens
        display_output = self._metrics_output_tokens + self._current_msg_output_tokens
        display_cache_creation = (
            self._metrics_cache_creation_tokens + self._current_msg_cache_creation
        )
        display_cache_read = (
            self._metrics_cache_read_tokens + self._current_msg_cache_read
        )

        # Calculate display cost including current message
        display_cost = self._metrics_cost_usd
        if self._current_msg_cost is not None:
            if display_cost is None:
                display_cost = self._current_msg_cost
            else:
                display_cost = display_cost + self._current_msg_cost

        snapshot = (
            display_input,
            display_output,
            display_cache_creation,
            display_cache_read,
            self._metrics_turns,
            display_cost,
        )
        if snapshot == self._last_metrics_snapshot:
            return

        self._last_metrics_snapshot = snapshot
        payload: dict[str, Any] = {
            "tokens_in": display_input,
            "tokens_out": display_output,
            "cache_creation_input_tokens": display_cache_creation,
            "cache_read_input_tokens": display_cache_read,
            "turns": self._metrics_turns,
        }
        if display_cost is not None:
            payload["total_cost_usd"] = display_cost
        if self._model:
            payload["model"] = self._model

        self.tracer.on_metrics_update(payload)

    def _handle_unknown_message(self, message: Any) -> None:
        """Handle unknown message types including SDK summary messages."""
        import logging
        logger = logging.getLogger(__name__)

        # Check if this is an SDK summary message with complete tool content
        # These have 'content' list but are not AssistantMessage instances
        if hasattr(message, 'content') and isinstance(message.content, list):
            logger.debug(f"Processing SDK summary message with {len(message.content)} content blocks")
            for block in message.content:
                if isinstance(block, dict):
                    self._process_dict_block(block)
            return

        if hasattr(message, '__dict__'):
            # Try to extract content from __dict__ for SDK summary messages
            msg_dict = message.__dict__
            if 'content' in msg_dict and isinstance(msg_dict['content'], list):
                logger.debug(f"Processing SDK summary message (from __dict__) with {len(msg_dict['content'])} content blocks")
                for block in msg_dict['content']:
                    if isinstance(block, dict):
                        self._process_dict_block(block)
                return
            self.tracer.on_message(f"[UNKNOWN] {type(message).__name__}")


def create_trace_hooks(
    tracer: TracerBase,
    trace_permissions: bool = False
) -> dict[str, list[HookMatcher]]:
    """
    Create SDK hook configuration for tracing.

    This creates hook matchers that integrate with the SDK's
    native hook system for PreToolUse and PostToolUse events.

    Args:
        tracer: The tracer to dispatch events to.
        trace_permissions: Also trace permission decisions.

    Returns:
        Hook configuration dict for ClaudeAgentOptions.
    """

    async def pre_tool_hook(
        hook_input: PreToolUseHookInput,
        transcript_path: Optional[str],
        context: HookContext
    ) -> dict[str, Any]:
        """Hook called before tool execution."""
        tracer.on_tool_start(
            tool_name=hook_input["tool_name"],
            tool_input=hook_input["tool_input"],
            tool_id=hook_input.get("session_id", "hook")
        )
        return {}  # Allow execution to continue

    async def post_tool_hook(
        hook_input: PostToolUseHookInput,
        transcript_path: Optional[str],
        context: HookContext
    ) -> dict[str, Any]:
        """Hook called after tool execution."""
        tracer.on_tool_complete(
            tool_name=hook_input["tool_name"],
            tool_id=hook_input.get("session_id", "hook"),
            result=hook_input.get("tool_response", ""),
            duration_ms=0,
            is_error="error" in str(hook_input.get("tool_response", "")).lower()
        )
        return {}

    async def stop_hook(
        hook_input: StopHookInput,
        transcript_path: Optional[str],
        context: HookContext
    ) -> dict[str, Any]:
        """Hook called when agent stops."""
        # Signal tracer that agent is stopping
        if hasattr(tracer, 'on_system_event'):
            tracer.on_system_event("stop", {"active": hook_input["stop_hook_active"]})
        return {}

    hooks: dict[str, list[HookMatcher]] = {
        "PreToolUse": [
            HookMatcher(
                matcher=None,  # Match all tools
                hooks=[pre_tool_hook],
                timeout=30.0,
            )
        ],
        "PostToolUse": [
            HookMatcher(
                matcher=None,
                hooks=[post_tool_hook],
                timeout=30.0,
            )
        ],
        "Stop": [
            HookMatcher(
                matcher=None,
                hooks=[stop_hook],
                timeout=10.0,
            )
        ],
    }

    return hooks


def create_stderr_callback(tracer: TracerBase):
    """
    Create a stderr callback that traces CLI errors.

    Args:
        tracer: The tracer to dispatch events to.

    Returns:
        Callback for ClaudeAgentOptions.stderr.
    """
    def stderr_callback(text: str) -> None:
        if text.strip():
            tracer.on_error(text.strip(), error_type="stderr")

    return stderr_callback
