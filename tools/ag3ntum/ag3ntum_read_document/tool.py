"""
ReadDocument - Comprehensive document reading tool for Ag3ntum.

Supports:
- Text files (source code, configs, markup)
- Tabular data (CSV, TSV, Excel, Parquet) via Pandas
- Office documents (DOCX, RTF, ODT, PPTX, EPUB) via Pandoc
- PDF files with automatic OCR via PyMuPDF + Tesseract
- Archives (ZIP, TAR, 7z) with list/read/extract modes
- Image metadata (dimensions, EXIF) via Pillow
- Audio metadata (duration, tags) via mutagen

Security features:
- Path validation via Ag3ntumPathValidator
- Zip bomb protection
- Content sanitization for LLM context
- Configurable limits and timeouts
"""
import asyncio
import logging
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from src.core.path_validator import get_path_validator, PathValidationError

from .cache import get_cache_manager
from .config import get_config
from .exceptions import (
    FileTooLargeError,
    ExtractionTimeoutError,
    FormatNotSupportedError,
    ReadDocumentError,
)
from .format_detector import detect_format, FormatCategory, is_cacheable
from .extractors import get_extractor

logger = logging.getLogger(__name__)

# Tool name constant
AG3NTUM_READ_DOCUMENT_TOOL: str = "mcp__ag3ntum__ReadDocument"


def create_read_document_tool(session_id: str):
    """
    Create ReadDocument tool bound to a specific session's workspace.

    Args:
        session_id: The session ID (used to get the PathValidator)

    Returns:
        Tool function decorated with @tool.
    """
    bound_session_id = session_id

    @tool(
        "ReadDocument",
        """Read and extract content from various document formats.

Supports text files, PDFs (with auto-OCR), Office documents, spreadsheets,
archives, images, and audio files.

Args:
    file_path: Path to the file (relative to workspace or /workspace/...)

    # For text/PDF/Office files:
    offset: Starting line number (1-indexed, for text files)
    limit: Maximum lines to read (for text files)
    pages: Page range (e.g., "1-20", "1,3,5-10" for PDF/Office)

    # For tabular data (CSV, Excel, Parquet):
    sheet: Sheet name or index (for Excel files)
    rows: Row range (e.g., "1-100", "head:50", "tail:20")
    columns: Column selection (e.g., "A,B,C" or "name,age")

    # For archives (ZIP, TAR, 7z):
    mode: "list" (default), "read", or "extract"
    archive_path: Path within archive to read/extract
    pattern: Glob pattern for filtering (e.g., "*.py")

    # General options:
    include_metadata: Include file metadata (default: true)
    format_hint: Override format detection (extension without dot)

Returns:
    Formatted content with metadata header, or error message.

Examples:
    # PDF files
    ReadDocument(file_path="report.pdf")                        # Read entire PDF
    ReadDocument(file_path="report.pdf", pages="1-10")          # First 10 pages
    ReadDocument(file_path="report.pdf", pages="1,3,5-7")       # Specific pages
    ReadDocument(file_path="report.pdf", include_metadata=false) # Skip metadata

    # Excel/Spreadsheet files
    ReadDocument(file_path="data.xlsx")                         # First sheet, all rows
    ReadDocument(file_path="data.xlsx", sheet="Sales")          # Specific sheet by name
    ReadDocument(file_path="data.xlsx", sheet=2)                # Third sheet (0-indexed)
    ReadDocument(file_path="data.xlsx", rows="head:100")        # First 100 rows
    ReadDocument(file_path="data.xlsx", rows="tail:50")         # Last 50 rows
    ReadDocument(file_path="data.xlsx", rows="10-50")           # Rows 10-50
    ReadDocument(file_path="data.xlsx", columns="A,B,C")        # Specific columns by letter
    ReadDocument(file_path="data.xlsx", columns="name,email")   # Columns by header name
    ReadDocument(file_path="data.xlsx", sheet="Q1", rows="head:20", columns="A,B,D")

    # CSV/TSV files
    ReadDocument(file_path="data.csv", rows="head:100")         # First 100 rows
    ReadDocument(file_path="data.tsv", columns="id,name,value") # Specific columns

    # ZIP archives
    ReadDocument(file_path="backup.zip")                        # List all contents
    ReadDocument(file_path="backup.zip", mode="list")           # Same as above
    ReadDocument(file_path="backup.zip", pattern="*.py")        # List only Python files
    ReadDocument(file_path="backup.zip", pattern="src/**/*")    # List files in src/
    ReadDocument(file_path="backup.zip", mode="read", archive_path="src/main.py")
    ReadDocument(file_path="backup.zip", mode="extract", archive_path="config.json")

    # TAR archives (including .tar.gz, .tar.bz2, .tar.xz)
    ReadDocument(file_path="archive.tar.gz", mode="list")
    ReadDocument(file_path="archive.tar.gz", mode="read", archive_path="README.md")

    # 7z archives
    ReadDocument(file_path="data.7z", mode="list", pattern="*.sql")

    # Images (returns metadata and properties)
    ReadDocument(file_path="photo.jpg")                         # EXIF metadata
    ReadDocument(file_path="diagram.png", include_metadata=false)

    # Audio files (returns metadata and properties)
    ReadDocument(file_path="song.mp3")                          # ID3 tags, duration, etc.

    # Text/code files
    ReadDocument(file_path="script.py", offset=100, limit=50)   # Lines 100-149
    ReadDocument(file_path="config.yaml")

    # Office documents
    ReadDocument(file_path="document.docx")
    ReadDocument(file_path="presentation.pptx")

    # Parquet files
    ReadDocument(file_path="data.parquet", rows="head:1000", columns="id,timestamp,value")
""",
        {
            "file_path": str,
            "offset": int,
            "limit": int,
            "pages": str,
            "sheet": str,
            "rows": str,
            "columns": str,
            "mode": str,
            "archive_path": str,
            "pattern": str,
            "include_metadata": bool,
            "format_hint": str,
        },
    )
    async def read_document(args: dict[str, Any]) -> dict[str, Any]:
        """Read and extract content from documents."""
        file_path = args.get("file_path", "")

        if not file_path:
            return _error("file_path is required")

        # Get validator for this session
        try:
            validator = get_path_validator(bound_session_id)
        except RuntimeError as e:
            logger.error(f"ReadDocument: PathValidator not configured - {e}")
            return _error(f"Internal error: {e}")

        # Validate path
        try:
            validated = validator.validate_path(file_path, operation="read")
        except PathValidationError as e:
            logger.warning(f"ReadDocument: Path validation failed - {e.reason}")
            return _error(f"Path validation failed: {e.reason}")

        path = validated.normalized

        # Check existence
        if not path.exists():
            return _error(f"File not found: {file_path}")

        if path.is_dir():
            return _error(f"Cannot read directory: {file_path}. Use LS tool instead.")

        # Load configuration
        config = get_config()

        # Detect format
        format_hint = args.get("format_hint")
        format_info = detect_format(path, format_hint)

        if format_info.category == FormatCategory.UNKNOWN:
            logger.warning(f"Unknown format: {path.suffix}")
            return _error(
                f"Unsupported file format: {path.suffix}. "
                f"Use format_hint to override detection."
            )

        # Check file size
        file_size = path.stat().st_size
        size_limit = config.limits.get(format_info.category.value)
        if file_size > size_limit:
            logger.warning(f"File too large: {file_size} > {size_limit}")
            return _error(
                f"File size ({file_size} bytes) exceeds limit "
                f"({size_limit} bytes) for {format_info.category.value} files"
            )

        # Check cache
        cache_manager = get_cache_manager()
        cache_key = None

        if is_cacheable(format_info):
            cache_key = cache_manager.compute_cache_key(path, args)
            cached = cache_manager.get(format_info.category.value, cache_key)
            if cached:
                logger.info(f"ReadDocument: Cache hit for {file_path}")
                return _result(cached.content)

        # Get extractor
        try:
            extractor = get_extractor(format_info)
        except FormatNotSupportedError as e:
            return _error(str(e))

        # Extract content with global timeout
        try:
            async with asyncio.timeout(config.global_timeout):
                result = await extractor.extract(path, args)
        except asyncio.TimeoutError:
            logger.error(f"ReadDocument: Extraction timeout for {file_path}")
            return _error(
                f"Extraction timed out after {config.global_timeout}s. "
                f"Try limiting pages/rows or using a smaller file."
            )
        except ReadDocumentError as e:
            logger.error(f"ReadDocument: {e}")
            return _error(str(e))
        except Exception as e:
            logger.exception(f"ReadDocument: Unexpected error - {e}")
            return _error(f"Extraction failed: {e}")

        # Format output
        output = extractor._format_output(result)

        # Cache result if applicable
        if cache_key and is_cacheable(format_info):
            try:
                cache_manager.put(
                    format_info.category.value,
                    cache_key,
                    output,
                    result.metadata,
                )
            except Exception as e:
                logger.warning(f"Cache write failed: {e}")

        logger.info(f"ReadDocument: Extracted {len(output)} chars from {file_path}")
        return _result(output)

    return read_document


def _result(text: str) -> dict[str, Any]:
    """Create a successful result response."""
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict[str, Any]:
    """Create an error response."""
    return {"content": [{"type": "text", "text": f"**Error:** {message}"}], "isError": True}


def create_ag3ntum_read_document_mcp_server(
    session_id: str,
    server_name: str = "ag3ntum",
    version: str = "1.0.0",
):
    """
    Create an in-process MCP server for the ReadDocument tool.

    Args:
        session_id: The session ID for PathValidator lookup.
        server_name: MCP server name.
        version: Server version.

    Returns:
        McpSdkServerConfig for use in ClaudeAgentOptions.mcp_servers.
    """
    read_document_tool = create_read_document_tool(session_id=session_id)

    logger.info(f"Created ReadDocument MCP server for session {session_id}")

    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=[read_document_tool],
    )
