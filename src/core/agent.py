#!/usr/bin/env python3
"""
Ag3ntum - CLI Entry Point

A self-sufficient AI agent with tool use capabilities.

Usage:
    # Run with task from CLI
    python agent.py --task "Your task description" --dir /path/to/working/dir

    # Run with task from file
    python agent.py --task-file ./tasks/my-task.md --dir /path/to/working/dir

    # Resume a previous session
    python agent.py --resume SESSION_ID --task "Continue the task"

    # List sessions
    python agent.py --list-sessions

    # Use custom config file
    python agent.py --config ./custom-agent.yaml --task "Task"

    # Override config values via CLI
    python agent.py --model claude-sonnet-4-5-20250929 --max-turns 50 --task "Task"

    # Show available tools and permissions
    python agent.py --show-tools
"""
import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

from ..config import (
    SESSIONS_DIR,
    AgentConfigLoader,
    ConfigNotFoundError,
    ConfigValidationError,
    get_config_loader,
)
from .cli_common import (
    add_cli_arguments,
    add_config_override_arguments,
    add_directory_arguments,
    add_logging_arguments,
    add_output_arguments,
    add_permission_arguments,
    add_role_argument,
    add_session_arguments,
    add_task_arguments,
    create_common_parser,
    parse_set_overrides,
)
from .constants import AnsiColors, LOG_PREVIEW_LENGTH, StatusIcons
from .exceptions import AgentError, TaskError
from .logging_config import setup_cli_logging
from .output import format_result, print_sessions_table
from .permission_config import (
    AVAILABLE_TOOLS,
    create_default_permissions_file,
)
from .permission_profiles import (
    ProfileNotFoundError,
    validate_profile_file,
)
from .permissions import (
    create_default_settings,
    load_permissions_from_config,
)
from .schemas import TaskExecutionParams, TaskStatus
from .sessions import SessionManager
from .task_runner import execute_agent_task
from .tasks import load_task
from .tracer import ExecutionTracer

# Configure basic logging (will be reconfigured by setup_cli_logging)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent")


# =============================================================================
# Argument Parsing
# =============================================================================

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = create_common_parser(
        description="Ag3ntum - Execute tasks with AI agent",
        epilog="""
Examples:
  %(prog)s --task "List all Python files" --dir ./my-project
  %(prog)s --task-file ./tasks/my-task.md --dir ./my-project
  %(prog)s --resume 20241229_123456_abc123 --task "Continue"
  %(prog)s --list-sessions
  %(prog)s --permissions-config ./custom-permissions.json --task "Task"
  %(prog)s --show-tools
  %(prog)s --init-permissions
        """
    )

    # Add all argument groups using shared functions
    add_task_arguments(parser)
    add_directory_arguments(parser)
    add_session_arguments(parser)
    add_cli_arguments(parser)  # CLI-specific: --config, --secrets, --set, etc.
    add_config_override_arguments(parser)
    add_permission_arguments(parser)
    add_role_argument(parser)
    add_output_arguments(parser)
    add_logging_arguments(parser)

    return parser.parse_args()


# =============================================================================
# Special Commands
# =============================================================================

def list_sessions() -> None:
    """List all sessions from file-based storage and print them."""
    session_manager = SessionManager(SESSIONS_DIR)
    sessions = session_manager.list_sessions()
    print_sessions_table(sessions)


def init_settings(working_dir: Path) -> None:
    """Initialize .claude settings in working directory."""
    create_default_settings(working_dir)
    print(f"Created .claude/settings.local.json in {working_dir}")


def init_permissions() -> None:
    """Create default permissions.json in AGENT/config/."""
    config_path = create_default_permissions_file()
    print(f"Created default permissions configuration at: {config_path}")
    print("\nYou can edit this file to customize:")
    print("  - Allowed/denied tool patterns")


def check_profile() -> None:
    """Validate that permission profile file exists in AGENT/config/."""
    try:
        profile_path = validate_profile_file()
        print("Permission profile found:")
        print(f"  ✓ Profile: {profile_path}")
        print("\nCustomize with CLI options:")
        print("  --profile ./custom-permissions.yaml")
    except ProfileNotFoundError as e:
        print(f"✗ {e}")
        print("\nTo create a profile, copy templates from the repository or create manually.")
        raise SystemExit(1)
    print("  - Enabled/disabled tools")
    print("  - Permission mode")
    print("  - Hooks for dynamic permission management")


def show_tools() -> None:
    """Display all available tools with descriptions."""
    print("\n" + "=" * 70)
    print("CLAUDE CODE AVAILABLE TOOLS")
    print("=" * 70)

    # Group tools by category
    categories: dict[str, list] = {}
    for tool in AVAILABLE_TOOLS.values():
        cat = tool.category.value
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(tool)

    for category, tools in sorted(categories.items()):
        print(f"\n[{category.upper()}]")
        print("-" * 50)
        for tool in tools:
            safe_marker = " (safe)" if tool.is_safe else ""
            print(f"\n  {tool.name}{safe_marker}")
            # Clean up description - remove leading/trailing whitespace from each line
            desc_lines = tool.description.strip().split("\n")
            for line in desc_lines:
                print(f"    {line.strip()}")
            if tool.example_patterns:
                print(f"    Examples: {', '.join(tool.example_patterns[:3])}")

    print("\n" + "=" * 70)
    print(f"Total: {len(AVAILABLE_TOOLS)} tools")
    print("=" * 70 + "\n")


def show_permissions(permissions_config: Optional[str] = None) -> None:
    """Display current permission configuration."""
    config_path = Path(permissions_config) if permissions_config else None
    manager = load_permissions_from_config(config_path=config_path)

    config = manager.config

    print("\n" + "=" * 70)
    print("PERMISSION CONFIGURATION")
    print("=" * 70)

    print(f"\nMode: {config.defaultMode.value}")

    print("\n[ENABLED TOOLS]")
    for tool in manager.enabled_tools:
        print(f"  - {tool}")

    if config.tools.disabled:
        print("\n[DISABLED TOOLS]")
        for tool in config.tools.disabled:
            print(f"  - {tool}")

    print("\n[ALLOW RULES]")
    for rule in config.permissions.allow:
        print(f"  ✓ {rule}")

    print("\n[DENY RULES]")
    for rule in config.permissions.deny:
        print(f"  ✗ {rule}")

    if config.permissions.ask:
        print("\n[ASK RULES] (require confirmation)")
        for rule in config.permissions.ask:
            print(f"  ? {rule}")

    # Show hooks if configured
    hooks_configured = (
        config.hooks.PreToolUse
        or config.hooks.PostToolUse
        or config.hooks.PermissionRequest
    )
    if hooks_configured:
        print("\n[HOOKS]")
        if config.hooks.PreToolUse:
            print(f"  PreToolUse: {len(config.hooks.PreToolUse)} hooks")
        if config.hooks.PostToolUse:
            print(f"  PostToolUse: {len(config.hooks.PostToolUse)} hooks")
        if config.hooks.PermissionRequest:
            print(f"  PermissionRequest: {len(config.hooks.PermissionRequest)} hooks")

    print("\n" + "=" * 70 + "\n")


# =============================================================================
# Task Execution
# =============================================================================

async def execute_task(args: argparse.Namespace, config_loader: AgentConfigLoader) -> int:
    """
    Execute the agent task using the unified task runner.

    Args:
        args: Parsed command-line arguments.
        config_loader: Loaded configuration from agent.yaml.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    # Determine working directory
    working_dir = Path(args.dir).resolve() if args.dir else Path.cwd()

    # Load task
    try:
        task = load_task(
            task=args.task,
            file_path=args.task_file,
            working_dir=working_dir
        )
    except TaskError as e:
        logger.error(f"Task error: {e}")
        return 1

    logger.info(f"Task: {task[:LOG_PREVIEW_LENGTH]}{'...' if len(task) > LOG_PREVIEW_LENGTH else ''}")

    # Handle --no-skills flag
    enable_skills = False if args.no_skills else None  # None = use config default

    # Build TaskExecutionParams from CLI args
    params = TaskExecutionParams(
        task=task,
        working_dir=working_dir,
        resume_session_id=args.resume,
        fork_session=args.fork_session,
        # Config overrides (already applied to config_loader, but pass explicitly for clarity)
        model=args.model,
        max_turns=args.max_turns,
        timeout_seconds=args.timeout,
        profile_path=Path(args.profile) if args.profile else None,
        additional_dirs=args.add_dir,
        enable_skills=enable_skills,
        # CLI uses ExecutionTracer for rich interactive output
        tracer=ExecutionTracer(verbose=True),
    )

    try:
        # Execute task using unified task runner
        result = await execute_agent_task(params, config_loader=config_loader)

        # Output results (CLI-specific formatting)
        if args.json:
            output = result.model_dump_json(indent=2)
        else:
            output = format_result(result)

        if args.output:
            Path(args.output).write_text(output)
            logger.info(f"Results written to: {args.output}")

        # Log metrics
        if result.metrics:
            logger.info(
                f"Metrics: turns={result.metrics.num_turns}, "
                f"duration={result.metrics.duration_ms}ms, "
                f"cost=${result.metrics.total_cost_usd or 0:.4f}"
            )

        if result.session_info:
            logger.info(f"Session ID: {result.session_info.session_id}")

        return 0 if result.status == TaskStatus.COMPLETE else 1

    except AgentError as e:
        logger.error(f"Agent error: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


# =============================================================================
# Main Entry Point
# =============================================================================

def main() -> int:
    """
    Main entry point.

    Returns:
        Exit code.
    """
    args = parse_args()

    # Setup logging
    setup_cli_logging(args.log_level)

    # Handle special commands
    if args.list_sessions:
        list_sessions()
        return 0

    if args.show_tools:
        show_tools()
        return 0

    if args.show_permissions:
        show_permissions(args.permissions_config)
        return 0

    if args.init_permissions:
        init_permissions()
        return 0

    if args.check_profile:
        check_profile()
        return 0

    if args.init:
        working_dir = Path(args.dir).resolve() if args.dir else Path.cwd()
        init_settings(working_dir)
        return 0

    # Validate required arguments
    if not args.task and not args.task_file:
        logger.error(
            "No task specified. Use --task or --task-file to provide a task."
        )
        return 1

    # Load configuration from agent.yaml and secrets.yaml
    try:
        config_path = Path(args.config) if args.config else None
        secrets_path = Path(args.secrets) if args.secrets else None

        config_loader = get_config_loader(
            config_path=config_path,
            secrets_path=secrets_path,
            force_new=True
        )

        # Parse and apply --set overrides first
        if args.set:
            set_overrides = parse_set_overrides(args.set)
            config_loader.apply_cli_overrides(**set_overrides)

        # Apply direct CLI overrides (take precedence over --set)
        config_loader.apply_cli_overrides(
            model=args.model,
            max_turns=args.max_turns,
            timeout_seconds=args.timeout,
        )

        # Load and validate configuration (sets ANTHROPIC_API_KEY env var)
        config_loader.load()

        # Show API key confirmation
        C = AnsiColors
        api_key = config_loader.get_api_key()
        key_preview = api_key[:20] + "..." if len(api_key) > 20 else api_key
        print(f"{C.SUCCESS}{StatusIcons.SUCCESS}{C.RESET} API Key loaded: {C.GRAY}{key_preview}{C.RESET}")
        print(f"{C.SUCCESS}{StatusIcons.SUCCESS}{C.RESET} Config loaded: {C.GRAY}{config_loader.config_path}{C.RESET}")

    except (ConfigNotFoundError, ConfigValidationError) as e:
        C = AnsiColors
        print(f"\n{C.ERROR}{StatusIcons.FAILURE} Configuration Error{C.RESET}\n", file=sys.stderr)
        print(f"{e}\n", file=sys.stderr)
        return 1
    except ValueError as e:
        # Handle --set parsing errors
        C = AnsiColors
        print(f"\n{C.ERROR}{StatusIcons.FAILURE} CLI Argument Error{C.RESET}\n", file=sys.stderr)
        print(f"{e}\n", file=sys.stderr)
        return 1

    # Run the task
    try:
        return asyncio.run(execute_task(args, config_loader))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
