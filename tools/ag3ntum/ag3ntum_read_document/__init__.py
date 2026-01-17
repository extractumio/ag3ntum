"""
Ag3ntumReadDocument - Comprehensive document reading tool.

Supports reading various document formats:
- Text files (source code, configs, markup)
- Tabular data (CSV, TSV, Excel, Parquet)
- Office documents (DOCX, RTF, ODT, PPTX, EPUB)
- PDF files with automatic OCR
- Archives (ZIP, TAR, 7z)
- Image metadata
- Audio metadata

Usage:
    from tools.ag3ntum.ag3ntum_read_document import create_read_document_tool

    tool = create_read_document_tool(session_id="my-session")
"""

from .tool import (
    create_read_document_tool,
    create_ag3ntum_read_document_mcp_server,
    AG3NTUM_READ_DOCUMENT_TOOL,
)

__all__ = [
    "create_read_document_tool",
    "create_ag3ntum_read_document_mcp_server",
    "AG3NTUM_READ_DOCUMENT_TOOL",
]
