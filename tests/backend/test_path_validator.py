"""
Unit tests for Ag3ntumPathValidator.

Tests cover:
- Path normalization (relative, absolute, /workspace style)
- Workspace boundary enforcement (path traversal prevention)
- Blocklist pattern matching (*.env, *.key, .git/**, etc.)
- Allowlist filtering
- Read-only path enforcement (skills/)
- Session-scoped validator management
- Edge cases and security bypass attempts
"""
from pathlib import Path

import pytest

from src.core.path_validator import (
    Ag3ntumPathValidator,
    PathValidatorConfig,
    PathValidationError,
    configure_path_validator,
    get_path_validator,
    cleanup_path_validator,
    has_path_validator,
)


class TestPathNormalization:
    """Test path normalization logic."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        """Create a temporary workspace directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace

    @pytest.fixture
    def validator(self, workspace: Path) -> Ag3ntumPathValidator:
        """Create a validator with default config."""
        config = PathValidatorConfig(workspace_path=workspace)
        return Ag3ntumPathValidator(config)

    def test_relative_path_normalized_to_workspace(
        self, validator: Ag3ntumPathValidator, workspace: Path
    ) -> None:
        """Relative path './foo.txt' resolves to workspace/foo.txt."""
        result = validator._normalize_path("./foo.txt")
        assert result == workspace / "foo.txt"

    def test_bare_filename_normalized_to_workspace(
        self, validator: Ag3ntumPathValidator, workspace: Path
    ) -> None:
        """Bare filename 'foo.txt' resolves to workspace/foo.txt."""
        result = validator._normalize_path("foo.txt")
        assert result == workspace / "foo.txt"

    def test_workspace_prefix_stripped(
        self, validator: Ag3ntumPathValidator, workspace: Path
    ) -> None:
        """/workspace/foo.txt is translated to real workspace path."""
        result = validator._normalize_path("/workspace/foo.txt")
        assert result == workspace / "foo.txt"

    def test_workspace_root_normalized(
        self, validator: Ag3ntumPathValidator, workspace: Path
    ) -> None:
        """/workspace alone resolves to workspace root."""
        result = validator._normalize_path("/workspace")
        assert result == workspace

    def test_nested_path_normalized(
        self, validator: Ag3ntumPathValidator, workspace: Path
    ) -> None:
        """Nested paths preserve directory structure."""
        result = validator._normalize_path("./src/main.py")
        assert result == workspace / "src" / "main.py"


class TestWorkspaceBoundary:
    """Test workspace boundary enforcement."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        """Create a temporary workspace directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace

    @pytest.fixture
    def validator(self, workspace: Path) -> Ag3ntumPathValidator:
        """Create a validator with default config."""
        config = PathValidatorConfig(workspace_path=workspace)
        return Ag3ntumPathValidator(config)

    def test_valid_path_within_workspace(
        self, validator: Ag3ntumPathValidator, workspace: Path
    ) -> None:
        """Valid path within workspace passes validation."""
        (workspace / "test.txt").touch()
        result = validator.validate_path("test.txt", "read")
        assert result.normalized == workspace / "test.txt"

    def test_path_traversal_blocked(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Path traversal attempt '../' is blocked."""
        with pytest.raises(PathValidationError, match="outside allowed directories"):
            validator.validate_path("../etc/passwd", "read")

    def test_double_traversal_blocked(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Double path traversal '../../' is blocked."""
        with pytest.raises(PathValidationError, match="outside allowed directories"):
            validator.validate_path("../../etc/passwd", "read")

    def test_absolute_path_outside_workspace_blocked(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Absolute path outside workspace is blocked."""
        with pytest.raises(PathValidationError, match="outside allowed directories"):
            validator.validate_path("/etc/passwd", "read")

    def test_home_directory_access_blocked(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Home directory access is blocked."""
        with pytest.raises(PathValidationError, match="outside allowed directories"):
            validator.validate_path("/home/user/.bashrc", "read")

    def test_symlink_escape_attempt_blocked(
        self, validator: Ag3ntumPathValidator, workspace: Path
    ) -> None:
        """Symlink to outside workspace - resolved path is blocked."""
        # Create a symlink pointing outside workspace
        evil_link = workspace / "evil_link"
        try:
            evil_link.symlink_to("/etc/passwd")
        except OSError:
            pytest.skip("Cannot create symlinks in this environment")

        # When resolved, path will be /etc/passwd which is outside workspace
        with pytest.raises(PathValidationError, match="outside allowed directories"):
            validator.validate_path("evil_link", "read")


class TestBlocklist:
    """Test blocklist pattern matching."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        """Create a temporary workspace directory."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace

    @pytest.fixture
    def validator(self, workspace: Path) -> Ag3ntumPathValidator:
        """Create a validator with default blocklist."""
        config = PathValidatorConfig(workspace_path=workspace)
        return Ag3ntumPathValidator(config)

    def test_env_file_blocked(self, validator: Ag3ntumPathValidator, workspace: Path) -> None:
        """*.env files are blocked by default."""
        (workspace / ".env").touch()
        with pytest.raises(PathValidationError, match="blocked by policy"):
            validator.validate_path(".env", "read")

    def test_production_env_blocked(self, validator: Ag3ntumPathValidator, workspace: Path) -> None:
        """production.env is blocked."""
        (workspace / "production.env").touch()
        with pytest.raises(PathValidationError, match="blocked by policy"):
            validator.validate_path("production.env", "read")

    def test_key_file_blocked(self, validator: Ag3ntumPathValidator, workspace: Path) -> None:
        """*.key files are blocked."""
        (workspace / "private.key").touch()
        with pytest.raises(PathValidationError, match="blocked by policy"):
            validator.validate_path("private.key", "read")

    def test_git_directory_blocked(self, validator: Ag3ntumPathValidator, workspace: Path) -> None:
        """.git/** paths are blocked."""
        git_dir = workspace / ".git"
        git_dir.mkdir()
        (git_dir / "config").touch()

        with pytest.raises(PathValidationError, match="blocked by policy"):
            validator.validate_path(".git/config", "read")

    def test_pycache_blocked(self, validator: Ag3ntumPathValidator, workspace: Path) -> None:
        """__pycache__/** paths are blocked."""
        cache_dir = workspace / "__pycache__"
        cache_dir.mkdir()

        with pytest.raises(PathValidationError, match="blocked by policy"):
            validator.validate_path("__pycache__/module.cpython-312.pyc", "read")

    def test_pyc_files_blocked(self, validator: Ag3ntumPathValidator, workspace: Path) -> None:
        """*.pyc files are blocked."""
        (workspace / "module.pyc").touch()
        with pytest.raises(PathValidationError, match="blocked by policy"):
            validator.validate_path("module.pyc", "read")

    def test_regular_file_not_blocked(
        self, validator: Ag3ntumPathValidator, workspace: Path
    ) -> None:
        """Regular files are not blocked."""
        (workspace / "main.py").touch()
        result = validator.validate_path("main.py", "read")
        assert result.normalized == workspace / "main.py"


class TestCustomBlocklist:
    """Test custom blocklist configuration."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace

    def test_custom_blocklist_pattern(self, workspace: Path) -> None:
        """Custom blocklist patterns are enforced."""
        config = PathValidatorConfig(
            workspace_path=workspace,
            blocklist=["*.secret", "credentials/**"]
        )
        validator = Ag3ntumPathValidator(config)

        (workspace / "api.secret").touch()
        with pytest.raises(PathValidationError, match="blocked by policy"):
            validator.validate_path("api.secret", "read")

    def test_empty_blocklist_allows_all(self, workspace: Path) -> None:
        """Empty blocklist allows previously blocked files."""
        config = PathValidatorConfig(
            workspace_path=workspace,
            blocklist=[]
        )
        validator = Ag3ntumPathValidator(config)

        (workspace / ".env").touch()
        result = validator.validate_path(".env", "read")
        assert result.normalized == workspace / ".env"


class TestAllowlist:
    """Test allowlist filtering."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace

    def test_allowlist_restricts_access(self, workspace: Path) -> None:
        """When allowlist is set, only matching paths are allowed."""
        config = PathValidatorConfig(
            workspace_path=workspace,
            blocklist=[],  # Disable blocklist
            allowlist=["*.py", "*.txt"]
        )
        validator = Ag3ntumPathValidator(config)

        # Python files allowed
        (workspace / "main.py").touch()
        result = validator.validate_path("main.py", "read")
        assert result.normalized == workspace / "main.py"

        # JavaScript files blocked
        (workspace / "app.js").touch()
        with pytest.raises(PathValidationError, match="not in allowlist"):
            validator.validate_path("app.js", "read")

    def test_allowlist_none_allows_all(self, workspace: Path) -> None:
        """When allowlist is None, all non-blocklisted paths are allowed."""
        config = PathValidatorConfig(
            workspace_path=workspace,
            blocklist=[],
            allowlist=None
        )
        validator = Ag3ntumPathValidator(config)

        (workspace / "app.js").touch()
        result = validator.validate_path("app.js", "read")
        assert result.normalized == workspace / "app.js"


class TestReadOnlyPaths:
    """Test read-only path enforcement."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "skills").mkdir()
        return workspace

    @pytest.fixture
    def validator(self, workspace: Path) -> Ag3ntumPathValidator:
        config = PathValidatorConfig(
            workspace_path=workspace,
            blocklist=[],
            readonly_prefixes=["skills/"]
        )
        return Ag3ntumPathValidator(config)

    def test_read_allowed_on_readonly_path(
        self, validator: Ag3ntumPathValidator, workspace: Path
    ) -> None:
        """Read operations are allowed on read-only paths."""
        (workspace / "skills" / "skill.py").touch()
        result = validator.validate_path("skills/skill.py", "read")
        assert result.is_readonly is True

    def test_write_blocked_on_readonly_path(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Write operations are blocked on read-only paths."""
        with pytest.raises(PathValidationError, match="read-only"):
            validator.validate_path("skills/skill.py", "write")

    def test_edit_blocked_on_readonly_path(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Edit operations are blocked on read-only paths."""
        with pytest.raises(PathValidationError, match="read-only"):
            validator.validate_path("skills/skill.py", "edit")

    def test_delete_blocked_on_readonly_path(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Delete operations are blocked on read-only paths."""
        with pytest.raises(PathValidationError, match="read-only"):
            validator.validate_path("skills/skill.py", "delete")

    def test_regular_path_not_readonly(
        self, validator: Ag3ntumPathValidator, workspace: Path
    ) -> None:
        """Regular paths are writable."""
        (workspace / "main.py").touch()
        result = validator.validate_path("main.py", "write")
        assert result.is_readonly is False


class TestSessionScopedValidators:
    """Test session-scoped validator management."""

    @pytest.fixture(autouse=True)
    def cleanup_validators(self):
        """Clean up validators after each test."""
        yield
        # Clean up any test validators
        from src.core.path_validator import _session_validators
        _session_validators.clear()

    def test_configure_creates_validator(self, tmp_path: Path) -> None:
        """configure_path_validator creates and stores a validator."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        validator = configure_path_validator("session_123", workspace)

        assert validator is not None
        assert has_path_validator("session_123") is True

    def test_get_validator_returns_configured(self, tmp_path: Path) -> None:
        """get_path_validator returns the configured validator."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        configure_path_validator("session_456", workspace)
        validator = get_path_validator("session_456")

        assert validator is not None
        assert validator.workspace == workspace

    def test_get_validator_not_configured_raises(self) -> None:
        """get_path_validator raises if session not configured."""
        with pytest.raises(RuntimeError, match="not configured"):
            get_path_validator("unknown_session")

    def test_cleanup_removes_validator(self, tmp_path: Path) -> None:
        """cleanup_path_validator removes the validator."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        configure_path_validator("session_789", workspace)
        assert has_path_validator("session_789") is True

        cleanup_path_validator("session_789")
        assert has_path_validator("session_789") is False

    def test_has_validator_false_for_unknown(self) -> None:
        """has_path_validator returns False for unknown session."""
        assert has_path_validator("nonexistent") is False

    def test_multiple_sessions_isolated(self, tmp_path: Path) -> None:
        """Each session has its own validator with own workspace."""
        workspace1 = tmp_path / "workspace1"
        workspace2 = tmp_path / "workspace2"
        workspace1.mkdir()
        workspace2.mkdir()

        configure_path_validator("session_a", workspace1)
        configure_path_validator("session_b", workspace2)

        validator_a = get_path_validator("session_a")
        validator_b = get_path_validator("session_b")

        assert validator_a.workspace == workspace1
        assert validator_b.workspace == workspace2


class TestEdgeCases:
    """Test edge cases and security bypass attempts."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace

    @pytest.fixture
    def validator(self, workspace: Path) -> Ag3ntumPathValidator:
        config = PathValidatorConfig(workspace_path=workspace)
        return Ag3ntumPathValidator(config)

    def test_null_byte_injection_blocked(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Null byte injection attempts are handled."""
        # Path with null byte - should fail normalization or boundary check
        with pytest.raises((PathValidationError, ValueError)):
            validator.validate_path("file.txt\x00.env", "read")

    def test_unicode_normalization_attack(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Unicode normalization attacks are handled."""
        # Various unicode tricks that might bypass filters
        # These should either be normalized or blocked
        # Using combining characters that look like ../
        try:
            result = validator.validate_path(".\u002e/passwd", "read")
            # If it didn't raise, it should still be within workspace
            assert result.normalized.is_relative_to(validator.workspace)
        except PathValidationError:
            pass  # Also acceptable

    def test_very_long_path_handled(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Very long paths are handled without crashing."""
        long_path = "a" * 1000 + "/file.txt"
        # Should either work or raise PathValidationError, not crash
        try:
            result = validator.validate_path(long_path, "read")
            assert result.normalized.is_relative_to(validator.workspace)
        except PathValidationError:
            pass  # Also acceptable

    def test_empty_path_handled(self, validator: Ag3ntumPathValidator) -> None:
        """Empty path is handled."""
        # Empty path should normalize to workspace root
        result = validator.validate_path("", "read", allow_directory=True)
        assert result.normalized == validator.workspace

    def test_dot_path_is_workspace(self, validator: Ag3ntumPathValidator) -> None:
        """'.' path represents workspace root."""
        result = validator.validate_path(".", "read", allow_directory=True)
        assert result.normalized == validator.workspace

    def test_triple_dot_not_special(
        self, validator: Ag3ntumPathValidator, workspace: Path
    ) -> None:
        """'...' is a valid filename, not a traversal."""
        (workspace / "...").touch()
        result = validator.validate_path("...", "read")
        assert result.normalized == workspace / "..."
