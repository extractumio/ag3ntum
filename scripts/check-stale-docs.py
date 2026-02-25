#!/usr/bin/env python3
"""
Detect documentation that may be stale.

Compares last-modified dates of source files vs their corresponding docs.
If source changed >30 days after doc was last updated, flag as potentially stale.

Usage:
    python scripts/check-stale-docs.py [--days 30]
"""
import argparse
import os
import sys
from datetime import datetime, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Mapping of source directories to their documentation files
DOC_MAPPINGS = {
    "src/core/": [
        "DOCUMENTS/TECHNICAL/current_architecture.md",
        "DOCUMENTS/TECHNICAL/layers_of_security_for_filesystem.md",
        "DOCUMENTS/TECHNICAL/sandbox_path_resolver.md",
    ],
    "src/api/": [
        "DOCUMENTS/TECHNICAL/current_architecture.md",
        "DOCUMENTS/TECHNICAL/inbound_waf_filter.md",
    ],
    "src/services/": [
        "DOCUMENTS/TECHNICAL/current_sse.md",
        "DOCUMENTS/TECHNICAL/current_event_hooks_callbacks.md",
        "DOCUMENTS/TECHNICAL/task_queue_and_auto_resume.md",
    ],
    "src/web_terminal_client/": [
        "DOCUMENTS/TECHNICAL/web_terminal_client.md",
    ],
    "tools/ag3ntum/": [
        "DOCUMENTS/TECHNICAL/current_architecture.md",
    ],
    "prompts/": [
        "DOCUMENTS/TECHNICAL/current_architecture.md",
    ],
}


def get_newest_mtime(directory):
    """Get the most recent modification time of any file in a directory."""
    newest = 0
    full_dir = os.path.join(PROJECT_ROOT, directory)
    if not os.path.exists(full_dir):
        return None
    for root, _, files in os.walk(full_dir):
        for f in files:
            if f.startswith(".") or "__pycache__" in root or "node_modules" in root:
                continue
            filepath = os.path.join(root, f)
            mtime = os.path.getmtime(filepath)
            if mtime > newest:
                newest = mtime
    return newest if newest > 0 else None


def get_file_mtime(filepath):
    """Get the modification time of a file."""
    full_path = os.path.join(PROJECT_ROOT, "..", filepath)
    if os.path.exists(full_path):
        return os.path.getmtime(full_path)
    # Also check within project root
    full_path = os.path.join(PROJECT_ROOT, filepath)
    if os.path.exists(full_path):
        return os.path.getmtime(full_path)
    return None


def main():
    parser = argparse.ArgumentParser(description="Detect potentially stale documentation")
    parser.add_argument("--days", type=int, default=30, help="Staleness threshold in days (default: 30)")
    args = parser.parse_args()

    threshold_seconds = args.days * 86400
    stale = []

    for src_dir, doc_files in DOC_MAPPINGS.items():
        src_mtime = get_newest_mtime(src_dir)
        if src_mtime is None:
            continue

        for doc_file in doc_files:
            doc_mtime = get_file_mtime(doc_file)
            if doc_mtime is None:
                stale.append(f"  MISSING: {doc_file} (covers {src_dir})")
                continue

            if src_mtime - doc_mtime > threshold_seconds:
                src_date = datetime.fromtimestamp(src_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
                doc_date = datetime.fromtimestamp(doc_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
                days_behind = int((src_mtime - doc_mtime) / 86400)
                stale.append(
                    f"  STALE: {doc_file}\n"
                    f"    Source ({src_dir}) last changed: {src_date}\n"
                    f"    Doc last changed: {doc_date} ({days_behind} days behind)"
                )

    if stale:
        print(f"Potentially stale documentation (threshold: {args.days} days):\n")
        print("\n".join(stale))
        sys.exit(1)
    else:
        print(f"All documentation is within {args.days}-day freshness threshold.")
        sys.exit(0)


if __name__ == "__main__":
    main()
