"""
Sensitive Data Scanner for Ag3ntum.

Detects and redacts sensitive information like API keys, tokens, and passwords
using detect-secrets library and custom patterns.

Key features:
- Same-length replacement to preserve formatting
- Integration with detect-secrets library
- Custom regex patterns for additional detection
- Allowlist support for known false positives
- Session file scanning with configurable limits

Usage:
    from src.security import get_scanner, scan_and_redact

    # Quick usage
    result = scan_and_redact(text)
    if result.has_secrets:
        print(f"Found {len(result.secrets)} secrets")
        print(result.redacted_text)

    # With scanner instance
    scanner = get_scanner()
    result = scanner.scan(text)
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Optional

from detect_secrets.core.scan import scan_line
from detect_secrets.settings import transient_settings

logger = logging.getLogger(__name__)

# Global scanner instance (lazy-loaded)
_scanner_instance: Optional["SensitiveDataScanner"] = None


@dataclass
class DetectedSecret:
    """Represents a detected secret in text."""

    secret_type: str
    secret_value: str
    line_number: int
    start_index: int  # Index within the line
    end_index: int  # Index within the line
    replacement: str = ""

    @property
    def length(self) -> int:
        """Length of the secret value."""
        return len(self.secret_value)


@dataclass
class ScanResult:
    """Result of scanning text for secrets."""

    original_text: str
    redacted_text: str
    secrets: list[DetectedSecret] = field(default_factory=list)
    secret_types: set[str] = field(default_factory=set)

    @property
    def has_secrets(self) -> bool:
        """Whether any secrets were found."""
        return len(self.secrets) > 0

    @property
    def secret_count(self) -> int:
        """Number of secrets found."""
        return len(self.secrets)


class SensitiveDataScanner:
    """
    Detect and redact sensitive information in text.

    Supports:
    - detect-secrets library plugins
    - Custom regex patterns
    - Same-length replacement (preserves formatting)
    - Allowlist for false positives
    """

    # Default custom patterns (used if config not loaded)
    DEFAULT_CUSTOM_PATTERNS: dict[str, list[str]] = {
        "generic_api_key": [
            r'(?i)(?:api[_-]?key|apikey)["\s:=]+["\']?([a-zA-Z0-9_\-]{20,})["\']?',
            r'(?i)(?:access[_-]?token|accesstoken)["\s:=]+["\']?([a-zA-Z0-9_\-]{20,})["\']?',
            r'(?i)(?:auth[_-]?token|authtoken)["\s:=]+["\']?([a-zA-Z0-9_\-]{20,})["\']?',
            r'(?i)(?:secret[_-]?key|secretkey)["\s:=]+["\']?([a-zA-Z0-9_\-]{20,})["\']?',
        ],
        "bearer_token": [
            r"(?i)bearer\s+([a-zA-Z0-9_\-\.]{20,})",
        ],
        "password": [
            r'(?i)(?:password|passwd|pwd)["\s:=]+["\']?([^\s"\']{8,})["\']?',
        ],
        "connection_string": [
            r"(?i)(?:mongodb|postgres|mysql|redis|amqp):\/\/[^\s]+",
            r"(?i)Server=[^;]+;.*(?:Password|Pwd)=[^;]+",
        ],
        "private_key": [
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----",
        ],
        "anthropic_key": [
            r"sk-ant-[a-zA-Z0-9_-]{40,}",
        ],
        "openai_key": [
            r"sk-[a-zA-Z0-9]{32,}",
        ],
        "gcp_key": [
            r"AIza[0-9A-Za-z_-]{35}",
        ],
    }

    def __init__(
        self,
        replacement_format: str = "asterisk",
        custom_patterns: Optional[dict[str, list[str]]] = None,
        detect_secrets_plugins: Optional[list[str]] = None,
        entropy_base64_limit: float = 4.5,
        entropy_hex_limit: float = 3.0,
        false_positive_strings: Optional[list[str]] = None,
        false_positive_patterns: Optional[list[str]] = None,
    ):
        """
        Initialize the scanner.

        Args:
            replacement_format: How to replace secrets:
                - "asterisk": Replace with asterisks (same length)
                - "redact_typed": Replace with [REDACTED:TYPE] (variable length)
                - "hash": Replace with hash prefix (same length)
            custom_patterns: Dict of pattern_name -> list of regex patterns
            detect_secrets_plugins: List of detect-secrets plugin names to enable
            entropy_base64_limit: Threshold for Base64 high-entropy detection
            entropy_hex_limit: Threshold for hex high-entropy detection
            false_positive_strings: Known false positive strings to skip
            false_positive_patterns: Regex patterns for false positives to skip
        """
        self.replacement_format = replacement_format
        self.custom_patterns = custom_patterns or self.DEFAULT_CUSTOM_PATTERNS
        self.detect_secrets_plugins = detect_secrets_plugins or []
        self.entropy_base64_limit = entropy_base64_limit
        self.entropy_hex_limit = entropy_hex_limit
        self.false_positive_strings = set(false_positive_strings or [])
        self.false_positive_patterns = [
            re.compile(p) for p in (false_positive_patterns or [])
        ]

        # Compile custom patterns
        self._compiled_patterns: dict[str, list[re.Pattern]] = {}
        for name, patterns in self.custom_patterns.items():
            self._compiled_patterns[name] = [re.compile(p) for p in patterns]

        # detect-secrets settings (lazy-loaded)
        self._detect_secrets_settings: Optional[dict] = None

    def _get_detect_secrets_settings(self) -> dict:
        """Get detect-secrets settings, building if necessary."""
        if self._detect_secrets_settings is not None:
            return self._detect_secrets_settings

        plugins = []
        for plugin_name in self.detect_secrets_plugins:
            if plugin_name == "Base64HighEntropyString":
                plugins.append({"name": plugin_name, "limit": self.entropy_base64_limit})
            elif plugin_name == "HexHighEntropyString":
                plugins.append({"name": plugin_name, "limit": self.entropy_hex_limit})
            else:
                plugins.append({"name": plugin_name})

        self._detect_secrets_settings = {
            "plugins_used": plugins,
            "filters_used": [
                {"path": "detect_secrets.filters.allowlist.is_line_allowlisted"},
            ],
        }
        return self._detect_secrets_settings

    def _generate_replacement(self, secret_value: str, secret_type: str) -> str:
        """
        Generate replacement string with SAME LENGTH as original.

        This is critical for preserving formatting in files.
        """
        length = len(secret_value)

        if self.replacement_format == "asterisk":
            # Same length asterisks
            return "*" * length

        elif self.replacement_format == "hash":
            # Hash-based replacement, same length
            hash_val = hashlib.sha256(secret_value.encode()).hexdigest()
            # Format: [HASH:xxxxx...] - adjust hash length to match
            prefix = "[HASH:"
            suffix = "]"
            overhead = len(prefix) + len(suffix)
            if length <= overhead:
                return "*" * length
            hash_chars = hash_val[: length - overhead]
            return f"{prefix}{hash_chars}{suffix}"

        elif self.replacement_format == "redact_typed":
            # This format does NOT preserve length (use only if formatting doesn't matter)
            type_label = secret_type.upper().replace(" ", "_")
            return f"[REDACTED:{type_label}]"

        else:
            # Default to asterisks
            return "*" * length

    def _is_false_positive(self, secret_value: str) -> bool:
        """Check if a detected secret is a known false positive."""
        # Check exact strings
        if secret_value in self.false_positive_strings:
            return True

        # Check patterns
        for pattern in self.false_positive_patterns:
            if pattern.search(secret_value):
                return True

        return False

    def _detect_with_detect_secrets(self, text: str) -> list[DetectedSecret]:
        """Use detect-secrets library to find secrets."""
        if not self.detect_secrets_plugins:
            return []

        detected: list[DetectedSecret] = []
        lines = text.split("\n")

        settings = self._get_detect_secrets_settings()

        with transient_settings(settings):
            for line_num, line in enumerate(lines, 1):
                for secret in scan_line(line):
                    if secret.secret_value:
                        # Skip false positives
                        if self._is_false_positive(secret.secret_value):
                            continue

                        # Find position in line
                        start_idx = line.find(secret.secret_value)
                        if start_idx != -1:
                            detected.append(
                                DetectedSecret(
                                    secret_type=secret.type,
                                    secret_value=secret.secret_value,
                                    line_number=line_num,
                                    start_index=start_idx,
                                    end_index=start_idx + len(secret.secret_value),
                                )
                            )

        return detected

    def _detect_with_custom_patterns(self, text: str) -> list[DetectedSecret]:
        """Use custom regex patterns to find secrets."""
        detected: list[DetectedSecret] = []
        lines = text.split("\n")

        for secret_type, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                for line_num, line in enumerate(lines, 1):
                    for match in pattern.finditer(line):
                        # Get the captured group if exists, otherwise full match
                        if match.groups():
                            secret_value = match.group(1)
                            start_idx = match.start(1)
                            end_idx = match.end(1)
                        else:
                            secret_value = match.group(0)
                            start_idx = match.start()
                            end_idx = match.end()

                        # Skip false positives
                        if self._is_false_positive(secret_value):
                            continue

                        # Skip very short matches (likely false positives)
                        if len(secret_value) < 8:
                            continue

                        detected.append(
                            DetectedSecret(
                                secret_type=secret_type,
                                secret_value=secret_value,
                                line_number=line_num,
                                start_index=start_idx,
                                end_index=end_idx,
                            )
                        )

        return detected

    def _deduplicate_secrets(
        self, secrets: list[DetectedSecret]
    ) -> list[DetectedSecret]:
        """Remove duplicate detections (same value on same line)."""
        seen: set[tuple[str, int]] = set()
        unique: list[DetectedSecret] = []

        for secret in secrets:
            key = (secret.secret_value, secret.line_number)
            if key not in seen:
                seen.add(key)
                unique.append(secret)

        return unique

    def scan(self, text: str) -> ScanResult:
        """
        Scan text for secrets and generate redacted version.

        Args:
            text: Input text to scan

        Returns:
            ScanResult with original text, redacted text, and list of secrets
        """
        if not text:
            return ScanResult(original_text=text, redacted_text=text)

        all_secrets: list[DetectedSecret] = []

        # Detect with detect-secrets library
        all_secrets.extend(self._detect_with_detect_secrets(text))

        # Detect with custom patterns
        all_secrets.extend(self._detect_with_custom_patterns(text))

        # Deduplicate
        all_secrets = self._deduplicate_secrets(all_secrets)

        # Generate replacements
        for secret in all_secrets:
            secret.replacement = self._generate_replacement(
                secret.secret_value, secret.secret_type
            )

        # Sort by length (longest first) to avoid partial replacement issues
        all_secrets.sort(key=lambda s: len(s.secret_value), reverse=True)

        # Replace secrets in text
        redacted_text = text
        for secret in all_secrets:
            redacted_text = redacted_text.replace(
                secret.secret_value, secret.replacement
            )

        # Collect unique types
        secret_types = {s.secret_type for s in all_secrets}

        return ScanResult(
            original_text=text,
            redacted_text=redacted_text,
            secrets=all_secrets,
            secret_types=secret_types,
        )

    def scan_file(
        self,
        filepath: Path | str,
        write_redacted: bool = False,
    ) -> ScanResult:
        """
        Scan a file for secrets.

        Args:
            filepath: Path to the file
            write_redacted: If True, overwrite file with redacted content

        Returns:
            ScanResult with scan details
        """
        filepath = Path(filepath)

        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"Failed to read file {filepath}: {e}")
            return ScanResult(original_text="", redacted_text="")

        result = self.scan(content)

        if write_redacted and result.has_secrets:
            try:
                filepath.write_text(result.redacted_text, encoding="utf-8")
                logger.info(
                    f"Redacted {result.secret_count} secrets in {filepath}"
                )
            except Exception as e:
                logger.error(f"Failed to write redacted file {filepath}: {e}")

        return result


def get_scanner() -> SensitiveDataScanner:
    """
    Get the global scanner instance (lazy-loaded with config).

    Returns:
        Configured SensitiveDataScanner instance
    """
    global _scanner_instance

    if _scanner_instance is None:
        # Try to load config
        try:
            from .scanner_config import get_scanner_config

            config = get_scanner_config()
            _scanner_instance = SensitiveDataScanner(
                replacement_format=config.replacement_format,
                custom_patterns=config.custom_patterns,
                detect_secrets_plugins=config.detect_secrets_plugins,
                entropy_base64_limit=config.entropy_base64_limit,
                entropy_hex_limit=config.entropy_hex_limit,
                false_positive_strings=config.false_positive_strings,
                false_positive_patterns=config.false_positive_patterns,
            )
        except Exception as e:
            logger.warning(f"Failed to load scanner config: {e}. Using defaults.")
            _scanner_instance = SensitiveDataScanner()

    return _scanner_instance


def reset_scanner() -> None:
    """Reset the global scanner instance (for testing or config reload)."""
    global _scanner_instance
    _scanner_instance = None


def scan_text(text: str) -> ScanResult:
    """
    Quick function to scan text for secrets.

    Args:
        text: Input text

    Returns:
        ScanResult with scan details
    """
    return get_scanner().scan(text)


def scan_and_redact(text: str) -> ScanResult:
    """
    Scan text and return redacted version.

    This is the primary function to use for filtering content.

    Args:
        text: Input text

    Returns:
        ScanResult containing redacted_text and details about found secrets
    """
    return get_scanner().scan(text)
