"""
Conversation session management for Ag3ntum.

Provides a ConversationSession class that wraps ClaudeSDKClient for
multi-turn interactive conversations with session continuity.

Usage:
    async with ConversationSession(options) as session:
        # First query
        response = await session.query("Analyze this codebase")

        # Follow-up with context preserved
        response = await session.query("What patterns did you find?")

        # Continue conversation...
        response = await session.query("Refactor the auth module")
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, Callable, Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

from .schemas import SessionInfo, TaskStatus, TokenUsage

logger = logging.getLogger(__name__)


@dataclass
class ConversationTurn:
    """
    Represents a single turn in a conversation.

    A turn consists of a user prompt and the assistant's response,
    along with metadata about tool usage and timing.
    """
    turn_number: int
    prompt: str
    response_text: str = ""
    tools_used: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    duration_ms: int = 0
    is_error: bool = False
    error_message: Optional[str] = None


@dataclass
class ConversationMetrics:
    """
    Accumulated metrics for a conversation session.

    Terminology:
    - conversation_turns: High-level user↔agent exchanges (prompts/responses)
    - agent_turns: Low-level SDK turns (tool calls, reasoning loops within a response)
    """
    conversation_turns: int = 0  # User prompt → agent response count
    agent_turns: int = 0  # SDK num_turns (tool calls, internal loops)
    total_duration_ms: int = 0
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    def add_result(self, result: ResultMessage) -> None:
        """
        Add metrics from a ResultMessage.

        Args:
            result: The ResultMessage from SDK.
        """
        self.conversation_turns += 1  # Each ResultMessage = one conversation turn
        self.agent_turns += result.num_turns  # SDK's internal turn count
        self.total_duration_ms += result.duration_ms
        if result.total_cost_usd:
            self.total_cost_usd += result.total_cost_usd

        if result.usage:
            self.input_tokens += result.usage.get("input_tokens", 0)
            self.output_tokens += result.usage.get("output_tokens", 0)
            self.cache_read_tokens += result.usage.get(
                "cache_read_input_tokens", 0
            )
            self.cache_creation_tokens += result.usage.get(
                "cache_creation_input_tokens", 0
            )
            self.total_tokens = (
                self.input_tokens
                + self.output_tokens
                + self.cache_read_tokens
                + self.cache_creation_tokens
            )

    def to_token_usage(self) -> TokenUsage:
        """Convert to TokenUsage schema."""
        return TokenUsage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_input_tokens=self.cache_read_tokens,
            cache_creation_input_tokens=self.cache_creation_tokens,
        )


class ConversationSession:
    """
    Manages a multi-turn conversation session with Claude.

    Wraps ClaudeSDKClient to provide:
    - Session continuity across multiple exchanges
    - Turn tracking and metrics accumulation
    - Interrupt support for long-running tasks
    - Message processing with callbacks

    Usage:
        options = ClaudeAgentOptions(
            allowed_tools=["Read", "Write", "Bash"],
            permission_mode="acceptEdits"
        )

        async with ConversationSession(options) as session:
            # First query
            await session.query("List all Python files")
            async for message in session.receive():
                print(message)

            # Follow-up with context
            await session.query("Now count the lines in each")
            async for message in session.receive():
                print(message)

    Args:
        options: ClaudeAgentOptions for configuring the client.
        on_message: Optional callback for each message received.
        on_tool_start: Optional callback when a tool starts.
        on_tool_complete: Optional callback when a tool completes.
        on_turn_complete: Optional callback when a turn completes.
    """

    def __init__(
        self,
        options: Optional[ClaudeAgentOptions] = None,
        on_message: Optional[Callable[[Any], None]] = None,
        on_tool_start: Optional[Callable[[str, dict, str], None]] = None,
        on_tool_complete: Optional[Callable[[str, str, Any, bool], None]] = None,
        on_turn_complete: Optional[Callable[[ConversationTurn], None]] = None,
    ) -> None:
        """
        Initialize the conversation session.

        Args:
            options: ClaudeAgentOptions for the SDK client.
            on_message: Callback for each message (message) -> None.
            on_tool_start: Callback for tool start (name, input, id) -> None.
            on_tool_complete: Callback for tool end (name, id, result, is_error) -> None.
            on_turn_complete: Callback when a turn completes (turn) -> None.
        """
        self._options = options or ClaudeAgentOptions()
        self._client: Optional[ClaudeSDKClient] = None
        self._connected = False
        self._session_id: Optional[str] = None

        # Callbacks
        self._on_message = on_message
        self._on_tool_start = on_tool_start
        self._on_tool_complete = on_tool_complete
        self._on_turn_complete = on_turn_complete

        # Conversation state
        self._turns: list[ConversationTurn] = []
        self._current_turn: Optional[ConversationTurn] = None
        self._metrics = ConversationMetrics()
        self._last_result: Optional[ResultMessage] = None

        # Interrupt handling
        self._interrupted = False

    @property
    def session_id(self) -> Optional[str]:
        """Get the current session ID."""
        return self._session_id

    @property
    def is_connected(self) -> bool:
        """Check if the session is connected."""
        return self._connected

    @property
    def turn_count(self) -> int:
        """Get the number of completed turns."""
        return len(self._turns)

    @property
    def metrics(self) -> ConversationMetrics:
        """Get accumulated metrics."""
        return self._metrics

    @property
    def turns(self) -> list[ConversationTurn]:
        """Get all completed turns."""
        return self._turns.copy()

    @property
    def last_result(self) -> Optional[ResultMessage]:
        """Get the last ResultMessage from the SDK."""
        return self._last_result

    @property
    def was_interrupted(self) -> bool:
        """Check if the session was interrupted."""
        return self._interrupted

    async def connect(self, initial_prompt: Optional[str] = None) -> None:
        """
        Connect to Claude and optionally send an initial prompt.

        Args:
            initial_prompt: Optional first prompt to send immediately.

        Raises:
            RuntimeError: If connection fails.
        """
        if self._connected:
            logger.warning("Already connected, ignoring connect() call")
            return

        self._client = ClaudeSDKClient(options=self._options)
        try:
            await self._client.connect(initial_prompt)
            self._connected = True
            logger.info("ConversationSession connected")
        except Exception as e:
            # Clean up client on connection failure
            self._client = None
            self._connected = False
            logger.error(f"Failed to connect: {e}")
            raise RuntimeError(f"Failed to connect to Claude: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from Claude and clean up."""
        if not self._connected or not self._client:
            return

        try:
            await self._client.disconnect()
        except Exception as e:
            logger.warning(f"Error during disconnect: {e}")
        finally:
            self._connected = False
            self._client = None
            logger.info(
                f"ConversationSession disconnected after {self.turn_count} turns"
            )

    async def query(self, prompt: str) -> None:
        """
        Send a query to Claude.

        This starts a new turn in the conversation. Use receive()
        to get the response messages.

        Args:
            prompt: The user prompt to send.

        Raises:
            RuntimeError: If not connected.
        """
        if not self._connected or not self._client:
            raise RuntimeError(
                "Not connected. Call connect() or use 'async with' context."
            )

        # Start new turn
        self._current_turn = ConversationTurn(
            turn_number=len(self._turns) + 1,
            prompt=prompt,
            started_at=datetime.now(),
        )
        self._interrupted = False

        logger.debug(f"Starting turn {self._current_turn.turn_number}: {prompt[:50]}...")

        await self._client.query(prompt)

    async def receive(self) -> AsyncIterator[Any]:
        """
        Receive messages from the current query.

        Yields messages until a ResultMessage is received,
        which indicates the query is complete.

        Yields:
            Messages from Claude (AssistantMessage, SystemMessage, etc.)

        Note:
            If an exception occurs during message processing, the current
            turn is marked as an error and stored for debugging.
        """
        if not self._connected or not self._client:
            raise RuntimeError("Not connected")

        if not self._current_turn:
            raise RuntimeError("No active query. Call query() first.")

        response_parts: list[str] = []

        try:
            async for message in self._client.receive_response():
                # Process message for turn tracking
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            response_parts.append(block.text)
                        elif isinstance(block, ToolUseBlock):
                            self._current_turn.tools_used.append(block.name)
                            if self._on_tool_start:
                                self._on_tool_start(block.name, block.input, block.id)
                        elif isinstance(block, ToolResultBlock):
                            is_error = block.is_error or False
                            if self._on_tool_complete:
                                self._on_tool_complete(
                                    "unknown",  # Tool name not in result block
                                    block.tool_use_id,
                                    block.content,
                                    is_error
                                )

                elif isinstance(message, SystemMessage):
                    # Extract session ID from init message
                    if message.subtype == "init":
                        self._session_id = message.data.get("session_id")
                        logger.debug(f"Session ID: {self._session_id}")

                elif isinstance(message, ResultMessage):
                    # Turn complete - process and terminate
                    self._last_result = message
                    self._current_turn.completed_at = datetime.now()
                    self._current_turn.duration_ms = message.duration_ms
                    self._current_turn.response_text = "".join(response_parts)
                    self._current_turn.is_error = message.is_error

                    if message.is_error:
                        self._current_turn.error_message = message.result

                    # Update metrics
                    self._metrics.add_result(message)

                    # Store completed turn
                    self._turns.append(self._current_turn)

                    if self._on_turn_complete:
                        self._on_turn_complete(self._current_turn)

                    self._current_turn = None
                    logger.debug(
                        f"Turn complete: {message.num_turns} SDK turns, "
                        f"{message.duration_ms}ms"
                    )

                    # Invoke callback and yield final message
                    if self._on_message:
                        self._on_message(message)
                    yield message

                    # Terminate generator - ResultMessage signals query completion
                    return

                # For non-terminal messages: invoke callback and yield
                if self._on_message:
                    self._on_message(message)
                yield message

        except Exception as e:
            # Handle exception during message iteration
            logger.error(f"Error during receive: {e}")

            if self._current_turn:
                # Mark turn as error and store it
                self._current_turn.completed_at = datetime.now()
                self._current_turn.is_error = True
                self._current_turn.error_message = str(e)
                self._current_turn.response_text = "".join(response_parts)
                self._turns.append(self._current_turn)
                self._current_turn = None

            raise

    async def interrupt(self) -> None:
        """
        Interrupt the current query.

        This sends a signal to stop Claude mid-execution.
        Use this for long-running tasks that need to be cancelled.
        """
        if not self._connected or not self._client:
            logger.warning("Cannot interrupt: not connected")
            return

        self._interrupted = True
        await self._client.interrupt()
        logger.info("Sent interrupt signal")

    async def query_and_receive(self, prompt: str) -> ResultMessage:
        """
        Convenience method to send a query and collect all messages.

        Args:
            prompt: The user prompt to send.

        Returns:
            The final ResultMessage.

        Raises:
            RuntimeError: If no ResultMessage is received.
        """
        await self.query(prompt)

        async for message in self.receive():
            if isinstance(message, ResultMessage):
                return message

        raise RuntimeError("No ResultMessage received")

    def get_session_info(self, working_dir: str) -> SessionInfo:
        """
        Create a SessionInfo from current conversation state.

        Args:
            working_dir: The working directory for the session.

        Returns:
            SessionInfo with accumulated metrics.
        """
        return SessionInfo(
            session_id=self._session_id or "unknown",
            working_dir=working_dir,
            status=TaskStatus.COMPLETE if not self._interrupted else TaskStatus.FAILED,
            resume_id=self._session_id,
            num_turns=self._metrics.agent_turns,  # SDK turn count
            duration_ms=self._metrics.total_duration_ms,
            total_cost_usd=self._metrics.total_cost_usd or None,
            cumulative_turns=self._metrics.agent_turns,
            cumulative_duration_ms=self._metrics.total_duration_ms,
            cumulative_cost_usd=self._metrics.total_cost_usd,
            cumulative_usage=self._metrics.to_token_usage(),
        )

    async def __aenter__(self) -> "ConversationSession":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.disconnect()
