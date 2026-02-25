"""
Tests for Ag3ntumReadDocument tool.

Tests the ReadDocument tool functionality:
- Format detection (text, tabular, archive, unknown)
- Security validation (file size limits, path validation, zip bomb protection)
- Text extraction (plain text, encoding, empty, binary)
- Archive security (file count, compression ratio, nesting depth)
- Cache manager (hit, miss, put, TTL expiry)
- Content sanitization (null bytes, control chars, truncation)
- Utility functions (page range, row range, column selection)
"""
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.ag3ntum.ag3ntum_read_document.config import (
    ArchiveConfig,
    CacheConfig,
    LimitsConfig,
    OutputConfig,
    ReadDocumentConfig,
    load_config,
)
from tools.ag3ntum.ag3ntum_read_document.cache import CacheManager
from tools.ag3ntum.ag3ntum_read_document.exceptions import (
    ArchiveFileCountError,
    ArchiveNestingError,
    ArchiveSecurityError,
    BannedExtensionError,
    FormatNotSupportedError,
    PageRangeError,
    ReadDocumentError,
    RowRangeError,
    ZipBombDetectedError,
)
from tools.ag3ntum.ag3ntum_read_document.format_detector import (
    FormatCategory,
    FormatInfo,
    _get_extension,
    is_cacheable,
    is_text_format,
)
from tools.ag3ntum.ag3ntum_read_document.security import (
    ArchiveMemberInfo,
    SanitizedContent,
    check_archive_nesting,
    check_banned_extension,
    check_symlink_safety,
    find_archive_member,
    sanitize_archive_path,
    sanitize_cell_content,
    sanitize_metadata,
    sanitize_output,
    validate_archive_security,
)
from tools.ag3ntum.ag3ntum_read_document.utils import (
    format_bytes,
    format_duration,
    parse_column_selection,
    parse_page_range,
    parse_row_range,
    safe_filename,
    truncate_string,
)
from tools.ag3ntum.ag3ntum_read_document.extractors import get_extractor
from tools.ag3ntum.ag3ntum_read_document.extractors.text import TextExtractor
from tools.ag3ntum.ag3ntum_read_document.extractors.base import (
    BaseExtractor,
    ExtractedContent,
)
from tools.ag3ntum.ag3ntum_read_document.tool import (
    AG3NTUM_READ_DOCUMENT_TOOL,
    _error,
    _result,
    create_read_document_tool,
)


# ---------------------------------------------------------------------------
# TestFormatDetection
# ---------------------------------------------------------------------------
class TestFormatDetection:
    """Tests for format detection logic."""

    def test_detect_text_extensions(self):
        """Text-based extensions resolve to TEXT category."""
        for ext in (".txt", ".py", ".js", ".md", ".yaml", ".json"):
            path = Path(f"test{ext}")
            info = _get_extension(path)
            assert info == ext, f"Expected {ext}, got {info}"

    def test_detect_tabular_extensions(self):
        """Tabular extensions resolve correctly."""
        for ext in (".csv", ".tsv"):
            assert _get_extension(Path(f"data{ext}")) == ext

    def test_detect_archive_extensions(self):
        """Archive extensions (including compound) resolve correctly."""
        assert _get_extension(Path("archive.zip")) == ".zip"
        assert _get_extension(Path("archive.tar")) == ".tar"
        assert _get_extension(Path("archive.tar.gz")) == ".tar.gz"
        assert _get_extension(Path("archive.tar.bz2")) == ".tar.bz2"
        assert _get_extension(Path("archive.tar.xz")) == ".tar.xz"

    def test_detect_no_extension(self):
        """File with no extension returns empty string."""
        assert _get_extension(Path("Makefile")) == ""

    def test_detect_multiple_dots(self):
        """File with multiple dots uses last suffix unless compound."""
        assert _get_extension(Path("my.backup.log")) == ".log"
        assert _get_extension(Path("data.v2.csv")) == ".csv"
        # Compound takes precedence
        assert _get_extension(Path("backup.tar.gz")) == ".tar.gz"

    def test_is_text_format(self):
        """is_text_format returns True for TEXT category non-binary."""
        text_info = FormatInfo(
            extension=".py",
            category=FormatCategory.TEXT,
            is_binary=False,
        )
        assert is_text_format(text_info) is True

    def test_is_text_format_false_for_binary(self):
        """is_text_format returns False when is_binary is True."""
        binary_info = FormatInfo(
            extension=".pdf",
            category=FormatCategory.PDF,
            is_binary=True,
        )
        assert is_text_format(binary_info) is False

    def test_is_cacheable_for_pdf(self):
        """PDF format is cacheable."""
        assert is_cacheable(FormatInfo(
            extension=".pdf", category=FormatCategory.PDF, is_binary=True,
        )) is True

    def test_is_cacheable_for_office(self):
        """Office format is cacheable."""
        assert is_cacheable(FormatInfo(
            extension=".docx", category=FormatCategory.OFFICE, is_binary=True,
        )) is True

    def test_is_cacheable_for_archive(self):
        """Archive format is cacheable."""
        assert is_cacheable(FormatInfo(
            extension=".zip", category=FormatCategory.ARCHIVE, is_binary=True,
        )) is True

    def test_is_not_cacheable_for_text(self):
        """Text format is not cacheable."""
        assert is_cacheable(FormatInfo(
            extension=".py", category=FormatCategory.TEXT, is_binary=False,
        )) is False

    def test_format_category_enum_values(self):
        """FormatCategory enum has expected string values."""
        assert FormatCategory.TEXT.value == "text"
        assert FormatCategory.PDF.value == "pdf"
        assert FormatCategory.ARCHIVE.value == "archive"
        assert FormatCategory.UNKNOWN.value == "unknown"


# ---------------------------------------------------------------------------
# TestFormatDetectionWithMagic
# ---------------------------------------------------------------------------
class TestFormatDetectionWithMagic:
    """Tests for detect_format that require mocking python-magic."""

    def test_detect_known_extension(self, tmp_path):
        """Known extension is detected without fallback to MIME."""
        test_file = tmp_path / "script.py"
        test_file.write_text("print('hello')")

        with patch(
            "tools.ag3ntum.ag3ntum_read_document.format_detector._get_mime_type",
            return_value="text/x-python",
        ):
            from tools.ag3ntum.ag3ntum_read_document.format_detector import detect_format
            info = detect_format(test_file)

        assert info.category == FormatCategory.TEXT
        assert info.extension == ".py"
        assert info.is_binary is False

    def test_detect_unknown_extension_text_mime(self, tmp_path):
        """Unknown extension falls back to MIME type detection."""
        test_file = tmp_path / "data.xyz"
        test_file.write_text("some text content")

        with patch(
            "tools.ag3ntum.ag3ntum_read_document.format_detector._get_mime_type",
            return_value="text/plain",
        ):
            from tools.ag3ntum.ag3ntum_read_document.format_detector import detect_format
            info = detect_format(test_file)

        assert info.category == FormatCategory.TEXT

    def test_detect_unknown_extension_unknown_mime(self, tmp_path):
        """Unknown extension + unknown MIME results in UNKNOWN category."""
        test_file = tmp_path / "data.xyz"
        test_file.write_bytes(b"\x00\x01\x02")

        with patch(
            "tools.ag3ntum.ag3ntum_read_document.format_detector._get_mime_type",
            return_value="application/octet-stream",
        ):
            from tools.ag3ntum.ag3ntum_read_document.format_detector import detect_format
            info = detect_format(test_file)

        assert info.category == FormatCategory.UNKNOWN

    def test_detect_format_with_hint(self, tmp_path):
        """format_hint overrides extension-based detection."""
        test_file = tmp_path / "data.log"
        test_file.write_text("col1,col2\n1,2")

        with patch(
            "tools.ag3ntum.ag3ntum_read_document.format_detector._get_mime_type",
            return_value="text/csv",
        ):
            from tools.ag3ntum.ag3ntum_read_document.format_detector import detect_format
            info = detect_format(test_file, format_hint="csv")

        assert info.category == FormatCategory.TABULAR
        assert info.extension == ".csv"

    def test_detect_special_filenames(self, tmp_path):
        """Special filenames without extension (Makefile, Dockerfile)."""
        for name in ("Makefile", "Dockerfile"):
            test_file = tmp_path / name
            test_file.write_text("content")

            with patch(
                "tools.ag3ntum.ag3ntum_read_document.format_detector._get_mime_type",
                return_value="text/plain",
            ):
                from tools.ag3ntum.ag3ntum_read_document.format_detector import detect_format
                info = detect_format(test_file)

            assert info.category == FormatCategory.TEXT, f"Failed for {name}"


# ---------------------------------------------------------------------------
# TestSecurityValidation
# ---------------------------------------------------------------------------
class TestSecurityValidation:
    """Tests for security validators."""

    def test_file_size_limit_enforcement(self):
        """LimitsConfig.get returns correct limit per category."""
        limits = LimitsConfig(text=100, pdf=200)
        assert limits.get("text") == 100
        assert limits.get("pdf") == 200
        # Unknown category falls back to text
        assert limits.get("nonexistent") == 100

    def test_sanitize_archive_path_traversal(self):
        """Archive paths with traversal components are sanitized."""
        assert sanitize_archive_path("../../etc/passwd") == "etc/passwd"
        assert sanitize_archive_path("./relative/path") == "relative/path"
        assert sanitize_archive_path("normal/path.txt") == "normal/path.txt"

    def test_sanitize_archive_path_absolute(self):
        """Absolute path: leading slash component is kept by PurePosixPath."""
        # PurePosixPath('/absolute/path').parts includes '/'
        # The '/' part is not '.', '..', or empty, so it's preserved as-is
        result = sanitize_archive_path("/absolute/path")
        # Just verify no '..' remains and content is accessible
        assert ".." not in result
        assert "absolute" in result
        assert "path" in result

    def test_sanitize_archive_path_backslash(self):
        """Backslash path separators are normalized."""
        assert sanitize_archive_path("dir\\file.txt") == "dir/file.txt"

    def test_sanitize_archive_path_drive_letter(self):
        """Windows drive letters are stripped."""
        result = sanitize_archive_path("C:/Users/file.txt")
        assert not result.startswith("C:")

    def test_check_banned_extension_blocks(self):
        """Banned extensions raise BannedExtensionError."""
        config = ArchiveConfig(banned_extensions=[".exe", ".dll"])
        with pytest.raises(BannedExtensionError):
            check_banned_extension("malware.exe", config)

    def test_check_banned_extension_allows(self):
        """Non-banned extensions pass without error."""
        config = ArchiveConfig(banned_extensions=[".exe", ".dll"])
        # Should not raise
        check_banned_extension("readme.txt", config)
        check_banned_extension("script.py", config)

    def test_check_symlink_safety_blocks_symlinks(self):
        """Symlink archive members are blocked."""
        member = ArchiveMemberInfo(
            name="link.txt", size=0, compressed_size=0,
            is_dir=False, is_symlink=True,
        )
        with pytest.raises(ArchiveSecurityError):
            check_symlink_safety(member)

    def test_check_symlink_safety_allows_regular(self):
        """Regular archive members pass symlink check."""
        member = ArchiveMemberInfo(
            name="file.txt", size=100, compressed_size=50,
            is_dir=False, is_symlink=False,
        )
        # Should not raise
        check_symlink_safety(member)


# ---------------------------------------------------------------------------
# TestContentSanitization
# ---------------------------------------------------------------------------
class TestContentSanitization:
    """Tests for content sanitization for LLM context."""

    def test_sanitize_removes_null_bytes(self):
        """Null bytes are stripped from output."""
        config = OutputConfig(
            max_chars=1000, max_lines=100,
            strip_null_bytes=True, strip_control_chars=False,
        )
        result = sanitize_output("hello\x00world", config)
        assert "\x00" not in result.content
        assert result.removed_null_bytes == 1

    def test_sanitize_removes_control_chars(self):
        """Control characters are stripped from output."""
        config = OutputConfig(
            max_chars=1000, max_lines=100,
            strip_null_bytes=False, strip_control_chars=True,
        )
        # \x01 is a control char, \n and \t are NOT removed
        result = sanitize_output("hello\x01world", config)
        assert "\x01" not in result.content
        assert result.removed_control_chars == 1

    def test_sanitize_truncates_by_chars(self):
        """Content exceeding max_chars is truncated."""
        config = OutputConfig(
            max_chars=10, max_lines=1000,
            strip_null_bytes=False, strip_control_chars=False,
            truncation_marker=" [TRUNC]",
        )
        result = sanitize_output("a" * 100, config)
        assert result.was_truncated is True
        assert len(result.content) <= 10 + len(" [TRUNC]")

    def test_sanitize_truncates_by_lines(self):
        """Content exceeding max_lines is truncated."""
        config = OutputConfig(
            max_chars=100_000, max_lines=3,
            strip_null_bytes=False, strip_control_chars=False,
            truncation_marker="[TRUNC]",
        )
        content = "\n".join(f"line{i}" for i in range(10))
        result = sanitize_output(content, config)
        assert result.was_truncated is True
        lines = result.content.split("\n")
        # 3 original lines + truncation marker
        assert len(lines) == 4

    def test_sanitize_no_changes_needed(self):
        """Clean content passes through unchanged."""
        config = OutputConfig(
            max_chars=1000, max_lines=100,
            strip_null_bytes=True, strip_control_chars=True,
        )
        result = sanitize_output("clean content", config)
        assert result.content == "clean content"
        assert result.was_truncated is False
        assert result.removed_null_bytes == 0
        assert result.removed_control_chars == 0

    def test_sanitize_metadata_field_limit(self):
        """Metadata field count is limited."""
        config = OutputConfig(max_metadata_fields=2, max_metadata_value_len=100)
        metadata = {f"field_{i}": f"value_{i}" for i in range(10)}
        result = sanitize_metadata(metadata, config)
        assert len(result) == 2

    def test_sanitize_metadata_value_truncation(self):
        """Long metadata values are truncated."""
        config = OutputConfig(
            max_metadata_fields=10, max_metadata_value_len=10,
        )
        result = sanitize_metadata({"key": "a" * 100}, config)
        assert len(result["key"]) == 10 + len("...")

    def test_sanitize_cell_content_truncation(self):
        """Cell content exceeding limit is truncated."""
        config = OutputConfig(max_cell_content=5)
        result = sanitize_cell_content("abcdefghij", config)
        assert result == "abcde..."

    def test_sanitize_cell_content_null_bytes(self):
        """Null bytes in cell content are removed."""
        config = OutputConfig(
            max_cell_content=1000,
            strip_null_bytes=True, strip_control_chars=False,
        )
        result = sanitize_cell_content("a\x00b", config)
        assert result == "ab"


# ---------------------------------------------------------------------------
# TestTextExtraction
# ---------------------------------------------------------------------------
class TestTextExtraction:
    """Tests for the TextExtractor."""

    @pytest.mark.asyncio
    async def test_extract_plain_text(self, tmp_path):
        """Extract plain text file with line numbers."""
        test_file = tmp_path / "hello.txt"
        test_file.write_text("Hello\nWorld")

        extractor = TextExtractor()
        with patch(
            "tools.ag3ntum.ag3ntum_read_document.extractors.text.get_config",
            return_value=ReadDocumentConfig(),
        ):
            result = await extractor.extract(test_file, {})

        assert "Hello" in result.content
        assert "World" in result.content
        assert result.format_type.startswith("Text")

    @pytest.mark.asyncio
    async def test_extract_with_offset_and_limit(self, tmp_path):
        """Offset and limit select the correct line range."""
        test_file = tmp_path / "lines.txt"
        test_file.write_text("\n".join(f"line{i}" for i in range(1, 11)))

        extractor = TextExtractor()
        with patch(
            "tools.ag3ntum.ag3ntum_read_document.extractors.text.get_config",
            return_value=ReadDocumentConfig(),
        ):
            result = await extractor.extract(
                test_file, {"offset": 3, "limit": 2}
            )

        assert "line3" in result.content
        assert "line4" in result.content
        assert "line1" not in result.content
        assert "line5" not in result.content

    @pytest.mark.asyncio
    async def test_extract_empty_file(self, tmp_path):
        """Empty file returns empty content without error."""
        test_file = tmp_path / "empty.txt"
        test_file.write_text("")

        extractor = TextExtractor()
        with patch(
            "tools.ag3ntum.ag3ntum_read_document.extractors.text.get_config",
            return_value=ReadDocumentConfig(),
        ):
            result = await extractor.extract(test_file, {})

        assert result.content is not None

    @pytest.mark.asyncio
    async def test_extract_utf8_encoding(self, tmp_path):
        """UTF-8 encoded content is read correctly."""
        test_file = tmp_path / "unicode.txt"
        test_file.write_text("Привет мир 世界", encoding="utf-8")

        extractor = TextExtractor()
        with patch(
            "tools.ag3ntum.ag3ntum_read_document.extractors.text.get_config",
            return_value=ReadDocumentConfig(),
        ):
            result = await extractor.extract(test_file, {})

        assert "Привет" in result.content
        assert "世界" in result.content

    @pytest.mark.asyncio
    async def test_extract_latin1_encoding_with_replace(self, tmp_path):
        """Latin-1 encoded file is read with errors='replace'."""
        test_file = tmp_path / "latin1.txt"
        test_file.write_bytes(b"caf\xe9 na\xefve")

        extractor = TextExtractor()
        with patch(
            "tools.ag3ntum.ag3ntum_read_document.extractors.text.get_config",
            return_value=ReadDocumentConfig(),
        ):
            result = await extractor.extract(test_file, {})

        # Should not raise, content extracted with replacement chars
        assert result.content is not None

    @pytest.mark.asyncio
    async def test_extract_metadata_included(self, tmp_path):
        """Metadata is included by default."""
        test_file = tmp_path / "meta.txt"
        test_file.write_text("content")

        extractor = TextExtractor()
        with patch(
            "tools.ag3ntum.ag3ntum_read_document.extractors.text.get_config",
            return_value=ReadDocumentConfig(),
        ):
            result = await extractor.extract(test_file, {})

        assert "filename" in result.metadata
        assert result.metadata["filename"] == "meta.txt"

    @pytest.mark.asyncio
    async def test_extract_metadata_excluded(self, tmp_path):
        """Metadata is excluded when include_metadata=False."""
        test_file = tmp_path / "nometa.txt"
        test_file.write_text("content")

        extractor = TextExtractor()
        with patch(
            "tools.ag3ntum.ag3ntum_read_document.extractors.text.get_config",
            return_value=ReadDocumentConfig(),
        ):
            result = await extractor.extract(
                test_file, {"include_metadata": False}
            )

        assert result.metadata == {}

    def test_supports_format_txt(self):
        """TextExtractor supports .txt extension."""
        extractor = TextExtractor()
        assert extractor.supports_format(".txt") is True
        assert extractor.supports_format(".py") is True
        assert extractor.supports_format(".pdf") is False


# ---------------------------------------------------------------------------
# TestZipBombProtection
# ---------------------------------------------------------------------------
class TestZipBombProtection:
    """Tests for archive security: zip bomb protection."""

    def test_archive_too_many_files(self):
        """Archive with too many files raises ArchiveFileCountError."""
        config = ArchiveConfig(max_file_count=5)
        members = [
            ArchiveMemberInfo(
                name=f"file{i}.txt", size=100, compressed_size=50,
                is_dir=False,
            )
            for i in range(10)
        ]
        with pytest.raises(ArchiveFileCountError):
            validate_archive_security(members, 500, config)

    def test_archive_excessive_compression_ratio(self):
        """Archive with high compression ratio raises ZipBombDetectedError."""
        config = ArchiveConfig(
            max_compression_ratio=10,
            max_total_size=999_999_999,
            max_file_count=100,
        )
        members = [
            ArchiveMemberInfo(
                name="bomb.txt",
                size=10_000_000,  # 10MB uncompressed
                compressed_size=100,  # 100 bytes compressed
                is_dir=False,
            ),
        ]
        # ratio = 10_000_000 / 100 = 100_000 >> 10
        with pytest.raises(ZipBombDetectedError):
            validate_archive_security(members, 100, config)

    def test_archive_total_size_exceeded(self):
        """Archive total uncompressed size exceeds limit."""
        config = ArchiveConfig(
            max_compression_ratio=1000,
            max_total_size=500,
            max_file_count=100,
        )
        members = [
            ArchiveMemberInfo(
                name=f"big{i}.txt", size=200, compressed_size=100,
                is_dir=False,
            )
            for i in range(5)
        ]
        # total = 1000, limit = 500
        with pytest.raises(ZipBombDetectedError):
            validate_archive_security(members, 500, config)

    def test_normal_archive_passes(self):
        """Normal archive passes all security checks."""
        config = ArchiveConfig(
            max_compression_ratio=100,
            max_total_size=1_000_000,
            max_file_count=100,
        )
        members = [
            ArchiveMemberInfo(
                name=f"file{i}.txt", size=100, compressed_size=50,
                is_dir=False,
            )
            for i in range(5)
        ]
        # Should not raise
        validate_archive_security(members, 250, config)

    def test_archive_nesting_depth_exceeded(self):
        """Nested archives beyond max depth raise ArchiveNestingError."""
        config = ArchiveConfig(max_nesting_depth=3)
        # depth 0, 1, 2 should pass; 3 should fail (>= max)
        check_archive_nesting(0, config)
        check_archive_nesting(1, config)
        check_archive_nesting(2, config)
        with pytest.raises(ArchiveNestingError):
            check_archive_nesting(3, config)

    def test_archive_directories_not_counted_in_file_count(self):
        """Directories in archive are not counted against file limit."""
        config = ArchiveConfig(max_file_count=2)
        members = [
            ArchiveMemberInfo(
                name="dir1/", size=0, compressed_size=0, is_dir=True,
            ),
            ArchiveMemberInfo(
                name="dir2/", size=0, compressed_size=0, is_dir=True,
            ),
            ArchiveMemberInfo(
                name="file1.txt", size=100, compressed_size=50, is_dir=False,
            ),
            ArchiveMemberInfo(
                name="file2.txt", size=100, compressed_size=50, is_dir=False,
            ),
        ]
        # 2 files, limit is 2 -> should pass
        validate_archive_security(members, 100, config)

    def test_find_archive_member_sanitized_match(self):
        """find_archive_member matches after sanitizing both paths."""
        members = [
            ArchiveMemberInfo(
                name="src/main.py", size=500, compressed_size=200,
                is_dir=False,
            ),
        ]
        result = find_archive_member(members, "src/main.py")
        assert result is not None
        assert result.name == "src/main.py"

    def test_find_archive_member_traversal_sanitized(self):
        """Path traversal in requested path is sanitized before matching."""
        members = [
            ArchiveMemberInfo(
                name="etc/passwd", size=100, compressed_size=50,
                is_dir=False,
            ),
        ]
        result = find_archive_member(members, "../../etc/passwd")
        assert result is not None

    def test_find_archive_member_not_found(self):
        """Non-existent path returns None."""
        members = [
            ArchiveMemberInfo(
                name="file.txt", size=100, compressed_size=50, is_dir=False,
            ),
        ]
        result = find_archive_member(members, "missing.txt")
        assert result is None


# ---------------------------------------------------------------------------
# TestCacheManager
# ---------------------------------------------------------------------------
class TestCacheManager:
    """Tests for CacheManager."""

    @pytest.fixture
    def cache_dir(self, tmp_path):
        """Create a temporary cache directory."""
        return tmp_path / "doc-cache"

    @pytest.fixture
    def cache_manager(self, cache_dir):
        """Create a CacheManager with temp directory."""
        config = CacheConfig(
            enabled=True, directory=str(cache_dir),
            max_size_mb=10, ttl_days=7,
        )
        return CacheManager(config)

    def test_cache_miss_returns_none(self, cache_manager):
        """Cache miss returns None and increments miss counter."""
        result = cache_manager.get("pdfs", "nonexistent_key")
        assert result is None
        assert cache_manager.misses == 1

    def test_cache_put_and_get(self, cache_manager):
        """Put content then get returns the cached entry."""
        cache_manager.put("pdfs", "test_key_123", "extracted content", {"pages": 5})

        entry = cache_manager.get("pdfs", "test_key_123")
        assert entry is not None
        assert entry.content == "extracted content"
        assert entry.metadata == {"pages": 5}
        assert cache_manager.hits == 1

    def test_cache_ttl_expiry(self, cache_dir):
        """Expired entries return None."""
        config = CacheConfig(
            enabled=True, directory=str(cache_dir),
            max_size_mb=10, ttl_days=0,  # 0 days TTL = everything is expired
        )
        manager = CacheManager(config)

        # Write an entry with a created_at in the past
        cache_path = manager._get_cache_path("pdfs", "old_key")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "content": "old content",
            "metadata": {},
            "created_at": time.time() - 86400 * 10,  # 10 days ago
        }
        with open(cache_path, "w") as f:
            json.dump(data, f)

        result = manager.get("pdfs", "old_key")
        assert result is None
        assert manager.misses == 1

    def test_cache_disabled_skips_all(self, tmp_path):
        """Disabled cache never stores or returns entries."""
        config = CacheConfig(
            enabled=False, directory=str(tmp_path / "cache"),
        )
        manager = CacheManager(config)

        manager.put("pdfs", "key", "content")
        assert manager.get("pdfs", "key") is None

    def test_cache_clear(self, cache_manager):
        """clear() removes all entries."""
        cache_manager.put("pdfs", "key1", "content1")
        cache_manager.put("office", "key2", "content2")

        cache_manager.clear()

        assert cache_manager.get("pdfs", "key1") is None
        assert cache_manager.get("office", "key2") is None

    def test_compute_cache_key_deterministic(self, cache_manager, tmp_path):
        """Same file + params produces the same cache key."""
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"PDF content here")

        key1 = cache_manager.compute_cache_key(
            test_file, {"pages": "1-5"}
        )
        key2 = cache_manager.compute_cache_key(
            test_file, {"pages": "1-5"}
        )
        assert key1 == key2

    def test_compute_cache_key_different_params(self, cache_manager, tmp_path):
        """Different params produce different cache keys."""
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"PDF content here")

        key1 = cache_manager.compute_cache_key(
            test_file, {"pages": "1-5"}
        )
        key2 = cache_manager.compute_cache_key(
            test_file, {"pages": "6-10"}
        )
        assert key1 != key2

    def test_cache_stats(self, cache_manager):
        """get_stats returns correct statistics."""
        cache_manager.put("pdfs", "k1", "content1")
        cache_manager.put("pdfs", "k2", "content2")
        cache_manager.get("pdfs", "k1")  # hit
        cache_manager.get("pdfs", "missing")  # miss

        stats = cache_manager.get_stats()
        assert stats.total_entries >= 2
        assert stats.hits == 1
        assert stats.misses == 1


# ---------------------------------------------------------------------------
# TestUtilityFunctions
# ---------------------------------------------------------------------------
class TestUtilityFunctions:
    """Tests for utility functions in utils.py."""

    def test_parse_page_range_single(self):
        """Single page number is parsed correctly (1-indexed to 0-indexed)."""
        assert parse_page_range("5", 10) == [4]

    def test_parse_page_range_range(self):
        """Page range is parsed correctly."""
        assert parse_page_range("1-3", 10) == [0, 1, 2]

    def test_parse_page_range_mixed(self):
        """Mixed page specification."""
        result = parse_page_range("1,3,5-7", 10)
        assert result == [0, 2, 4, 5, 6]

    def test_parse_page_range_none(self):
        """None returns all pages."""
        assert parse_page_range(None, 5) == [0, 1, 2, 3, 4]

    def test_parse_page_range_invalid(self):
        """Invalid range raises PageRangeError."""
        with pytest.raises(PageRangeError):
            parse_page_range("0", 10)  # 0 is not valid (1-indexed)
        with pytest.raises(PageRangeError):
            parse_page_range("11", 10)  # Beyond total
        with pytest.raises(PageRangeError):
            parse_page_range("abc", 10)

    def test_parse_row_range_head(self):
        """head:N returns first N rows."""
        assert parse_row_range("head:50", 100) == (0, 50)

    def test_parse_row_range_tail(self):
        """tail:N returns last N rows."""
        assert parse_row_range("tail:20", 100) == (80, 100)

    def test_parse_row_range_range(self):
        """Numeric range is parsed correctly."""
        assert parse_row_range("10-50", 100) == (9, 50)

    def test_parse_row_range_none(self):
        """None returns all rows."""
        assert parse_row_range(None, 100) == (0, 100)

    def test_parse_row_range_invalid(self):
        """Invalid row range raises RowRangeError."""
        with pytest.raises(RowRangeError):
            parse_row_range("abc", 100)
        with pytest.raises(RowRangeError):
            parse_row_range("50-10", 100)  # start > end

    def test_parse_column_selection_by_name(self):
        """Column names are matched case-insensitively."""
        cols = ["Name", "Age", "Email"]
        assert parse_column_selection("name,email", cols) == ["Name", "Email"]

    def test_parse_column_selection_by_letter(self):
        """Excel-style column letters are converted to indices."""
        cols = ["ColA", "ColB", "ColC"]
        assert parse_column_selection("A,C", cols) == ["ColA", "ColC"]

    def test_parse_column_selection_none(self):
        """None returns all columns."""
        cols = ["A", "B", "C"]
        assert parse_column_selection(None, cols) == ["A", "B", "C"]

    def test_format_bytes(self):
        """Byte sizes are formatted to human-readable strings."""
        assert "B" in format_bytes(100)
        assert "KB" in format_bytes(1024)
        assert "MB" in format_bytes(1024 * 1024)

    def test_format_duration(self):
        """Durations are formatted correctly."""
        assert format_duration(30.0) == "30.0s"
        assert "m" in format_duration(120.0)
        assert "h" in format_duration(3600.0)

    def test_truncate_string(self):
        """Long strings are truncated with suffix."""
        assert truncate_string("abcdef", 4) == "a..."
        assert truncate_string("abc", 10) == "abc"

    def test_safe_filename(self):
        """Unsafe characters are replaced in filenames."""
        result = safe_filename('file<>:"/\\|?*.txt')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result


# ---------------------------------------------------------------------------
# TestExtractorRegistry
# ---------------------------------------------------------------------------
class TestExtractorRegistry:
    """Tests for extractor registry and get_extractor."""

    def test_get_extractor_text(self):
        """TEXT category returns TextExtractor."""
        info = FormatInfo(extension=".py", category=FormatCategory.TEXT)
        extractor = get_extractor(info)
        assert isinstance(extractor, TextExtractor)

    def test_get_extractor_unknown_raises(self):
        """UNKNOWN category with no registry entry raises error."""
        info = FormatInfo(extension=".xyz", category=FormatCategory.UNKNOWN)
        with pytest.raises(FormatNotSupportedError):
            get_extractor(info)


# ---------------------------------------------------------------------------
# TestExtractedContent
# ---------------------------------------------------------------------------
class TestExtractedContent:
    """Tests for ExtractedContent dataclass."""

    def test_format_header_basic(self):
        """format_header includes format type."""
        ec = ExtractedContent(content="data", format_type="PDF")
        header = ec.format_header()
        assert "PDF" in header

    def test_format_header_with_pages(self):
        """format_header includes page info."""
        ec = ExtractedContent(
            content="data", format_type="PDF",
            total_pages=10, extracted_pages=[0, 1, 2],
        )
        header = ec.format_header()
        assert "10" in header

    def test_format_header_with_notes(self):
        """format_header includes processing notes."""
        ec = ExtractedContent(content="data", format_type="Text")
        ec.add_note("5 more lines not shown")
        header = ec.format_header()
        assert "5 more lines" in header

    def test_format_output(self):
        """_format_output combines header and content."""
        extractor = TextExtractor()
        ec = ExtractedContent(
            content="line content", format_type="Text (.py)",
        )
        output = extractor._format_output(ec)
        assert "Text (.py)" in output
        assert "line content" in output
        assert "---" in output

    def test_format_page_range_contiguous(self):
        """Page range formatting for contiguous pages."""
        ec = ExtractedContent(content="", format_type="PDF")
        assert ec._format_page_range([0, 1, 2]) == "1-3"

    def test_format_page_range_scattered(self):
        """Page range formatting for scattered pages."""
        ec = ExtractedContent(content="", format_type="PDF")
        assert ec._format_page_range([0, 2, 4, 5, 6]) == "1, 3, 5-7"


# ---------------------------------------------------------------------------
# TestToolHelpers
# ---------------------------------------------------------------------------
class TestToolHelpers:
    """Tests for tool-level helper functions."""

    def test_tool_name_constant(self):
        """Tool name constant is correct."""
        assert AG3NTUM_READ_DOCUMENT_TOOL == "mcp__ag3ntum__ReadDocument"

    def test_result_format(self):
        """_result produces correct response structure."""
        r = _result("Hello")
        assert r == {"content": [{"type": "text", "text": "Hello"}]}
        assert "is_error" not in r

    def test_error_format(self):
        """_error produces correct error response structure."""
        r = _error("Something broke")
        assert r["is_error"] is True
        assert "**Error:**" in r["content"][0]["text"]
        assert "Something broke" in r["content"][0]["text"]

    def test_create_read_document_tool_returns_tool(self):
        """create_read_document_tool returns an SdkMcpTool object."""
        with patch(
            "tools.ag3ntum.ag3ntum_read_document.tool.get_path_validator"
        ):
            tool_obj = create_read_document_tool("test-session")
            assert tool_obj is not None
            assert hasattr(tool_obj, "name")
            assert tool_obj.name == "ReadDocument"


# ---------------------------------------------------------------------------
# TestExceptions
# ---------------------------------------------------------------------------
class TestExceptions:
    """Tests for custom exception classes."""

    def test_read_document_error_str(self):
        """ReadDocumentError string includes context."""
        err = ReadDocumentError("Failed", {"key": "val"})
        assert "key=val" in str(err)

    def test_read_document_error_no_context(self):
        """ReadDocumentError without context shows just the message."""
        err = ReadDocumentError("Failed")
        assert str(err) == "Failed"

    def test_zip_bomb_error_attributes(self):
        """ZipBombDetectedError has expected attributes."""
        err = ZipBombDetectedError(
            compressed_size=100,
            uncompressed_size=10_000_000,
            ratio=100_000.0,
            max_ratio=100,
        )
        assert err.reason == "compression_ratio_exceeded"
        assert "100000.0" in str(err)

    def test_banned_extension_error_attributes(self):
        """BannedExtensionError has filename and extension."""
        err = BannedExtensionError("malware.exe", ".exe")
        assert err.filename == "malware.exe"
        assert err.extension == ".exe"

    def test_archive_nesting_error(self):
        """ArchiveNestingError has depth info."""
        err = ArchiveNestingError(5, 3)
        assert "5" in str(err)
        assert "3" in str(err)

    def test_format_not_supported_error(self):
        """FormatNotSupportedError has extension and mime."""
        err = FormatNotSupportedError(".xyz", "application/octet-stream")
        assert err.extension == ".xyz"
        assert err.mime_type == "application/octet-stream"


# ---------------------------------------------------------------------------
# TestConfigLoading
# ---------------------------------------------------------------------------
class TestConfigLoading:
    """Tests for configuration loading."""

    def test_default_config(self):
        """Default config has reasonable values."""
        config = ReadDocumentConfig()
        assert config.global_timeout == 180.0
        assert config.limits.text == 10_485_760
        assert config.archive.max_nesting_depth == 3

    def test_load_config_missing_file(self, tmp_path):
        """Missing config file returns defaults."""
        config = load_config(tmp_path / "nonexistent.yaml")
        assert config.global_timeout == 180.0

    def test_load_config_empty_file(self, tmp_path):
        """Empty config file returns defaults."""
        config_path = tmp_path / "empty.yaml"
        config_path.write_text("")
        config = load_config(config_path)
        assert config.global_timeout == 180.0

    def test_load_config_with_values(self, tmp_path):
        """Config values from YAML override defaults."""
        config_path = tmp_path / "tools-security.yaml"
        config_path.write_text(
            "tools:\n"
            "  read_document:\n"
            "    global_timeout: 60.0\n"
            "    limits:\n"
            "      text: 5000000\n"
        )
        config = load_config(config_path)
        assert config.global_timeout == 60.0
        assert config.limits.text == 5_000_000
