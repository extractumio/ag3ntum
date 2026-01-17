"""
Base extractor class for ReadDocument tool.

Defines the interface that all format-specific extractors must implement.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExtractedContent:
    """
    Result of content extraction.

    Unified format for all extractor outputs.
    """

    # Main content (text representation)
    content: str

    # Format/type identifier
    format_type: str

    # Optional metadata about the file/content
    metadata: dict[str, Any] = field(default_factory=dict)

    # For paginated content (PDF, Office)
    total_pages: int | None = None
    extracted_pages: list[int] | None = None

    # For tabular data
    total_rows: int | None = None
    total_columns: int | None = None
    column_names: list[str] | None = None

    # For archives
    total_files: int | None = None
    total_dirs: int | None = None
    compressed_size: int | None = None
    uncompressed_size: int | None = None

    # Processing info
    was_truncated: bool = False
    ocr_pages_used: int = 0
    processing_notes: list[str] = field(default_factory=list)

    def add_note(self, note: str) -> None:
        """Add a processing note."""
        self.processing_notes.append(note)

    def format_header(self) -> str:
        """Format a header section with metadata."""
        lines = []

        if self.format_type:
            lines.append(f"**Format:** {self.format_type}")

        if self.total_pages is not None:
            if self.extracted_pages:
                page_range = self._format_page_range(self.extracted_pages)
                lines.append(f"**Pages:** {page_range} of {self.total_pages}")
            else:
                lines.append(f"**Pages:** {self.total_pages}")

        if self.ocr_pages_used > 0:
            lines.append(f"**OCR Applied:** {self.ocr_pages_used} pages")

        if self.total_rows is not None:
            lines.append(f"**Rows:** {self.total_rows}")
            if self.total_columns:
                lines.append(f"**Columns:** {self.total_columns}")

        if self.total_files is not None:
            lines.append(f"**Files:** {self.total_files}")
            if self.total_dirs:
                lines.append(f"**Directories:** {self.total_dirs}")

        if self.compressed_size is not None and self.uncompressed_size is not None:
            from ..utils import format_bytes

            lines.append(
                f"**Size:** {format_bytes(self.compressed_size)} compressed, "
                f"{format_bytes(self.uncompressed_size)} uncompressed"
            )

        if self.metadata:
            lines.append("")
            lines.append("**Metadata:**")
            for key, value in list(self.metadata.items())[:10]:  # Limit displayed metadata
                lines.append(f"  - {key}: {value}")

        if self.processing_notes:
            lines.append("")
            for note in self.processing_notes:
                lines.append(f"*{note}*")

        return "\n".join(lines)

    def _format_page_range(self, pages: list[int]) -> str:
        """Format a list of page numbers as a compact range string."""
        if not pages:
            return ""

        # Convert to 1-indexed for display
        pages = [p + 1 for p in pages]

        ranges = []
        start = pages[0]
        end = pages[0]

        for page in pages[1:]:
            if page == end + 1:
                end = page
            else:
                if start == end:
                    ranges.append(str(start))
                else:
                    ranges.append(f"{start}-{end}")
                start = end = page

        if start == end:
            ranges.append(str(start))
        else:
            ranges.append(f"{start}-{end}")

        return ", ".join(ranges)


class BaseExtractor(ABC):
    """
    Abstract base class for content extractors.

    Each format category has its own extractor implementation.
    """

    @abstractmethod
    async def extract(self, path: Path, args: dict[str, Any]) -> ExtractedContent:
        """
        Extract content from a file.

        Args:
            path: Path to the file
            args: Extraction arguments (format-specific)

        Returns:
            ExtractedContent with extracted text and metadata
        """
        pass

    @abstractmethod
    def supports_format(self, extension: str) -> bool:
        """
        Check if this extractor supports a file extension.

        Args:
            extension: File extension (with dot, e.g., ".pdf")

        Returns:
            True if supported
        """
        pass

    def _format_output(self, extracted: ExtractedContent) -> str:
        """
        Format extracted content for output.

        Combines header and content into final output string.
        """
        header = extracted.format_header()
        if header:
            return f"{header}\n\n---\n\n{extracted.content}"
        return extracted.content
