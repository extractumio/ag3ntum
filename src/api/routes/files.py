"""
File browsing endpoints for Ag3ntum API.

Provides endpoints for:
- GET /files/{session_id}/browse - List directory contents with tree structure
- GET /files/{session_id}/content - Get file content for preview
- GET /files/{session_id}/download - Download a file
- DELETE /files/{session_id} - Delete a file
"""
import logging
import mimetypes
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.database import get_db
from ...services.session_service import session_service, InvalidSessionIdError
from ..deps import get_current_user_id, get_current_user_id_from_query_or_header

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


def get_mime_type(file_path: Path) -> str:
    """Get MIME type for a file."""
    mime_type, _ = mimetypes.guess_type(str(file_path))
    return mime_type or 'application/octet-stream'


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
        Resolved absolute path that is guaranteed to be within workspace

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

    # Normalize the user path
    normalized = user_path.strip()

    # Remove leading slashes to ensure it's relative
    while normalized.startswith('/') or normalized.startswith('\\'):
        normalized = normalized[1:]

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

    # Resolve to get the real path (follows symlinks)
    try:
        resolved_path = target_path.resolve()
    except (OSError, ValueError) as e:
        logger.warning(f"Failed to resolve path {target_path}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid path",
        )

    # CRITICAL: Verify the resolved path is within the workspace
    # Use try/except for is_relative_to compatibility
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
            # Resolve this symlink and check if it points outside workspace
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


def get_file_info(
    file_path: Path,
    workspace_root: Path,
    include_hidden: bool = False
) -> Optional[FileInfo]:
    """
    Get information about a file or directory.

    Args:
        file_path: Absolute path to the file
        workspace_root: Root workspace directory for relative path calculation
        include_hidden: Whether to include hidden files

    Returns:
        FileInfo object or None if file should be excluded
    """
    try:
        name = file_path.name
        is_hidden = name.startswith('.')

        # Skip hidden files if not requested
        if is_hidden and not include_hidden:
            return None

        # Get file stats
        file_stat = file_path.stat()

        # Calculate relative path
        try:
            relative_path = str(file_path.relative_to(workspace_root))
        except ValueError:
            relative_path = name

        is_dir = file_path.is_dir()

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
        info = get_file_info(entry, workspace_root, include_hidden)
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

    if not target_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Directory not found: {path}",
        )

    if not target_dir.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not a directory: {path}",
        )

    # List directory contents (use resolved workspace root for consistency)
    files, total_count, truncated = list_directory(
        directory=target_dir,
        workspace_root=workspace_root.resolve(),
        include_hidden=include_hidden,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
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

    # Get workspace and resolve file path
    if not session.working_dir:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session working directory not configured",
        )

    workspace_root = Path(session.working_dir) / "workspace"

    # Validate path with security checks
    file_path = validate_path_security(path, workspace_root)

    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {path}",
        )

    if file_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot preview directory: {path}",
        )

    # Get file info
    file_stat = file_path.stat()
    mime_type = get_mime_type(file_path)
    is_binary = not is_viewable_file(file_path)

    response = FileContentResponse(
        path=path,
        name=file_path.name,
        mime_type=mime_type,
        size=file_stat.st_size,
        is_binary=is_binary,
    )

    # Read content if viewable
    if not is_binary:
        try:
            if file_stat.st_size > MAX_PREVIEW_SIZE:
                # Read only first portion
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    response.content = f.read(MAX_PREVIEW_SIZE)
                response.is_truncated = True
            else:
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    response.content = f.read()
        except Exception as e:
            logger.warning(f"Failed to read file {file_path}: {e}")
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

    # Validate path with security checks
    file_path = validate_path_security(path, workspace_root)

    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {path}",
        )

    if file_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot download directory: {path}",
        )

    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type=get_mime_type(file_path),
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

    # Get workspace and resolve file path
    if not session.working_dir:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session working directory not configured",
        )

    workspace_root = Path(session.working_dir) / "workspace"

    # Validate path with security checks
    file_path = validate_path_security(path, workspace_root)

    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {path}",
        )

    if file_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete directories. Delete files individually.",
        )

    # Additional safety check: don't delete symlinks that point outside workspace
    # (validate_path_security already checks this, but defense in depth)
    if file_path.is_symlink():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete symlinks for security reasons",
        )

    try:
        file_path.unlink()
        logger.info(f"Deleted file {path} from session {session_id} by user {user_id}")
        return {"status": "deleted", "path": path}
    except Exception as e:
        logger.error(f"Failed to delete file {path}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete file: {str(e)}",
        )
