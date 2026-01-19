"""
Configuration loading for ReadDocument tool.

Loads settings from tools-security.yaml and provides typed access.
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Default config path relative to project root
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "security" / "tools-security.yaml"


@dataclass
class LimitsConfig:
    """File size limits by format category (bytes)."""

    text: int = 10_485_760  # 10MB
    pdf: int = 104_857_600  # 100MB
    office: int = 52_428_800  # 50MB
    archive: int = 524_288_000  # 500MB
    image: int = 52_428_800  # 50MB
    tabular: int = 104_857_600  # 100MB
    audio: int = 52_428_800  # 50MB

    def get(self, category: str) -> int:
        """Get limit for a category, with fallback to text limit."""
        return getattr(self, category, self.text)


@dataclass
class PDFConfig:
    """PDF-specific settings."""

    max_pages_text: int = 100
    max_pages_ocr: int = 20
    per_page_timeout: float = 5.0
    ocr_per_page_timeout: float = 30.0
    ocr_text_threshold: int = 50  # chars below this triggers OCR


@dataclass
class ArchiveConfig:
    """Archive security settings."""

    max_compression_ratio: int = 100
    max_total_size: int = 524_288_000  # 500MB
    max_file_count: int = 10_000
    max_single_file: int = 104_857_600  # 100MB
    max_nesting_depth: int = 3
    extraction_dir: str = ".tmp/extracted"
    banned_extensions: list[str] = field(
        default_factory=lambda: [
            ".exe",
            ".dll",
            ".so",
            ".dylib",
            ".com",
            ".scr",
            ".msi",
            ".app",
            ".deb",
            ".rpm",
            ".dmg",
            ".iso",
            ".img",
        ]
    )


@dataclass
class OutputConfig:
    """Output sanitization settings."""

    max_chars: int = 500_000
    max_lines: int = 10_000
    max_cell_content: int = 1_000
    strip_null_bytes: bool = True
    strip_control_chars: bool = True
    max_metadata_fields: int = 50
    max_metadata_value_len: int = 1_000
    truncation_marker: str = "\n... [content truncated] ..."


@dataclass
class TimeoutsConfig:
    """Timeout settings (seconds)."""

    global_timeout: float = 180.0  # 3 minutes
    pdf_per_page: float = 5.0
    ocr_per_page: float = 30.0
    archive_list: float = 30.0
    archive_extract: float = 60.0
    pandoc: float = 60.0
    tabular_load: float = 60.0


@dataclass
class MemoryConfig:
    """Memory protection limits."""

    max_dataframe_rows: int = 100_000
    max_dataframe_cols: int = 500
    chunk_size: int = 10_000


@dataclass
class CacheConfig:
    """Caching settings."""

    enabled: bool = True
    directory: str = "~/.tmp/doc-cache"
    max_size_mb: int = 1024
    ttl_days: int = 7

    @property
    def directory_path(self) -> Path:
        """Get expanded cache directory path."""
        return Path(self.directory).expanduser()


@dataclass
class ReadDocumentConfig:
    """Complete configuration for ReadDocument tool."""

    global_timeout: float = 180.0
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    pdf: PDFConfig = field(default_factory=PDFConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    timeouts: TimeoutsConfig = field(default_factory=TimeoutsConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)


def _load_yaml_config(config_path: Path) -> dict[str, Any]:
    """Load YAML configuration file."""
    if not config_path.exists():
        logger.warning(f"Config file not found: {config_path}, using defaults")
        return {}

    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Failed to load config from {config_path}: {e}")
        return {}


def _dict_to_dataclass(data: dict[str, Any], cls: type) -> Any:
    """Convert a dict to a dataclass, ignoring extra fields."""
    if not data:
        return cls()

    # Get field names from the dataclass
    field_names = {f.name for f in cls.__dataclass_fields__.values()}

    # Filter to only known fields
    filtered = {k: v for k, v in data.items() if k in field_names}

    return cls(**filtered)


def load_config(config_path: Path | None = None) -> ReadDocumentConfig:
    """
    Load ReadDocument configuration from YAML file.

    Args:
        config_path: Path to tools-security.yaml. If None, uses default.

    Returns:
        ReadDocumentConfig with values from YAML or defaults.
    """
    path = config_path or DEFAULT_CONFIG_PATH
    yaml_data = _load_yaml_config(path)

    # Navigate to tools.read_document section
    tool_config = yaml_data.get("tools", {}).get("read_document", {})

    if not tool_config:
        logger.info("No read_document config in YAML, using defaults")
        return ReadDocumentConfig()

    # Build config from nested sections
    config = ReadDocumentConfig(
        global_timeout=tool_config.get("global_timeout", 180.0),
        limits=_dict_to_dataclass(tool_config.get("limits", {}), LimitsConfig),
        pdf=_dict_to_dataclass(tool_config.get("pdf", {}), PDFConfig),
        archive=_dict_to_dataclass(tool_config.get("archive", {}), ArchiveConfig),
        output=_dict_to_dataclass(tool_config.get("output", {}), OutputConfig),
        timeouts=_dict_to_dataclass(tool_config.get("timeouts", {}), TimeoutsConfig),
        memory=_dict_to_dataclass(tool_config.get("memory", {}), MemoryConfig),
        cache=_dict_to_dataclass(tool_config.get("cache", {}), CacheConfig),
    )

    logger.info(f"Loaded ReadDocument config from {path}")
    return config


# Global config instance (lazy loaded)
_config: ReadDocumentConfig | None = None


def get_config() -> ReadDocumentConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config(config_path: Path | None = None) -> ReadDocumentConfig:
    """Reload configuration from file."""
    global _config
    _config = load_config(config_path)
    return _config
