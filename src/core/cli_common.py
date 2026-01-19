"""
Shared CLI argument parsing utilities for Ag3ntum.

Provides common argument definitions and parser building blocks for both
CLI (agent_cli.py) and HTTP client (agent_http.py) entry points.

Usage:
    from .cli_common import add_common_arguments, add_task_arguments

    parser = argparse.ArgumentParser()
    add_task_arguments(parser)
    add_common_arguments(parser)
"""
import argparse
from typing import Any

from .constants import DEFAULT_POLL_INTERVAL


# =============================================================================
# Argument Group Builders
# =============================================================================

def add_task_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add task specification arguments to parser.

    These are mutually exclusive: --task or --task-file.

    Args:
        parser: ArgumentParser to add arguments to.
    """
    task_group = parser.add_mutually_exclusive_group()
    task_group.add_argument(
        "--task", "-t",
        type=str,
        help="Task description to execute"
    )
    task_group.add_argument(
        "--task-file", "-f",
        type=str,
        help="Path to file containing task description"
    )


def add_directory_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add working directory arguments to parser.

    Args:
        parser: ArgumentParser to add arguments to.
    """
    parser.add_argument(
        "--dir", "-d",
        type=str,
        help="Working directory for the agent"
    )
    parser.add_argument(
        "--add-dir", "-a",
        action="append",
        default=[],
        help="Additional directories the agent can access (can be repeated)"
    )


def add_session_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add session management arguments to parser.

    Args:
        parser: ArgumentParser to add arguments to.
    """
    parser.add_argument(
        "--resume", "-r",
        type=str,
        metavar="SESSION_ID",
        help="Resume a previous session by ID"
    )
    parser.add_argument(
        "--fork-session",
        action="store_true",
        help="Fork to new session when resuming instead of continuing original"
    )
    parser.add_argument(
        "--list-sessions", "-l",
        action="store_true",
        help="List all sessions and exit"
    )


def add_config_override_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add agent configuration override arguments to parser.

    These allow overriding values from agent.yaml via CLI.

    Args:
        parser: ArgumentParser to add arguments to.
    """
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=None,
        help="Claude model to use (overrides agent.yaml)"
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Maximum number of turns (overrides agent.yaml)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Timeout in seconds (overrides agent.yaml)"
    )
    parser.add_argument(
        "--no-skills",
        action="store_true",
        help="Disable custom skills (overrides agent.yaml)"
    )


def add_permission_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add permission configuration arguments to parser.

    Args:
        parser: ArgumentParser to add arguments to.
    """
    parser.add_argument(
        "--profile",
        type=str,
        metavar="PATH",
        help="Path to permission profile file (YAML or JSON)"
    )
    parser.add_argument(
        "--permission-mode",
        type=str,
        choices=["default", "acceptEdits", "plan", "bypassPermissions"],
        default=None,
        help="Permission mode to use (overrides config file)"
    )


def add_output_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add output-related arguments to parser.

    Args:
        parser: ArgumentParser to add arguments to.
    """
    parser.add_argument(
        "--output", "-o",
        type=str,
        help="Output file for results (default: stdout)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )


def add_logging_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add logging configuration arguments to parser.

    Args:
        parser: ArgumentParser to add arguments to.
    """
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)"
    )


def add_role_argument(parser: argparse.ArgumentParser) -> None:
    """
    Add role selection argument to parser.

    Args:
        parser: ArgumentParser to add arguments to.
    """
    parser.add_argument(
        "--role",
        type=str,
        default=None,
        help="Role template name (loads prompts/roles/<role>.md)"
    )


# =============================================================================
# HTTP Client Specific Arguments
# =============================================================================

def add_http_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add HTTP client-specific arguments to parser.

    Args:
        parser: ArgumentParser to add arguments to.
    """
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help=f"Polling interval in seconds (default: {DEFAULT_POLL_INTERVAL})"
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Don't wait for task completion, just start and exit"
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default=None,
        help="Override API base URL (default: from config/api.yaml)"
    )


# =============================================================================
# CLI Specific Arguments
# =============================================================================

def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
    """
    Add CLI-specific arguments to parser.

    Args:
        parser: ArgumentParser to add arguments to.
    """
    # Config file path arguments
    parser.add_argument(
        "--config", "-c",
        type=str,
        metavar="PATH",
        help="Path to agent.yaml configuration file (default: config/agent.yaml)"
    )
    parser.add_argument(
        "--secrets",
        type=str,
        metavar="PATH",
        help="Path to secrets.yaml file (default: config/secrets.yaml)"
    )

    # Universal configuration overrides
    parser.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        default=[],
        help="""
Override any agent.yaml value using dot notation.
Can be repeated. Examples:
  --set agent.model=claude-sonnet-4-5-20250929
  --set agent.max_turns=50
  --set agent.permission_mode=acceptEdits"""
    )

    # Legacy permissions config
    parser.add_argument(
        "--permissions-config", "-p",
        type=str,
        metavar="PATH",
        help="Path to legacy permissions.json configuration file"
    )

    # Special commands
    parser.add_argument(
        "--show-tools",
        action="store_true",
        help="Show all available tools and their descriptions"
    )
    parser.add_argument(
        "--show-permissions",
        action="store_true",
        help="Show current permission configuration"
    )
    parser.add_argument(
        "--init-permissions",
        action="store_true",
        help="Create default permissions.json in AGENT/config/"
    )
    parser.add_argument(
        "--check-profile",
        action="store_true",
        help="""
Validate that permission profile file exists in AGENT/config/:
  - permissions.yaml
Supports .yaml, .yml, and .json formats."""
    )
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize .claude settings in working directory"
    )


# =============================================================================
# Parser Builders
# =============================================================================

def create_common_parser(
    description: str,
    epilog: str = "",
) -> argparse.ArgumentParser:
    """
    Create a parser with common formatting settings.

    Args:
        description: Parser description.
        epilog: Parser epilog (examples).

    Returns:
        Configured ArgumentParser.
    """
    return argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )


# =============================================================================
# Override Parsing Utilities
# =============================================================================

def parse_set_overrides(set_args: list[str]) -> dict[str, Any]:
    """
    Parse --set KEY=VALUE arguments into a dictionary of overrides.

    Args:
        set_args: List of "key=value" strings from --set arguments.

    Returns:
        Dictionary mapping keys to values. Nested keys use dot notation
        (e.g., "agent.model" becomes {"model": value}).

    Raises:
        ValueError: If a --set argument is malformed.
    """
    overrides: dict[str, Any] = {}

    for arg in set_args:
        if "=" not in arg:
            raise ValueError(
                f"Invalid --set argument: '{arg}'\n"
                f"Expected format: KEY=VALUE (e.g., agent.model=claude-sonnet-4-5)"
            )

        key, value = arg.split("=", 1)
        key = key.strip()
        value_str = value.strip()

        # Remove "agent." prefix if present (for consistency)
        if key.startswith("agent."):
            key = key[6:]

        # Type conversion for known fields
        typed_value: Any = value_str

        if key in ("max_turns", "timeout_seconds", "max_buffer_size"):
            try:
                typed_value = int(value_str)
            except ValueError:
                raise ValueError(
                    f"Invalid value for {key}: '{value_str}' (expected integer)"
                )
        elif key in ("enable_skills", "enable_file_checkpointing", "include_partial_messages"):
            typed_value = value_str.lower() in ("true", "1", "yes", "on")
        elif value_str.lower() in ("null", "none"):
            typed_value = None

        overrides[key] = typed_value

    return overrides
