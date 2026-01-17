"""
Text file extractor for ReadDocument tool.

Handles plain text and source code files with line numbering.
"""
import logging
from pathlib import Path
from typing import Any

from ..config import get_config
from ..security import sanitize_output
from .base import BaseExtractor, ExtractedContent

logger = logging.getLogger(__name__)


class TextExtractor(BaseExtractor):
    """Extractor for text-based files (source code, configs, markup, etc.)."""

    # Extensions this extractor supports (comprehensive list)
    SUPPORTED_EXTENSIONS = {
        ".txt",
        ".md",
        ".rst",
        ".py",
        ".js",
        ".ts",
        ".jsx",
        ".tsx",
        ".java",
        ".c",
        ".cpp",
        ".cc",
        ".h",
        ".hpp",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".pl",
        ".pm",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".bat",
        ".cmd",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".html",
        ".htm",
        ".xhtml",
        ".css",
        ".scss",
        ".sass",
        ".less",
        ".xml",
        ".xsl",
        ".xslt",
        ".svg",
        ".sql",
        ".graphql",
        ".gql",
        ".proto",
        ".swift",
        ".kt",
        ".kts",
        ".scala",
        ".clj",
        ".cljs",
        ".ex",
        ".exs",
        ".erl",
        ".hrl",
        ".hs",
        ".lhs",
        ".lua",
        ".r",
        ".R",
        ".jl",
        ".m",
        ".mm",
        ".f",
        ".f90",
        ".f95",
        ".asm",
        ".s",
        ".v",
        ".sv",
        ".vhd",
        ".vhdl",
        ".tcl",
        ".cmake",
        ".make",
        ".mk",
        ".dockerfile",
        ".vagrantfile",
        ".tf",
        ".tfvars",
        ".hcl",
        ".cgi",
        ".htaccess",
        ".nginx",
        ".env",
        ".gitignore",
        ".dockerignore",
        ".editorconfig",
        ".log",
    }

    def supports_format(self, extension: str) -> bool:
        """Check if extension is supported."""
        return extension.lower() in self.SUPPORTED_EXTENSIONS

    async def extract(self, path: Path, args: dict[str, Any]) -> ExtractedContent:
        """
        Extract text content with line numbering.

        Args:
            path: Path to the text file
            args:
                - offset: Starting line (1-indexed, default: 1)
                - limit: Maximum lines to read (default: all)
                - include_metadata: Include file metadata (default: True)

        Returns:
            ExtractedContent with numbered lines
        """
        config = get_config()
        offset = args.get("offset", 1)
        limit = args.get("limit")
        include_metadata = args.get("include_metadata", True)

        # Read file content
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.error(f"Failed to read text file {path}: {e}")
            raise

        lines = content.splitlines()
        total_lines = len(lines)

        # Apply offset and limit
        start_idx = max(0, offset - 1)
        end_idx = start_idx + limit if limit else len(lines)
        selected_lines = lines[start_idx:end_idx]

        # Format with line numbers
        numbered_lines = []
        for i, line in enumerate(selected_lines, start=start_idx + 1):
            numbered_lines.append(f"{i:6}|{line}")

        output = "\n".join(numbered_lines)

        # Sanitize output
        sanitized = sanitize_output(output, config.output)

        # Build metadata
        metadata = {}
        if include_metadata:
            stat = path.stat()
            metadata = {
                "filename": path.name,
                "size_bytes": stat.st_size,
                "total_lines": total_lines,
                "encoding": "utf-8",
            }

        # Create result
        result = ExtractedContent(
            content=sanitized.content,
            format_type=f"Text ({path.suffix or 'plain'})",
            metadata=metadata,
            was_truncated=sanitized.was_truncated or (limit and end_idx < total_lines),
        )

        # Add truncation note
        if limit and end_idx < total_lines:
            result.add_note(f"{total_lines - end_idx} more lines not shown")

        logger.info(f"Extracted {len(selected_lines)} lines from {path.name}")
        return result
