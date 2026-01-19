"""
File format detection for ReadDocument tool.

Uses file extension and MIME type detection to identify file formats.

Required dependencies:
    pip install python-magic
"""
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import magic  # Required: python-magic

logger = logging.getLogger(__name__)


class FormatCategory(str, Enum):
    """High-level format categories for limit enforcement."""

    TEXT = "text"
    PDF = "pdf"
    OFFICE = "office"
    ARCHIVE = "archive"
    IMAGE = "image"
    TABULAR = "tabular"
    AUDIO = "audio"
    STRUCTURED = "structured"
    UNKNOWN = "unknown"


@dataclass
class FormatInfo:
    """Detected format information."""

    extension: str
    category: FormatCategory
    mime_type: str | None = None
    format_name: str | None = None  # Human-readable name
    is_binary: bool = False


# Extension to format mapping
EXTENSION_MAP: dict[str, tuple[FormatCategory, str, bool]] = {
    # Text-based files
    ".txt": (FormatCategory.TEXT, "Plain Text", False),
    ".md": (FormatCategory.TEXT, "Markdown", False),
    ".rst": (FormatCategory.TEXT, "reStructuredText", False),
    ".py": (FormatCategory.TEXT, "Python", False),
    ".js": (FormatCategory.TEXT, "JavaScript", False),
    ".ts": (FormatCategory.TEXT, "TypeScript", False),
    ".jsx": (FormatCategory.TEXT, "JSX", False),
    ".tsx": (FormatCategory.TEXT, "TSX", False),
    ".java": (FormatCategory.TEXT, "Java", False),
    ".c": (FormatCategory.TEXT, "C", False),
    ".cpp": (FormatCategory.TEXT, "C++", False),
    ".cc": (FormatCategory.TEXT, "C++", False),
    ".h": (FormatCategory.TEXT, "C Header", False),
    ".hpp": (FormatCategory.TEXT, "C++ Header", False),
    ".go": (FormatCategory.TEXT, "Go", False),
    ".rs": (FormatCategory.TEXT, "Rust", False),
    ".rb": (FormatCategory.TEXT, "Ruby", False),
    ".php": (FormatCategory.TEXT, "PHP", False),
    ".pl": (FormatCategory.TEXT, "Perl", False),
    ".pm": (FormatCategory.TEXT, "Perl Module", False),
    ".sh": (FormatCategory.TEXT, "Shell Script", False),
    ".bash": (FormatCategory.TEXT, "Bash Script", False),
    ".zsh": (FormatCategory.TEXT, "Zsh Script", False),
    ".fish": (FormatCategory.TEXT, "Fish Script", False),
    ".ps1": (FormatCategory.TEXT, "PowerShell", False),
    ".bat": (FormatCategory.TEXT, "Batch Script", False),
    ".cmd": (FormatCategory.TEXT, "Command Script", False),
    ".yaml": (FormatCategory.TEXT, "YAML", False),
    ".yml": (FormatCategory.TEXT, "YAML", False),
    ".json": (FormatCategory.TEXT, "JSON", False),
    ".toml": (FormatCategory.TEXT, "TOML", False),
    ".ini": (FormatCategory.TEXT, "INI", False),
    ".cfg": (FormatCategory.TEXT, "Config", False),
    ".conf": (FormatCategory.TEXT, "Config", False),
    ".html": (FormatCategory.TEXT, "HTML", False),
    ".htm": (FormatCategory.TEXT, "HTML", False),
    ".xhtml": (FormatCategory.TEXT, "XHTML", False),
    ".css": (FormatCategory.TEXT, "CSS", False),
    ".scss": (FormatCategory.TEXT, "SCSS", False),
    ".sass": (FormatCategory.TEXT, "Sass", False),
    ".less": (FormatCategory.TEXT, "Less", False),
    ".xml": (FormatCategory.TEXT, "XML", False),
    ".xsl": (FormatCategory.TEXT, "XSL", False),
    ".xslt": (FormatCategory.TEXT, "XSLT", False),
    ".svg": (FormatCategory.TEXT, "SVG", False),
    ".sql": (FormatCategory.TEXT, "SQL", False),
    ".graphql": (FormatCategory.TEXT, "GraphQL", False),
    ".gql": (FormatCategory.TEXT, "GraphQL", False),
    ".proto": (FormatCategory.TEXT, "Protocol Buffers", False),
    ".swift": (FormatCategory.TEXT, "Swift", False),
    ".kt": (FormatCategory.TEXT, "Kotlin", False),
    ".kts": (FormatCategory.TEXT, "Kotlin Script", False),
    ".scala": (FormatCategory.TEXT, "Scala", False),
    ".clj": (FormatCategory.TEXT, "Clojure", False),
    ".cljs": (FormatCategory.TEXT, "ClojureScript", False),
    ".ex": (FormatCategory.TEXT, "Elixir", False),
    ".exs": (FormatCategory.TEXT, "Elixir Script", False),
    ".erl": (FormatCategory.TEXT, "Erlang", False),
    ".hrl": (FormatCategory.TEXT, "Erlang Header", False),
    ".hs": (FormatCategory.TEXT, "Haskell", False),
    ".lhs": (FormatCategory.TEXT, "Literate Haskell", False),
    ".lua": (FormatCategory.TEXT, "Lua", False),
    ".r": (FormatCategory.TEXT, "R", False),
    ".R": (FormatCategory.TEXT, "R", False),
    ".jl": (FormatCategory.TEXT, "Julia", False),
    ".m": (FormatCategory.TEXT, "MATLAB/Objective-C", False),
    ".mm": (FormatCategory.TEXT, "Objective-C++", False),
    ".f": (FormatCategory.TEXT, "Fortran", False),
    ".f90": (FormatCategory.TEXT, "Fortran 90", False),
    ".f95": (FormatCategory.TEXT, "Fortran 95", False),
    ".asm": (FormatCategory.TEXT, "Assembly", False),
    ".s": (FormatCategory.TEXT, "Assembly", False),
    ".v": (FormatCategory.TEXT, "Verilog", False),
    ".sv": (FormatCategory.TEXT, "SystemVerilog", False),
    ".vhd": (FormatCategory.TEXT, "VHDL", False),
    ".vhdl": (FormatCategory.TEXT, "VHDL", False),
    ".tcl": (FormatCategory.TEXT, "Tcl", False),
    ".cmake": (FormatCategory.TEXT, "CMake", False),
    ".make": (FormatCategory.TEXT, "Makefile", False),
    ".mk": (FormatCategory.TEXT, "Makefile", False),
    ".dockerfile": (FormatCategory.TEXT, "Dockerfile", False),
    ".vagrantfile": (FormatCategory.TEXT, "Vagrantfile", False),
    ".tf": (FormatCategory.TEXT, "Terraform", False),
    ".tfvars": (FormatCategory.TEXT, "Terraform Vars", False),
    ".hcl": (FormatCategory.TEXT, "HCL", False),
    ".cgi": (FormatCategory.TEXT, "CGI Script", False),
    ".htaccess": (FormatCategory.TEXT, "Apache Config", False),
    ".nginx": (FormatCategory.TEXT, "Nginx Config", False),
    ".env": (FormatCategory.TEXT, "Environment", False),
    ".gitignore": (FormatCategory.TEXT, "Gitignore", False),
    ".dockerignore": (FormatCategory.TEXT, "Dockerignore", False),
    ".editorconfig": (FormatCategory.TEXT, "EditorConfig", False),
    ".log": (FormatCategory.TEXT, "Log File", False),
    # PDF
    ".pdf": (FormatCategory.PDF, "PDF", True),
    # Office documents
    ".docx": (FormatCategory.OFFICE, "Word Document", True),
    ".doc": (FormatCategory.OFFICE, "Word Document (Legacy)", True),
    ".rtf": (FormatCategory.OFFICE, "Rich Text Format", True),
    ".odt": (FormatCategory.OFFICE, "OpenDocument Text", True),
    ".pptx": (FormatCategory.OFFICE, "PowerPoint", True),
    ".ppt": (FormatCategory.OFFICE, "PowerPoint (Legacy)", True),
    ".odp": (FormatCategory.OFFICE, "OpenDocument Presentation", True),
    ".epub": (FormatCategory.OFFICE, "EPUB", True),
    # Archives
    ".zip": (FormatCategory.ARCHIVE, "ZIP Archive", True),
    ".tar": (FormatCategory.ARCHIVE, "TAR Archive", True),
    ".gz": (FormatCategory.ARCHIVE, "Gzip", True),
    ".tgz": (FormatCategory.ARCHIVE, "TAR.GZ Archive", True),
    ".bz2": (FormatCategory.ARCHIVE, "Bzip2", True),
    ".xz": (FormatCategory.ARCHIVE, "XZ", True),
    ".7z": (FormatCategory.ARCHIVE, "7-Zip Archive", True),
    ".rar": (FormatCategory.ARCHIVE, "RAR Archive", True),
    # Images
    ".png": (FormatCategory.IMAGE, "PNG Image", True),
    ".jpg": (FormatCategory.IMAGE, "JPEG Image", True),
    ".jpeg": (FormatCategory.IMAGE, "JPEG Image", True),
    ".gif": (FormatCategory.IMAGE, "GIF Image", True),
    ".bmp": (FormatCategory.IMAGE, "BMP Image", True),
    ".tiff": (FormatCategory.IMAGE, "TIFF Image", True),
    ".tif": (FormatCategory.IMAGE, "TIFF Image", True),
    ".webp": (FormatCategory.IMAGE, "WebP Image", True),
    ".ico": (FormatCategory.IMAGE, "Icon", True),
    ".psd": (FormatCategory.IMAGE, "Photoshop Document", True),
    ".heic": (FormatCategory.IMAGE, "HEIC Image", True),
    ".heif": (FormatCategory.IMAGE, "HEIF Image", True),
    # Tabular data
    ".csv": (FormatCategory.TABULAR, "CSV", False),
    ".tsv": (FormatCategory.TABULAR, "TSV", False),
    ".xlsx": (FormatCategory.TABULAR, "Excel Spreadsheet", True),
    ".xls": (FormatCategory.TABULAR, "Excel Spreadsheet (Legacy)", True),
    ".ods": (FormatCategory.TABULAR, "OpenDocument Spreadsheet", True),
    ".parquet": (FormatCategory.TABULAR, "Apache Parquet", True),
    # Audio
    ".mp3": (FormatCategory.AUDIO, "MP3 Audio", True),
    ".wav": (FormatCategory.AUDIO, "WAV Audio", True),
    ".flac": (FormatCategory.AUDIO, "FLAC Audio", True),
    ".ogg": (FormatCategory.AUDIO, "Ogg Audio", True),
    ".m4a": (FormatCategory.AUDIO, "M4A Audio", True),
    ".aac": (FormatCategory.AUDIO, "AAC Audio", True),
    ".wma": (FormatCategory.AUDIO, "WMA Audio", True),
    ".aiff": (FormatCategory.AUDIO, "AIFF Audio", True),
    # Structured data
    ".sqlite": (FormatCategory.STRUCTURED, "SQLite Database", True),
    ".db": (FormatCategory.STRUCTURED, "Database", True),
    ".sqlite3": (FormatCategory.STRUCTURED, "SQLite Database", True),
}

# Compound extensions (checked first)
COMPOUND_EXTENSIONS = {
    ".tar.gz": (FormatCategory.ARCHIVE, "TAR.GZ Archive", True),
    ".tar.bz2": (FormatCategory.ARCHIVE, "TAR.BZ2 Archive", True),
    ".tar.xz": (FormatCategory.ARCHIVE, "TAR.XZ Archive", True),
}


def _get_mime_type(path: Path) -> str | None:
    """Get MIME type using python-magic."""
    try:
        mime = magic.Magic(mime=True)
        return mime.from_file(str(path))
    except Exception as e:
        logger.debug(f"MIME detection failed for {path}: {e}")
        return None


def _get_extension(path: Path) -> str:
    """Get file extension, handling compound extensions."""
    name = path.name.lower()

    # Check compound extensions first
    for compound_ext in COMPOUND_EXTENSIONS:
        if name.endswith(compound_ext):
            return compound_ext

    # Get simple extension
    suffix = path.suffix.lower()
    return suffix if suffix else ""


def detect_format(path: Path, format_hint: str | None = None) -> FormatInfo:
    """
    Detect file format from path and optionally MIME type.

    Args:
        path: Path to the file
        format_hint: Optional format override (extension without dot)

    Returns:
        FormatInfo with detected format details
    """
    # Use hint if provided
    if format_hint:
        ext = f".{format_hint.lower().lstrip('.')}"
    else:
        ext = _get_extension(path)

    # Get MIME type for additional info
    mime_type = _get_mime_type(path)

    # Check compound extensions first
    if ext in COMPOUND_EXTENSIONS:
        category, name, is_binary = COMPOUND_EXTENSIONS[ext]
        return FormatInfo(
            extension=ext,
            category=category,
            mime_type=mime_type,
            format_name=name,
            is_binary=is_binary,
        )

    # Check simple extension
    if ext in EXTENSION_MAP:
        category, name, is_binary = EXTENSION_MAP[ext]
        return FormatInfo(
            extension=ext,
            category=category,
            mime_type=mime_type,
            format_name=name,
            is_binary=is_binary,
        )

    # Handle special filenames without extension
    filename_lower = path.name.lower()
    if filename_lower in ("makefile", "dockerfile", "vagrantfile", "gemfile", "rakefile"):
        return FormatInfo(
            extension="",
            category=FormatCategory.TEXT,
            mime_type=mime_type,
            format_name=filename_lower.capitalize(),
            is_binary=False,
        )

    # Unknown format - try to detect from MIME
    if mime_type:
        if mime_type.startswith("text/"):
            return FormatInfo(
                extension=ext,
                category=FormatCategory.TEXT,
                mime_type=mime_type,
                format_name="Text File",
                is_binary=False,
            )
        elif mime_type.startswith("image/"):
            return FormatInfo(
                extension=ext,
                category=FormatCategory.IMAGE,
                mime_type=mime_type,
                format_name="Image",
                is_binary=True,
            )
        elif mime_type.startswith("audio/"):
            return FormatInfo(
                extension=ext,
                category=FormatCategory.AUDIO,
                mime_type=mime_type,
                format_name="Audio",
                is_binary=True,
            )

    # Truly unknown
    logger.warning(f"Unknown format: extension={ext}, mime={mime_type}")
    return FormatInfo(
        extension=ext,
        category=FormatCategory.UNKNOWN,
        mime_type=mime_type,
        format_name="Unknown",
        is_binary=True,  # Assume binary for safety
    )


def is_text_format(format_info: FormatInfo) -> bool:
    """Check if format is text-based (can be read as plain text)."""
    return format_info.category == FormatCategory.TEXT and not format_info.is_binary


def is_cacheable(format_info: FormatInfo) -> bool:
    """Check if format results should be cached."""
    return format_info.category in (
        FormatCategory.PDF,
        FormatCategory.OFFICE,
        FormatCategory.ARCHIVE,
    )
