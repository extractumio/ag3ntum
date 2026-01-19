"""
Archive extractor for ReadDocument tool.

Handles ZIP, TAR, TAR.GZ, TAR.BZ2, TAR.XZ, and 7z archives.
"""
import asyncio
import fnmatch
import io
import logging
import os
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import get_config
from ..exceptions import (
    ArchivePathNotFoundError,
    ArchiveSecurityError,
    BannedExtensionError,
    ExtractionTimeoutError,
)
from ..security import (
    ArchiveMemberInfo,
    check_banned_extension,
    check_symlink_safety,
    find_archive_member,
    sanitize_archive_path,
    sanitize_output,
    validate_archive_security,
)
from ..utils import format_bytes
from .base import BaseExtractor, ExtractedContent

logger = logging.getLogger(__name__)

# Required dependency for 7z support
import py7zr  # Required: py7zr


class ArchiveExtractor(BaseExtractor):
    """Extractor for archive files."""

    SUPPORTED_EXTENSIONS = {
        ".zip",
        ".tar",
        ".tar.gz",
        ".tgz",
        ".tar.bz2",
        ".tar.xz",
        ".7z",
        ".gz",
        ".bz2",
        ".xz",
    }

    def supports_format(self, extension: str) -> bool:
        """Check if extension is supported."""
        return extension.lower() in self.SUPPORTED_EXTENSIONS

    async def extract(self, path: Path, args: dict[str, Any]) -> ExtractedContent:
        """
        Extract or list archive contents.

        Args:
            path: Path to the archive
            args:
                - mode: "list" (default) | "extract" | "read"
                - archive_path: Path within archive to read/extract
                - pattern: Glob pattern for filtering (e.g., "*.py")
                - include_metadata: Include archive metadata (default: True)

        Returns:
            ExtractedContent with archive listing or file content
        """
        config = get_config()
        mode = args.get("mode", "list")
        archive_path = args.get("archive_path")
        pattern = args.get("pattern")

        ext = self._get_archive_extension(path)

        # Open and analyze archive
        if ext == ".zip":
            return await self._handle_zip(path, mode, archive_path, pattern, config)
        elif ext in (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".gz", ".bz2", ".xz"):
            return await self._handle_tar(path, mode, archive_path, pattern, config)
        elif ext == ".7z":
            return await self._handle_7z(path, mode, archive_path, pattern, config)
        else:
            raise ArchiveSecurityError(f"Unsupported archive format: {ext}", "unsupported_format")

    def _get_archive_extension(self, path: Path) -> str:
        """Get archive extension, handling compound extensions."""
        name = path.name.lower()
        for compound in (".tar.gz", ".tar.bz2", ".tar.xz"):
            if name.endswith(compound):
                return compound
        return path.suffix.lower()

    async def _handle_zip(
        self,
        path: Path,
        mode: str,
        archive_path: str | None,
        pattern: str | None,
        config: Any,
    ) -> ExtractedContent:
        """Handle ZIP archives."""
        archive_config = config.archive

        with zipfile.ZipFile(path, "r") as zf:
            # Get member info for security validation
            members = []
            for info in zf.infolist():
                # Detect symlinks: ZIP stores symlinks with external_attr indicating S_IFLNK
                # or with a special flag. Check both methods.
                is_symlink = (info.external_attr >> 16) & 0o170000 == 0o120000
                members.append(
                    ArchiveMemberInfo(
                        name=info.filename,
                        size=info.file_size,
                        compressed_size=info.compress_size,
                        is_dir=info.is_dir(),
                        is_symlink=is_symlink,
                    )
                )

            # Security validation
            compressed_size = path.stat().st_size
            validate_archive_security(members, compressed_size, archive_config)

            if mode == "list":
                return self._format_listing(path, members, pattern, config)

            elif mode == "read":
                if not archive_path:
                    raise ArchiveSecurityError("archive_path required for read mode", "missing_path")

                safe_path = sanitize_archive_path(archive_path)
                check_banned_extension(safe_path, archive_config)

                # Find matching file using safe comparison
                member = find_archive_member(members, archive_path)
                if not member:
                    raise ArchivePathNotFoundError(str(path), archive_path)

                if member.is_dir:
                    raise ArchiveSecurityError("Cannot read directory", "is_directory")

                # Security: block symlinks
                check_symlink_safety(member)

                if member.size > archive_config.max_single_file:
                    raise ArchiveSecurityError(
                        f"File too large: {member.size} bytes",
                        "file_too_large",
                    )

                # Read content using the original member name (not sanitized)
                content = zf.read(member.name).decode("utf-8", errors="replace")
                sanitized = sanitize_output(content, config.output)

                return ExtractedContent(
                    content=sanitized.content,
                    format_type="File from ZIP Archive",
                    metadata={"source_archive": path.name, "internal_path": safe_path},
                    was_truncated=sanitized.was_truncated,
                )

            elif mode == "extract":
                if not archive_path:
                    raise ArchiveSecurityError("archive_path required for extract mode", "missing_path")

                safe_path = sanitize_archive_path(archive_path)
                check_banned_extension(safe_path, archive_config)

                # Find matching file using safe comparison
                member = find_archive_member(members, archive_path)
                if not member:
                    raise ArchivePathNotFoundError(str(path), archive_path)

                # Security: block symlinks
                check_symlink_safety(member)

                # Determine extraction destination
                workspace = path.parent  # Assume archive is in workspace
                extract_dir = workspace / archive_config.extraction_dir / path.stem
                extract_dir.mkdir(parents=True, exist_ok=True)

                # Use sanitized path for destination (prevents traversal)
                dest_path = extract_dir / safe_path
                dest_path.parent.mkdir(parents=True, exist_ok=True)

                # Safe extraction: read into memory first, then write to safe destination
                # This prevents symlink-following attacks
                file_content = zf.read(member.name)
                with open(dest_path, "wb") as dst:
                    dst.write(file_content)

                return ExtractedContent(
                    content=f"Extracted: {safe_path}\nTo: {dest_path}",
                    format_type="Archive Extraction",
                    metadata={
                        "source_archive": path.name,
                        "internal_path": safe_path,
                        "destination": str(dest_path),
                    },
                )

            else:
                raise ArchiveSecurityError(f"Unknown mode: {mode}", "invalid_mode")

    async def _handle_tar(
        self,
        path: Path,
        mode: str,
        archive_path: str | None,
        pattern: str | None,
        config: Any,
    ) -> ExtractedContent:
        """Handle TAR archives (including compressed variants)."""
        archive_config = config.archive

        # Determine open mode
        name = path.name.lower()
        if name.endswith((".tar.gz", ".tgz")):
            tar_mode = "r:gz"
        elif name.endswith(".tar.bz2"):
            tar_mode = "r:bz2"
        elif name.endswith(".tar.xz"):
            tar_mode = "r:xz"
        else:
            tar_mode = "r"

        with tarfile.open(path, tar_mode) as tf:
            # Get member info with symlink detection
            members = []
            member_map = {}  # Map sanitized names to TarInfo objects
            for info in tf.getmembers():
                is_symlink = info.issym() or info.islnk()
                member_info = ArchiveMemberInfo(
                    name=info.name,
                    size=info.size,
                    compressed_size=info.size,  # TAR doesn't track this per-file
                    is_dir=info.isdir(),
                    is_symlink=is_symlink,
                )
                members.append(member_info)
                # Map by sanitized name for lookup
                safe_name = sanitize_archive_path(info.name)
                member_map[safe_name] = (info, member_info)

            # Security validation
            compressed_size = path.stat().st_size
            validate_archive_security(members, compressed_size, archive_config)

            if mode == "list":
                return self._format_listing(path, members, pattern, config)

            elif mode == "read":
                if not archive_path:
                    raise ArchiveSecurityError("archive_path required for read mode", "missing_path")

                safe_path = sanitize_archive_path(archive_path)
                check_banned_extension(safe_path, archive_config)

                # Find member using safe lookup
                member_info = find_archive_member(members, archive_path)
                if not member_info:
                    raise ArchivePathNotFoundError(str(path), archive_path)

                if member_info.is_dir:
                    raise ArchiveSecurityError("Cannot read directory", "is_directory")

                # Security: block symlinks
                check_symlink_safety(member_info)

                if member_info.size > archive_config.max_single_file:
                    raise ArchiveSecurityError(
                        f"File too large: {member_info.size} bytes",
                        "file_too_large",
                    )

                # Get the actual TarInfo object for extraction
                tar_info, _ = member_map.get(safe_path, (None, None))
                if tar_info is None:
                    raise ArchivePathNotFoundError(str(path), archive_path)

                f = tf.extractfile(tar_info)
                if f is None:
                    raise ArchiveSecurityError("Cannot extract file", "extraction_failed")

                content = f.read().decode("utf-8", errors="replace")
                sanitized = sanitize_output(content, config.output)

                return ExtractedContent(
                    content=sanitized.content,
                    format_type="File from TAR Archive",
                    metadata={"source_archive": path.name, "internal_path": safe_path},
                    was_truncated=sanitized.was_truncated,
                )

            elif mode == "extract":
                if not archive_path:
                    raise ArchiveSecurityError("archive_path required for extract mode", "missing_path")

                safe_path = sanitize_archive_path(archive_path)
                check_banned_extension(safe_path, archive_config)

                # Find member using safe lookup
                member_info = find_archive_member(members, archive_path)
                if not member_info:
                    raise ArchivePathNotFoundError(str(path), archive_path)

                # Security: block symlinks
                check_symlink_safety(member_info)

                workspace = path.parent
                extract_dir = workspace / archive_config.extraction_dir / path.stem
                extract_dir.mkdir(parents=True, exist_ok=True)

                # Get the actual TarInfo object
                tar_info, _ = member_map.get(safe_path, (None, None))
                if tar_info is None:
                    raise ArchivePathNotFoundError(str(path), archive_path)

                # Safe extraction: extract to memory first, then write to safe destination
                # This avoids tf.extract() which can follow symlinks
                f = tf.extractfile(tar_info)
                if f is None:
                    raise ArchiveSecurityError("Cannot extract file", "extraction_failed")

                dest_path = extract_dir / safe_path
                dest_path.parent.mkdir(parents=True, exist_ok=True)

                file_content = f.read()
                with open(dest_path, "wb") as dst:
                    dst.write(file_content)

                return ExtractedContent(
                    content=f"Extracted: {safe_path}\nTo: {dest_path}",
                    format_type="Archive Extraction",
                    metadata={
                        "source_archive": path.name,
                        "internal_path": safe_path,
                        "destination": str(dest_path),
                    },
                )

            else:
                raise ArchiveSecurityError(f"Unknown mode: {mode}", "invalid_mode")

    async def _handle_7z(
        self,
        path: Path,
        mode: str,
        archive_path: str | None,
        pattern: str | None,
        config: Any,
    ) -> ExtractedContent:
        """Handle 7z archives."""
        archive_config = config.archive

        with py7zr.SevenZipFile(path, "r") as szf:
            # Get member info
            members = []
            for name, info in szf.archiveinfo().files.items():
                members.append(
                    ArchiveMemberInfo(
                        name=name,
                        size=info.get("uncompressed", 0),
                        compressed_size=info.get("compressed", 0),
                        is_dir=info.get("is_dir", False),
                    )
                )

            # Security validation
            compressed_size = path.stat().st_size
            validate_archive_security(members, compressed_size, archive_config)

            if mode == "list":
                return self._format_listing(path, members, pattern, config)

            elif mode == "read":
                if not archive_path:
                    raise ArchiveSecurityError("archive_path required for read mode", "missing_path")

                safe_path = sanitize_archive_path(archive_path)
                check_banned_extension(safe_path, archive_config)

                # Extract to memory
                szf.reset()
                data = szf.read([safe_path])
                if safe_path not in data:
                    raise ArchivePathNotFoundError(str(path), archive_path)

                bio = data[safe_path]
                content = bio.read().decode("utf-8", errors="replace")
                sanitized = sanitize_output(content, config.output)

                return ExtractedContent(
                    content=sanitized.content,
                    format_type="File from 7z Archive",
                    metadata={"source_archive": path.name, "internal_path": safe_path},
                    was_truncated=sanitized.was_truncated,
                )

            elif mode == "extract":
                if not archive_path:
                    raise ArchiveSecurityError("archive_path required for extract mode", "missing_path")

                safe_path = sanitize_archive_path(archive_path)
                check_banned_extension(safe_path, archive_config)

                workspace = path.parent
                extract_dir = workspace / archive_config.extraction_dir / path.stem
                extract_dir.mkdir(parents=True, exist_ok=True)

                szf.reset()
                szf.extract(extract_dir, [safe_path])

                dest_path = extract_dir / safe_path

                return ExtractedContent(
                    content=f"Extracted: {safe_path}\nTo: {dest_path}",
                    format_type="Archive Extraction",
                    metadata={
                        "source_archive": path.name,
                        "internal_path": safe_path,
                        "destination": str(dest_path),
                    },
                )

            else:
                raise ArchiveSecurityError(f"Unknown mode: {mode}", "invalid_mode")

    def _format_listing(
        self,
        path: Path,
        members: list[ArchiveMemberInfo],
        pattern: str | None,
        config: Any,
    ) -> ExtractedContent:
        """Format archive listing as text."""
        # Filter by pattern if specified
        if pattern:
            members = [m for m in members if fnmatch.fnmatch(m.name, pattern)]

        # Separate files and dirs
        files = [m for m in members if not m.is_dir]
        dirs = [m for m in members if m.is_dir]

        # Calculate totals
        total_uncompressed = sum(m.size for m in files)
        total_compressed = sum(m.compressed_size for m in files)

        # Format listing
        lines = []
        lines.append(f"**Archive:** {path.name}")
        lines.append(
            f"**Size:** {format_bytes(path.stat().st_size)} compressed, "
            f"{format_bytes(total_uncompressed)} uncompressed"
        )
        lines.append(f"**Contents:** {len(files)} files, {len(dirs)} directories")

        if pattern:
            lines.append(f"**Filter:** {pattern}")

        lines.append("")
        lines.append("```")

        # Sort files by name
        for member in sorted(files, key=lambda m: m.name):
            size_str = format_bytes(member.size).rjust(10)
            lines.append(f"  {member.name:<60} {size_str}")

        lines.append("```")

        content = "\n".join(lines)
        sanitized = sanitize_output(content, config.output)

        return ExtractedContent(
            content=sanitized.content,
            format_type="Archive Listing",
            total_files=len(files),
            total_dirs=len(dirs),
            compressed_size=int(path.stat().st_size),
            uncompressed_size=total_uncompressed,
            was_truncated=sanitized.was_truncated,
        )
