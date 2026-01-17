"""
Inbound WAF (Web Application Firewall) Filter for Ag3ntum API.

Provides security filtering for all incoming requests:
- Content size limits (prevent DoS via large payloads)
- Text content truncation (prevent memory exhaustion)
- File upload limits (prevent storage exhaustion)

This module enforces basic security constraints before request processing.
"""
import logging
from typing import Any

from fastapi import HTTPException, Request, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration Constants
# =============================================================================

# Maximum text content length (5MB)
# Applies to: task descriptions, file content strings, etc.
# Note: This must be higher than large_input.threshold_bytes (200KB) in agent.yaml
# so that large input processing can work correctly
MAX_TEXT_CONTENT_LENGTH: int = 5 * 1024 * 1024  # 5MB

# Maximum file upload size (10MB in bytes)
# Can be overridden by file_upload.max_file_size_bytes in agent.yaml
MAX_FILE_UPLOAD_SIZE: int = 10 * 1024 * 1024  # 10MB

# Maximum number of files per upload request
# Can be overridden by file_upload.max_files_per_upload in agent.yaml
MAX_FILES_PER_UPLOAD: int = 20

# Maximum total upload size per request (50MB)
# Can be overridden by file_upload.max_total_upload_size_bytes in agent.yaml
MAX_TOTAL_UPLOAD_SIZE: int = 50 * 1024 * 1024  # 50MB

# Maximum JSON request body size (60MB - allows for large multipart uploads)
# Must be >= MAX_TOTAL_UPLOAD_SIZE + overhead for multipart encoding
MAX_REQUEST_BODY_SIZE: int = 60 * 1024 * 1024  # 60MB

# Blocked file extensions (dangerous executable types)
# Can be overridden by file_upload.blocked_extensions in agent.yaml
BLOCKED_EXTENSIONS: set[str] = {
    ".exe", ".dll", ".so", ".dylib",
    ".sh", ".bat", ".cmd", ".ps1", ".vbs",
    ".jar",
}

# Allowed file extensions (empty = all allowed except blocked)
# Can be overridden by file_upload.allowed_extensions in agent.yaml
ALLOWED_EXTENSIONS: set[str] = set()


# =============================================================================
# Filter Functions
# =============================================================================

def truncate_text_content(text: str | None, field_name: str = "content") -> str | None:
    """
    Truncate text content to MAX_TEXT_CONTENT_LENGTH.
    
    Args:
        text: Text content to truncate
        field_name: Name of the field (for logging)
        
    Returns:
        Truncated text or None if input is None
    """
    if text is None:
        return None
    
    if not isinstance(text, str):
        return text
    
    original_length = len(text)
    
    if original_length > MAX_TEXT_CONTENT_LENGTH:
        logger.warning(
            f"WAF: Truncating {field_name} from {original_length:,} "
            f"to {MAX_TEXT_CONTENT_LENGTH:,} characters"
        )
        truncated = text[:MAX_TEXT_CONTENT_LENGTH]
        return truncated
    
    return text


def validate_file_size(content_length: int, max_size: int | None = None) -> None:
    """
    Validate file upload size.

    Args:
        content_length: Size in bytes
        max_size: Optional custom max size (defaults to MAX_FILE_UPLOAD_SIZE)

    Raises:
        HTTPException: If size exceeds limit
    """
    limit = max_size if max_size is not None else MAX_FILE_UPLOAD_SIZE
    if content_length > limit:
        size_mb = content_length / (1024 * 1024)
        limit_mb = limit / (1024 * 1024)
        logger.warning(
            f"WAF: Rejected file upload - size {size_mb:.2f}MB exceeds limit {limit_mb:.2f}MB"
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size ({size_mb:.1f}MB) exceeds maximum allowed size ({limit_mb}MB)"
        )


def validate_file_count(file_count: int, max_files: int | None = None) -> None:
    """
    Validate number of files in an upload request.

    Args:
        file_count: Number of files being uploaded
        max_files: Optional custom max count (defaults to MAX_FILES_PER_UPLOAD)

    Raises:
        HTTPException: If count exceeds limit
    """
    limit = max_files if max_files is not None else MAX_FILES_PER_UPLOAD
    if file_count > limit:
        logger.warning(
            f"WAF: Rejected upload - {file_count} files exceeds limit of {limit}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many files ({file_count}). Maximum allowed is {limit} files per upload."
        )


def validate_total_upload_size(total_size: int, max_total: int | None = None) -> None:
    """
    Validate total size of all files in an upload request.

    Args:
        total_size: Total size in bytes
        max_total: Optional custom max size (defaults to MAX_TOTAL_UPLOAD_SIZE)

    Raises:
        HTTPException: If total size exceeds limit
    """
    limit = max_total if max_total is not None else MAX_TOTAL_UPLOAD_SIZE
    if total_size > limit:
        size_mb = total_size / (1024 * 1024)
        limit_mb = limit / (1024 * 1024)
        logger.warning(
            f"WAF: Rejected upload - total size {size_mb:.2f}MB exceeds limit {limit_mb:.2f}MB"
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Total upload size ({size_mb:.1f}MB) exceeds maximum allowed ({limit_mb}MB)"
        )


def validate_file_extension(
    filename: str,
    blocked: set[str] | None = None,
    allowed: set[str] | None = None,
) -> None:
    """
    Validate file extension against blocked/allowed lists.

    Args:
        filename: Name of the file
        blocked: Set of blocked extensions (defaults to BLOCKED_EXTENSIONS)
        allowed: Set of allowed extensions (defaults to ALLOWED_EXTENSIONS, empty = all allowed)

    Raises:
        HTTPException: If extension is blocked or not in allowed list
    """
    blocked_set = blocked if blocked is not None else BLOCKED_EXTENSIONS
    allowed_set = allowed if allowed is not None else ALLOWED_EXTENSIONS

    # Get extension (lowercase for comparison)
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[-1].lower()

    # Check if blocked
    if ext in blocked_set:
        logger.warning(f"WAF: Rejected file upload - blocked extension: {ext}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{ext}' is not allowed for security reasons"
        )

    # Check if allowed (only if allowed list is not empty)
    if allowed_set and ext not in allowed_set:
        logger.warning(f"WAF: Rejected file upload - extension not in allowed list: {ext}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type '{ext}' is not in the allowed file types"
        )


def validate_request_body_size(content_length: int) -> None:
    """
    Validate overall request body size.
    
    Args:
        content_length: Size in bytes
        
    Raises:
        HTTPException: If size exceeds MAX_REQUEST_BODY_SIZE
    """
    if content_length > MAX_REQUEST_BODY_SIZE:
        size_mb = content_length / (1024 * 1024)
        limit_mb = MAX_REQUEST_BODY_SIZE / (1024 * 1024)
        logger.warning(
            f"WAF: Rejected request - body size {size_mb:.2f}MB exceeds limit {limit_mb:.2f}MB"
        )
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Request size ({size_mb:.1f}MB) exceeds maximum allowed size ({limit_mb}MB)"
        )


def filter_request_data(data: dict[str, Any]) -> dict[str, Any]:
    """
    Apply WAF filtering to request data.
    
    Truncates text fields that exceed MAX_TEXT_CONTENT_LENGTH.
    
    Args:
        data: Request data dictionary
        
    Returns:
        Filtered data dictionary
    """
    if not isinstance(data, dict):
        return data
    
    # Fields that should be truncated if too long
    TEXT_FIELDS = {
        "task",           # Task descriptions
        "prompt",         # Prompts
        "message",        # Messages
        "content",        # Content fields
        "text",           # Text fields
        "description",    # Descriptions
        "comments",       # Comments
        "error",          # Error messages
        "output",         # Output text
    }
    
    filtered_data = data.copy()
    
    for field, value in filtered_data.items():
        # Truncate text fields
        if field in TEXT_FIELDS and isinstance(value, str):
            filtered_data[field] = truncate_text_content(value, field)
        
        # Recursively filter nested dicts
        elif isinstance(value, dict):
            filtered_data[field] = filter_request_data(value)
        
        # Filter lists of dicts
        elif isinstance(value, list):
            filtered_data[field] = [
                filter_request_data(item) if isinstance(item, dict) else item
                for item in value
            ]
    
    return filtered_data


def filter_pydantic_model(model: BaseModel) -> BaseModel:
    """
    Apply WAF filtering to a Pydantic model.
    
    Args:
        model: Pydantic model instance
        
    Returns:
        New model instance with filtered data
    """
    # Get model data as dict
    data = model.model_dump()
    
    # Apply filtering
    filtered_data = filter_request_data(data)
    
    # Create new instance with filtered data
    return model.__class__(**filtered_data)


# =============================================================================
# FastAPI Middleware
# =============================================================================

async def validate_request_size(request: Request) -> None:
    """
    Middleware to validate request body size.
    
    Should be called early in request processing.
    
    Args:
        request: FastAPI Request object
        
    Raises:
        HTTPException: If request size exceeds limits
    """
    content_length = request.headers.get("content-length")
    
    if content_length:
        try:
            size = int(content_length)
            validate_request_body_size(size)
        except ValueError:
            # Invalid content-length header - let FastAPI handle it
            pass


# =============================================================================
# Utility Functions
# =============================================================================

def get_text_size_info(text: str | None) -> dict[str, Any]:
    """
    Get size information for text content.
    
    Args:
        text: Text content
        
    Returns:
        Dict with size info: {length, size_bytes, truncated, limit}
    """
    if text is None:
        return {
            "length": 0,
            "size_bytes": 0,
            "truncated": False,
            "limit": MAX_TEXT_CONTENT_LENGTH,
        }
    
    length = len(text)
    size_bytes = len(text.encode("utf-8"))
    truncated = length > MAX_TEXT_CONTENT_LENGTH
    
    return {
        "length": length,
        "size_bytes": size_bytes,
        "truncated": truncated,
        "limit": MAX_TEXT_CONTENT_LENGTH,
    }


def format_size(size_bytes: int) -> str:
    """
    Format size in bytes to human-readable string.
    
    Args:
        size_bytes: Size in bytes
        
    Returns:
        Formatted string (e.g., "1.5MB", "256KB")
    """
    if size_bytes < 1024:
        return f"{size_bytes}B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f}MB"
