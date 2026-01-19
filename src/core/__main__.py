"""
Allow running the core package as a module.

Usage:
    python -m src.core --task "Your task" --dir .

Or via the wrapper script:
    python run_agent.py --task "Your task" --dir .
"""
import sys

from .agent import main

if __name__ == "__main__":
    sys.exit(main())

