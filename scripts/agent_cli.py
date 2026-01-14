#!/usr/bin/env python3
"""
CLI Entry point for Ag3ntum (direct execution).

This script runs the agent directly without going through the HTTP API.
It imports from src/core/agent.py and executes tasks locally.

Configuration is loaded from config/agent.yaml and config/secrets.yaml.

For HTTP-based execution via the API, use agent_http.py instead.

Usage:
    cd Project/
    
    # Always use venv Python directly (recommended):
    ./venv/bin/python scripts/agent_cli.py --task "Your task here"
    ./venv/bin/python scripts/agent_cli.py --help
    
    # Or activate venv first, then use python:
    source venv/bin/activate
    python scripts/agent_cli.py --task "Your task here"
"""
import sys
from pathlib import Path

# Add project root to sys.path so that 'src' can be imported as a package
_project_root = Path(__file__).parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "tools"))

from src.core.agent import main  # noqa: E402


def main_wrapper() -> int:
    """Wrapper to provide helpful error messages."""
    # If no arguments provided, show usage hint
    if len(sys.argv) == 1:
        print("Ag3ntum CLI - Direct Execution Mode")
        print("\nUsage:")
        print("  ./venv/bin/python scripts/agent_cli.py [OPTIONS]")
        print("\nCommon Parameters:")
        print("  --task, -t TEXT          Task description to execute")
        print("  --task-file, -f PATH     Path to file containing task")
        print("  --dir, -d PATH           Working directory for the agent")
        print("  --resume, -r SESSION_ID  Resume a previous session")
        print("  --list-sessions, -l      List all sessions")
        print("  --show-tools             Show available tools")
        print("\nConfiguration:")
        print("  --model, -m TEXT         Claude model (e.g. claude-sonnet-4-5-20250929)")
        print("  --max-turns INT          Maximum conversation turns (default: 50)")
        print("  --timeout INT            Timeout in seconds")
        print("  --profile PATH           Permission profile file (YAML/JSON)")
        print("  --role ROLE              Role template name")
        print("\nOutput:")
        print("  --output, -o PATH        Output file for results")
        print("  --json                   Output as JSON")
        print("  --log-level LEVEL        Logging level (DEBUG|INFO|WARNING|ERROR)")
        print("\nExamples:")
        print("  ./venv/bin/python scripts/agent_cli.py --task \"List all Python files\" --dir ./my-project")
        print("  ./venv/bin/python scripts/agent_cli.py --show-tools")
        print("  ./venv/bin/python scripts/agent_cli.py --list-sessions")
        print("\nFor full help: ./venv/bin/python scripts/agent_cli.py --help")
        return 1
    
    return main()


if __name__ == "__main__":
    sys.exit(main_wrapper())
