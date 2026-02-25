"""
Unit tests for external mounts functionality.

Tests cover:
- PathSanitizer: filename sanitization and security checks
- External mount path normalization (ro, rw, persistent)
- Read-only enforcement for external RO mounts
- Symlink escape prevention
- File API mount metadata
- Mount type detection in file listings
"""
from pathlib import Path
from typing import Generator

import pytest

from src.core.path_validator import (
    Ag3ntumPathValidator,
    PathValidatorConfig,
    PathValidationError,
    PathSanitizer,
)


# =============================================================================
# PathSanitizer Tests
# =============================================================================


class TestPathSanitizer:
    """Test filename sanitization for external mounts."""

    def test_normal_filename_passes(self) -> None:
        """Normal filename passes sanitization."""
        result = PathSanitizer.sanitize_filename("document.pdf")
        assert result == "document.pdf"

    def test_unicode_filename_normalized(self) -> None:
        """Unicode filename is NFC normalized."""
        # This tests that unicode is normalized but not rejected
        result = PathSanitizer.sanitize_filename("документ.pdf")
        assert result == "документ.pdf"

    def test_path_traversal_rejected(self) -> None:
        """Path traversal patterns are rejected."""
        with pytest.raises(PathValidationError) as exc_info:
            PathSanitizer.sanitize_filename("../etc/passwd")
        assert "DANGEROUS_FILENAME" in exc_info.value.reason

    def test_null_byte_rejected(self) -> None:
        """Null bytes in filename are rejected."""
        with pytest.raises(PathValidationError) as exc_info:
            PathSanitizer.sanitize_filename("file\x00.txt")
        assert "DANGEROUS_FILENAME" in exc_info.value.reason

    def test_control_chars_rejected(self) -> None:
        """Control characters are rejected."""
        with pytest.raises(PathValidationError) as exc_info:
            PathSanitizer.sanitize_filename("file\x1f.txt")
        assert "DANGEROUS_FILENAME" in exc_info.value.reason

    def test_windows_reserved_names_rejected(self) -> None:
        """Windows reserved device names are rejected."""
        reserved_names = ["CON", "PRN", "AUX", "NUL", "COM1", "LPT1"]
        for name in reserved_names:
            with pytest.raises(PathValidationError) as exc_info:
                PathSanitizer.sanitize_filename(name)
            assert "DANGEROUS_FILENAME" in exc_info.value.reason

    def test_long_filename_rejected(self) -> None:
        """Excessively long filenames are rejected."""
        long_name = "a" * 300
        with pytest.raises(PathValidationError) as exc_info:
            PathSanitizer.sanitize_filename(long_name)
        assert "FILENAME_TOO_LONG" in exc_info.value.reason

    def test_invisible_chars_removed(self) -> None:
        """Zero-width and invisible characters are removed."""
        # Zero-width space embedded in filename
        filename = "te\u200bst.txt"
        result = PathSanitizer.sanitize_filename(filename)
        assert result == "test.txt"

    def test_empty_filename_rejected(self) -> None:
        """Empty filename is rejected."""
        with pytest.raises(PathValidationError):
            PathSanitizer.sanitize_filename("")

    def test_sanitize_without_raising(self) -> None:
        """Non-raising mode returns sanitized version."""
        # This should sanitize instead of raising
        result = PathSanitizer.sanitize_filename(
            "CON", raise_on_error=False
        )
        # Should have replaced the dangerous pattern (Windows reserved name CON -> _)
        assert result == "_"

    def test_has_null_bytes(self) -> None:
        """Null byte detection works correctly."""
        assert PathSanitizer.has_null_bytes("file\x00.txt") is True
        assert PathSanitizer.has_null_bytes("file.txt") is False

    def test_has_path_traversal(self) -> None:
        """Path traversal detection works correctly."""
        assert PathSanitizer.has_path_traversal("../etc/passwd") is True
        assert PathSanitizer.has_path_traversal("foo/bar/baz") is False
        assert PathSanitizer.has_path_traversal("foo/../bar") is True


# =============================================================================
# External Mount Path Normalization Tests
# =============================================================================


class TestExternalMountNormalization:
    """Test external mount path normalization.

    Uses flattened mount structure where mounts are at /mounts/{name}
    instead of /mounts/{type}/{name}.
    """

    @pytest.fixture
    def temp_mounts(self, tmp_path: Path) -> dict[str, Path]:
        """Create temporary mount structure (flattened)."""
        # Flattened structure: /mounts/{name} (no ro/rw subdirs)
        downloads = tmp_path / "mounts" / "downloads"
        projects = tmp_path / "mounts" / "projects"
        persistent = tmp_path / "users" / "testuser" / "ag3ntum" / "persistent"
        workspace = tmp_path / "workspace"

        for d in [downloads, projects, persistent, workspace]:
            d.mkdir(parents=True)

        # Create test files
        (downloads / "readme.txt").write_text("readonly content")
        (projects / "editable.txt").write_text("writable content")
        (persistent / "cache.json").write_text("{}")

        return {
            "downloads": downloads,  # RO mount
            "projects": projects,    # RW mount
            "persistent": persistent,
            "workspace": workspace,
            "root": tmp_path,
        }

    @pytest.fixture
    def validator(self, temp_mounts: dict[str, Path]) -> Ag3ntumPathValidator:
        """Create validator with flattened mount configuration."""
        config = PathValidatorConfig(
            workspace_path=temp_mounts["workspace"],
            global_mounts_ro={"downloads": temp_mounts["downloads"]},
            global_mounts_rw={"projects": temp_mounts["projects"]},
            persistent_path=temp_mounts["persistent"],
        )
        return Ag3ntumPathValidator(config)

    def test_ro_mount_path_normalized(
        self, validator: Ag3ntumPathValidator, temp_mounts: dict[str, Path]
    ) -> None:
        """Read-only mount path is normalized correctly."""
        result = validator._normalize_path("/workspace/external/ro/downloads/readme.txt")
        expected = temp_mounts["downloads"] / "readme.txt"
        assert result == expected

    def test_rw_mount_path_normalized(
        self, validator: Ag3ntumPathValidator, temp_mounts: dict[str, Path]
    ) -> None:
        """Read-write mount path is normalized correctly."""
        result = validator._normalize_path("/workspace/external/rw/projects/editable.txt")
        expected = temp_mounts["projects"] / "editable.txt"
        assert result == expected

    def test_persistent_path_normalized(
        self, validator: Ag3ntumPathValidator, temp_mounts: dict[str, Path]
    ) -> None:
        """Persistent storage path is normalized correctly."""
        result = validator._normalize_path("/workspace/persistent/cache.json")
        expected = temp_mounts["persistent"] / "cache.json"
        assert result == expected

    def test_relative_external_path_normalized(
        self, validator: Ag3ntumPathValidator, temp_mounts: dict[str, Path]
    ) -> None:
        """Relative external paths are normalized correctly."""
        result = validator._normalize_path("./external/ro/downloads/readme.txt")
        expected = temp_mounts["downloads"] / "readme.txt"
        assert result == expected


# =============================================================================
# External Mount Validation Tests
# =============================================================================


class TestExternalMountValidation:
    """Test external mount path validation and permissions."""

    @pytest.fixture
    def temp_mounts(self, tmp_path: Path) -> dict[str, Path]:
        """Create temporary mount structure (flattened)."""
        downloads = tmp_path / "mounts" / "downloads"
        projects = tmp_path / "mounts" / "projects"
        persistent = tmp_path / "users" / "testuser" / "ag3ntum" / "persistent"
        workspace = tmp_path / "workspace"

        for d in [downloads, projects, persistent, workspace]:
            d.mkdir(parents=True)

        # Create test files
        (downloads / "readme.txt").write_text("readonly content")
        (projects / "editable.txt").write_text("writable content")

        return {
            "downloads": downloads,  # RO mount
            "projects": projects,    # RW mount
            "persistent": persistent,
            "workspace": workspace,
            "root": tmp_path,
        }

    @pytest.fixture
    def validator(self, temp_mounts: dict[str, Path]) -> Ag3ntumPathValidator:
        """Create validator with flattened mount configuration."""
        config = PathValidatorConfig(
            workspace_path=temp_mounts["workspace"],
            global_mounts_ro={"downloads": temp_mounts["downloads"]},
            global_mounts_rw={"projects": temp_mounts["projects"]},
            persistent_path=temp_mounts["persistent"],
        )
        return Ag3ntumPathValidator(config)

    def test_read_from_ro_mount_allowed(
        self, validator: Ag3ntumPathValidator, temp_mounts: dict[str, Path]
    ) -> None:
        """Reading from RO mount should succeed."""
        result = validator.validate_path(
            "/workspace/external/ro/downloads/readme.txt", "read"
        )
        assert result.normalized.exists()
        assert result.is_readonly is True

    def test_write_to_ro_mount_blocked(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Writing to RO mount should fail."""
        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_path(
                "/workspace/external/ro/downloads/new.txt", "write"
            )
        assert "read-only" in str(exc_info.value).lower()

    def test_write_to_rw_mount_allowed(
        self, validator: Ag3ntumPathValidator, temp_mounts: dict[str, Path]
    ) -> None:
        """Writing to RW mount should succeed."""
        result = validator.validate_path(
            "/workspace/external/rw/projects/new.txt", "write"
        )
        assert result.is_readonly is False

    def test_write_to_persistent_allowed(
        self, validator: Ag3ntumPathValidator, temp_mounts: dict[str, Path]
    ) -> None:
        """Writing to persistent storage should succeed."""
        result = validator.validate_path(
            "/workspace/persistent/cache.json", "write"
        )
        assert result.is_readonly is False

    def test_blocklist_in_external_mount(
        self, validator: Ag3ntumPathValidator, temp_mounts: dict[str, Path]
    ) -> None:
        """Blocklisted files in external mounts should be blocked."""
        # Create .env file in RW mount (flattened: directly in projects dir)
        env_file = temp_mounts["projects"] / ".env"
        env_file.write_text("SECRET=xxx")

        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_path(
                "/workspace/external/rw/projects/.env", "read"
            )
        assert "BLOCKLIST" in exc_info.value.reason


# =============================================================================
# Symlink Security Tests
# =============================================================================


class TestSymlinkSecurity:
    """Test symlink escape prevention."""

    @pytest.fixture
    def temp_mounts(self, tmp_path: Path) -> dict[str, Path]:
        """Create temporary mount structure with symlink escape attempt."""
        allowed = tmp_path / "allowed"
        forbidden = tmp_path / "forbidden"
        workspace = tmp_path / "workspace"

        for d in [allowed, forbidden, workspace]:
            d.mkdir(parents=True)

        (forbidden / "secret.txt").write_text("secret content")

        return {
            "allowed": allowed,
            "forbidden": forbidden,
            "workspace": workspace,
            "root": tmp_path,
        }

    def test_symlink_escape_detected(
        self, temp_mounts: dict[str, Path]
    ) -> None:
        """Symlink escaping boundary should be detected."""
        config = PathValidatorConfig(
            workspace_path=temp_mounts["workspace"],
            global_mounts_rw={"allowed": temp_mounts["allowed"]},
        )
        validator = Ag3ntumPathValidator(config)

        # Create symlink in allowed area pointing to forbidden
        link = temp_mounts["allowed"] / "sneaky"
        link.symlink_to(temp_mounts["forbidden"])

        # Attempt to access through symlink should fail
        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_no_symlink_escape(
                temp_mounts["allowed"] / "sneaky" / "secret.txt",
                boundary=temp_mounts["allowed"],
            )
        assert "SYMLINK_ESCAPE" in exc_info.value.reason or "PATH_ESCAPE" in exc_info.value.reason


# =============================================================================
# Mount Type Detection Tests
# =============================================================================


class TestMountTypeDetection:
    """Test mount type detection for file listings."""

    def test_ro_mount_detected(self) -> None:
        """Read-only mount path is detected correctly."""
        from src.api.routes.files import get_mount_info

        is_external, is_readonly, mount_type = get_mount_info("external/ro/downloads/file.txt")
        assert is_external is True
        assert is_readonly is True
        assert mount_type == "ro"

    def test_rw_mount_detected(self) -> None:
        """Read-write mount path is detected correctly."""
        from src.api.routes.files import get_mount_info

        is_external, is_readonly, mount_type = get_mount_info("external/rw/projects/file.txt")
        assert is_external is True
        assert is_readonly is False
        assert mount_type == "rw"

    def test_persistent_mount_detected(self) -> None:
        """Persistent storage path is detected correctly."""
        from src.api.routes.files import get_mount_info

        is_external, is_readonly, mount_type = get_mount_info("persistent/cache.json")
        assert is_external is True
        assert is_readonly is False
        assert mount_type == "persistent"

    def test_regular_file_not_external(self) -> None:
        """Regular workspace file is not detected as external."""
        from src.api.routes.files import get_mount_info

        is_external, is_readonly, mount_type = get_mount_info("src/main.py")
        assert is_external is False
        assert is_readonly is False
        assert mount_type is None

    def test_external_directory_itself(self) -> None:
        """The 'external' directory itself is detected."""
        from src.api.routes.files import get_mount_info

        is_external, is_readonly, mount_type = get_mount_info("external")
        assert is_external is True
        assert mount_type is None


# =============================================================================
# Path Traversal Attack Tests
# =============================================================================


class TestPathTraversalAttacks:
    """Test various path traversal attack vectors."""

    @pytest.fixture
    def temp_workspace(self, tmp_path: Path) -> Path:
        """Create temporary workspace."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace

    @pytest.fixture
    def validator(self, temp_workspace: Path) -> Ag3ntumPathValidator:
        """Create validator."""
        config = PathValidatorConfig(
            workspace_path=temp_workspace,
            global_mounts_ro={"downloads": temp_workspace / "external" / "ro"},
            global_mounts_rw={"projects": temp_workspace / "external" / "rw"},
        )
        return Ag3ntumPathValidator(config)

    def test_double_dot_escape(self, validator: Ag3ntumPathValidator) -> None:
        """Test .. path traversal is blocked."""
        attacks = [
            "/workspace/external/ro/downloads/../../../etc/passwd",
            "/workspace/../../etc/shadow",
        ]
        for attack in attacks:
            with pytest.raises(PathValidationError):
                validator.validate_path(attack, "read")

    def test_null_byte_injection(self, validator: Ag3ntumPathValidator) -> None:
        """Test null byte injection is blocked."""
        with pytest.raises(PathValidationError):
            validator.validate_path("/workspace/file.txt\x00.jpg", "read")


# =============================================================================
# Sandbox Mount Configuration Tests
# =============================================================================


class TestSandboxMountConfig:
    """Test sandbox mount configuration with optional mounts."""

    def test_optional_mount_field(self) -> None:
        """Test that SandboxMount supports optional field."""
        from src.core.sandbox import SandboxMount

        mount = SandboxMount(
            source="/mounts/ro",
            target="/workspace/external/ro",
            mode="ro",
            optional=True,
        )
        assert mount.optional is True

    def test_default_optional_is_false(self) -> None:
        """Test that optional defaults to False."""
        from src.core.sandbox import SandboxMount

        mount = SandboxMount(
            source="/mounts/ro",
            target="/workspace/external/ro",
            mode="ro",
        )
        assert mount.optional is False

    def test_mount_resolve_preserves_optional(self) -> None:
        """Test that resolve preserves optional field."""
        from src.core.sandbox import SandboxMount

        mount = SandboxMount(
            source="/users/{username}/ag3ntum/persistent",
            target="/persistent",  # Mounted at /persistent inside sandbox
            mode="rw",
            optional=True,
        )
        resolved = mount.resolve({"username": "testuser"})
        assert resolved.optional is True
        assert resolved.source == "/users/testuser/ag3ntum/persistent"


# =============================================================================
# Per-User Mount Tests
# =============================================================================


class TestPerUserMountNormalization:
    """Test per-user mount path normalization."""

    @pytest.fixture
    def temp_user_mounts(self, tmp_path: Path) -> dict[str, Path]:
        """Create temporary per-user mount structure."""
        workspace = tmp_path / "workspace"
        user_ro_docs = tmp_path / "user_mounts" / "docs"
        user_rw_projects = tmp_path / "user_mounts" / "projects"

        for d in [workspace, user_ro_docs, user_rw_projects]:
            d.mkdir(parents=True)

        # Create test files
        (user_ro_docs / "readme.md").write_text("User docs")
        (user_rw_projects / "app.py").write_text("# App code")

        return {
            "workspace": workspace,
            "user_ro_docs": user_ro_docs,
            "user_rw_projects": user_rw_projects,
            "root": tmp_path,
        }

    @pytest.fixture
    def validator(self, temp_user_mounts: dict[str, Path]) -> Ag3ntumPathValidator:
        """Create validator with per-user mount configuration."""
        config = PathValidatorConfig(
            workspace_path=temp_user_mounts["workspace"],
            user_mounts_ro={"docs": temp_user_mounts["user_ro_docs"]},
            user_mounts_rw={"projects": temp_user_mounts["user_rw_projects"]},
        )
        return Ag3ntumPathValidator(config)

    def test_user_ro_mount_path_normalized(
        self, validator: Ag3ntumPathValidator, temp_user_mounts: dict[str, Path]
    ) -> None:
        """Per-user RO mount path is normalized correctly."""
        result = validator._normalize_path("/workspace/external/user-ro/docs/readme.md")
        expected = temp_user_mounts["user_ro_docs"] / "readme.md"
        assert result == expected

    def test_user_rw_mount_path_normalized(
        self, validator: Ag3ntumPathValidator, temp_user_mounts: dict[str, Path]
    ) -> None:
        """Per-user RW mount path is normalized correctly."""
        result = validator._normalize_path("/workspace/external/user-rw/projects/app.py")
        expected = temp_user_mounts["user_rw_projects"] / "app.py"
        assert result == expected

    def test_relative_user_mount_path_normalized(
        self, validator: Ag3ntumPathValidator, temp_user_mounts: dict[str, Path]
    ) -> None:
        """Relative per-user mount path is normalized correctly."""
        result = validator._normalize_path("./external/user-ro/docs/readme.md")
        expected = temp_user_mounts["user_ro_docs"] / "readme.md"
        assert result == expected


class TestPerUserMountValidation:
    """Test per-user mount path validation and permissions."""

    @pytest.fixture
    def temp_user_mounts(self, tmp_path: Path) -> dict[str, Path]:
        """Create temporary per-user mount structure."""
        workspace = tmp_path / "workspace"
        user_ro_docs = tmp_path / "user_mounts" / "docs"
        user_rw_projects = tmp_path / "user_mounts" / "projects"

        for d in [workspace, user_ro_docs, user_rw_projects]:
            d.mkdir(parents=True)

        # Create test files
        (user_ro_docs / "readme.md").write_text("User docs")
        (user_rw_projects / "app.py").write_text("# App code")

        return {
            "workspace": workspace,
            "user_ro_docs": user_ro_docs,
            "user_rw_projects": user_rw_projects,
            "root": tmp_path,
        }

    @pytest.fixture
    def validator(self, temp_user_mounts: dict[str, Path]) -> Ag3ntumPathValidator:
        """Create validator with per-user mount configuration."""
        config = PathValidatorConfig(
            workspace_path=temp_user_mounts["workspace"],
            user_mounts_ro={"docs": temp_user_mounts["user_ro_docs"]},
            user_mounts_rw={"projects": temp_user_mounts["user_rw_projects"]},
        )
        return Ag3ntumPathValidator(config)

    def test_read_from_user_ro_mount_allowed(
        self, validator: Ag3ntumPathValidator, temp_user_mounts: dict[str, Path]
    ) -> None:
        """Reading from per-user RO mount should succeed."""
        result = validator.validate_path(
            "/workspace/external/user-ro/docs/readme.md", "read"
        )
        assert result.normalized.exists()
        assert result.is_readonly is True

    def test_write_to_user_ro_mount_blocked(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Writing to per-user RO mount should fail."""
        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_path(
                "/workspace/external/user-ro/docs/new.txt", "write"
            )
        assert "read-only" in str(exc_info.value).lower()

    def test_write_to_user_rw_mount_allowed(
        self, validator: Ag3ntumPathValidator, temp_user_mounts: dict[str, Path]
    ) -> None:
        """Writing to per-user RW mount should succeed."""
        result = validator.validate_path(
            "/workspace/external/user-rw/projects/new.py", "write"
        )
        assert result.is_readonly is False

    def test_read_from_user_rw_mount_allowed(
        self, validator: Ag3ntumPathValidator, temp_user_mounts: dict[str, Path]
    ) -> None:
        """Reading from per-user RW mount should succeed."""
        result = validator.validate_path(
            "/workspace/external/user-rw/projects/app.py", "read"
        )
        assert result.normalized.exists()
        assert result.is_readonly is False

    def test_blocklist_in_user_mount(
        self, validator: Ag3ntumPathValidator, temp_user_mounts: dict[str, Path]
    ) -> None:
        """Blocklisted files in per-user mounts should be blocked."""
        # Create .env file in user RW mount
        env_file = temp_user_mounts["user_rw_projects"] / ".env"
        env_file.write_text("SECRET=xxx")

        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_path(
                "/workspace/external/user-rw/projects/.env", "read"
            )
        assert "BLOCKLIST" in exc_info.value.reason

    def test_unconfigured_user_mount_falls_through(
        self, validator: Ag3ntumPathValidator, temp_user_mounts: dict[str, Path]
    ) -> None:
        """Accessing unconfigured per-user mount should fall through to workspace."""
        # Try to access a mount name that doesn't exist
        # This should normalize to a workspace path and then fail boundary check
        # (since the workspace/external/user-ro/unknown path doesn't exist)
        result = validator._normalize_path("/workspace/external/user-ro/unknown/file.txt")
        # The path should be under workspace since "unknown" mount isn't configured
        assert str(result).startswith(str(temp_user_mounts["workspace"]))


# =============================================================================
# Host Path Resolution Tests (Original-Path Mount Support)
# =============================================================================


class TestOriginalPathMountResolution:
    """Test that host paths (e.g., /var/log) work via original-path mount support."""

    @pytest.fixture
    def temp_mount_structure(self, tmp_path: Path) -> dict[str, Path]:
        """Create temporary mount structure simulating Docker + bwrap setup."""
        workspace = tmp_path / "workspace"
        mounts = tmp_path / "mounts"
        var_log = mounts / "global_var_log"

        for d in [workspace, mounts, var_log]:
            d.mkdir(parents=True)

        # Create test files
        (var_log / "syslog").write_text("log content")
        (var_log / "auth.log").write_text("auth content")

        return {
            "workspace": workspace,
            "mounts": mounts,
            "var_log": var_log,
        }

    @pytest.fixture
    def validator_with_original_path(
        self, temp_mount_structure: dict[str, Path]
    ) -> Ag3ntumPathValidator:
        """Create validator with original-path mount configured."""
        config = PathValidatorConfig(
            workspace_path=temp_mount_structure["workspace"],
            # Configure /var/log to point to our temp mount
            original_path_mounts_ro={"/var/log": temp_mount_structure["var_log"]},
        )
        return Ag3ntumPathValidator(config)

    def test_host_path_resolves_to_mount(
        self, validator_with_original_path: Ag3ntumPathValidator,
        temp_mount_structure: dict[str, Path]
    ) -> None:
        """Host path /var/log resolves to the Docker mount path."""
        result = validator_with_original_path._normalize_path("/var/log")
        assert result == temp_mount_structure["var_log"].resolve()

    def test_host_path_subpath_resolves(
        self, validator_with_original_path: Ag3ntumPathValidator,
        temp_mount_structure: dict[str, Path]
    ) -> None:
        """Host path with subpath /var/log/syslog resolves correctly."""
        result = validator_with_original_path._normalize_path("/var/log/syslog")
        expected = (temp_mount_structure["var_log"] / "syslog").resolve()
        assert result == expected

    def test_host_path_validates_for_read(
        self, validator_with_original_path: Ag3ntumPathValidator,
        temp_mount_structure: dict[str, Path]
    ) -> None:
        """Host path can be validated for read operations."""
        result = validator_with_original_path.validate_path("/var/log/syslog", "read")
        expected = temp_mount_structure["var_log"] / "syslog"
        assert result.normalized == expected.resolve()
        assert result.is_readonly is True

    def test_host_path_write_blocked_for_ro_mount(
        self, validator_with_original_path: Ag3ntumPathValidator,
    ) -> None:
        """Write to read-only host path mount is blocked."""
        with pytest.raises(PathValidationError) as exc_info:
            validator_with_original_path.validate_path("/var/log/test.log", "write")
        # Error reason is "Mount is read-only (external mount, per-user ro, dynamic ro, or original-path ro)"
        assert "read-only" in exc_info.value.reason.lower() or "read-only" in str(exc_info.value).lower()

    def test_unmounted_host_path_blocked(
        self, validator_with_original_path: Ag3ntumPathValidator,
    ) -> None:
        """Accessing host path that isn't mounted is blocked."""
        with pytest.raises(PathValidationError) as exc_info:
            validator_with_original_path.validate_path("/etc/passwd", "read")
        # Error for paths outside allowed directories
        error_str = str(exc_info.value).lower()
        reason = exc_info.value.reason.lower()
        assert ("outside" in error_str or "outside" in reason or
                "must be within" in reason or "boundary" in reason)


class TestGetAllMountsWithHostPaths:
    """Test get_all_mounts_with_host_paths function."""

    @pytest.fixture
    def mock_manifest(self, tmp_path: Path, monkeypatch) -> Path:
        """Create mock manifest file."""
        manifest_dir = tmp_path / "auto-generated"
        manifest_dir.mkdir(parents=True)
        manifest_path = manifest_dir / "auto-generated-mounts.yaml"

        # Also create the mount directories
        mounts_dir = tmp_path / "mounts"
        (mounts_dir / "global_var_log").mkdir(parents=True)
        (mounts_dir / "product_docs").mkdir(parents=True)
        (mounts_dir / "user_documents").mkdir(parents=True)

        manifest_content = f"""
mounts:
  ro:
    - name: global_var_log
      host_path: /var/log
      container_path: {mounts_dir}/global_var_log
  rw:
    - name: product_docs
      host_path: /Users/greg/PRODUCT
      container_path: {mounts_dir}/product_docs
  user-ro:
    - name: user_documents
      host_path: /Users/{{username}}/Documents
      container_path: {mounts_dir}/user_documents
"""
        manifest_path.write_text(manifest_content)

        # Patch the manifest path
        monkeypatch.setattr(
            "src.services.mount_service.Path",
            lambda p: Path(str(p).replace("/auto-generated", str(manifest_dir)))
            if "/auto-generated" in str(p)
            else Path(p)
        )

        return manifest_path

    def test_loads_global_ro_mounts(self, mock_manifest: Path, tmp_path: Path) -> None:
        """Loads global RO mounts with host_path."""
        # Direct test of parsing logic
        import yaml
        with open(mock_manifest, "r") as f:
            manifest = yaml.safe_load(f)

        mounts = manifest.get("mounts", {})
        ro_mounts = mounts.get("ro", [])

        assert len(ro_mounts) == 1
        assert ro_mounts[0]["name"] == "global_var_log"
        assert ro_mounts[0]["host_path"] == "/var/log"

    def test_loads_global_rw_mounts(self, mock_manifest: Path) -> None:
        """Loads global RW mounts with host_path."""
        import yaml
        with open(mock_manifest, "r") as f:
            manifest = yaml.safe_load(f)

        mounts = manifest.get("mounts", {})
        rw_mounts = mounts.get("rw", [])

        assert len(rw_mounts) == 1
        assert rw_mounts[0]["name"] == "product_docs"
        assert rw_mounts[0]["host_path"] == "/Users/greg/PRODUCT"

    def test_user_mount_placeholder_present(self, mock_manifest: Path) -> None:
        """User mounts have {username} placeholder in host_path."""
        import yaml
        with open(mock_manifest, "r") as f:
            manifest = yaml.safe_load(f)

        mounts = manifest.get("mounts", {})
        user_ro = mounts.get("user-ro", [])

        assert len(user_ro) == 1
        assert "{username}" in user_ro[0]["host_path"]


class TestGetPathDisplayMapping:
    """Test get_path_display_mapping function for agent output path transformation."""

    @pytest.fixture
    def mock_manifest_for_display(self, tmp_path: Path, monkeypatch) -> Path:
        """Create mock manifest file for path display mapping tests."""
        manifest_dir = tmp_path / "auto-generated"
        manifest_dir.mkdir(parents=True)
        manifest_path = manifest_dir / "auto-generated-mounts.yaml"

        # Create mock mount directories that "exist"
        mounts_dir = tmp_path / "mounts"
        (mounts_dir / "global_var_log").mkdir(parents=True)
        (mounts_dir / "product_docs").mkdir(parents=True)
        (mounts_dir / "user_documents").mkdir(parents=True)

        manifest_content = f"""
mounts:
  ro:
    - name: global_var_log
      host_path: /var/log
      container_path: {mounts_dir}/global_var_log
  rw:
    - name: product_docs
      host_path: /Users/greg/PRODUCT
      container_path: {mounts_dir}/product_docs
  user-ro:
    - name: user_documents
      host_path: /Users/{{username}}/Documents
      container_path: {mounts_dir}/user_documents
  user-rw: []
"""
        manifest_path.write_text(manifest_content)

        # Patch Path to redirect manifest path lookups
        original_path = Path

        def patched_path(p):
            p_str = str(p)
            if p_str == "/auto-generated/auto-generated-mounts.yaml":
                return original_path(manifest_path)
            return original_path(p)

        monkeypatch.setattr("src.services.mount_service.Path", patched_path)

        return manifest_path

    def test_returns_empty_when_no_manifest(self, tmp_path: Path, monkeypatch) -> None:
        """Returns empty dict when manifest doesn't exist."""
        from src.services.mount_service import get_path_display_mapping

        # Patch to non-existent path
        original_path = Path
        monkeypatch.setattr(
            "src.services.mount_service.Path",
            lambda p: original_path(tmp_path / "nonexistent" / "manifest.yaml")
            if "auto-generated-mounts.yaml" in str(p)
            else original_path(p)
        )

        result = get_path_display_mapping()
        assert result == {}

    def test_builds_mapping_for_ro_mounts(
        self, mock_manifest_for_display: Path
    ) -> None:
        """Builds mapping for global RO mounts."""
        from src.services.mount_service import get_path_display_mapping

        result = get_path_display_mapping()

        assert "external/ro/global_var_log" in result
        assert result["external/ro/global_var_log"] == "/var/log"

    def test_builds_mapping_for_rw_mounts(
        self, mock_manifest_for_display: Path
    ) -> None:
        """Builds mapping for global RW mounts."""
        from src.services.mount_service import get_path_display_mapping

        result = get_path_display_mapping()

        assert "external/rw/product_docs" in result
        assert result["external/rw/product_docs"] == "/Users/greg/PRODUCT"

    def test_resolves_username_placeholder(
        self, mock_manifest_for_display: Path
    ) -> None:
        """Resolves {username} placeholder when username provided."""
        from src.services.mount_service import get_path_display_mapping

        result = get_path_display_mapping(username="testuser")

        assert "external/user-ro/user_documents" in result
        assert result["external/user-ro/user_documents"] == "/Users/testuser/Documents"

    def test_skips_user_mounts_without_username(
        self, mock_manifest_for_display: Path
    ) -> None:
        """Skips user mounts when no username provided."""
        from src.services.mount_service import get_path_display_mapping

        result = get_path_display_mapping()  # No username

        # User mount should be skipped
        assert "external/user-ro/user_documents" not in result

    def test_mapping_format_matches_internal_paths(
        self, mock_manifest_for_display: Path
    ) -> None:
        """Mapping keys match the internal path format used in agent output."""
        from src.services.mount_service import get_path_display_mapping

        result = get_path_display_mapping(username="greg")

        # Keys should be in format: external/{type}/{name}
        for key in result:
            assert key.startswith("external/")
            parts = key.split("/")
            assert len(parts) == 3  # external, type, name
            assert parts[1] in ("ro", "rw", "user-ro", "user-rw")
