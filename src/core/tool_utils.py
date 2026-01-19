"""
Shared utility functions for tool permission handling.

This module contains common functions used by both hooks.py and permissions.py
for building tool call strings and actionable denial messages.
"""
import sys
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class PermissionManagerProtocol(Protocol):
    """Protocol for permission managers that support pattern lookup."""

    def get_allowed_patterns_for_tool(self, tool_name: str) -> list[str]:
        """Get allowed patterns for a specific tool."""
        ...


# Tool parameter mappings for building tool call strings
TOOL_PARAM_MAP: dict[str, tuple[str, ...]] = {
    "Read": ("file_path", "path"),
    "Write": ("file_path", "path"),
    "Edit": ("file_path", "path"),
    "MultiEdit": ("file_path", "path"),
    "Glob": ("path", "cwd"),
    "Grep": ("path", "file"),
    "LS": ("path", "dir"),
    "Bash": ("command",),
    "Ag3ntumBash": ("command",),  # MCP tool: mcp__ag3ntum__Bash
    "WebFetch": ("url",),
    "WebSearch": ("query",),
}


def normalize_tool_name(tool_name: str) -> str:
    """
    Normalize MCP tool names to their permission-matching format.

    Converts MCP tool prefixes to the format used in permission patterns:
    - mcp__ag3ntum__Bash -> Ag3ntumBash
    - mcp__ag3ntum__Read -> Ag3ntumRead

    Standard SDK tool names are returned unchanged.

    Args:
        tool_name: Raw tool name from SDK.

    Returns:
        Normalized tool name for permission matching.
    """
    prefix = "mcp__ag3ntum__"
    if tool_name.startswith(prefix):
        suffix = tool_name[len(prefix):]
        return f"Ag3ntum{suffix}"
    return tool_name


def build_tool_call_string(tool_name: str, tool_input: dict[str, Any]) -> str:
    """
    Build a tool call string for permission matching.

    Constructs a standardized string representation of a tool call
    suitable for pattern matching against permission rules.

    MCP tool names are normalized (e.g., mcp__ag3ntum__Bash -> Ag3ntumBash).

    Args:
        tool_name: Name of the tool being called.
        tool_input: Input parameters for the tool.

    Returns:
        Formatted tool call string, e.g., "Read(./input/task.md)" or "Ag3ntumBash(ls -la)".

    Examples:
        >>> build_tool_call_string("Read", {"file_path": "./input/task.md"})
        'Read(./input/task.md)'
        >>> build_tool_call_string("Bash", {"command": "ls -la"})
        'Bash(ls -la)'
        >>> build_tool_call_string("mcp__ag3ntum__Bash", {"command": "ls -la"})
        'Ag3ntumBash(ls -la)'
    """
    # Normalize MCP tool names for permission matching
    normalized_name = normalize_tool_name(tool_name)

    if normalized_name in TOOL_PARAM_MAP:
        param_keys = TOOL_PARAM_MAP[normalized_name]
        value = ""
        for key in param_keys:
            if key in tool_input:
                value = tool_input[key]
                break
        return f"{normalized_name}({value})"
    return normalized_name


def build_actionable_denial_message(
    tool_name: str,
    tool_input: dict[str, Any],
    is_final_denial: bool,
    permission_manager: Any = None,
    max_patterns_shown: int = 5,
    max_value_length: int = 50,
) -> str:
    """
    Build an actionable denial message with allowed patterns.

    Instead of just saying "denied", provides concrete guidance on
    what patterns the agent CAN use for this tool.

    Args:
        tool_name: The tool that was denied.
        tool_input: The input that was attempted.
        is_final_denial: If True, this is the last chance before interrupt.
        permission_manager: Optional permission manager to get allowed patterns.
        max_patterns_shown: Maximum number of allowed patterns to show.
        max_value_length: Maximum length of displayed values before truncation.

    Returns:
        Actionable message with allowed patterns and optional interrupt warning.
    """
    # Normalize MCP tool names for consistent messaging
    normalized_name = normalize_tool_name(tool_name)

    # Get allowed patterns for this tool from the permission manager
    allowed_patterns: list[str] = []
    if permission_manager is not None:
        if hasattr(permission_manager, 'get_allowed_patterns_for_tool'):
            allowed_patterns = permission_manager.get_allowed_patterns_for_tool(normalized_name)

    # Build the base denial message
    if normalized_name in ("Bash", "Ag3ntumBash"):
        command = tool_input.get("command", "")
        truncated = command[:max_value_length] + "..." if len(command) > max_value_length else command
        base_msg = f"{normalized_name} command '{truncated}' is not permitted."
    else:
        path = tool_input.get("file_path", tool_input.get("path", ""))
        truncated = path[:max_value_length] if len(path) > max_value_length else path
        base_msg = f"{normalized_name} for '{truncated}' is not permitted."

    # Add guidance about what IS allowed
    if allowed_patterns:
        patterns_str = ", ".join(f"'{p}'" for p in allowed_patterns[:max_patterns_shown])
        guidance = f" Allowed patterns for {normalized_name}: {patterns_str}."
    else:
        guidance = f" No {normalized_name} operations are allowed in this security context."

    # Add interrupt warning if this is final denial
    if is_final_denial:
        warning = " FINAL WARNING: Agent will be stopped if this tool is denied again."
    else:
        warning = ""

    return base_msg + guidance + warning


def extract_patterns_for_tool(
    tool_name: str,
    permission_list: list[str],
) -> list[str]:
    """
    Extract patterns for a specific tool from a permission list.

    Parses patterns like "Read(./input/**)" or "Bash(python *)" to extract
    the inner pattern part, or returns "*" for bare tool names.

    Args:
        tool_name: Name of the tool to extract patterns for.
        permission_list: List of permission patterns (allow or deny list).

    Returns:
        List of extracted patterns for the tool.

    Examples:
        >>> extract_patterns_for_tool("Read", ["Read(./input/**)", "Write"])
        ['./input/**']
        >>> extract_patterns_for_tool("Bash", ["Bash"])
        ['*']
    """
    patterns = []
    prefix = f"{tool_name}("

    for pattern in permission_list:
        if pattern.startswith(prefix):
            # Extract the pattern inside parentheses
            inner = pattern[len(prefix):-1] if pattern.endswith(")") else pattern[len(prefix):]
            patterns.append(inner)
        elif pattern == tool_name:
            # Tool name without parentheses means all uses are allowed
            patterns.append("*")

    return patterns


def build_script_command(
    script_path: Path,
    args: Optional[list[str]] = None,
) -> list[str]:
    """
    Build a command list for executing a script.

    Determines the appropriate interpreter based on file extension and
    constructs the full command with any additional arguments.

    Args:
        script_path: Path to the script file.
        args: Optional additional arguments to pass to the script.

    Returns:
        Command list suitable for subprocess.run().

    Examples:
        >>> build_script_command(Path("script.py"))
        ['/usr/bin/python3', 'script.py']
        >>> build_script_command(Path("script.sh"), ["--verbose"])
        ['bash', 'script.sh', '--verbose']
    """
    if script_path.suffix == ".py":
        cmd = [sys.executable, str(script_path)]
    elif script_path.suffix in [".sh", ".bash"]:
        cmd = ["bash", str(script_path)]
    else:
        cmd = [str(script_path)]

    if args:
        cmd.extend(args)

    return cmd


def format_token_usage(
    input_tokens: int,
    output_tokens: int,
    cache_creation: int = 0,
    cache_read: int = 0,
    model: Optional[str] = None,
    get_context_size_fn: Optional[Any] = None,
) -> tuple[str, Optional[str]]:
    """
    Format token usage information into display strings.

    Args:
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.
        cache_creation: Number of cache creation tokens.
        cache_read: Number of cache read tokens.
        model: Optional model name for context size calculation.
        get_context_size_fn: Optional function to get model context size.

    Returns:
        Tuple of (tokens_line, cache_line) where cache_line may be None.
    """
    total_input = input_tokens + cache_creation + cache_read
    total_tokens = total_input + output_tokens

    token_parts = [f"Tokens: {total_tokens:,}"]
    token_parts.append(f"(in: {total_input:,}, out: {output_tokens:,})")

    if model and get_context_size_fn:
        context_size = get_context_size_fn(model)
        if context_size:
            context_percent = (total_input / context_size) * 100
            token_parts.append(
                f"Context: {total_input:,}/{context_size:,} ({context_percent:.1f}%)"
            )

    tokens_line = " | ".join(token_parts)

    cache_line = None
    if cache_creation > 0 or cache_read > 0:
        cache_parts = []
        if cache_creation > 0:
            cache_parts.append(f"cache_write: {cache_creation:,}")
        if cache_read > 0:
            cache_parts.append(f"cache_read: {cache_read:,}")
        cache_line = " | ".join(cache_parts)

    return tokens_line, cache_line
