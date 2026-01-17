# ReadDocument Tool Design

## Overview

The `mcp__ag3ntum__ReadDocument` tool extends the basic file reading capability with support for rich document formats, archives, and media files. It provides intelligent content extraction while maintaining security boundaries and performance limits.

**Runtime context**: Tool runs as user `45050` within the agent environment. All system dependencies (Pandoc, Tesseract) are part of the system requirements.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                      ReadDocument Tool                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────────┐      │
│  │ Path        │───▶│ Format       │───▶│ Content Extractor  │      │
│  │ Validator   │    │ Detector     │    │ Registry           │      │
│  └─────────────┘    └──────────────┘    └────────────────────┘      │
│                                                ▼                     │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────────────┐      │
│  │ Cache       │◀───│ Security     │◀───│ Format-Specific    │      │
│  │ Manager     │    │ Validator    │    │ Extractors         │      │
│  └─────────────┘    └──────────────┘    └────────────────────┘      │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Supported Formats

### 1. Text-Based Files (Passthrough to existing logic)
- Source code: `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.c`, `.cpp`, `.h`, etc.
- Config files: `.yaml`, `.yml`, `.json`, `.toml`, `.ini`, `.cfg`
- Markup: `.md`, `.rst`, `.txt`, `.html`, `.xml`, `.svg`
- Scripts: `.sh`, `.bash`, `.zsh`, `.ps1`, `.bat`

### 2. Tabular Data (via Pandas)
- `.csv` - Comma-separated values
- `.tsv` - Tab-separated values
- `.xlsx`, `.xls` - Excel spreadsheets (specific sheet selection)
- `.parquet` - Apache Parquet
- `.json` (tabular) - JSON with array of objects

**Parameters:**
- `sheet`: Sheet name/index for Excel files
- `rows`: Row range (e.g., "1-100", "head:50", "tail:20")
- `columns`: Column selection (e.g., "A,B,C" or "name,age,salary")

### 3. Office Documents (via Pandoc - REQUIRED)
- `.docx` - Microsoft Word
- `.doc` - Legacy Word (limited support)
- `.rtf` - Rich Text Format
- `.odt` - OpenDocument Text
- `.pptx` - PowerPoint (extracts text + slide notes)
- `.odp` - OpenDocument Presentation
- `.epub` - E-books

**Parameters:**
- `pages`: Page range for extraction
- `include_metadata`: Include document metadata

**Note**: Pandoc is a required system dependency. If missing, the tool will raise `DependencyMissingError` and log the error.

### 4. PDF Documents (via PyMuPDF)
- `.pdf` - PDF files with text extraction
- **Auto-OCR**: Automatically detects scanned/image-based pages and applies OCR via Tesseract

**Parameters:**
- `pages`: Page range (e.g., "1-20", "1,3,5-10")
- `extract_images`: Return image descriptions (default: false)
- `extract_tables`: Attempt table extraction (default: false)

**Auto-OCR Behavior:**
- Analyzes each page's text content density
- Pages with < 50 characters per page are considered "scanned"
- Automatically applies Tesseract OCR to scanned pages
- OCR limited to configurable max pages (default: 20)
- Mixed documents: text pages extracted directly, scanned pages OCR'd

**Limits (configurable in YAML):**
- Maximum 20 pages for OCR operations
- Maximum 100 pages for text extraction

### 5. Archives (zip, tar, tar.gz)
- `.zip` - ZIP archives
- `.tar` - TAR archives
- `.tar.gz`, `.tgz` - Compressed TAR
- `.tar.bz2` - BZ2 compressed TAR
- `.tar.xz` - XZ compressed TAR
- `.7z` - 7-Zip archives

**Parameters:**
- `mode`: "list" (default) | "extract" | "read"
- `archive_path`: Internal path to read/extract
- `pattern`: Glob pattern for filtering (e.g., "*.py")

**Output modes:**
- `list`: Show archive contents with sizes, dates
- `extract`: Extract specific file to `.tmp/extracted/<archive_name>/`
- `read`: Read specific file content directly from archive (in-memory)

**Extraction Path:**
```
{workspace}/.tmp/extracted/{archive_basename}/
└── {internal_path}
```

### 6. Images (via Pillow + exifread)
- `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.tiff`, `.webp`, `.ico`
- `.svg` - Read as text
- `.psd` - Basic info only

**Extracted Information:**
- Dimensions (width x height)
- Color mode/depth
- File size
- EXIF metadata (camera, GPS, timestamps)
- ICC color profile info

### 7. Audio Metadata (via mutagen)
- `.mp3`, `.wav`, `.flac`, `.ogg`, `.m4a`, `.aac`

**Extracted Information:**
- Duration
- Codec/format information
- Bitrate
- Metadata tags (title, artist, album, etc.)

### 8. Structured Data
- `.sqlite`, `.db` - SQLite databases (schema + preview)
- `.xml` with schema detection
- `.proto` - Protocol Buffer definitions (as text)
- `.graphql` - GraphQL schemas (as text)

---

## Tool Parameters

```yaml
ReadDocument:
  parameters:
    # Required
    file_path: str  # Path to file (relative or /workspace/...)

    # Content selection (mutually exclusive groups)
    offset: int         # Starting line (1-indexed) - for text files
    limit: int          # Max lines to read - for text files
    pages: str          # Page range - for PDF/Office docs
    rows: str           # Row range - for tabular data
    columns: str        # Column selection - for tabular data
    sheet: str|int      # Sheet name/index - for Excel

    # Archive-specific
    mode: str           # "list" | "extract" | "read"
    archive_path: str   # Path within archive
    pattern: str        # Glob pattern for archive listing

    # Processing options
    include_metadata: bool  # Include file metadata (default: true)
    format_hint: str    # Force format detection override

    # Output control
    output_format: str  # "text" (default) | "json" | "markdown"
```

---

## Caching Strategy

### Cache Location
```
~/.tmp/doc-cache/
├── index.json              # Cache index with file hashes
├── pdfs/
│   └── {hash_prefix}/
│       └── {full_hash}.json
├── office/
│   └── {hash_prefix}/
│       └── {full_hash}.json
└── archives/
    └── {hash_prefix}/
        └── {full_hash}/
            └── manifest.json
```

### Cache Key Computation
```python
def compute_cache_key(file_path: Path, params: dict) -> str:
    """Generate deterministic cache key."""
    # File content hash (first 64KB + size + mtime)
    with open(file_path, 'rb') as f:
        content_sample = f.read(65536)
    stat = file_path.stat()

    hasher = hashlib.sha256()
    hasher.update(content_sample)
    hasher.update(str(stat.st_size).encode())
    hasher.update(str(stat.st_mtime_ns).encode())

    # Include relevant params in cache key
    param_str = json.dumps(sorted(params.items()), sort_keys=True)
    hasher.update(param_str.encode())

    return hasher.hexdigest()
```

### Cache Behavior
- **Cached**: PDF extractions, Office document conversions, archive manifests
- **Not cached**: Text files, CSV/tabular reads (fast enough), images (small output)
- **TTL**: Configurable (default: 7 days)
- **Max size**: Configurable (default: 1GB), LRU eviction

---

## Security Measures

All security parameters are configured in `tools-security.yaml`.

### 1. Zip Bomb Protection
Configurable in YAML under `archive` section:
- `max_compression_ratio`: Max decompressed/compressed ratio
- `max_total_size`: Max total extracted size
- `max_file_count`: Max files in archive
- `max_single_file`: Max single file size
- `max_nesting_depth`: Max nested archive depth
- `banned_extensions`: List of forbidden file extensions

### 2. Content Sanitization (LLM Context Poisoning Prevention)
Configurable in YAML under `output` section:
- `max_chars`: Max output characters
- `max_lines`: Max output lines
- `max_cell_content`: Max content per table cell
- `strip_null_bytes`: Remove null bytes from text
- `strip_control_chars`: Remove non-printable chars
- `max_metadata_fields`: Limit metadata field count
- `max_metadata_value_len`: Truncate long metadata values

### 3. File Size Limits
Configurable in YAML under `limits` section, per format category.

### 4. Timeout Protection
Configurable in YAML under `timeouts` section.

### 5. Path Validation
- All paths validated through existing `Ag3ntumPathValidator`
- Archive internal paths sanitized (no `..`, absolute paths stripped)
- Extracted files go to `{workspace}/.tmp/extracted/{archive_name}/` only

### 6. Memory Protection
Configurable in YAML under `memory` section.

---

## Configuration (tools-security.yaml)

```yaml
tools:
  read_document:
    # Global timeout for any single file read (seconds)
    global_timeout: 180  # 3 minutes

    # File size limits by category (bytes)
    limits:
      text: 10485760          # 10MB
      pdf: 104857600          # 100MB
      office: 52428800        # 50MB
      archive: 524288000      # 500MB
      image: 52428800         # 50MB
      tabular: 104857600      # 100MB
      audio: 52428800         # 50MB

    # PDF-specific settings
    pdf:
      max_pages_text: 100
      max_pages_ocr: 20
      per_page_timeout: 5
      ocr_per_page_timeout: 30
      # Auto-OCR threshold: pages with fewer chars are considered scanned
      ocr_text_threshold: 50

    # Archive security
    archive:
      max_compression_ratio: 100
      max_total_size: 524288000     # 500MB
      max_file_count: 10000
      max_single_file: 104857600    # 100MB
      max_nesting_depth: 3
      # Extraction destination within session workspace
      extraction_dir: ".tmp/extracted"
      # Banned extensions - compiled binaries/executables only
      # NOTE: Scripts (.sh, .bat, .ps1, .php, .js, .cgi, etc.) are ALLOWED
      # to support reading website backups (PHP, NodeJS, CGI projects)
      banned_extensions:
        - ".exe"      # Windows executable
        - ".dll"      # Windows dynamic library
        - ".so"       # Linux shared object
        - ".dylib"    # macOS dynamic library
        - ".com"      # DOS executable
        - ".scr"      # Windows screensaver (executable)
        - ".msi"      # Windows installer package
        - ".app"      # macOS application bundle
        - ".deb"      # Debian package
        - ".rpm"      # RPM package
        - ".dmg"      # macOS disk image
        - ".iso"      # Disk image
        - ".img"      # Disk image

    # Output sanitization (LLM context protection)
    output:
      max_chars: 500000
      max_lines: 10000
      max_cell_content: 1000
      strip_null_bytes: true
      strip_control_chars: true
      max_metadata_fields: 50
      max_metadata_value_len: 1000
      truncation_marker: "\n... [content truncated] ..."

    # Timeouts (seconds)
    timeouts:
      global: 180               # 3 minutes max per file
      pdf_per_page: 5           # 5 seconds per PDF page
      ocr_per_page: 30          # 30 seconds per OCR page
      archive_list: 30          # 30 seconds for archive listing
      archive_extract: 60       # 60 seconds per file extraction
      pandoc: 60                # 60 seconds for Office conversion
      tabular_load: 60          # 60 seconds for loading tabular data

    # Memory limits
    memory:
      max_dataframe_rows: 100000
      max_dataframe_cols: 500
      chunk_size: 10000

    # Caching
    cache:
      enabled: true
      directory: "~/.tmp/doc-cache"
      max_size_mb: 1024
      ttl_days: 7
```

---

## Module Structure

```
Project/tools/ag3ntum/ag3ntum_read_document/
├── __init__.py                 # Exports create_read_document_tool
├── tool.py                     # Main tool implementation
├── config.py                   # Configuration loading from YAML
├── cache.py                    # Cache manager
├── security.py                 # Security validators & sanitizers
├── format_detector.py          # File format detection (MIME + extension)
├── extractors/
│   ├── __init__.py             # Extractor registry
│   ├── base.py                 # BaseExtractor abstract class
│   ├── text.py                 # Text file extractor
│   ├── tabular.py              # Pandas-based extractor (CSV, Excel, Parquet)
│   ├── pdf.py                  # PyMuPDF extractor with auto-OCR
│   ├── office.py               # Pandoc-based extractor (DOCX, RTF, etc.)
│   ├── archive.py              # Archive handler (ZIP, TAR, 7z)
│   ├── image.py                # Image metadata extractor
│   └── audio.py                # Audio metadata extractor
├── exceptions.py               # Custom exceptions
└── utils.py                    # Shared utilities
```

---

## Implementation Flow

```python
async def read_document(args: dict) -> dict:
    """Main entry point."""
    file_path = args.get("file_path")

    # 1. Validate path
    validated = validator.validate_path(file_path, operation="read")
    path = validated.normalized

    # 2. Detect format
    format_info = detect_format(path, args.get("format_hint"))

    # 3. Check file size limits (format-specific)
    size = path.stat().st_size
    limit = config.limits.get(format_info.category)
    if size > limit:
        raise FileTooLargeError(f"File size {size} exceeds limit {limit} for {format_info.category}")

    # 4. Check cache (for cacheable formats)
    if format_info.category in CACHEABLE_CATEGORIES:
        cache_key = compute_cache_key(path, args)
        cached = cache_manager.get(format_info.category, cache_key)
        if cached:
            logger.info(f"Cache hit for {file_path}")
            return _apply_selection(cached, args)

    # 5. Get extractor and extract content with timeout
    extractor = get_extractor(format_info)

    try:
        async with asyncio.timeout(config.timeouts.global):
            result = await extractor.extract(path, args)
    except asyncio.TimeoutError:
        raise ExtractionTimeoutError(f"Extraction exceeded {config.timeouts.global}s limit")

    # 6. Validate and sanitize output
    result = security.sanitize_output(result, config.output)

    # 7. Cache if applicable
    if format_info.category in CACHEABLE_CATEGORIES:
        cache_manager.put(format_info.category, cache_key, result)

    # 8. Apply selection (offset/limit/pages/rows)
    return _apply_selection(result, args)
```

---

## PDF Auto-OCR Flow

```python
async def extract_pdf(path: Path, args: dict) -> ExtractedContent:
    """Extract PDF with automatic OCR detection."""
    doc = fitz.open(path)
    pages_to_process = parse_page_range(args.get("pages"), len(doc))

    # Limit total pages
    if len(pages_to_process) > config.pdf.max_pages_text:
        pages_to_process = pages_to_process[:config.pdf.max_pages_text]

    results = []
    ocr_page_count = 0

    for page_num in pages_to_process:
        page = doc[page_num]
        text = page.get_text()

        # Auto-detect scanned page: low text content
        if len(text.strip()) < config.pdf.ocr_text_threshold:
            # This page needs OCR
            if ocr_page_count >= config.pdf.max_pages_ocr:
                results.append(PageContent(
                    page=page_num + 1,
                    text="[OCR limit reached - page skipped]",
                    is_ocr=False
                ))
                continue

            # Apply OCR with timeout
            async with asyncio.timeout(config.timeouts.ocr_per_page):
                pix = page.get_pixmap()
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                text = pytesseract.image_to_string(img)
                ocr_page_count += 1

            results.append(PageContent(
                page=page_num + 1,
                text=text,
                is_ocr=True
            ))
        else:
            # Regular text extraction
            results.append(PageContent(
                page=page_num + 1,
                text=text,
                is_ocr=False
            ))

    return ExtractedContent(
        format="pdf",
        pages=results,
        metadata=extract_pdf_metadata(doc),
        ocr_pages_used=ocr_page_count
    )
```

---

## Example Outputs

### PDF Document (with auto-OCR)
```
**Document:** report.pdf
**Pages:** 1-5 of 42
**OCR Applied:** Pages 3, 4 (scanned)
**Metadata:**
  - Title: Q4 Financial Report
  - Author: Finance Team
  - Created: 2024-01-15

---

## Page 1

# Q4 Financial Report

Executive Summary...

---

## Page 3 [OCR]

[Content extracted via OCR]
...
```

### Excel Spreadsheet
```
**File:** sales_data.xlsx
**Sheet:** Q4_Sales (1 of 3 sheets)
**Rows:** 1-50 of 1,234
**Columns:** A-F

| Row | Product    | Region   | Sales   | Units | Date       |
|-----|------------|----------|---------|-------|------------|
| 1   | Widget A   | North    | $1,234  | 100   | 2024-01-01 |
| 2   | Widget B   | South    | $2,345  | 150   | 2024-01-02 |
...

**Summary:**
  - Total rows: 1,234
  - Columns: Product, Region, Sales, Units, Date, Margin
```

### ZIP Archive (list mode)
```
**Archive:** project-backup.zip
**Total Size:** 15.2 MB (compressed), 48.7 MB (uncompressed)
**Files:** 234 files, 45 directories

Contents (matching pattern: *.py):
  src/main.py                    4.2 KB   2024-01-15 10:30
  src/utils/helpers.py           2.1 KB   2024-01-14 09:15
  src/models/user.py             3.8 KB   2024-01-15 11:45
  tests/test_main.py             5.6 KB   2024-01-15 12:00
  ...

  (23 files matching pattern)
```

### ZIP Archive (extract mode)
```
**Extracted:** src/main.py
**From:** project-backup.zip
**To:** .tmp/extracted/project-backup/src/main.py
**Size:** 4.2 KB

File is now available at: /workspace/.tmp/extracted/project-backup/src/main.py
```

### Image File
```
**Image:** photo.jpg
**Dimensions:** 4032 x 3024 pixels
**Format:** JPEG
**Color:** RGB, 8-bit
**Size:** 3.2 MB

**EXIF Metadata:**
  - Camera: iPhone 14 Pro
  - Lens: 6.86mm f/1.78
  - Exposure: 1/120s at ISO 50
  - Date: 2024-01-15 14:30:22
  - GPS: 37.7749° N, 122.4194° W
  - Software: iOS 17.2
```

---

## Dependencies

### Python packages (requirements.txt additions)
```
pandas>=2.0.0
openpyxl>=3.1.0          # Excel .xlsx support
xlrd>=2.0.1              # Legacy .xls support
PyMuPDF>=1.23.0          # PDF reading
pytesseract>=0.3.10      # Tesseract OCR wrapper
Pillow>=10.0.0           # Image handling
exifread>=3.0.0          # EXIF metadata
mutagen>=1.46.0          # Audio metadata
python-magic>=0.4.27     # MIME type detection
pypandoc>=1.12           # Pandoc wrapper
py7zr>=0.20.0            # 7z support
pyarrow>=14.0.0          # Parquet support
```

### System dependencies (REQUIRED - in system requirements.txt)
- `pandoc` - For Office document conversion (REQUIRED)
- `tesseract-ocr` - For PDF OCR (REQUIRED)
- `libmagic` - For MIME type detection

**No fallbacks**: If any required dependency is missing, the tool raises `DependencyMissingError` with a clear message and logs the error.

---

## Error Handling

```python
class ReadDocumentError(Exception):
    """Base exception for ReadDocument errors."""
    pass

class FormatNotSupportedError(ReadDocumentError):
    """File format not supported."""
    pass

class FileTooLargeError(ReadDocumentError):
    """File exceeds size limit."""
    pass

class ExtractionTimeoutError(ReadDocumentError):
    """Extraction exceeded time limit."""
    pass

class ArchiveSecurityError(ReadDocumentError):
    """Archive failed security checks (zip bomb, etc.)."""
    pass

class BannedExtensionError(ArchiveSecurityError):
    """File has a banned extension."""
    pass

class CacheError(ReadDocumentError):
    """Cache operation failed (non-fatal, logged)."""
    pass

class DependencyMissingError(ReadDocumentError):
    """Required system dependency is not installed."""
    pass

class ContentSanitizationError(ReadDocumentError):
    """Content failed sanitization checks."""
    pass
```

All errors are logged with full context before being raised.

---

## Integration

Add to `ag3ntum_file_tools.py`:

```python
from .ag3ntum_read_document import create_read_document_tool

# In create_ag3ntum_tools_mcp_server():
tools.extend([
    create_read_tool(session_id=session_id),
    create_read_document_tool(session_id=session_id),  # NEW
    create_write_tool(session_id=session_id),
    # ... rest of tools
])
```

---

## Migration Notes

- `mcp__ag3ntum__Read` remains for simple text file reading (fast path)
- `mcp__ag3ntum__ReadDocument` used for:
  - Any non-text file format (PDF, Office, archives, images, audio)
  - When metadata extraction is needed
  - When selective reading (pages, sheets, rows) is needed
  - When archive operations are needed

The agent should choose the appropriate tool based on context. For simple source code reading, `Read` is faster. For documents, archives, or media files, `ReadDocument` provides richer extraction.

---

## Future Enhancements

1. **OCR improvements**: Layout-preserving OCR, table detection in PDFs
2. **Code intelligence**: Syntax-aware extraction (functions, classes)
3. **Search within documents**: Full-text search in PDFs/Office docs
4. **Diff support**: Compare document versions
5. **Streaming**: Stream large file content progressively
