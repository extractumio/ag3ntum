"""
Logging configuration for Ag3ntum.

Provides unified logging setup utilities for CLI, HTTP client, and API.
All modules should use these functions instead of configuring logging directly.

Usage:
    from .logging_config import setup_file_logging, setup_dual_logging

    # For CLI (file-only logging)
    setup_file_logging(log_level="INFO")

    # For API (console + file logging)
    setup_dual_logging(log_level="DEBUG", loggers=["src.api", "uvicorn"])
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import colorlog

from ..config import LOGS_DIR
from .constants import (
    COLORLOG_COLORS,
    LOG_BACKUP_COUNT,
    LOG_FILE_BACKEND,
    LOG_FILE_CLI,
    LOG_FILE_HTTP,
    LOG_FORMAT_COLORED,
    LOG_FORMAT_FILE,
    LOG_MAX_BYTES,
)

logger = logging.getLogger(__name__)


def _get_log_level(log_level: str) -> int:
    """
    Convert log level string to logging constant.

    Args:
        log_level: Log level name (DEBUG, INFO, WARNING, ERROR).

    Returns:
        Logging level constant.
    """
    return getattr(logging, log_level.upper(), logging.INFO)


def _create_rotating_file_handler(
    log_file: Path,
    level: int,
    max_bytes: int = LOG_MAX_BYTES,
    backup_count: int = LOG_BACKUP_COUNT,
) -> RotatingFileHandler:
    """
    Create a rotating file handler with standard configuration.

    Args:
        log_file: Path to the log file.
        level: Logging level.
        max_bytes: Maximum file size before rotation.
        backup_count: Number of backup files to keep.

    Returns:
        Configured RotatingFileHandler.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        filename=str(log_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT_FILE))
    return handler


def _create_console_handler(level: int, colored: bool = False) -> logging.Handler:
    """
    Create a console (stdout) handler.

    Args:
        level: Logging level.
        colored: Whether to use colored output.

    Returns:
        Configured StreamHandler.
    """
    if colored:
        handler = colorlog.StreamHandler(sys.stdout)
        handler.setFormatter(
            colorlog.ColoredFormatter(
                LOG_FORMAT_COLORED,
                log_colors=COLORLOG_COLORS,
                secondary_log_colors={},
                style="%",
            )
        )
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(LOG_FORMAT_FILE))

    handler.setLevel(level)
    return handler


def setup_file_logging(
    log_level: str = "INFO",
    log_file: Optional[Path] = None,
    log_name: str = LOG_FILE_CLI,
    max_bytes: int = LOG_MAX_BYTES,
    backup_count: int = LOG_BACKUP_COUNT,
) -> None:
    """
    Configure file-only logging (for CLI and HTTP client).

    Replaces all handlers on the root logger with a single rotating file handler.
    This ensures clean log separation between different entry points.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR).
        log_file: Path to log file. If None, uses LOGS_DIR / log_name.
        log_name: Name of log file (default: agent_cli.log).
        max_bytes: Maximum size of log file before rotation.
        backup_count: Number of backup files to keep.
    """
    level = _get_log_level(log_level)

    if log_file is None:
        log_file = LOGS_DIR / log_name

    file_handler = _create_rotating_file_handler(
        log_file=log_file,
        level=level,
        max_bytes=max_bytes,
        backup_count=backup_count,
    )

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)


def setup_dual_logging(
    log_level: str = "INFO",
    log_file: Optional[Path] = None,
    log_name: str = LOG_FILE_BACKEND,
    loggers: Optional[list[str]] = None,
    max_bytes: int = LOG_MAX_BYTES,
    backup_count: int = LOG_BACKUP_COUNT,
) -> None:
    """
    Configure dual logging: colored console + rotating file.

    For API/backend use where you want both console output for development
    and file logging for production. Configures specific loggers to prevent
    log bleeding from other components.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR).
        log_file: Path to log file. If None, uses LOGS_DIR / log_name.
        log_name: Name of log file (default: backend.log).
        loggers: List of logger names to configure. If None, uses root logger.
        max_bytes: Maximum size of log file before rotation.
        backup_count: Number of backup files to keep.
    """
    level = _get_log_level(log_level)

    if log_file is None:
        log_file = LOGS_DIR / log_name

    file_handler = _create_rotating_file_handler(
        log_file=log_file,
        level=level,
        max_bytes=max_bytes,
        backup_count=backup_count,
    )
    console_handler = _create_console_handler(level=level, colored=True)

    if loggers is None:
        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.setLevel(level)
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)
    else:
        # Configure specific loggers (prevents log bleeding)
        for logger_name in loggers:
            log = logging.getLogger(logger_name)
            log.handlers.clear()
            log.setLevel(level)
            log.addHandler(file_handler)
            log.addHandler(console_handler)
            log.propagate = False


def setup_cli_logging(log_level: str = "INFO") -> None:
    """
    Configure logging for CLI entry point (agent_cli.py).

    Shorthand for setup_file_logging with CLI defaults.

    Args:
        log_level: Logging level.
    """
    setup_file_logging(log_level=log_level, log_name=LOG_FILE_CLI)


def setup_http_logging(log_level: str = "INFO") -> None:
    """
    Configure logging for HTTP client entry point (agent_http.py).

    Shorthand for setup_file_logging with HTTP client defaults.

    Args:
        log_level: Logging level.
    """
    setup_file_logging(log_level=log_level, log_name=LOG_FILE_HTTP)


def setup_backend_logging(log_level: str = "INFO") -> None:
    """
    Configure logging for API backend (src/api/main.py).

    Uses dual logging (console + file) for specific backend loggers.

    Args:
        log_level: Logging level.
    """
    backend_loggers = [
        "src.api",
        "src.services",
        "src.core",
        "src.db",
        "ag3ntum",
        "tools.ag3ntum",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "fastapi",
    ]

    setup_dual_logging(
        log_level=log_level,
        log_name=LOG_FILE_BACKEND,
        loggers=backend_loggers,
    )

    # Enable uvicorn access logs for HTTP request tracking
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
