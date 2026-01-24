"""
File browsing and upload endpoints for Ag3ntum API.

Provides endpoints for:
- GET /files/{session_id}/browse - List directory contents with tree structure
- GET /files/{session_id}/content - Get file content for preview
- GET /files/{session_id}/download - Download a file
- POST /files/{session_id}/upload - Upload files to workspace
- DELETE /files/{session_id} - Delete a file

Sensitive Data: Text files are scanned for API keys, tokens, passwords in both:
- File uploads: Secrets redacted before writing to disk
- File previews: Secrets redacted before displaying in File Explorer
Detected secrets are redacted with same-length placeholders to preserve formatting.
"""
import logging
import mimetypes
import re
import unicodedata
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.database import get_db
from ...services.session_service import session_service, InvalidSessionIdError
from ...security import scan_and_redact, is_scanner_enabled
from ...services.mount_service import (
    resolve_external_symlink,
    resolve_file_path_for_session,
    normalize_path_for_session,
    is_path_writable_for_session,
    translate_docker_path_to_sandbox,
)
from ...core.sandbox_path_resolver import (
    has_sandbox_path_resolver,
    PathResolutionError,
)
from ..deps import get_current_user_id, get_current_user_id_from_query_or_header
from ..waf_filter import (
    MAX_FILE_UPLOAD_SIZE,
    MAX_FILES_PER_UPLOAD,
    MAX_TOTAL_UPLOAD_SIZE,
    BLOCKED_EXTENSIONS,
    ALLOWED_EXTENSIONS,
    validate_file_count,
    validate_total_upload_size,
    validate_file_extension,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])

# Maximum number of files to return in a single directory listing
MAX_FILES_PER_DIRECTORY = 1000

# Maximum file size for content preview (5MB)
MAX_PREVIEW_SIZE = 5 * 1024 * 1024

# File extensions that can be previewed as text
TEXT_EXTENSIONS = {
    '.txt', '.md', '.py', '.js', '.ts', '.tsx', '.jsx', '.json', '.yaml', '.yml',
    '.xml', '.html', '.css', '.scss', '.less', '.sh', '.bash', '.zsh', '.fish',
    '.sql', '.csv', '.tsv', '.log', '.conf', '.cfg', '.ini', '.env', '.toml',
    '.rs', '.go', '.java', '.c', '.cpp', '.h', '.hpp', '.rb', '.php', '.swift',
    '.kt', '.scala', '.r', '.R', '.m', '.mm', '.lua', '.pl', '.pm', '.vim',
    '.dockerfile', '.makefile', '.cmake', '.gradle', '.properties', '.gitignore',
    '.dockerignore', '.editorconfig', '.prettierrc', '.eslintrc', '.babelrc',
}

# Binary extensions that should not be previewed
BINARY_EXTENSIONS = {
    '.exe', '.dll', '.so', '.dylib', '.bin', '.dat', '.db', '.sqlite', '.sqlite3',
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.ico', '.webp', '.svg', '.pdf',
    '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp',
    '.zip', '.tar', '.gz', '.bz2', '.xz', '.7z', '.rar', '.iso', '.dmg',
    '.mp3', '.mp4', '.avi', '.mov', '.mkv', '.webm', '.wav', '.flac', '.ogg',
    '.woff', '.woff2', '.ttf', '.otf', '.eot',
}


class FileInfo(BaseModel):
    """Information about a file or directory."""
    name: str
    path: str  # Relative path from workspace root
    is_directory: bool
    size: int  # Size in bytes (0 for directories)
    created_at: str  # ISO format timestamp
    modified_at: str  # ISO format timestamp
    mime_type: Optional[str] = None
    is_hidden: bool = False
    is_viewable: bool = False  # True if content can be previewed
    is_readonly: bool = False  # True if file/folder is in read-only area
    is_external: bool = False  # True if file is in external mount
    mount_type: Optional[Literal["ro", "rw", "persistent", "user-ro", "user-rw"]] = None  # Type of external mount
    children: Optional[list["FileInfo"]] = None  # For directories when expanded


class DirectoryListing(BaseModel):
    """Response for directory listing."""
    path: str  # Current directory path
    files: list[FileInfo]
    total_count: int  # Total files in directory
    truncated: bool  # True if listing was truncated due to limit


class FileContentResponse(BaseModel):
    """Response for file content preview."""
    path: str
    name: str
    mime_type: str
    size: int
    content: Optional[str] = None  # Text content (if viewable)
    is_binary: bool = False
    is_truncated: bool = False
    error: Optional[str] = None


class UploadedFileInfo(BaseModel):
    """Information about a successfully uploaded file."""
    name: str
    path: str  # Relative path from workspace root
    size: int
    mime_type: str


class UploadResponse(BaseModel):
    """Response for file upload endpoint."""
    uploaded: list[UploadedFileInfo]
    total_count: int
    errors: list[str] = []  # Per-file errors (file still may be partially successful)


def get_mime_type(file_path: Path) -> str:
    """Get MIME type for a file."""
    mime_type, _ = mimetypes.guess_type(str(file_path))
    return mime_type or 'application/octet-stream'


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename for safe storage.

    Removes or replaces dangerous characters to prevent:
    - Path traversal attacks (../, /, \\)
    - Null byte injection
    - Control characters
    - Shell metacharacters

    Args:
        filename: Original filename from upload

    Returns:
        Sanitized filename safe for storage

    Raises:
        HTTPException: If filename is empty or invalid after sanitization
    """
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename cannot be empty",
        )

    # Remove null bytes
    sanitized = filename.replace('\x00', '')

    # Remove control characters (ASCII 0-31 except tab which we replace with space)
    sanitized = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', sanitized)
    sanitized = sanitized.replace('\t', ' ')

    # Remove/replace path separators and traversal patterns
    sanitized = sanitized.replace('/', '_')
    sanitized = sanitized.replace('\\', '_')
    sanitized = re.sub(r'\.\.+', '.', sanitized)  # Replace .. with single .

    # Remove shell metacharacters that could be dangerous
    # Keep alphanumeric, spaces, dots, dashes, underscores
    sanitized = re.sub(r'[^\w\s.\-]', '', sanitized, flags=re.UNICODE)

    # Collapse multiple spaces/underscores
    sanitized = re.sub(r'[\s_]+', '_', sanitized)

    # Remove leading/trailing whitespace and dots (hidden files on Unix)
    sanitized = sanitized.strip(' ._')

    # Ensure we have something left
    if not sanitized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename contains only invalid characters",
        )

    # Limit length to 255 characters (common filesystem limit)
    if len(sanitized) > 255:
        # Try to preserve extension
        name_parts = sanitized.rsplit('.', 1)
        if len(name_parts) == 2 and len(name_parts[1]) < 10:
            ext = '.' + name_parts[1]
            sanitized = name_parts[0][:255 - len(ext)] + ext
        else:
            sanitized = sanitized[:255]

    return sanitized


def validate_path_security(
    user_path: str,
    workspace_root: Path,
) -> Path:
    """
    Validate and resolve a user-provided path securely.

    Args:
        user_path: User-provided relative path
        workspace_root: Absolute path to the workspace root

    Returns:
        Resolved absolute path that is guaranteed to be within workspace.
        For external mount paths with symlinks, returns the unresolved path
        to handle Docker container mount symlinks that point to non-existent
        host paths.

    Raises:
        HTTPException: If the path is invalid or escapes the workspace
    """
    # Normalize the workspace root first
    workspace_root = workspace_root.resolve()

    # Reject obviously malicious patterns before any processing
    # Check for null bytes (can truncate paths in some contexts)
    if '\x00' in user_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid path: null bytes not allowed",
        )

    # Normalize the user path - ensure UTF-8 handling
    # URL-decode the path in case it was double-encoded
    try:
        # First URL-decode (handles %XX sequences)
        decoded_path = urllib.parse.unquote(user_path)
        # Normalize Unicode to NFC form (macOS uses NFD by default)
        normalized = unicodedata.normalize('NFC', decoded_path.strip())
    except Exception:
        normalized = user_path.strip()

    # Remove leading slashes to ensure it's relative
    while normalized.startswith('/') or normalized.startswith('\\'):
        normalized = normalized[1:]

    # Handle sandbox-format paths (e.g., "/workspace/file.txt" or "workspace/file.txt")
    # These come from agent messages which use the sandbox path format
    if normalized.startswith('workspace/'):
        normalized = normalized[len('workspace/'):]
    elif normalized == 'workspace':
        normalized = ''

    # Check for path traversal attempts (including encoded variants)
    # This catches .., encoded .., and various bypass attempts
    path_parts = normalized.replace('\\', '/').split('/')
    for part in path_parts:
        # Decode common URL encodings that might bypass checks
        decoded_part = part
        try:
            # Handle %2e (.) and %2f (/) encodings
            decoded_part = urllib.parse.unquote(part)
        except Exception:
            pass

        # Check for parent directory traversal
        if decoded_part == '..' or decoded_part.strip() == '..':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid path: parent directory traversal not allowed",
            )

        # Check for empty parts that could indicate path manipulation
        # (e.g., "foo//bar" or "foo/./bar")
        if part == '.' and len(path_parts) > 1:
            # Allow single "." but not in paths
            continue

    # Construct the target path
    target_path = workspace_root / normalized

    # Check if path is within the external/ directory (mount points)
    # External mounts are intentional symlinks that point outside workspace
    is_external_path = normalized.startswith("external/") or normalized == "external"

    # For external paths, we need special handling because symlinks may point
    # to Docker container paths (e.g., /mounts/ro/name) that don't exist on host
    if is_external_path:
        # For external paths, don't follow symlinks - just validate structure
        # Check each component exists (as file, dir, or symlink)
        current = workspace_root
        for part in path_parts:
            if not part or part == '.':
                continue
            current = current / part
            # Use lexists to check if path exists (including broken symlinks)
            if not current.exists() and not current.is_symlink():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Path not found: {normalized}",
                )
        # Return unresolved path for external mounts
        return target_path

    # For non-external paths, resolve to get the real path (follows symlinks)
    try:
        resolved_path = target_path.resolve()
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to resolve path {target_path}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid path",
        )

    # CRITICAL: Verify the resolved path is within the workspace
    try:
        resolved_path.relative_to(workspace_root)
    except ValueError:
        # Path escapes workspace boundary
        logger.warning(
            f"Path traversal attempt blocked: {user_path} resolved to {resolved_path}, "
            f"which is outside workspace {workspace_root}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid path: escapes workspace boundary",
        )

    # Additional check: verify that no component of the path before resolution
    # is a symlink pointing outside the workspace
    # This prevents symlink attacks where a symlink inside workspace points outside
    current = workspace_root
    for part in normalized.replace('\\', '/').split('/'):
        if not part or part == '.':
            continue
        current = current / part
        if current.is_symlink():
            # For non-external symlinks, check if they point outside workspace
            try:
                symlink_target = current.resolve()
                symlink_target.relative_to(workspace_root)
            except ValueError:
                logger.warning(
                    f"Symlink attack blocked: {current} points to {symlink_target}, "
                    f"which is outside workspace {workspace_root}"
                )
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid path: symlink points outside workspace",
                )
            except OSError:
                # Broken symlink or permission error
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid path: cannot resolve symlink",
                )

    return resolved_path


def validate_and_resolve_path_for_session(
    session_id: str,
    sandbox_path: str,
) -> tuple[Path, bool, str]:
    """
    Validate and resolve a sandbox path using the session's SandboxPathResolver.

    This is the standard method for path resolution in the File Explorer API.
    It uses the SandboxPathResolver for consistent path handling across all
    components and provides better error messages with sandbox paths.

    Args:
        session_id: The session ID
        sandbox_path: Path in sandbox format (e.g., 'external/persistent/file.png')

    Returns:
        Tuple of (docker_path, is_external, mount_type):
        - docker_path: Resolved Docker filesystem path
        - is_external: True if path is in an external mount
        - mount_type: Type of mount ('workspace', 'persistent', 'external_ro', etc.)

    Raises:
        HTTPException: If path is invalid or cannot be resolved
    """
    if not has_sandbox_path_resolver(session_id):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session path resolver not configured",
        )

    try:
        docker_path, is_external, mount_type = resolve_file_path_for_session(
            session_id, sandbox_path
        )
        return docker_path, is_external, mount_type
    except PathResolutionError as e:
        # Translate error to HTTPException with user-friendly message
        logger.warning(f"Path resolution failed for session {session_id}: {e}")
        if e.reason == "EMPTY_PATH":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Empty path not allowed",
            )
        elif e.reason == "NULL_BYTES":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid path: null bytes not allowed",
            )
        elif e.reason == "OUTSIDE_MOUNTS":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Path outside allowed directories: {sandbox_path}",
            )
        elif e.reason == "UNKNOWN_MOUNT":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Mount not found: {sandbox_path}",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid path: {sandbox_path}",
            )


def is_viewable_file(file_path: Path) -> bool:
    """Check if a file can be previewed as text."""
    ext = file_path.suffix.lower()

    # Check known text extensions
    if ext in TEXT_EXTENSIONS:
        return True

    # Check known binary extensions
    if ext in BINARY_EXTENSIONS:
        return False

    # Check MIME type
    mime_type = get_mime_type(file_path)
    if mime_type.startswith('text/'):
        return True
    if mime_type in ('application/json', 'application/xml', 'application/javascript'):
        return True

    return False


def normalize_path_for_mount_check(path: str) -> str:
    """
    Normalize a path for external mount checking.

    Strips /workspace/ prefix and leading slashes so paths from agent messages
    (which use sandbox format like "/workspace/external/...") can be correctly
    identified as external mount paths.

    Args:
        path: Path that may be in sandbox format or relative format

    Returns:
        Normalized relative path (e.g., "external/persistent/file.txt")
    """
    normalized = path.replace("\\", "/")

    # Remove leading slashes
    while normalized.startswith("/"):
        normalized = normalized[1:]

    # Remove workspace/ prefix (sandbox format from agent messages)
    if normalized.startswith("workspace/"):
        normalized = normalized[len("workspace/"):]
    elif normalized == "workspace":
        normalized = ""

    return normalized


def get_mount_info(relative_path: str) -> tuple[bool, bool, Optional[str]]:
    """
    Determine if a path is in an external mount and its type.

    Args:
        relative_path: Path relative to workspace root (can include /workspace/ prefix)

    Returns:
        Tuple of (is_external, is_readonly, mount_type)
        mount_type is one of: "ro", "rw", "persistent", "user-ro", "user-rw", or None
    """
    # Normalize path separators and strip workspace prefix
    normalized = normalize_path_for_mount_check(relative_path)

    # Check for external mount paths (order matters - more specific first)
    if normalized.startswith("external/user-ro/") or normalized == "external/user-ro":
        return True, True, "user-ro"
    elif normalized.startswith("external/user-rw/") or normalized == "external/user-rw":
        return True, False, "user-rw"
    elif normalized.startswith("external/ro/") or normalized == "external/ro":
        return True, True, "ro"
    elif normalized.startswith("external/rw/") or normalized == "external/rw":
        return True, False, "rw"
    elif normalized.startswith("external/persistent/") or normalized == "external/persistent":
        return True, False, "persistent"
    elif normalized == "external":
        # The external directory itself
        return True, False, None

    return False, False, None


def get_file_info(
    file_path: Path,
    workspace_root: Path,
    include_hidden: bool = False,
    relative_path_prefix: Optional[str] = None
) -> Optional[FileInfo]:
    """
    Get information about a file or directory.

    Args:
        file_path: Absolute path to the file
        workspace_root: Root workspace directory for relative path calculation
        include_hidden: Whether to include hidden files
        relative_path_prefix: If provided, use this prefix for the relative path
            instead of computing from workspace_root. Used for external mounts.

    Returns:
        FileInfo object or None if file should be excluded
    """
    try:
        # Normalize filename to NFC (macOS uses NFD by default)
        name = unicodedata.normalize('NFC', file_path.name)
        is_hidden = name.startswith('.')

        # Skip hidden files if not requested
        if is_hidden and not include_hidden:
            return None

        # Get file stats
        file_stat = file_path.stat()

        # Calculate relative path
        if relative_path_prefix:
            # For external mounts, use the provided prefix
            relative_path = f"{relative_path_prefix}/{name}"
        else:
            try:
                relative_path = str(file_path.relative_to(workspace_root))
            except ValueError:
                relative_path = name

        is_dir = file_path.is_dir()

        # Determine mount info (is_external, is_readonly, mount_type)
        is_external, is_readonly, mount_type = get_mount_info(relative_path)

        return FileInfo(
            name=name,
            path=relative_path,
            is_directory=is_dir,
            size=0 if is_dir else file_stat.st_size,
            created_at=datetime.fromtimestamp(
                file_stat.st_ctime, tz=timezone.utc
            ).isoformat(),
            modified_at=datetime.fromtimestamp(
                file_stat.st_mtime, tz=timezone.utc
            ).isoformat(),
            mime_type=None if is_dir else get_mime_type(file_path),
            is_hidden=is_hidden,
            is_viewable=False if is_dir else is_viewable_file(file_path),
            is_readonly=is_readonly,
            is_external=is_external,
            mount_type=mount_type,
        )
    except (OSError, PermissionError) as e:
        logger.warning(f"Failed to get info for {file_path}: {e}")
        return None


def list_directory(
    directory: Path,
    workspace_root: Path,
    include_hidden: bool = False,
    sort_by: str = "modified_at",
    sort_order: str = "desc",
    limit: int = MAX_FILES_PER_DIRECTORY,
    relative_path_prefix: Optional[str] = None,
) -> tuple[list[FileInfo], int, bool]:
    """
    List contents of a directory.

    Args:
        directory: Directory to list
        workspace_root: Root workspace for relative paths
        include_hidden: Whether to include hidden files
        sort_by: Field to sort by (name, size, created_at, modified_at)
        sort_order: Sort order (asc, desc)
        limit: Maximum number of items to return
        relative_path_prefix: If provided, use this prefix for relative paths
            instead of computing from workspace_root. Used for external mounts
            where the actual directory is outside the workspace.

    Returns:
        Tuple of (file_list, total_count, truncated)
    """
    files: list[FileInfo] = []

    try:
        entries = list(directory.iterdir())
    except (OSError, PermissionError) as e:
        logger.warning(f"Failed to list directory {directory}: {e}")
        return [], 0, False

    # Get file info for each entry
    for entry in entries:
        info = get_file_info(entry, workspace_root, include_hidden, relative_path_prefix)
        if info:
            files.append(info)

    total_count = len(files)

    # Sort files: directories always first, then by sort criteria within each group
    reverse = sort_order == "desc"

    # Separate directories and files
    dirs = [f for f in files if f.is_directory]
    regular_files = [f for f in files if not f.is_directory]

    # Define sort key based on field (without directory priority - we handle that separately)
    def get_sort_key(f: FileInfo):
        if sort_by == "name":
            return f.name.lower()
        elif sort_by == "size":
            return f.size
        elif sort_by == "created_at":
            return f.created_at
        else:  # modified_at (default)
            return f.modified_at

    # Sort each group separately
    dirs.sort(key=get_sort_key, reverse=reverse)
    regular_files.sort(key=get_sort_key, reverse=reverse)

    # Combine: directories first, then files
    files = dirs + regular_files

    # Apply limit
    truncated = len(files) > limit
    files = files[:limit]

    return files, total_count, truncated


# =============================================================================
# GET /files/{session_id}/browse - List directory contents
# =============================================================================

@router.get("/{session_id}/browse", response_model=DirectoryListing)
async def browse_files(
    session_id: str,
    path: str = Query(default="", description="Relative path to browse (empty for root)"),
    include_hidden: bool = Query(default=False, description="Include hidden files"),
    sort_by: Literal["name", "size", "created_at", "modified_at"] = Query(
        default="modified_at", description="Field to sort by"
    ),
    sort_order: Literal["asc", "desc"] = Query(
        default="desc", description="Sort order"
    ),
    limit: int = Query(default=MAX_FILES_PER_DIRECTORY, le=MAX_FILES_PER_DIRECTORY),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> DirectoryListing:
    """
    Browse files in a session's workspace.

    Returns a list of files and directories at the specified path.
    Directories are listed first, followed by files.
    """
    # Validate and get session
    try:
        session = await session_service.get_session(
            db=db,
            session_id=session_id,
            user_id=user_id,
        )
    except InvalidSessionIdError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Get workspace directory from session's working_dir
    if not session.working_dir:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session working directory not configured",
        )

    workspace_root = Path(session.working_dir) / "workspace"

    # Ensure workspace exists
    if not workspace_root.exists():
        workspace_root.mkdir(parents=True, exist_ok=True)

    # Resolve target directory with security validation
    if path:
        target_dir = validate_path_security(path, workspace_root)
    else:
        target_dir = workspace_root.resolve()

    # Check if this is an external mount path that needs special handling
    # Normalize the path to handle sandbox format (e.g., /workspace/external/...)
    normalized_path = normalize_path_for_mount_check(path) if path else ""
    is_external_path = normalized_path.startswith("external/") or normalized_path == "external"
    actual_dir = target_dir

    if is_external_path:
        # For external paths, try to resolve the mount symlink to actual path
        if target_dir.is_symlink():
            resolved = resolve_external_symlink(target_dir)
            if resolved:
                actual_dir = resolved
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"External mount not accessible: {path}",
                )
        elif not target_dir.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Directory not found: {path}",
            )
    else:
        if not target_dir.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Directory not found: {path}",
            )

    if not actual_dir.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not a directory: {path}",
        )

    # List directory contents (use resolved workspace root for consistency)
    files, total_count, truncated = list_directory(
        directory=actual_dir,
        workspace_root=workspace_root.resolve(),
        include_hidden=include_hidden,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        relative_path_prefix=normalized_path if is_external_path else None,
    )

    return DirectoryListing(
        path=path or "/",
        files=files,
        total_count=total_count,
        truncated=truncated,
    )


# =============================================================================
# GET /files/{session_id}/content - Get file content for preview
# =============================================================================

@router.get("/{session_id}/content", response_model=FileContentResponse)
async def get_file_content(
    session_id: str,
    path: str = Query(..., description="Relative path to the file"),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> FileContentResponse:
    """
    Get file content for preview.

    Returns file content as text if the file is viewable (text-based).
    For binary files, returns metadata only with is_binary=True.
    """
    # Validate and get session
    try:
        session = await session_service.get_session(
            db=db,
            session_id=session_id,
            user_id=user_id,
        )
    except InvalidSessionIdError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Validate and resolve path using session-aware resolver
    actual_file, is_external, mount_type = validate_and_resolve_path_for_session(
        session_id, path
    )

    if not actual_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {path}",
        )

    if actual_file.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot preview directory: {path}",
        )

    # Get file info
    file_stat = actual_file.stat()
    mime_type = get_mime_type(actual_file)
    is_binary = not is_viewable_file(actual_file)

    response = FileContentResponse(
        path=path,
        name=actual_file.name,
        mime_type=mime_type,
        size=file_stat.st_size,
        is_binary=is_binary,
    )

    # Read content if viewable
    if not is_binary:
        try:
            if file_stat.st_size > MAX_PREVIEW_SIZE:
                # Read only first portion
                with open(actual_file, 'r', encoding='utf-8', errors='replace') as f:
                    response.content = f.read(MAX_PREVIEW_SIZE)
                response.is_truncated = True
            else:
                with open(actual_file, 'r', encoding='utf-8', errors='replace') as f:
                    response.content = f.read()

            # Scan content for secrets and redact before returning
            if is_scanner_enabled() and response.content:
                try:
                    scan_result = scan_and_redact(response.content)
                    if scan_result.has_secrets:
                        response.content = scan_result.redacted_text
                        logger.info(
                            f"Redacted {scan_result.secret_count} secrets from file preview: {path}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to scan file content for secrets: {e}")

        except Exception as e:
            logger.warning(f"Failed to read file {actual_file}: {e}")
            response.is_binary = True
            response.error = "Failed to read file content"

    return response


# =============================================================================
# GET /files/{session_id}/download - Download a file
# =============================================================================

@router.get("/{session_id}/download")
async def download_file(
    session_id: str,
    path: str = Query(..., description="Relative path to the file"),
    user_id: str = Depends(get_current_user_id_from_query_or_header),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """
    Download a file from the session workspace.

    Returns the file with appropriate Content-Disposition header for download.

    Note: This endpoint accepts authentication via either:
    - Authorization header (Bearer token)
    - Query parameter 'token' (for browser downloads via window.open)
    """
    # Validate and get session
    try:
        session = await session_service.get_session(
            db=db,
            session_id=session_id,
            user_id=user_id,
        )
    except InvalidSessionIdError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Get workspace and resolve file path
    if not session.working_dir:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session working directory not configured",
        )

    workspace_root = Path(session.working_dir) / "workspace"

    # Validate and resolve path using session-aware resolver
    # This uses SandboxPathResolver for consistent path handling
    actual_file, is_external, mount_type = validate_and_resolve_path_for_session(
        session_id, path, workspace_root
    )

    if not actual_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {path}",
        )

    if actual_file.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot download directory: {path}",
        )

    logger.debug(
        f"Download: {path} -> {actual_file} (external={is_external}, type={mount_type})"
    )

    return FileResponse(
        path=actual_file,
        filename=actual_file.name,
        media_type=get_mime_type(actual_file),
    )


# =============================================================================
# POST /files/{session_id}/upload - Upload files to workspace
# =============================================================================

def _get_unique_filename(target_dir: Path, filename: str) -> str:
    """
    Generate a unique filename by adding a numeric suffix if file exists.

    Args:
        target_dir: Directory where file will be saved
        filename: Original filename

    Returns:
        Unique filename that doesn't conflict with existing files
    """
    target_path = target_dir / filename
    if not target_path.exists():
        return filename

    # Split name and extension
    if "." in filename:
        name, ext = filename.rsplit(".", 1)
        ext = "." + ext
    else:
        name = filename
        ext = ""

    # Find unique name with counter
    counter = 1
    while True:
        new_name = f"{name}_{counter}{ext}"
        if not (target_dir / new_name).exists():
            return new_name
        counter += 1
        # Safety limit to prevent infinite loop
        if counter > 10000:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot generate unique filename for {filename}"
            )


@router.post("/{session_id}/upload", response_model=UploadResponse)
async def upload_files(
    session_id: str,
    files: list[UploadFile] = File(..., description="Files to upload"),
    path: str = Form(default="", description="Target directory (relative to workspace root)"),
    overwrite: bool = Form(default=False, description="Overwrite existing files"),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> UploadResponse:
    """
    Upload files to a session's workspace.

    Files are saved to the specified directory within the workspace.
    By default, existing files are not overwritten (returns error for that file).

    Limits:
    - Max files per upload: 20 (configurable in agent.yaml)
    - Max file size: 10MB per file (configurable in agent.yaml)
    - Max total upload size: 50MB (configurable in agent.yaml)
    - Blocked extensions: .exe, .dll, .so, .sh, .bat, etc.

    Returns list of successfully uploaded files and any per-file errors.
    """
    # ==========================================================================
    # 1. Validate file count upfront
    # ==========================================================================
    validate_file_count(len(files))

    if len(files) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided for upload",
        )

    # ==========================================================================
    # 2. Validate and get session
    # ==========================================================================
    try:
        session = await session_service.get_session(
            db=db,
            session_id=session_id,
            user_id=user_id,
        )
    except InvalidSessionIdError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Get workspace directory
    if not session.working_dir:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session working directory not configured",
        )

    workspace_root = Path(session.working_dir) / "workspace"

    # Ensure workspace exists
    if not workspace_root.exists():
        workspace_root.mkdir(parents=True, exist_ok=True)

    # ==========================================================================
    # 3. Validate target directory path
    # ==========================================================================
    if path:
        target_dir = validate_path_security(path, workspace_root)
        # Ensure target is a directory (create if doesn't exist)
        if target_dir.exists() and not target_dir.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Target path is not a directory: {path}",
            )
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)
    else:
        target_dir = workspace_root

    # ==========================================================================
    # 4. Pre-validate all files (extension check) before processing any
    # ==========================================================================
    for upload_file in files:
        original_filename = upload_file.filename or "unnamed"
        try:
            validate_file_extension(
                original_filename,
                blocked=BLOCKED_EXTENSIONS,
                allowed=ALLOWED_EXTENSIONS if ALLOWED_EXTENSIONS else None,
            )
        except HTTPException as e:
            # Re-raise with filename context
            raise HTTPException(
                status_code=e.status_code,
                detail=f"{original_filename}: {e.detail}",
            )

    # ==========================================================================
    # 5. Process files with size tracking
    # ==========================================================================
    uploaded: list[UploadedFileInfo] = []
    errors: list[str] = []
    total_size: int = 0
    used_filenames: set[str] = set()  # Track filenames to detect collisions after sanitization

    for upload_file in files:
        try:
            original_filename = upload_file.filename or "unnamed"

            # Sanitize filename
            try:
                safe_filename = sanitize_filename(original_filename)
            except HTTPException as e:
                errors.append(f"{original_filename}: {e.detail}")
                continue

            # Handle filename collision from sanitization (e.g., "a!b.txt" and "a@b.txt" both become "ab.txt")
            if safe_filename in used_filenames:
                safe_filename = _get_unique_filename(target_dir, safe_filename)
            used_filenames.add(safe_filename)

            # Construct target file path
            target_path = target_dir / safe_filename

            # Check if file already exists
            if target_path.exists() and not overwrite:
                errors.append(f"{safe_filename}: File already exists (use overwrite=true to replace)")
                continue

            # Check if target is a symlink (don't overwrite symlinks for security)
            if target_path.exists() and target_path.is_symlink():
                errors.append(f"{safe_filename}: Cannot overwrite symlinks for security reasons")
                continue

            # Read file content in chunks to check size without loading all into memory first
            # For files under MAX_FILE_UPLOAD_SIZE, we read fully; for larger, we detect early
            CHUNK_SIZE = 1024 * 1024  # 1MB chunks
            content_chunks: list[bytes] = []
            file_size = 0

            while True:
                chunk = await upload_file.read(CHUNK_SIZE)
                if not chunk:
                    break
                file_size += len(chunk)

                # Check per-file size limit early
                if file_size > MAX_FILE_UPLOAD_SIZE:
                    size_mb = file_size / (1024 * 1024)
                    limit_mb = MAX_FILE_UPLOAD_SIZE / (1024 * 1024)
                    errors.append(
                        f"{safe_filename}: File size exceeds maximum allowed size ({limit_mb:.0f}MB)"
                    )
                    break

                # Check total upload size limit early
                if total_size + file_size > MAX_TOTAL_UPLOAD_SIZE:
                    total_mb = (total_size + file_size) / (1024 * 1024)
                    limit_mb = MAX_TOTAL_UPLOAD_SIZE / (1024 * 1024)
                    errors.append(
                        f"{safe_filename}: Total upload size would exceed limit ({limit_mb:.0f}MB)"
                    )
                    break

                content_chunks.append(chunk)

            # Skip if size limits were exceeded
            if file_size > MAX_FILE_UPLOAD_SIZE or total_size + file_size > MAX_TOTAL_UPLOAD_SIZE:
                continue

            # Combine chunks and write file
            content = b"".join(content_chunks)
            file_size = len(content)  # Recalculate exact size

            # Final size validation
            if file_size > MAX_FILE_UPLOAD_SIZE:
                size_mb = file_size / (1024 * 1024)
                limit_mb = MAX_FILE_UPLOAD_SIZE / (1024 * 1024)
                errors.append(
                    f"{safe_filename}: File size ({size_mb:.1f}MB) exceeds "
                    f"maximum allowed size ({limit_mb:.0f}MB)"
                )
                continue

            # Check total size before writing
            if total_size + file_size > MAX_TOTAL_UPLOAD_SIZE:
                validate_total_upload_size(total_size + file_size)  # Will raise HTTPException

            # Scan text files for sensitive data before writing
            secrets_redacted = 0
            ext = Path(safe_filename).suffix.lower()
            is_text_file = ext in TEXT_EXTENSIONS

            if is_scanner_enabled() and is_text_file:
                try:
                    # Decode as text for scanning
                    text_content = content.decode("utf-8", errors="replace")
                    scan_result = scan_and_redact(text_content)
                    if scan_result.has_secrets:
                        # Re-encode the redacted content
                        content = scan_result.redacted_text.encode("utf-8")
                        file_size = len(content)
                        secrets_redacted = scan_result.secret_count
                        logger.warning(
                            f"Redacted {secrets_redacted} secrets from uploaded file "
                            f"{safe_filename} in session {session_id}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to scan uploaded file {safe_filename}: {e}")

            # Write file atomically (write to temp, then rename)
            temp_path = target_path.with_suffix(target_path.suffix + ".tmp")
            try:
                temp_path.write_bytes(content)
                temp_path.rename(target_path)
            except Exception as e:
                # Clean up temp file if it exists
                if temp_path.exists():
                    temp_path.unlink()
                raise e

            # Update total size
            total_size += file_size

            # Get relative path for response
            relative_path = str(target_path.relative_to(workspace_root))

            uploaded.append(UploadedFileInfo(
                name=safe_filename,
                path=relative_path,
                size=file_size,
                mime_type=get_mime_type(target_path),
            ))

            log_msg = f"Uploaded file {relative_path} ({file_size} bytes) to session {session_id} by user {user_id}"
            if secrets_redacted > 0:
                log_msg += f" ({secrets_redacted} secrets redacted)"
            logger.info(log_msg)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to upload file {upload_file.filename}: {e}")
            errors.append(f"{upload_file.filename or 'unnamed'}: Upload failed - {str(e)}")

    return UploadResponse(
        uploaded=uploaded,
        total_count=len(uploaded),
        errors=errors,
    )


# =============================================================================
# DELETE /files/{session_id} - Delete a file
# =============================================================================

@router.delete("/{session_id}")
async def delete_file(
    session_id: str,
    path: str = Query(..., description="Relative path to the file"),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Delete a file from the session workspace.

    Returns success status. Cannot delete directories (for safety).
    """
    # Validate and get session
    try:
        session = await session_service.get_session(
            db=db,
            session_id=session_id,
            user_id=user_id,
        )
    except InvalidSessionIdError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Validate and resolve path using session-aware resolver
    actual_file, is_external, mount_type = validate_and_resolve_path_for_session(
        session_id, path
    )

    # Prevent deletion of files in read-only external mounts
    if is_external and mount_type in ("ro", "user-ro", "external_ro", "user_mount_ro"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete files in read-only external mounts",
        )

    if not actual_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {path}",
        )

    if actual_file.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete directories. Delete files individually.",
        )

    # Additional safety check: don't delete symlinks that point outside workspace
    # (validate_path_security already checks this, but defense in depth)
    if actual_file.is_symlink():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete symlinks for security reasons",
        )

    try:
        actual_file.unlink()
        logger.info(f"Deleted file {path} from session {session_id} by user {user_id}")
        return {"status": "deleted", "path": path}
    except Exception as e:
        logger.error(f"Failed to delete file {path}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete file: {str(e)}",
        )


# =============================================================================
# GET /files/{session_id}/mounts - Get mount configuration
# =============================================================================

class MountInfo(BaseModel):
    """Information about an external mount."""
    name: str
    path: str  # Relative path from workspace root (e.g., "./external/ro/downloads")
    description: Optional[str] = None


class MountsResponse(BaseModel):
    """Response for mount configuration endpoint."""
    ro: list[MountInfo]  # Read-only mounts
    rw: list[MountInfo]  # Read-write mounts
    persistent: bool  # Whether persistent storage is available


@router.get("/{session_id}/mounts", response_model=MountsResponse)
async def get_mounts(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> MountsResponse:
    """
    Get external mount configuration for a session.

    Returns information about available external mounts:
    - ro: Read-only mounts (agent cannot write)
    - rw: Read-write mounts (agent can modify)
    - persistent: Whether persistent storage is available
    """
    import yaml

    # Validate and get session
    try:
        session = await session_service.get_session(
            db=db,
            session_id=session_id,
            user_id=user_id,
        )
    except InvalidSessionIdError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format",
        )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Load mounts configuration
    # TODO: Consider extracting to shared utility (duplicated in agent_core.py)
    ro_mounts: list[MountInfo] = []
    rw_mounts: list[MountInfo] = []
    has_persistent = False

    # Load from mounts manifest (auto-generated by run.sh)
    mounts_file = Path("/data/auto-generated/auto-generated-mounts.yaml")
    if mounts_file.exists():
        try:
            with open(mounts_file, "r", encoding="utf-8") as f:
                manifest = yaml.safe_load(f) or {}

            mounts_data = manifest.get("mounts", {})

            # Read-only mounts
            if isinstance(mounts_data.get("ro"), list):
                for mount in mounts_data["ro"]:
                    if isinstance(mount, dict) and mount.get("name"):
                        ro_mounts.append(MountInfo(
                            name=mount["name"],
                            path=f"./external/ro/{mount['name']}",
                            description=mount.get("description"),
                        ))

            # Read-write mounts
            if isinstance(mounts_data.get("rw"), list):
                for mount in mounts_data["rw"]:
                    if isinstance(mount, dict) and mount.get("name"):
                        rw_mounts.append(MountInfo(
                            name=mount["name"],
                            path=f"./external/rw/{mount['name']}",
                            description=mount.get("description"),
                        ))

        except Exception as e:
            logger.warning(f"Failed to load mounts config: {e}")

    # Check for persistent storage
    # Get username from session's user_id
    from sqlalchemy import select
    from ...db.models import User

    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user:
            persistent_dir = Path(f"/users/{user.username}/ag3ntum/persistent")
            has_persistent = persistent_dir.exists()
    except Exception as e:
        logger.warning(f"Failed to check persistent storage: {e}")

    return MountsResponse(
        ro=ro_mounts,
        rw=rw_mounts,
        persistent=has_persistent,
    )
