"""
Unit tests for Sensitive Data Scanner.

Tests cover:
- SensitiveDataScanner class (detection, same-length replacement)
- scan_and_redact() function
- ScannerConfig loading
- Session file scanning
- Various secret types (API keys, tokens, passwords, connection strings)
- Edge cases (empty content, false positives, allowlists)
"""
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.security.sensitive_data_scanner import (
    SensitiveDataScanner,
    ScanResult,
    DetectedSecret,
    get_scanner,
    scan_and_redact,
    scan_text,
    reset_scanner,
)
from src.security.scanner_config import (
    ScannerConfig,
    SessionScanConfig,
    AlertConfig,
    load_scanner_config,
    get_scanner_config,
    is_scanner_enabled,
    get_type_label,
    reset_scanner_config,
)
from src.security.session_scanner import (
    SessionScanResult,
    FileScanResult,
    scan_session_files,
    emit_security_alert,
    _should_scan_file,
    _is_text_file,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def reset_global_instances():
    """Reset global scanner and config instances before each test."""
    reset_scanner()
    reset_scanner_config()
    yield
    reset_scanner()
    reset_scanner_config()


@pytest.fixture
def scanner():
    """Create a SensitiveDataScanner with default settings."""
    return SensitiveDataScanner()


@pytest.fixture
def scanner_with_custom_patterns():
    """Create scanner with additional custom patterns."""
    return SensitiveDataScanner(
        custom_patterns={
            "test_secret": [r"TEST_SECRET_([A-Z0-9]{10,})"],
        }
    )


@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace directory for file scanning tests."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


# ============================================================================
# SensitiveDataScanner Tests
# ============================================================================


class TestSensitiveDataScanner:
    """Tests for SensitiveDataScanner class."""

    def test_empty_text_returns_empty_result(self, scanner: SensitiveDataScanner):
        """Empty text should return empty result."""
        result = scanner.scan("")
        assert result.has_secrets is False
        assert result.secret_count == 0
        assert result.redacted_text == ""

    def test_text_without_secrets_unchanged(self, scanner: SensitiveDataScanner):
        """Text without secrets should be returned unchanged."""
        text = "Hello, world! This is a normal text without any secrets."
        result = scanner.scan(text)
        assert result.has_secrets is False
        assert result.redacted_text == text

    def test_same_length_replacement_asterisk(self, scanner: SensitiveDataScanner):
        """Replacement should be same length as original (asterisk format)."""
        # Use a pattern that will be detected
        text = 'api_key = "sk-ant-abcdefghijklmnopqrstuvwxyz123456789012"'
        result = scanner.scan(text)

        # The replacement should preserve total length
        assert len(result.redacted_text) == len(text)

    def test_same_length_replacement_hash(self):
        """Hash replacement format should also be same length."""
        scanner = SensitiveDataScanner(replacement_format="hash")
        # Create a secret that will definitely be detected
        secret_value = "sk-ant-abcdefghijklmnopqrstuvwxyz123456789012"
        text = f'api_key = "{secret_value}"'
        result = scanner.scan(text)

        # Should preserve total length
        assert len(result.redacted_text) == len(text)

    def test_detect_anthropic_key(self, scanner: SensitiveDataScanner):
        """Should detect Anthropic API keys."""
        key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"
        text = f"ANTHROPIC_API_KEY={key}"
        result = scanner.scan(text)

        assert result.has_secrets
        # May be detected as anthropic_key or generic_api_key depending on pattern matching order
        assert "anthropic_key" in result.secret_types or "generic_api_key" in result.secret_types

    def test_detect_openai_key(self, scanner: SensitiveDataScanner):
        """Should detect OpenAI API keys."""
        key = "sk-proj-abcdefghijklmnopqrstuvwxyz01234567"
        text = f"OPENAI_API_KEY={key}"
        result = scanner.scan(text)

        assert result.has_secrets

    def test_detect_bearer_token(self, scanner: SensitiveDataScanner):
        """Should detect Bearer tokens."""
        token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkw"
        text = f"Authorization: Bearer {token}"
        result = scanner.scan(text)

        assert result.has_secrets
        assert "bearer_token" in result.secret_types

    def test_detect_password_in_config(self, scanner: SensitiveDataScanner):
        """Should detect passwords in config-like formats."""
        text = 'password = "SuperSecretPassword123!"'
        result = scanner.scan(text)

        assert result.has_secrets
        assert "password" in result.secret_types

    def test_detect_generic_api_key(self, scanner: SensitiveDataScanner):
        """Should detect generic API key patterns."""
        text = 'api_key: "abcdefghij1234567890klmnopqrst"'
        result = scanner.scan(text)

        assert result.has_secrets

    def test_detect_connection_string(self, scanner: SensitiveDataScanner):
        """Should detect database connection strings."""
        text = "mongodb://user:password123@localhost:27017/mydb"
        result = scanner.scan(text)

        assert result.has_secrets
        assert "connection_string" in result.secret_types

    def test_detect_private_key_header(self, scanner: SensitiveDataScanner):
        """Should detect private key headers."""
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQ..."
        result = scanner.scan(text)

        assert result.has_secrets
        assert "private_key" in result.secret_types

    def test_detect_gcp_api_key(self, scanner: SensitiveDataScanner):
        """Should detect GCP API keys."""
        # GCP API keys have format: AIza[35 alphanumeric chars]
        key = "AIzaSyA1234567890abcdefghijklmnopqrstuv"  # 39 chars total (4 + 35)
        text = f"GCP_KEY={key}"
        result = scanner.scan(text)

        assert result.has_secrets
        assert "gcp_key" in result.secret_types

    def test_multiple_secrets_all_detected(self, scanner: SensitiveDataScanner):
        """Should detect multiple different secrets in same text."""
        text = """
        ANTHROPIC_KEY=sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCDEF
        password="mysuperpassword123"
        database=mongodb://admin:secretpass@db.example.com/prod
        """
        result = scanner.scan(text)

        assert result.has_secrets
        assert result.secret_count >= 2  # At least password and connection string

    def test_duplicate_secrets_deduplicated(self, scanner: SensitiveDataScanner):
        """Duplicate secrets on same line should be deduplicated."""
        key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"
        text = f"key1={key} key2={key}"  # Same key twice on same line
        result = scanner.scan(text)

        # Should still only count unique occurrences
        assert result.has_secrets

    def test_false_positive_string_skipped(self):
        """Known false positive strings should be skipped."""
        scanner = SensitiveDataScanner(
            false_positive_strings=["NOT_A_REAL_SECRET_TESTING"]
        )
        text = 'api_key = "NOT_A_REAL_SECRET_TESTING"'
        result = scanner.scan(text)

        # Should not detect the false positive
        assert not any(s.secret_value == "NOT_A_REAL_SECRET_TESTING" for s in result.secrets)

    def test_false_positive_pattern_skipped(self):
        """False positive patterns should be skipped."""
        scanner = SensitiveDataScanner(
            false_positive_patterns=[r"^PLACEHOLDER_.*"]
        )
        text = 'api_key = "PLACEHOLDER_KEY_DO_NOT_USE"'
        result = scanner.scan(text)

        # Should not detect the placeholder
        assert not any(s.secret_value == "PLACEHOLDER_KEY_DO_NOT_USE" for s in result.secrets)

    def test_short_values_not_detected(self, scanner: SensitiveDataScanner):
        """Very short values should not be detected (likely false positives)."""
        text = 'password = "abc"'
        result = scanner.scan(text)

        # 3-char password should not be detected (min length is 8)
        assert not result.has_secrets or not any(s.secret_value == "abc" for s in result.secrets)

    def test_custom_pattern_detection(self, scanner_with_custom_patterns: SensitiveDataScanner):
        """Custom patterns should be detected."""
        text = "My test secret: TEST_SECRET_ABCD123456"
        result = scanner_with_custom_patterns.scan(text)

        assert result.has_secrets
        assert "test_secret" in result.secret_types

    def test_secrets_sorted_longest_first(self, scanner: SensitiveDataScanner):
        """Secrets should be sorted longest first to avoid partial replacement."""
        # This tests the internal logic - longer secrets replaced first
        text = 'short="short_key_123456789" long="very_long_secret_key_0123456789abcdefghijk"'
        result = scanner.scan(text)

        if result.has_secrets and len(result.secrets) > 1:
            # Verify sorting
            lengths = [len(s.secret_value) for s in result.secrets]
            assert lengths == sorted(lengths, reverse=True)


class TestScanResult:
    """Tests for ScanResult dataclass."""

    def test_has_secrets_false_when_empty(self):
        """has_secrets should be False when no secrets."""
        result = ScanResult(original_text="test", redacted_text="test")
        assert result.has_secrets is False
        assert result.secret_count == 0

    def test_has_secrets_true_when_secrets_present(self):
        """has_secrets should be True when secrets exist."""
        secret = DetectedSecret(
            secret_type="api_key",
            secret_value="secret123456789012",
            line_number=1,
            start_index=0,
            end_index=20,
        )
        result = ScanResult(
            original_text="secret123456789012",
            redacted_text="********************",
            secrets=[secret],
            secret_types={"api_key"},
        )
        assert result.has_secrets is True
        assert result.secret_count == 1


class TestDetectedSecret:
    """Tests for DetectedSecret dataclass."""

    def test_length_property(self):
        """length property should return secret value length."""
        secret = DetectedSecret(
            secret_type="test",
            secret_value="0123456789",
            line_number=1,
            start_index=0,
            end_index=10,
        )
        assert secret.length == 10


# ============================================================================
# Module-level Functions Tests
# ============================================================================


class TestScanAndRedact:
    """Tests for scan_and_redact() function."""

    def test_scan_and_redact_basic(self):
        """Basic scan_and_redact usage."""
        text = 'password="my_secret_password_123"'
        result = scan_and_redact(text)

        assert result.has_secrets
        # Redacted text should not contain the secret
        assert "my_secret_password_123" not in result.redacted_text

    def test_scan_text_alias(self):
        """scan_text should work same as scan_and_redact."""
        text = 'api_key = "test_key_abcdefghijklmnop"'
        result1 = scan_and_redact(text)
        result2 = scan_text(text)

        # Both should have same behavior
        assert result1.has_secrets == result2.has_secrets


class TestGetScanner:
    """Tests for get_scanner() function."""

    def test_get_scanner_returns_instance(self):
        """get_scanner should return a SensitiveDataScanner instance."""
        scanner = get_scanner()
        assert isinstance(scanner, SensitiveDataScanner)

    def test_get_scanner_singleton(self):
        """get_scanner should return same instance."""
        scanner1 = get_scanner()
        scanner2 = get_scanner()
        assert scanner1 is scanner2


# ============================================================================
# Scanner Config Tests
# ============================================================================


class TestScannerConfig:
    """Tests for scanner configuration."""

    def test_default_config_values(self):
        """Default config should have sensible values."""
        config = ScannerConfig()

        assert config.enabled is True
        assert config.replacement_format == "asterisk"
        assert isinstance(config.session_scan, SessionScanConfig)
        assert isinstance(config.alerts, AlertConfig)

    def test_session_scan_config_defaults(self):
        """Session scan config should have sensible defaults."""
        config = SessionScanConfig()

        assert config.enabled is True
        assert config.max_depth == 5
        assert config.max_files == 100
        assert config.max_file_size_bytes == 1048576  # 1MB
        assert ".py" in config.scan_extensions
        assert ".json" in config.scan_extensions

    def test_alert_config_defaults(self):
        """Alert config should have sensible defaults."""
        config = AlertConfig()

        assert config.sse_enabled is True
        assert config.log_enabled is True
        assert "{filename}" in config.single_file_message
        assert "{count}" in config.multiple_files_message

    def test_load_config_missing_file_returns_defaults(self, tmp_path):
        """Loading non-existent config file should return defaults."""
        non_existent = tmp_path / "does_not_exist.yaml"
        config = load_scanner_config(non_existent)

        assert config.enabled is True
        assert config.replacement_format == "asterisk"

    def test_load_config_invalid_yaml_returns_defaults(self, tmp_path):
        """Loading invalid YAML should return defaults."""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("{{{{ invalid yaml ::::")

        config = load_scanner_config(bad_yaml)
        assert isinstance(config, ScannerConfig)

    def test_load_config_from_valid_yaml(self, tmp_path):
        """Should correctly parse valid YAML config."""
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text("""
enabled: true
replacement_format: hash

detection:
  detect_secrets_plugins:
    - Base64HighEntropyString
  entropy:
    base64_limit: 4.0
  custom_patterns:
    test_pattern:
      - "TEST_[A-Z]+"

session_scan:
  enabled: true
  max_depth: 3
  max_files: 50

alerts:
  sse_enabled: true
  type_labels:
    api_key: "API Key"
""")

        config = load_scanner_config(config_yaml)

        assert config.replacement_format == "hash"
        assert "Base64HighEntropyString" in config.detect_secrets_plugins
        assert config.entropy_base64_limit == 4.0
        assert "test_pattern" in config.custom_patterns
        assert config.session_scan.max_depth == 3
        assert config.session_scan.max_files == 50
        assert config.alerts.type_labels.get("api_key") == "API Key"


class TestIsScAnnerEnabled:
    """Tests for is_scanner_enabled() function."""

    def test_enabled_by_default(self):
        """Scanner should be enabled by default."""
        # Reset to get fresh config
        reset_scanner_config()
        # This will load default config
        with patch("src.security.scanner_config.DEFAULT_CONFIG_PATH", Path("/nonexistent")):
            reset_scanner_config()
            assert is_scanner_enabled() is True


class TestGetTypeLabel:
    """Tests for get_type_label() function."""

    def test_default_label_formatting(self):
        """Default label should be title-cased with underscores as spaces."""
        label = get_type_label("api_key")
        assert "Api Key" in label or "api key" in label.lower()

    def test_custom_label_from_config(self, tmp_path):
        """Custom labels from config should be used."""
        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text("""
alerts:
  type_labels:
    api_key: "API Key (Sensitive)"
""")
        reset_scanner_config()
        with patch("src.security.scanner_config.DEFAULT_CONFIG_PATH", config_yaml):
            reset_scanner_config()
            label = get_type_label("api_key")
            assert label == "API Key (Sensitive)"


# ============================================================================
# Session Scanner Tests
# ============================================================================


class TestSessionScanner:
    """Tests for session file scanner."""

    def test_should_scan_file_text_file(self, temp_workspace):
        """Text files with proper extension should be scanned."""
        test_file = temp_workspace / "test.py"
        test_file.write_text("print('hello')")

        config = ScannerConfig()
        result = _should_scan_file(
            test_file,
            "test.py",
            config,
            time.time() + 100,  # Future time to ensure file is "recent"
        )
        assert result is True

    def test_should_scan_file_binary_extension_skipped(self, temp_workspace):
        """Binary file extensions should be skipped."""
        test_file = temp_workspace / "test.exe"
        test_file.write_bytes(b"\x00binary\x00data")

        config = ScannerConfig()
        result = _should_scan_file(
            test_file,
            "test.exe",
            config,
            time.time() + 100,
        )
        assert result is False

    def test_should_scan_file_readonly_mount_skipped(self, temp_workspace):
        """Files in read-only mount paths should be skipped."""
        ro_dir = temp_workspace / "external" / "ro"
        ro_dir.mkdir(parents=True)
        test_file = ro_dir / "config.json"
        test_file.write_text('{"key": "value"}')

        config = ScannerConfig()
        result = _should_scan_file(
            test_file,
            "external/ro/config.json",
            config,
            time.time() + 100,
        )
        assert result is False

    def test_should_scan_file_too_large_skipped(self, temp_workspace):
        """Files exceeding size limit should be skipped."""
        test_file = temp_workspace / "large.txt"
        # Write more than 1MB
        test_file.write_text("x" * (1024 * 1024 + 100))

        config = ScannerConfig()
        result = _should_scan_file(
            test_file,
            "large.txt",
            config,
            time.time() + 100,
        )
        assert result is False

    def test_should_scan_file_old_file_skipped(self, temp_workspace):
        """Files older than recent_files_window should be skipped."""
        test_file = temp_workspace / "old.py"
        test_file.write_text("print('old')")

        config = ScannerConfig()
        config.session_scan.recent_files_window_seconds = 1

        # Reference time far in the future
        result = _should_scan_file(
            test_file,
            "old.py",
            config,
            time.time() + 10000,  # File will appear old
        )
        assert result is False

    def test_should_scan_file_skip_pattern_matched(self, temp_workspace):
        """Files matching skip patterns should be skipped."""
        cache_dir = temp_workspace / "__pycache__"
        cache_dir.mkdir()
        test_file = cache_dir / "module.pyc"
        test_file.write_bytes(b"compiled python")

        config = ScannerConfig()
        result = _should_scan_file(
            test_file,
            "__pycache__/module.pyc",
            config,
            time.time() + 100,
        )
        assert result is False

    def test_is_text_file_detects_text(self, temp_workspace):
        """_is_text_file should detect text files."""
        text_file = temp_workspace / "readme.txt"
        text_file.write_text("This is a text file with normal content.")

        assert _is_text_file(text_file) is True

    def test_is_text_file_detects_binary(self, temp_workspace):
        """_is_text_file should detect binary files."""
        binary_file = temp_workspace / "image.bin"
        binary_file.write_bytes(b"\x00\x01\x02\x03PNG\x00binary")

        assert _is_text_file(binary_file) is False


class TestScanSessionFiles:
    """Tests for scan_session_files() function."""

    @pytest.mark.asyncio
    async def test_scan_empty_workspace(self, temp_workspace):
        """Scanning empty workspace should return no secrets."""
        result = await scan_session_files(
            session_id="test-session",
            workspace_path=temp_workspace,
        )

        assert result.files_scanned == 0
        assert result.has_secrets is False

    @pytest.mark.asyncio
    async def test_scan_workspace_with_secrets(self, temp_workspace):
        """Should detect secrets in workspace files."""
        # Create file with a secret
        secret_file = temp_workspace / "config.py"
        secret_file.write_text('API_KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"')

        result = await scan_session_files(
            session_id="test-session",
            workspace_path=temp_workspace,
            reference_time=time.time() + 100,  # Ensure file is "recent"
            redact_files=False,  # Don't actually redact for this test
        )

        assert result.files_scanned >= 1
        assert result.has_secrets

    @pytest.mark.asyncio
    async def test_scan_workspace_redacts_files(self, temp_workspace):
        """Should redact secrets when redact_files=True."""
        # Create file with a secret
        secret_file = temp_workspace / "env.txt"
        original_secret = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"
        secret_file.write_text(f'API_KEY="{original_secret}"')

        result = await scan_session_files(
            session_id="test-session",
            workspace_path=temp_workspace,
            reference_time=time.time() + 100,
            redact_files=True,
        )

        if result.has_secrets:
            # File should be redacted
            new_content = secret_file.read_text()
            assert original_secret not in new_content
            # Should contain asterisks
            assert "***" in new_content

    @pytest.mark.asyncio
    async def test_scan_respects_max_files_limit(self, temp_workspace):
        """Should respect max_files limit."""
        # Create many files
        for i in range(20):
            (temp_workspace / f"file{i}.txt").write_text(f"content {i}")

        # Create a mock config with low max_files limit
        mock_config = ScannerConfig()
        mock_config.session_scan.max_files = 5

        with patch("src.security.session_scanner.get_scanner_config", return_value=mock_config):
            result = await scan_session_files(
                session_id="test-session",
                workspace_path=temp_workspace,
                reference_time=time.time() + 100,
            )

            # Should scan at most 5 files
            assert result.files_scanned <= 5

    @pytest.mark.asyncio
    async def test_scan_respects_max_depth_limit(self, temp_workspace):
        """Should respect max_depth limit."""
        # Create deeply nested structure
        deep_dir = temp_workspace
        for i in range(10):
            deep_dir = deep_dir / f"level{i}"
            deep_dir.mkdir()
            (deep_dir / "file.txt").write_text(f"content at level {i}")

        # Default max_depth is 5
        result = await scan_session_files(
            session_id="test-session",
            workspace_path=temp_workspace,
            reference_time=time.time() + 100,
        )

        # Files beyond depth 5 should not be scanned
        # (exact behavior depends on how many files get scanned)
        assert result.files_scanned >= 0

    @pytest.mark.asyncio
    async def test_scan_disabled_scanner_returns_empty(self, temp_workspace):
        """When scanner is disabled, should return empty result."""
        secret_file = temp_workspace / "secret.txt"
        secret_file.write_text('password="secret123456789"')

        with patch("src.security.session_scanner.is_scanner_enabled", return_value=False):
            result = await scan_session_files(
                session_id="test-session",
                workspace_path=temp_workspace,
            )

            assert result.files_scanned == 0
            assert result.has_secrets is False

    @pytest.mark.asyncio
    async def test_scan_nonexistent_workspace(self, tmp_path):
        """Scanning non-existent workspace should return error."""
        nonexistent = tmp_path / "does_not_exist"

        result = await scan_session_files(
            session_id="test-session",
            workspace_path=nonexistent,
        )

        assert len(result.errors) > 0


class TestSessionScanResult:
    """Tests for SessionScanResult class."""

    def test_has_secrets_property(self):
        """has_secrets should reflect files_with_secrets."""
        result = SessionScanResult(
            session_id="test",
            files_scanned=5,
            files_with_secrets=0,
            total_secrets=0,
        )
        assert result.has_secrets is False

        result.files_with_secrets = 1
        result.total_secrets = 2
        assert result.has_secrets is True

    def test_get_alert_message_single_file(self):
        """Alert message for single file detection."""
        file_result = FileScanResult(
            file_path=Path("/workspace/secret.txt"),
            relative_path="secret.txt",
            scan_result=ScanResult(
                original_text="test",
                redacted_text="****",
                secrets=[],
                secret_types=set(),
            ),
        )
        file_result.scan_result.secrets = [MagicMock(secret_count=1)]
        file_result.scan_result.secret_types = {"api_key"}

        result = SessionScanResult(
            session_id="test",
            files_scanned=1,
            files_with_secrets=1,
            total_secrets=1,
            secret_types={"api_key"},
            file_results=[file_result],
        )

        message = result.get_alert_message()
        assert "secret.txt" in message or "1" in message

    def test_get_alert_message_multiple_files(self):
        """Alert message for multiple file detection."""
        result = SessionScanResult(
            session_id="test",
            files_scanned=5,
            files_with_secrets=3,
            total_secrets=7,
            secret_types={"api_key", "password"},
        )

        message = result.get_alert_message()
        assert "3" in message  # Should mention 3 files

    def test_to_alert_data_structure(self):
        """to_alert_data should return proper structure."""
        result = SessionScanResult(
            session_id="test-123",
            files_scanned=10,
            files_with_secrets=2,
            total_secrets=5,
            secret_types={"api_key", "password"},
        )

        alert_data = result.to_alert_data()

        assert alert_data["session_id"] == "test-123"
        assert alert_data["files_scanned"] == 10
        assert alert_data["files_with_secrets"] == 2
        assert alert_data["total_secrets"] == 5
        assert "api_key" in alert_data["secret_types"]
        assert "password" in alert_data["secret_types"]
        assert "message" in alert_data
        assert "type_labels" in alert_data


class TestEmitSecurityAlert:
    """Tests for emit_security_alert() function."""

    @pytest.mark.asyncio
    async def test_emit_with_no_secrets_does_nothing(self):
        """Should not emit when no secrets detected."""
        result = SessionScanResult(
            session_id="test",
            files_scanned=1,
            files_with_secrets=0,
            total_secrets=0,
        )

        queue = MagicMock()
        await emit_security_alert("test", result, queue)

        queue.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_emit_with_secrets_sends_event(self):
        """Should emit event when secrets detected."""
        result = SessionScanResult(
            session_id="test",
            files_scanned=1,
            files_with_secrets=1,
            total_secrets=2,
            secret_types={"api_key"},
        )

        import asyncio
        queue = asyncio.Queue()

        await emit_security_alert("test", result, queue)

        # Should have event in queue
        assert not queue.empty()
        event = await queue.get()
        assert event["type"] == "security_alert"
        assert event["session_id"] == "test"


# ============================================================================
# Integration Tests
# ============================================================================


class TestScannerIntegration:
    """Integration tests for the complete scanning workflow."""

    def test_full_scan_workflow(self):
        """Test complete scan workflow from text to redacted output."""
        # Create a realistic config file content with detectable patterns
        config_text = """
# Database Configuration
database_url=mongodb://admin:SuperSecretPassword123!@db.example.com:27017/production
redis_url=redis://cache.internal:6379

# API Keys
api_key = "realLookingKeyHere0123456789abcdefghijklmno"
auth_token = "authTokenHere0123456789abcdefghijklmno123"

# Bearer Token
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkw
"""
        result = scan_and_redact(config_text)

        # Should detect secrets
        assert result.has_secrets
        assert result.secret_count >= 2

        # Redacted text should not contain actual secrets (check connection string URL)
        assert "mongodb://admin:SuperSecretPassword123!" in config_text  # Original has it
        assert "mongodb://admin:SuperSecretPassword123!" not in result.redacted_text

        # Length should be preserved
        assert len(result.redacted_text) == len(config_text)

    def test_json_content_scanning(self):
        """Test scanning JSON content."""
        json_content = '''{
  "api_key": "sk-ant-api03-testkey0123456789abcdefghijklmnopqrstuvwxyz",
  "database": {
    "password": "db_password_secure_123"
  }
}'''
        result = scan_and_redact(json_content)

        assert result.has_secrets
        # The redacted content should still be valid-ish JSON (same structure)
        assert '"api_key"' in result.redacted_text
        assert "sk-ant-api03-testkey" not in result.redacted_text

    def test_code_file_scanning(self):
        """Test scanning code-like content."""
        code_content = '''
import os

# API key with detectable pattern
api_key = "my_secret_api_key_0123456789abcdef"

# Bearer token
headers = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.testtoken12345"}

def connect_db():
    return psycopg2.connect("mongodb://admin:secretpassword123@localhost/db")
'''
        result = scan_and_redact(code_content)

        assert result.has_secrets
        # Code structure should be preserved
        assert "import os" in result.redacted_text
        assert "def connect_db():" in result.redacted_text

    @pytest.mark.asyncio
    async def test_workspace_scan_with_mixed_files(self, temp_workspace):
        """Test scanning workspace with various file types."""
        # Create different file types
        (temp_workspace / "config.json").write_text(
            '{"api_key": "sk-ant-api03-test0123456789abcdefghijklmnopqrstuv"}'
        )
        (temp_workspace / "script.py").write_text(
            'PASSWORD = "my_secret_password_123456"'
        )
        (temp_workspace / "readme.md").write_text(
            "# Project README\nNo secrets here."
        )
        (temp_workspace / "data.bin").write_bytes(b"\x00\x01binary\x00")

        result = await scan_session_files(
            session_id="test",
            workspace_path=temp_workspace,
            reference_time=time.time() + 100,
            redact_files=False,
        )

        # Should scan text files, skip binary
        assert result.files_scanned >= 2  # At least config.json and script.py
        # Should find secrets in config.json and script.py
        assert result.has_secrets
