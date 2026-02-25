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

    # Paths gitignored in .dockerignore but present on dev machines.
    # When git is unavailable (Docker), these are silently skipped.
    GITIGNORED_DOC_PREFIXES = ("docs/", "docs\\")

    @pytest.mark.unit
    def test_see_references_resolve(self):
        """All markdown link references in CLAUDE.md must point to existing files.

        Uses git ls-files to check tracked files (handles gitignored docs).
        Falls back to filesystem check if git is unavailable.
        """
        import subprocess

        claude_md = os.path.join(PROJECT_ROOT, "CLAUDE.md")
        if not os.path.exists(claude_md):
            pytest.skip("CLAUDE.md not found")

        with open(claude_md) as f:
            content = f.read()

        # Get tracked files from git
        tracked_files = None
        has_git = False
        try:
            result = subprocess.run(
                ["git", "ls-files"],
                cwd=PROJECT_ROOT,
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                tracked_files = set(result.stdout.strip().split("\n"))
                has_git = True
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

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
            # Check: tracked in git OR exists on filesystem
            resolved = os.path.join(PROJECT_ROOT, path)
            in_git = tracked_files is not None and path in tracked_files
            on_disk = os.path.exists(resolved)
            if in_git or on_disk:
                continue
            # Skip gitignored paths via git check-ignore
            if has_git:
                ign = subprocess.run(
                    ["git", "check-ignore", "-q", path],
                    cwd=PROJECT_ROOT,
                    capture_output=True, timeout=5,
                )
                if ign.returncode == 0:
                    continue  # path is gitignored, skip
            else:
                # No git available (Docker): skip known gitignored prefixes
                if path.startswith(self.GITIGNORED_DOC_PREFIXES):
                    continue
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
