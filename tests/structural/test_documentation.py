"""
Verify documentation quality and freshness.

These tests ensure CLAUDE.md stays healthy and all internal references resolve.
Part of the Harness Engineering documentation quality gates.
"""
import os
import re
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDocumentation:

    @pytest.mark.unit
    def test_claude_md_size_limit(self):
        """CLAUDE.md must stay under 250 lines (per self-improvement protocol)."""
        claude_md = os.path.join(PROJECT_ROOT, "CLAUDE.md")
        if not os.path.exists(claude_md):
            pytest.skip("CLAUDE.md not found")
        with open(claude_md) as f:
            lines = f.readlines()
        assert len(lines) <= 250, (
            f"CLAUDE.md is {len(lines)} lines (limit: 250).\n"
            f"Extract verbose content to docs/ and link from CLAUDE.md.\n"
            f"See the Self-Improvement Protocol in CLAUDE.md for guidance."
        )

    @pytest.mark.unit
    def test_see_references_resolve(self):
        """All markdown link references in CLAUDE.md must point to existing files."""
        claude_md = os.path.join(PROJECT_ROOT, "CLAUDE.md")
        if not os.path.exists(claude_md):
            pytest.skip("CLAUDE.md not found")

        with open(claude_md) as f:
            content = f.read()

        # Match markdown links: [text](path)
        link_pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
        broken = []

        for match in link_pattern.finditer(content):
            text, path = match.group(1), match.group(2)
            # Skip external URLs
            if path.startswith("http://") or path.startswith("https://"):
                continue
            # Skip anchors
            if path.startswith("#"):
                continue
            # Resolve relative to PROJECT_ROOT
            resolved = os.path.join(PROJECT_ROOT, path)
            if not os.path.exists(resolved):
                broken.append(
                    f"  [{text}]({path}) -> FILE NOT FOUND\n"
                    f"    Expected at: {resolved}"
                )

        assert not broken, (
            f"\nBroken references in CLAUDE.md:\n"
            + "\n".join(broken)
            + "\n\nFix: Update the path or create the missing file."
        )

    @pytest.mark.unit
    def test_gotchas_max_length(self):
        """Each gotcha in CLAUDE.md should be concise (max 3 lines including header)."""
        claude_md = os.path.join(PROJECT_ROOT, "CLAUDE.md")
        if not os.path.exists(claude_md):
            pytest.skip("CLAUDE.md not found")

        with open(claude_md) as f:
            content = f.read()

        # Find the Gotchas section
        gotchas_match = re.search(r'## Gotchas\n(.*?)(?=\n## |\Z)', content, re.DOTALL)
        if not gotchas_match:
            pytest.skip("No Gotchas section found in CLAUDE.md")

        gotchas_text = gotchas_match.group(1).strip()
        # Each gotcha is a numbered item: "N. **title** — description"
        gotcha_pattern = re.compile(r'^\d+\.\s+', re.MULTILINE)
        gotcha_starts = [m.start() for m in gotcha_pattern.finditer(gotchas_text)]

        long_gotchas = []
        for i, start in enumerate(gotcha_starts):
            end = gotcha_starts[i + 1] if i + 1 < len(gotcha_starts) else len(gotchas_text)
            gotcha = gotchas_text[start:end].strip()
            lines = gotcha.split("\n")
            if len(lines) > 3:
                first_line = lines[0][:80]
                long_gotchas.append(
                    f"  Gotcha starting with: {first_line}...\n"
                    f"    Has {len(lines)} lines (max: 3). Compress or move details to docs/."
                )

        assert not long_gotchas, (
            f"\nGotchas exceeding max length:\n"
            + "\n".join(long_gotchas)
            + "\n\nFix: Keep each gotcha to max 2 lines (cause + prevention)."
        )
