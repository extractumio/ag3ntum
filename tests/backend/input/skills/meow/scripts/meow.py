#!/usr/bin/env python3
"""
Meow Facts - Fetch random cat facts from the MeowFacts API.

API: https://meowfacts.herokuapp.com/
"""
import argparse
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path


MEOW_FACTS_URL = "https://meowfacts.herokuapp.com/"
TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "meow.md"


def read_template() -> str:
    """
    Read the template file content.

    Returns:
        Template string content.

    Raises:
        RuntimeError: If the template file cannot be read.
    """
    try:
        return TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"Failed to read template file: {e}")


def format_output(facts: list[str]) -> str:
    """
    Format cat facts using the template.

    Args:
        facts: List of cat fact strings.

    Returns:
        Formatted output string with {TEXT} replaced by facts.
    """
    template = read_template()

    if len(facts) == 1:
        text = facts[0]
    else:
        text = "\n".join(f"{i}. {fact}" for i, fact in enumerate(facts, 1))

    return template.replace("{TEXT}", text)


def fetch_cat_facts(count: int = 1) -> list[str]:
    """
    Fetch random cat facts from the MeowFacts API.

    Args:
        count: Number of facts to fetch (default: 1).

    Returns:
        List of cat fact strings.

    Raises:
        RuntimeError: If the API request fails.
    """
    url = MEOW_FACTS_URL
    if count > 1:
        url = f"{MEOW_FACTS_URL}?count={count}"

    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("data", [])
    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to fetch cat facts: {e}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse API response: {e}")


def main() -> int:
    """
    Main entry point for the meow facts CLI.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    parser = argparse.ArgumentParser(
        description="Fetch random cat facts from MeowFacts API"
    )
    parser.add_argument(
        "--count", "-c",
        type=int,
        default=1,
        help="Number of cat facts to fetch (default: 1)"
    )
    parser.add_argument(
        "--json", "-j",
        action="store_true",
        help="Output as JSON"
    )

    args = parser.parse_args()

    try:
        facts = fetch_cat_facts(args.count)

        if args.json:
            print(json.dumps({"data": facts}, indent=2))
        else:
            output = format_output(facts)
            print(output)

        return 0

    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

