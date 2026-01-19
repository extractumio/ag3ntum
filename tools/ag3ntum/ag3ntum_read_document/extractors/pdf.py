"""
PDF extractor for ReadDocument tool.

Uses PyMuPDF for text extraction with automatic OCR for scanned pages.
"""
import asyncio
import logging
from pathlib import Path
from typing import Any

from ..config import get_config
from ..exceptions import ExtractionTimeoutError
from ..security import sanitize_metadata, sanitize_output
from ..utils import check_dependency, parse_page_range
from .base import BaseExtractor, ExtractedContent

logger = logging.getLogger(__name__)

# Required dependencies
import fitz  # Required: PyMuPDF


class PDFExtractor(BaseExtractor):
    """Extractor for PDF files with automatic OCR support."""

    SUPPORTED_EXTENSIONS = {".pdf"}

    def supports_format(self, extension: str) -> bool:
        """Check if extension is supported."""
        return extension.lower() in self.SUPPORTED_EXTENSIONS

    async def extract(self, path: Path, args: dict[str, Any]) -> ExtractedContent:
        """
        Extract PDF content with automatic OCR for scanned pages.

        Args:
            path: Path to the PDF file
            args:
                - pages: Page range (e.g., "1-20", "1,3,5-10")
                - extract_images: Return image descriptions (default: False)
                - extract_tables: Attempt table extraction (default: False)
                - include_metadata: Include document metadata (default: True)

        Returns:
            ExtractedContent with text from PDF pages
        """
        config = get_config()
        pdf_config = config.pdf
        pages_spec = args.get("pages")
        include_metadata = args.get("include_metadata", True)

        # Open document
        try:
            doc = fitz.open(path)
        except Exception as e:
            logger.error(f"Failed to open PDF {path}: {e}")
            raise

        # Use try/finally to ensure document is always closed (prevents resource leak)
        try:
            total_pages = len(doc)

            # Parse page range
            pages_to_process = parse_page_range(pages_spec, total_pages)

            # Apply page limit for text extraction
            if len(pages_to_process) > pdf_config.max_pages_text:
                pages_to_process = pages_to_process[: pdf_config.max_pages_text]
                logger.warning(f"Page limit applied: {pdf_config.max_pages_text}")

            # Extract content from each page
            page_contents = []
            ocr_pages_used = 0
            ocr_page_numbers = []

            for page_num in pages_to_process:
                try:
                    page = doc[page_num]
                    text = page.get_text()

                    # Auto-detect scanned page: low text content
                    if len(text.strip()) < pdf_config.ocr_text_threshold:
                        # This page needs OCR
                        if ocr_pages_used >= pdf_config.max_pages_ocr:
                            page_contents.append(
                                f"\n--- Page {page_num + 1} ---\n"
                                f"[Scanned page - OCR limit reached]\n"
                            )
                            continue

                        # Apply OCR
                        try:
                            text = await self._ocr_page(page, pdf_config.ocr_per_page_timeout)
                            ocr_pages_used += 1
                            ocr_page_numbers.append(page_num + 1)
                            page_contents.append(
                                f"\n--- Page {page_num + 1} [OCR] ---\n{text}\n"
                            )
                        except asyncio.TimeoutError:
                            page_contents.append(
                                f"\n--- Page {page_num + 1} ---\n"
                                f"[OCR timed out]\n"
                            )
                    else:
                        # Regular text extraction
                        page_contents.append(f"\n--- Page {page_num + 1} ---\n{text}\n")

                except Exception as e:
                    logger.warning(f"Failed to extract page {page_num + 1}: {e}")
                    page_contents.append(
                        f"\n--- Page {page_num + 1} ---\n[Extraction failed: {e}]\n"
                    )

            # Combine content
            content = "".join(page_contents)

            # Sanitize output
            sanitized = sanitize_output(content, config.output)

            # Extract metadata
            metadata = {}
            if include_metadata:
                raw_metadata = doc.metadata or {}
                metadata = sanitize_metadata(
                    {
                        "title": raw_metadata.get("title", ""),
                        "author": raw_metadata.get("author", ""),
                        "subject": raw_metadata.get("subject", ""),
                        "creator": raw_metadata.get("creator", ""),
                        "producer": raw_metadata.get("producer", ""),
                        "creation_date": raw_metadata.get("creationDate", ""),
                        "modification_date": raw_metadata.get("modDate", ""),
                    },
                    config.output,
                )
                # Remove empty values
                metadata = {k: v for k, v in metadata.items() if v}

            result = ExtractedContent(
                content=sanitized.content,
                format_type="PDF Document",
                metadata=metadata,
                total_pages=total_pages,
                extracted_pages=pages_to_process,
                was_truncated=sanitized.was_truncated,
                ocr_pages_used=ocr_pages_used,
            )

            if ocr_page_numbers:
                result.add_note(f"OCR applied to pages: {', '.join(map(str, ocr_page_numbers))}")

            if len(pages_to_process) < total_pages:
                result.add_note(f"{total_pages - len(pages_to_process)} pages not extracted")

            logger.info(
                f"Extracted {len(pages_to_process)} pages from {path.name} "
                f"(OCR: {ocr_pages_used})"
            )
            return result

        finally:
            # Always close the document to prevent resource leak
            doc.close()

    async def _ocr_page(self, page: Any, timeout: float) -> str:
        """
        Apply OCR to a PDF page using PyMuPDF's native capabilities.

        Args:
            page: PyMuPDF page object
            timeout: Timeout in seconds

        Returns:
            Extracted text from OCR

        Note:
            Requires Tesseract to be installed on the system.
        """
        # Check tesseract availability (system dependency)
        check_dependency("tesseract", "PDF OCR")

        def do_ocr():
            # Use PyMuPDF's native OCR support (requires system tesseract)
            tp = page.get_textpage_ocr(flags=0, dpi=300, full=True)
            return page.get_text(textpage=tp)

        # Run OCR with timeout
        loop = asyncio.get_event_loop()
        try:
            text = await asyncio.wait_for(
                loop.run_in_executor(None, do_ocr),
                timeout=timeout,
            )
            return text
        except asyncio.TimeoutError:
            logger.warning(f"OCR timed out after {timeout}s")
            raise
