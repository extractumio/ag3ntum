"""
SDK Hooks implementation for Ag3ntum.

Provides a comprehensive hooks system using Claude Agent SDK hook callbacks
for PreToolUse, PostToolUse, UserPromptSubmit, Stop, and SubagentStop events.

This module replaces the simpler can_use_tool callback approach with the
full SDK hooks system for better control and extensibility.

SECURITY NOTE: Security hooks have been simplified. The following are now
handled by other components:
- Dangerous command blocking -> Ag3ntumBash's bwrap sandbox
- Absolute path blocking -> Ag3ntumPathValidator
- Sandbox execution -> Ag3ntumBash's built-in sandbox
- Path normalization -> Ag3ntumPathValidator

Usage:
    from hooks import HooksManager, create_permission_hook, create_audit_hook

    manager = HooksManager()
    manager.add_pre_tool_hook(create_permission_hook(permission_manager))
    manager.add_post_tool_hook(create_audit_hook(log_file))

    options = ClaudeAgentOptions(
        hooks=manager.build_hooks_config()
    )
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from claude_agent_sdk import HookMatcher

from .tool_utils import build_actionable_denial_message, build_tool_call_string

logger = logging.getLogger(__name__)


# Type aliases for hook callbacks
# Signature: (input_data, tool_use_id, context) -> dict
HookCallback = Callable[
    [dict[str, Any], Optional[str], Any],
    Awaitable[dict[str, Any]]
]


@dataclass
class HookResult:
    """
    Result from a hook callback.

    Used to communicate decisions and modifications back to the SDK.
    """
    # For PreToolUse: permission decision
    permission_decision: Optional[str] = None  # "allow", "deny", "ask"
    permission_reason: Optional[str] = None

    # For input modification
    updated_input: Optional[dict[str, Any]] = None

    # For blocking/interrupting
    block: bool = False
    interrupt: bool = False

    # For adding system messages
    system_message: Optional[str] = None

    # Hook-specific output
    hook_output: Optional[dict[str, Any]] = None

    def to_sdk_response(self, hook_event: str) -> dict[str, Any]:
        """
        Convert to SDK hook response format.

        Args:
            hook_event: The hook event name (PreToolUse, PostToolUse, etc.)

        Returns:
            Dictionary in SDK hook response format.
        """
        response: dict[str, Any] = {}

        if self.block:
            response["decision"] = "block"

        if self.system_message:
            response["systemMessage"] = self.system_message

        if self.hook_output or self.permission_decision:
            hook_specific: dict[str, Any] = {"hookEventName": hook_event}

            if self.permission_decision:
                hook_specific["permissionDecision"] = self.permission_decision
            if self.permission_reason:
                hook_specific["permissionDecisionReason"] = self.permission_reason
            if self.updated_input:
                hook_specific["updatedInput"] = self.updated_input
            if self.interrupt:
                hook_specific["interrupt"] = True
            if self.hook_output:
                hook_specific.update(self.hook_output)

            response["hookSpecificOutput"] = hook_specific

        return response


@dataclass
class ToolUsageRecord:
    """
    Record of a tool usage for auditing.
    """
    tool_name: str
    tool_id: str
    input_data: dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)
    duration_ms: Optional[int] = None
    result: Optional[Any] = None
    is_error: bool = False
    permission_decision: Optional[str] = None


class HooksManager:
    """
    Manages SDK hooks for the agent.

    Provides methods to register hook callbacks and build the
    hooks configuration for ClaudeAgentOptions.

    Supports:
    - PreToolUse: Permission checking, input validation, blocking
    - PostToolUse: Audit logging, result processing, metrics
    - UserPromptSubmit: Prompt enhancement, context injection
    - Stop: Session cleanup, finalization
    - SubagentStop: Subagent result processing
    """

    def __init__(self) -> None:
        """Initialize the hooks manager."""
        # Hook callbacks by event type
        self._pre_tool_hooks: list[tuple[Optional[str], HookCallback]] = []
        self._post_tool_hooks: list[tuple[Optional[str], HookCallback]] = []
        self._user_prompt_hooks: list[HookCallback] = []
        self._stop_hooks: list[HookCallback] = []
        self._subagent_stop_hooks: list[HookCallback] = []

        # Audit trail
        self._tool_usage_records: list[ToolUsageRecord] = []

        # Tracing callback
        self._on_permission_check: Optional[Callable[[str, str], None]] = None

    def set_permission_check_callback(
        self,
        callback: Callable[[str, str], None]
    ) -> None:
        """
        Set callback for permission check notifications.

        Args:
            callback: Function called with (tool_name, decision).
        """
        self._on_permission_check = callback

    def add_pre_tool_hook(
        self,
        callback: HookCallback,
        matcher: Optional[str] = None
    ) -> None:
        """
        Add a PreToolUse hook callback.

        Args:
            callback: Async function (input_data, tool_use_id, context) -> dict
            matcher: Optional tool name pattern (e.g., "Bash", "Write|Edit")
        """
        self._pre_tool_hooks.append((matcher, callback))

    def add_post_tool_hook(
        self,
        callback: HookCallback,
        matcher: Optional[str] = None
    ) -> None:
        """
        Add a PostToolUse hook callback.

        Args:
            callback: Async function (input_data, tool_use_id, context) -> dict
            matcher: Optional tool name pattern.
        """
        self._post_tool_hooks.append((matcher, callback))

    def add_user_prompt_hook(self, callback: HookCallback) -> None:
        """
        Add a UserPromptSubmit hook callback.

        Args:
            callback: Async function (input_data, None, context) -> dict
        """
        self._user_prompt_hooks.append(callback)

    def add_stop_hook(self, callback: HookCallback) -> None:
        """
        Add a Stop hook callback.

        Args:
            callback: Async function (input_data, None, context) -> dict
        """
        self._stop_hooks.append(callback)

    def add_subagent_stop_hook(self, callback: HookCallback) -> None:
        """
        Add a SubagentStop hook callback.

        Args:
            callback: Async function (input_data, None, context) -> dict
        """
        self._subagent_stop_hooks.append(callback)

    @property
    def tool_usage_records(self) -> list[ToolUsageRecord]:
        """Get all tool usage records."""
        return self._tool_usage_records.copy()

    def clear_records(self) -> None:
        """Clear all tool usage records."""
        self._tool_usage_records.clear()

    def build_hooks_config(self) -> dict[str, list[HookMatcher]]:
        """
        Build the hooks configuration for ClaudeAgentOptions.

        Returns:
            Dictionary mapping hook events to HookMatcher lists.
        """
        config: dict[str, list[HookMatcher]] = {}

        # Build PreToolUse hooks
        if self._pre_tool_hooks:
            pre_tool_matchers: list[HookMatcher] = []
            for matcher, callback in self._pre_tool_hooks:
                pre_tool_matchers.append(
                    HookMatcher(matcher=matcher, hooks=[callback])
                )
            config["PreToolUse"] = pre_tool_matchers

        # Build PostToolUse hooks
        if self._post_tool_hooks:
            post_tool_matchers: list[HookMatcher] = []
            for matcher, callback in self._post_tool_hooks:
                post_tool_matchers.append(
                    HookMatcher(matcher=matcher, hooks=[callback])
                )
            config["PostToolUse"] = post_tool_matchers

        # Build UserPromptSubmit hooks
        if self._user_prompt_hooks:
            config["UserPromptSubmit"] = [
                HookMatcher(hooks=self._user_prompt_hooks)
            ]

        # Build Stop hooks
        if self._stop_hooks:
            config["Stop"] = [
                HookMatcher(hooks=self._stop_hooks)
            ]

        # Build SubagentStop hooks
        if self._subagent_stop_hooks:
            config["SubagentStop"] = [
                HookMatcher(hooks=self._subagent_stop_hooks)
            ]

        return config


def create_permission_hook(
    permission_manager: Any,
    on_permission_check: Optional[Callable[[str, str], None]] = None,
    denial_tracker: Optional[Any] = None,
    trace_processor: Optional[Any] = None,
    max_denials_before_interrupt: int = 3,
    system_message_builder: Optional[Callable[[str, dict[str, Any]], Optional[str]]] = None,
) -> HookCallback:
    """
    Create a PreToolUse hook for permission checking.

    This replaces the can_use_tool callback with a proper SDK hook.
    Uses smart interrupt logic and provides actionable denial messages.

    Args:
        permission_manager: Permission manager with is_allowed() method.
        on_permission_check: Optional callback for tracing decisions.
        denial_tracker: Optional tracker to record denials.
        trace_processor: Optional trace processor for status updates.
        max_denials_before_interrupt: Number of denials before interrupting.

    Returns:
        Async hook callback function.
    """
    # Track denial counts for smart interrupt
    denial_counts: dict[str, int] = {}

    async def permission_hook(
        input_data: dict[str, Any],
        tool_use_id: Optional[str],
        context: Any
    ) -> dict[str, Any]:
        """PreToolUse hook for permission checking with smart interrupt."""
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        logger.debug(f"Permission hook: {tool_name} with input: {tool_input}")

        # Security check: block sandbox bypass attempts - immediate interrupt
        if tool_input.get("dangerouslyDisableSandbox"):
            security_msg = "Security violation: sandbox bypass attempted. Agent stopped."
            logger.warning(f"SECURITY: {security_msg}")

            if on_permission_check:
                on_permission_check(tool_name, "deny")

            if denial_tracker:
                denial_tracker.record_denial(
                    tool_name=tool_name,
                    tool_call=f"{tool_name}(dangerouslyDisableSandbox=True)",
                    message=security_msg,
                    is_security_violation=True,
                    interrupt=True,
                )

            if trace_processor and hasattr(trace_processor, "set_permission_denied"):
                trace_processor.set_permission_denied(True)

            return HookResult(
                permission_decision="deny",
                permission_reason=security_msg,
                interrupt=True,
            ).to_sdk_response("PreToolUse")

        # Check permission rules
        tool_call = build_tool_call_string(tool_name, tool_input)
        allowed = permission_manager.is_allowed(tool_call)
        decision = "allow" if allowed else "deny"

        logger.debug(f"Permission check: {tool_call} -> {decision}")

        if on_permission_check:
            on_permission_check(tool_name, decision)

        if allowed:
            result = HookResult(permission_decision="allow")
            if system_message_builder is not None:
                message = system_message_builder(tool_name, tool_input)
                if message:
                    result.system_message = message
            return result.to_sdk_response("PreToolUse")

        # Denied - track denial count for smart interrupt
        denial_counts[tool_name] = denial_counts.get(tool_name, 0) + 1
        current_count = denial_counts[tool_name]
        total_denials = sum(denial_counts.values())

        should_interrupt = (
            current_count >= max_denials_before_interrupt or
            total_denials >= max_denials_before_interrupt * 2
        )
        is_penultimate = current_count == max_denials_before_interrupt - 1

        denial_msg = build_actionable_denial_message(
            tool_name=tool_name,
            tool_input=tool_input,
            is_final_denial=is_penultimate,
            permission_manager=permission_manager,
        )

        # Append interrupt reason if this is the final denial
        if should_interrupt:
            denial_msg = (
                f"TASK STOPPED: Maximum permission failures exceeded "
                f"({current_count}/{max_denials_before_interrupt} denials for {tool_name}). "
                f"Last error: {denial_msg}"
            )

        logger.info(
            f"PERMISSION DENIAL: {tool_name} denied "
            f"(count={current_count}/{max_denials_before_interrupt}, "
            f"interrupt={should_interrupt})"
        )

        if denial_tracker:
            denial_tracker.record_denial(
                tool_name=tool_name,
                tool_call=tool_call,
                message=denial_msg,
                is_security_violation=False,
                interrupt=should_interrupt,
            )

        if should_interrupt:
            if trace_processor and hasattr(trace_processor, "set_permission_denied"):
                trace_processor.set_permission_denied(True)

        result = HookResult(
            permission_decision="deny",
            permission_reason=denial_msg,
            interrupt=should_interrupt,
        )
        if system_message_builder is not None:
            message = system_message_builder(tool_name, tool_input)
            if message:
                result.system_message = message
        return result.to_sdk_response("PreToolUse")

    return permission_hook


def create_audit_hook(
    log_file: Optional[Path] = None,
    on_tool_complete: Optional[Callable[[str, str, Any, bool], None]] = None,
) -> HookCallback:
    """
    Create a PostToolUse hook for audit logging.

    Args:
        log_file: Optional file path to write audit logs.
        on_tool_complete: Optional callback for tool completion.

    Returns:
        Async hook callback function.
    """

    async def audit_hook(
        input_data: dict[str, Any],
        tool_use_id: Optional[str],
        context: Any
    ) -> dict[str, Any]:
        """PostToolUse hook for audit logging."""
        tool_name = input_data.get("tool_name", "")
        tool_result = input_data.get("tool_result", {})
        is_error = tool_result.get("is_error", False)

        timestamp = datetime.now().isoformat()
        log_entry = {
            "timestamp": timestamp,
            "tool_name": tool_name,
            "tool_use_id": tool_use_id,
            "is_error": is_error,
        }

        logger.debug(f"Audit: {tool_name} completed, error={is_error}")

        # Write to log file if specified
        if log_file:
            try:
                with log_file.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(log_entry) + "\n")
            except IOError as e:
                logger.warning(f"Failed to write audit log: {e}")

        # Call completion callback
        if on_tool_complete:
            result_content = tool_result.get("content")
            on_tool_complete(tool_name, tool_use_id or "", result_content, is_error)

        return {}  # No modifications

    return audit_hook


# Header reminder message for PostToolUse
_HEADER_REMINDER = """<system-reminder>
REMEMBER: Start your NEXT response with the structured header:
---
status: COMPLETE|PARTIAL|FAILED
error:
---
The header MUST be the first content. Do NOT write any text before it.
</system-reminder>"""


def create_header_reminder_hook(
    enable: bool = True,
    skip_tools: Optional[set[str]] = None,
) -> HookCallback:
    """
    Create a PostToolUse hook that reminds the agent to start with headers.

    This hook injects a system reminder after tool completions to reinforce
    the requirement that all responses must start with the structured header.

    Args:
        enable: Whether the reminder is enabled.
        skip_tools: Set of tool names to skip (e.g., internal tools).

    Returns:
        Async hook callback function.
    """
    _skip = skip_tools or {"TodoWrite", "AskUserQuestion"}

    async def header_reminder_hook(
        input_data: dict[str, Any],
        tool_use_id: Optional[str],
        context: Any
    ) -> dict[str, Any]:
        """PostToolUse hook to inject header reminder."""
        if not enable:
            return {}

        tool_name = input_data.get("tool_name", "")
        # Skip internal/meta tools
        if any(skip in tool_name for skip in _skip):
            return {}

        return {"systemMessage": _HEADER_REMINDER}

    return header_reminder_hook


def create_prompt_enhancement_hook(
    add_timestamp: bool = True,
    add_context: Optional[str] = None,
) -> HookCallback:
    """
    Create a UserPromptSubmit hook for prompt enhancement.

    Args:
        add_timestamp: Whether to add timestamp to prompts.
        add_context: Optional context string to prepend.

    Returns:
        Async hook callback function.
    """

    async def enhance_prompt_hook(
        input_data: dict[str, Any],
        tool_use_id: Optional[str],
        context: Any
    ) -> dict[str, Any]:
        """UserPromptSubmit hook for prompt enhancement."""
        original_prompt = input_data.get("prompt", "")

        parts = []

        if add_timestamp:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            parts.append(f"[{timestamp}]")

        if add_context:
            parts.append(add_context)

        parts.append(original_prompt)

        updated_prompt = " ".join(parts)

        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "updatedPrompt": updated_prompt,
            }
        }

    return enhance_prompt_hook


def create_stop_hook(
    on_stop: Optional[Callable[[dict[str, Any]], Awaitable[None]]] = None,
    cleanup_fn: Optional[Callable[[], Awaitable[None]]] = None,
) -> HookCallback:
    """
    Create a Stop hook for session cleanup.

    Args:
        on_stop: Optional callback with stop data.
        cleanup_fn: Optional async cleanup function.

    Returns:
        Async hook callback function.
    """

    async def stop_hook(
        input_data: dict[str, Any],
        tool_use_id: Optional[str],
        context: Any
    ) -> dict[str, Any]:
        """Stop hook for session cleanup."""
        logger.info("Stop hook triggered")

        if on_stop:
            try:
                await on_stop(input_data)
            except Exception as e:
                logger.warning(f"Error in on_stop callback: {e}")

        if cleanup_fn:
            try:
                await cleanup_fn()
            except Exception as e:
                logger.warning(f"Cleanup error in stop hook: {e}")

        return {}

    return stop_hook


def create_subagent_stop_hook(
    on_subagent_complete: Optional[Callable[[str, dict[str, Any]], Awaitable[None]]] = None,
) -> HookCallback:
    """
    Create a SubagentStop hook for subagent result processing.

    Args:
        on_subagent_complete: Optional callback with (subagent_type, result).

    Returns:
        Async hook callback function.
    """

    async def subagent_stop_hook(
        input_data: dict[str, Any],
        tool_use_id: Optional[str],
        context: Any
    ) -> dict[str, Any]:
        """SubagentStop hook for subagent processing."""
        subagent_type = input_data.get("subagent_type", "unknown")
        result = input_data.get("result", {})

        logger.debug(f"Subagent stop: {subagent_type}")

        if on_subagent_complete:
            try:
                await on_subagent_complete(subagent_type, result)
            except Exception as e:
                logger.warning(f"Error in subagent complete callback: {e}")

        return {}

    return subagent_stop_hook


# =============================================================================
# REMOVED SECURITY HOOKS (now handled by Ag3ntumPathValidator and bwrap)
# =============================================================================
#
# The following hooks have been REMOVED as security is now enforced by:
# 1. Ag3ntumBash's bwrap sandbox (OS-level filesystem isolation)
# 2. Ag3ntumPathValidator (for Python file tools like Ag3ntumRead/Write)
#
# REMOVED:
# - create_dangerous_command_hook() - bwrap sandbox prevents dangerous actions
# - create_absolute_path_block_hook() - Ag3ntumPathValidator handles this
# - create_sandbox_execution_hook() - Ag3ntumBash has built-in sandbox
# - create_path_normalization_hook() - Ag3ntumPathValidator handles translation
#
# Native Claude Code tools (Read, Write, Bash, etc.) are blocked via
# disallowed_tools in agent configuration. Only Ag3ntum tools are used.
