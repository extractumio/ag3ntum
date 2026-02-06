#!/usr/bin/env python3
"""
Record real Anthropic API SSE events for schema validation.

This script captures SSE events from real API calls and saves them to
the test fixtures file. Run this when Claude Code updates and SSE schema
tests start failing.

Usage:
    ./scripts/record_sse_samples.py

    # Or with custom API key
    ANTHROPIC_API_KEY=sk-ant-... ./scripts/record_sse_samples.py

    # Use a specific model
    ./scripts/record_sse_samples.py --model claude-sonnet-4-20250514

The script will:
1. Make 3 API calls (text-only, single tool, multiple tools)
2. Capture raw SSE events
3. Update tests/backend/fixtures/anthropic_sse_samples.json
4. Print a summary of changes

After running, execute: ./run.sh test --subset "sse_schemas"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

try:
    import httpx
except ImportError:
    print("Error: httpx not installed. Run: pip install httpx")
    sys.exit(1)


FIXTURE_PATH = project_root / "tests" / "backend" / "fixtures" / "anthropic_sse_samples.json"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"


def get_api_key() -> str:
    """Get API key from environment or config."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return api_key

    # Try loading from secrets.yaml
    secrets_path = project_root / "config" / "secrets.yaml"
    if secrets_path.exists():
        try:
            import yaml
            with open(secrets_path) as f:
                secrets = yaml.safe_load(f)
                # Try both uppercase and lowercase key names
                api_key = secrets.get("ANTHROPIC_API_KEY") or secrets.get("anthropic_api_key")
                if api_key:
                    return api_key
        except Exception as e:
            print(f"Warning: Could not load secrets.yaml: {e}")

    print("Error: ANTHROPIC_API_KEY not found in environment or config/secrets.yaml")
    sys.exit(1)


def capture_sse_stream(
    api_key: str,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
) -> list[str]:
    """Make a streaming API call and capture raw SSE events."""
    headers = {
        "x-api-key": api_key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }

    payload = {
        "model": model,
        "max_tokens": 1024,
        "stream": True,
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools

    events = []
    with httpx.Client(timeout=60) as client:
        with client.stream("POST", API_URL, headers=headers, json=payload) as response:
            response.raise_for_status()

            current_event = ""
            for line in response.iter_lines():
                if line.startswith("event:"):
                    current_event = line + "\n"
                elif line.startswith("data:"):
                    current_event += line + "\n\n"
                    events.append(current_event)
                    current_event = ""

    return events


def record_text_only(api_key: str, model: str) -> list[str]:
    """Record a simple text-only response."""
    print("  Recording text-only stream...")
    messages = [
        {"role": "user", "content": "Say hello in exactly 5 words."}
    ]
    return capture_sse_stream(api_key, model, messages)


def record_tool_call(api_key: str, model: str) -> list[str]:
    """Record a response with a single tool call."""
    print("  Recording single tool call stream...")
    messages = [
        {"role": "user", "content": "Read the file /test.txt for me."}
    ]
    tools = [
        {
            "name": "Read",
            "description": "Read a file from the filesystem",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path to the file to read"
                    }
                },
                "required": ["file_path"]
            }
        }
    ]
    return capture_sse_stream(api_key, model, messages, tools)


def record_multiple_tools(api_key: str, model: str) -> list[str]:
    """Record a response with multiple tool calls."""
    print("  Recording multiple tools stream...")
    messages = [
        {"role": "user", "content": "Read both /file1.txt and /file2.txt for me."}
    ]
    tools = [
        {
            "name": "Read",
            "description": "Read a file from the filesystem",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path to the file to read"
                    }
                },
                "required": ["file_path"]
            }
        }
    ]
    return capture_sse_stream(api_key, model, messages, tools)


def save_fixtures(
    text_stream: list[str],
    tool_stream: list[str],
    multiple_tools_stream: list[str],
) -> None:
    """Save recorded events to fixture file."""
    data = {
        "_description": "Recorded SSE event samples from Anthropic API for schema validation",
        "_instructions": "Auto-generated by scripts/record_sse_samples.py - do not edit manually",
        "_last_updated": datetime.now().strftime("%Y-%m-%d"),
        "_api_version": API_VERSION,
        "text_only_stream": text_stream,
        "tool_call_stream": tool_stream,
        "multiple_tools_stream": multiple_tools_stream,
    }

    # Ensure directory exists
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(FIXTURE_PATH, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\nSaved to: {FIXTURE_PATH}")


def print_summary(
    text_stream: list[str],
    tool_stream: list[str],
    multiple_tools_stream: list[str],
) -> None:
    """Print a summary of recorded events."""
    print("\n" + "=" * 60)
    print("RECORDING SUMMARY")
    print("=" * 60)

    def summarize(name: str, events: list[str]) -> None:
        event_types = []
        for event in events:
            if event.startswith("event:"):
                event_type = event.split("\n")[0].replace("event: ", "")
                event_types.append(event_type)
        print(f"\n{name}:")
        print(f"  Total events: {len(events)}")
        print(f"  Event types: {' -> '.join(event_types)}")

    summarize("text_only_stream", text_stream)
    summarize("tool_call_stream", tool_stream)
    summarize("multiple_tools_stream", multiple_tools_stream)

    print("\n" + "=" * 60)
    print("Next step: ./run.sh test --subset 'sse_schemas'")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record Anthropic API SSE events for schema validation"
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-20250514",
        help="Model to use for API calls (default: claude-sonnet-4-20250514)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making API calls"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Anthropic SSE Event Recorder")
    print("=" * 60)
    print(f"Model: {args.model}")
    print(f"Output: {FIXTURE_PATH}")

    if args.dry_run:
        print("\n[DRY RUN] Would make 3 API calls:")
        print("  1. Text-only response")
        print("  2. Single tool call")
        print("  3. Multiple tool calls")
        return

    api_key = get_api_key()
    print(f"API Key: {api_key[:12]}...{api_key[-4:]}")
    print()

    print("Recording SSE events from Anthropic API...")

    try:
        text_stream = record_text_only(api_key, args.model)
        tool_stream = record_tool_call(api_key, args.model)
        multiple_tools_stream = record_multiple_tools(api_key, args.model)
    except httpx.HTTPStatusError as e:
        print(f"\nAPI Error: {e.response.status_code}")
        print(f"Response: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)

    save_fixtures(text_stream, tool_stream, multiple_tools_stream)
    print_summary(text_stream, tool_stream, multiple_tools_stream)


if __name__ == "__main__":
    main()
