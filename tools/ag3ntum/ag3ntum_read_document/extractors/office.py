"""
Office document extractor for ReadDocument tool.

Uses Pandoc for converting DOCX, RTF, ODT, PPTX, EPUB to text/markdown.
"""
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

from ..config import get_config
from ..exceptions import DependencyMissingError, ExtractionTimeoutError
from ..security import sanitize_metadata, sanitize_output
from ..utils import check_dependency, run_command
from .base import BaseExtractor, ExtractedContent

logger = logging.getLogger(__name__)


class OfficeExtractor(BaseExtractor):
    """Extractor for Office documents using Pandoc."""

    SUPPORTED_EXTENSIONS = {
        ".docx",
        ".doc",
        ".rtf",
        ".odt",
        ".pptx",
        ".ppt",
        ".odp",
        ".epub",
    }

    # Pandoc input format mapping
    PANDOC_FORMATS = {
        ".docx": "docx",
        ".doc": "doc",
        ".rtf": "rtf",
        ".odt": "odt",
        ".pptx": "pptx",
        ".ppt": "ppt",
        ".odp": "odp",
        ".epub": "epub",
    }

    # Cache the metadata template path to avoid creating temp files on every call
    _metadata_template_path: str | None = None

    def supports_format(self, extension: str) -> bool:
        """Check if extension is supported."""
        return extension.lower() in self.SUPPORTED_EXTENSIONS

    async def extract(self, path: Path, args: dict[str, Any]) -> ExtractedContent:
        """
        Extract Office document content using Pandoc.

        Args:
            path: Path to the Office document
            args:
                - include_metadata: Include document metadata (default: True)

        Returns:
            ExtractedContent with markdown-formatted text
        """
        # Check Pandoc availability
        check_dependency("pandoc", "Office document conversion")

        config = get_config()
        include_metadata = args.get("include_metadata", True)
        ext = path.suffix.lower()

        # Determine input format
        input_format = self.PANDOC_FORMATS.get(ext)
        if not input_format:
            input_format = ext.lstrip(".")

        # Build pandoc command
        cmd = [
            "pandoc",
            str(path),
            "-f",
            input_format,
            "-t",
            "markdown",
            "--wrap=none",
        ]

        # Run pandoc with timeout
        timeout = config.timeouts.pandoc
        try:
            loop = asyncio.get_event_loop()
            stdout, stderr, returncode = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: run_command(cmd, timeout)),
                timeout=timeout + 5,  # Extra buffer for executor overhead
            )
        except asyncio.TimeoutError:
            logger.error(f"Pandoc conversion timed out after {timeout}s")
            raise ExtractionTimeoutError(timeout, "pandoc conversion")

        if returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace")
            logger.error(f"Pandoc failed: {error_msg}")
            raise RuntimeError(f"Pandoc conversion failed: {error_msg}")

        content = stdout.decode("utf-8", errors="replace")

        # Sanitize output
        sanitized = sanitize_output(content, config.output)

        # Extract metadata (if available via pandoc)
        metadata = {}
        if include_metadata:
            metadata = await self._extract_metadata(path, input_format, config)

        result = ExtractedContent(
            content=sanitized.content,
            format_type=self._get_format_name(ext),
            metadata=metadata,
            was_truncated=sanitized.was_truncated,
        )

        logger.info(f"Extracted {len(sanitized.content)} chars from {path.name}")
        return result

    async def _extract_metadata(
        self, path: Path, input_format: str, config: Any
    ) -> dict[str, Any]:
        """
        Extract document metadata using Pandoc.

        Returns:
            Metadata dict
        """
        try:
            # Use pandoc to extract metadata as JSON
            cmd = [
                "pandoc",
                str(path),
                "-f",
                input_format,
                "-t",
                "plain",
                "--template",
                self._get_metadata_template(),
            ]

            # This is a quick operation, use short timeout
            loop = asyncio.get_event_loop()
            stdout, stderr, returncode = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: run_command(cmd, 10)),
                timeout=15,
            )

            if returncode == 0:
                # Parse metadata output
                metadata_text = stdout.decode("utf-8", errors="replace")
                return self._parse_metadata(metadata_text)

        except Exception as e:
            logger.debug(f"Metadata extraction failed: {e}")

        # Fallback: basic file metadata
        stat = path.stat()
        return {
            "filename": path.name,
            "size_bytes": stat.st_size,
        }

    def _get_metadata_template(self) -> str:
        """
        Get a simple template for metadata extraction.

        Note: Creates a temp file with the template since pandoc
        requires a file path for --template. The file is cached at
        the class level to avoid creating new temp files on every call.
        """
        # Return cached path if available
        if OfficeExtractor._metadata_template_path is not None:
            template_path = Path(OfficeExtractor._metadata_template_path)
            if template_path.exists():
                return OfficeExtractor._metadata_template_path

        template = """$if(title)$title: $title$
$endif$
$if(author)$author: $for(author)$$author$$sep$, $endfor$
$endif$
$if(date)$date: $date$
$endif$
$if(subject)$subject: $subject$
$endif$
"""
        # Create temp file and cache the path
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(template)
            OfficeExtractor._metadata_template_path = f.name
            return f.name

    def _parse_metadata(self, text: str) -> dict[str, Any]:
        """Parse metadata from template output."""
        metadata = {}
        for line in text.strip().split("\n"):
            if ": " in line:
                key, value = line.split(": ", 1)
                metadata[key.strip()] = value.strip()
        return metadata

    def _get_format_name(self, ext: str) -> str:
        """Get human-readable format name."""
        names = {
            ".docx": "Word Document",
            ".doc": "Word Document (Legacy)",
            ".rtf": "Rich Text Format",
            ".odt": "OpenDocument Text",
            ".pptx": "PowerPoint Presentation",
            ".ppt": "PowerPoint Presentation (Legacy)",
            ".odp": "OpenDocument Presentation",
            ".epub": "EPUB E-book",
        }
        return names.get(ext, "Office Document")
