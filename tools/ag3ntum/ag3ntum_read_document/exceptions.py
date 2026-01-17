"""
Custom exceptions for ReadDocument tool.

All exceptions inherit from ReadDocumentError for easy catching.
Each exception includes context for logging and error messages.
"""


class ReadDocumentError(Exception):
    """Base exception for all ReadDocument errors."""

    def __init__(self, message: str, context: dict | None = None):
        super().__init__(message)
        self.message = message
        self.context = context or {}

    def __str__(self) -> str:
        if self.context:
            ctx = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{self.message} [{ctx}]"
        return self.message


class FormatNotSupportedError(ReadDocumentError):
    """File format is not supported for reading."""

    def __init__(self, extension: str, mime_type: str | None = None):
        super().__init__(
            f"Unsupported file format: {extension}",
            context={"extension": extension, "mime_type": mime_type},
        )
        self.extension = extension
        self.mime_type = mime_type


class FileTooLargeError(ReadDocumentError):
    """File exceeds the size limit for its format category."""

    def __init__(self, file_size: int, limit: int, category: str):
        super().__init__(
            f"File size {file_size} bytes exceeds limit {limit} bytes for {category}",
            context={"file_size": file_size, "limit": limit, "category": category},
        )
        self.file_size = file_size
        self.limit = limit
        self.category = category


class ExtractionTimeoutError(ReadDocumentError):
    """Content extraction exceeded the time limit."""

    def __init__(self, timeout: float, operation: str):
        super().__init__(
            f"Extraction timed out after {timeout}s during {operation}",
            context={"timeout": timeout, "operation": operation},
        )
        self.timeout = timeout
        self.operation = operation


class ArchiveSecurityError(ReadDocumentError):
    """Archive failed security validation checks."""

    def __init__(self, message: str, reason: str, **kwargs):
        super().__init__(message, context={"reason": reason, **kwargs})
        self.reason = reason


class ZipBombDetectedError(ArchiveSecurityError):
    """Potential zip bomb detected based on compression ratio or size."""

    def __init__(
        self,
        compressed_size: int,
        uncompressed_size: int,
        ratio: float,
        max_ratio: float,
    ):
        super().__init__(
            f"Potential zip bomb: compression ratio {ratio:.1f}:1 exceeds max {max_ratio}:1",
            reason="compression_ratio_exceeded",
            compressed_size=compressed_size,
            uncompressed_size=uncompressed_size,
            ratio=ratio,
            max_ratio=max_ratio,
        )


class BannedExtensionError(ArchiveSecurityError):
    """File has a banned extension and cannot be extracted/read."""

    def __init__(self, filename: str, extension: str):
        super().__init__(
            f"Banned file extension: {extension} in {filename}",
            reason="banned_extension",
            filename=filename,
            extension=extension,
        )
        self.filename = filename
        self.extension = extension


class ArchiveNestingError(ArchiveSecurityError):
    """Archive nesting depth exceeds the maximum allowed."""

    def __init__(self, depth: int, max_depth: int):
        super().__init__(
            f"Archive nesting depth {depth} exceeds maximum {max_depth}",
            reason="nesting_depth_exceeded",
            depth=depth,
            max_depth=max_depth,
        )


class ArchiveFileCountError(ArchiveSecurityError):
    """Archive contains too many files."""

    def __init__(self, count: int, max_count: int):
        super().__init__(
            f"Archive contains {count} files, exceeds maximum {max_count}",
            reason="file_count_exceeded",
            count=count,
            max_count=max_count,
        )


class CacheError(ReadDocumentError):
    """Cache operation failed. Non-fatal - should be logged and ignored."""

    def __init__(self, operation: str, reason: str):
        super().__init__(
            f"Cache {operation} failed: {reason}",
            context={"operation": operation, "reason": reason},
        )
        self.operation = operation
        self.reason = reason


class DependencyMissingError(ReadDocumentError):
    """Required system dependency is not installed."""

    def __init__(self, dependency: str, purpose: str):
        super().__init__(
            f"Required dependency '{dependency}' is not installed. Needed for: {purpose}",
            context={"dependency": dependency, "purpose": purpose},
        )
        self.dependency = dependency
        self.purpose = purpose


class ContentSanitizationError(ReadDocumentError):
    """Content failed sanitization checks or was truncated."""

    def __init__(self, reason: str, original_size: int, sanitized_size: int):
        super().__init__(
            f"Content sanitization: {reason}",
            context={
                "reason": reason,
                "original_size": original_size,
                "sanitized_size": sanitized_size,
            },
        )


class PageRangeError(ReadDocumentError):
    """Invalid page range specification."""

    def __init__(self, page_spec: str, total_pages: int):
        super().__init__(
            f"Invalid page range '{page_spec}' for document with {total_pages} pages",
            context={"page_spec": page_spec, "total_pages": total_pages},
        )


class RowRangeError(ReadDocumentError):
    """Invalid row range specification for tabular data."""

    def __init__(self, row_spec: str, total_rows: int):
        super().__init__(
            f"Invalid row range '{row_spec}' for data with {total_rows} rows",
            context={"row_spec": row_spec, "total_rows": total_rows},
        )


class SheetNotFoundError(ReadDocumentError):
    """Requested sheet does not exist in the workbook."""

    def __init__(self, sheet: str | int, available_sheets: list[str]):
        super().__init__(
            f"Sheet '{sheet}' not found. Available: {available_sheets}",
            context={"requested_sheet": sheet, "available_sheets": available_sheets},
        )


class OCRLimitExceededError(ReadDocumentError):
    """OCR page limit exceeded."""

    def __init__(self, requested_pages: int, max_pages: int):
        super().__init__(
            f"OCR requested for {requested_pages} pages, max allowed is {max_pages}",
            context={"requested_pages": requested_pages, "max_pages": max_pages},
        )


class ArchivePathNotFoundError(ReadDocumentError):
    """Requested path does not exist within the archive."""

    def __init__(self, archive_path: str, internal_path: str):
        super().__init__(
            f"Path '{internal_path}' not found in archive",
            context={"archive_path": archive_path, "internal_path": internal_path},
        )
