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
    """Test external mount path normalization."""

    @pytest.fixture
    def temp_mounts(self, tmp_path: Path) -> dict[str, Path]:
        """Create temporary mount structure."""
        ro_dir = tmp_path / "mounts" / "ro" / "downloads"
        rw_dir = tmp_path / "mounts" / "rw" / "projects"
        persistent = tmp_path / "users" / "testuser" / "ag3ntum" / "persistent"
        workspace = tmp_path / "workspace"

        for d in [ro_dir, rw_dir, persistent, workspace]:
            d.mkdir(parents=True)

        # Create test files
        (ro_dir / "readme.txt").write_text("readonly content")
        (rw_dir / "editable.txt").write_text("writable content")
        (persistent / "cache.json").write_text("{}")

        return {
            "ro": tmp_path / "mounts" / "ro",
            "rw": tmp_path / "mounts" / "rw",
            "persistent": persistent,
            "workspace": workspace,
            "root": tmp_path,
        }

    @pytest.fixture
    def validator(self, temp_mounts: dict[str, Path]) -> Ag3ntumPathValidator:
        """Create validator with mount configuration."""
        config = PathValidatorConfig(
            workspace_path=temp_mounts["workspace"],
            external_ro_base=temp_mounts["ro"],
            external_rw_base=temp_mounts["rw"],
            persistent_path=temp_mounts["persistent"],
        )
        return Ag3ntumPathValidator(config)

    def test_ro_mount_path_normalized(
        self, validator: Ag3ntumPathValidator, temp_mounts: dict[str, Path]
    ) -> None:
        """Read-only mount path is normalized correctly."""
        result = validator._normalize_path("/workspace/external/ro/downloads/readme.txt")
        expected = temp_mounts["ro"] / "downloads" / "readme.txt"
        assert result == expected

    def test_rw_mount_path_normalized(
        self, validator: Ag3ntumPathValidator, temp_mounts: dict[str, Path]
    ) -> None:
        """Read-write mount path is normalized correctly."""
        result = validator._normalize_path("/workspace/external/rw/projects/editable.txt")
        expected = temp_mounts["rw"] / "projects" / "editable.txt"
        assert result == expected

    def test_persistent_path_normalized(
        self, validator: Ag3ntumPathValidator, temp_mounts: dict[str, Path]
    ) -> None:
        """Persistent storage path is normalized correctly."""
        result = validator._normalize_path("/workspace/external/persistent/cache.json")
        expected = temp_mounts["persistent"] / "cache.json"
        assert result == expected

    def test_relative_external_path_normalized(
        self, validator: Ag3ntumPathValidator, temp_mounts: dict[str, Path]
    ) -> None:
        """Relative external paths are normalized correctly."""
        result = validator._normalize_path("./external/ro/downloads/readme.txt")
        expected = temp_mounts["ro"] / "downloads" / "readme.txt"
        assert result == expected


# =============================================================================
# External Mount Validation Tests
# =============================================================================


class TestExternalMountValidation:
    """Test external mount path validation and permissions."""

    @pytest.fixture
    def temp_mounts(self, tmp_path: Path) -> dict[str, Path]:
        """Create temporary mount structure."""
        ro_dir = tmp_path / "mounts" / "ro" / "downloads"
        rw_dir = tmp_path / "mounts" / "rw" / "projects"
        persistent = tmp_path / "users" / "testuser" / "ag3ntum" / "persistent"
        workspace = tmp_path / "workspace"

        for d in [ro_dir, rw_dir, persistent, workspace]:
            d.mkdir(parents=True)

        # Create test files
        (ro_dir / "readme.txt").write_text("readonly content")
        (rw_dir / "editable.txt").write_text("writable content")

        return {
            "ro": tmp_path / "mounts" / "ro",
            "rw": tmp_path / "mounts" / "rw",
            "persistent": persistent,
            "workspace": workspace,
            "root": tmp_path,
        }

    @pytest.fixture
    def validator(self, temp_mounts: dict[str, Path]) -> Ag3ntumPathValidator:
        """Create validator with mount configuration."""
        config = PathValidatorConfig(
            workspace_path=temp_mounts["workspace"],
            external_ro_base=temp_mounts["ro"],
            external_rw_base=temp_mounts["rw"],
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
            "/workspace/external/persistent/cache.json", "write"
        )
        assert result.is_readonly is False

    def test_blocklist_in_external_mount(
        self, validator: Ag3ntumPathValidator, temp_mounts: dict[str, Path]
    ) -> None:
        """Blocklisted files in external mounts should be blocked."""
        # Create .env file in RW mount
        env_file = temp_mounts["rw"] / "projects" / ".env"
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
            external_rw_base=temp_mounts["allowed"],
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

        is_external, is_readonly, mount_type = get_mount_info("external/persistent/cache.json")
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
            external_ro_base=temp_workspace / "external" / "ro",
            external_rw_base=temp_workspace / "external" / "rw",
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
            target="/workspace/external/persistent",
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
