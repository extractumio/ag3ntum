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
    DEFAULT_READONLY_PREFIXES,
    DEFAULT_BLOCKLIST,
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

    def test_dotenv_local_blocked(self, validator: Ag3ntumPathValidator, workspace: Path) -> None:
        """.env.local (dotenv variant) is blocked by .env.* pattern."""
        (workspace / ".env.local").touch()
        with pytest.raises(PathValidationError, match="blocked by policy"):
            validator.validate_path(".env.local", "read")

    def test_dotenv_development_blocked(self, validator: Ag3ntumPathValidator, workspace: Path) -> None:
        """.env.development is blocked by .env.* pattern."""
        (workspace / ".env.development").touch()
        with pytest.raises(PathValidationError, match="blocked by policy"):
            validator.validate_path(".env.development", "read")

    def test_dotenv_production_blocked(self, validator: Ag3ntumPathValidator, workspace: Path) -> None:
        """.env.production is blocked by .env.* pattern."""
        (workspace / ".env.production").touch()
        with pytest.raises(PathValidationError, match="blocked by policy"):
            validator.validate_path(".env.production", "read")

    def test_dotenv_example_allowed(self, validator: Ag3ntumPathValidator, workspace: Path) -> None:
        """.env.example is exempt from blocklist (safe template)."""
        (workspace / ".env.example").touch()
        result = validator.validate_path(".env.example", "read")
        assert result.normalized == workspace / ".env.example"

    def test_dotenv_sample_allowed(self, validator: Ag3ntumPathValidator, workspace: Path) -> None:
        """.env.sample is exempt from blocklist (safe template)."""
        (workspace / ".env.sample").touch()
        result = validator.validate_path(".env.sample", "read")
        assert result.normalized == workspace / ".env.sample"

    def test_dotenv_template_allowed(self, validator: Ag3ntumPathValidator, workspace: Path) -> None:
        """.env.template is exempt from blocklist (safe template)."""
        (workspace / ".env.template").touch()
        result = validator.validate_path(".env.template", "read")
        assert result.normalized == workspace / ".env.template"

    def test_dotenv_defaults_allowed(self, validator: Ag3ntumPathValidator, workspace: Path) -> None:
        """.env.defaults is exempt from blocklist (safe template)."""
        (workspace / ".env.defaults").touch()
        result = validator.validate_path(".env.defaults", "read")
        assert result.normalized == workspace / ".env.defaults"

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


class TestClaudeFolderProtection:
    """Test .claude/ folder is protected as read-only.

    Security: The .claude/skills/ folder contains skill symlinks set up by
    infrastructure. Agents should not be able to modify it (e.g., delete
    symlinks and create malicious skill replacements).
    """

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / ".claude" / "skills").mkdir(parents=True)
        return workspace

    @pytest.fixture
    def validator(self, workspace: Path) -> Ag3ntumPathValidator:
        # Use DEFAULT_READONLY_PREFIXES to validate DRY constant is correct
        config = PathValidatorConfig(
            workspace_path=workspace,
            blocklist=[],
            readonly_prefixes=DEFAULT_READONLY_PREFIXES.copy()
        )
        return Ag3ntumPathValidator(config)

    def test_claude_in_default_readonly_prefixes(self) -> None:
        """Verify .claude/ is in the default readonly prefixes constant."""
        assert ".claude/" in DEFAULT_READONLY_PREFIXES, (
            "SECURITY: .claude/ must be in DEFAULT_READONLY_PREFIXES to prevent "
            "agents from tampering with skills"
        )

    def test_read_allowed_on_claude_folder(
        self, validator: Ag3ntumPathValidator, workspace: Path
    ) -> None:
        """Read operations are allowed on .claude/ paths."""
        (workspace / ".claude" / "skills" / "my-skill").mkdir()
        (workspace / ".claude" / "skills" / "my-skill" / "SKILL.md").touch()
        result = validator.validate_path(".claude/skills/my-skill/SKILL.md", "read")
        assert result.is_readonly is True

    def test_write_blocked_on_claude_folder(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Write operations are blocked on .claude/ paths."""
        with pytest.raises(PathValidationError, match="read-only"):
            validator.validate_path(".claude/skills/malicious/SKILL.md", "write")

    def test_delete_blocked_on_claude_folder(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Delete operations are blocked on .claude/ paths (prevents symlink deletion)."""
        with pytest.raises(PathValidationError, match="read-only"):
            validator.validate_path(".claude/skills/existing-skill", "delete")

    def test_edit_blocked_on_claude_folder(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Edit operations are blocked on .claude/ paths."""
        with pytest.raises(PathValidationError, match="read-only"):
            validator.validate_path(".claude/skills/my-skill/SKILL.md", "edit")

    def test_claude_settings_file_readonly(
        self, validator: Ag3ntumPathValidator, workspace: Path
    ) -> None:
        """Settings files in .claude/ are also read-only."""
        (workspace / ".claude" / "settings.json").touch()
        result = validator.validate_path(".claude/settings.json", "read")
        assert result.is_readonly is True

        with pytest.raises(PathValidationError, match="read-only"):
            validator.validate_path(".claude/settings.json", "write")


class TestClaudeFolderIntegration:
    """Integration tests for .claude/ protection using configure_path_validator.

    These tests validate that the protection works end-to-end when using the
    standard configuration flow that real sessions use.
    """

    @pytest.fixture(autouse=True)
    def cleanup_validators(self):
        """Clean up validators after each test."""
        yield
        from src.core.path_validator import _session_validators
        _session_validators.clear()

    def test_configure_path_validator_protects_claude_folder(self, tmp_path: Path) -> None:
        """configure_path_validator with defaults protects .claude/ folder."""
        workspace = tmp_path / "users" / "testuser" / "sessions" / "test123" / "workspace"
        workspace.mkdir(parents=True)
        (workspace / ".claude" / "skills" / "my-skill").mkdir(parents=True)

        # Configure validator using the standard function (as real sessions do)
        validator = configure_path_validator(
            session_id="test123",
            workspace_path=workspace,
            username="testuser",
        )

        # Read should work
        (workspace / ".claude" / "skills" / "my-skill" / "SKILL.md").touch()
        result = validator.validate_path(".claude/skills/my-skill/SKILL.md", "read")
        assert result.is_readonly is True

        # Write should be blocked
        with pytest.raises(PathValidationError, match="read-only"):
            validator.validate_path(".claude/skills/malicious/SKILL.md", "write")

        # Delete should be blocked (prevents symlink tampering)
        with pytest.raises(PathValidationError, match="read-only"):
            validator.validate_path(".claude/skills/my-skill", "delete")

    def test_session_validator_inherits_claude_protection(self, tmp_path: Path) -> None:
        """Validators retrieved via get_path_validator have .claude/ protection."""
        workspace = tmp_path / "users" / "testuser" / "sessions" / "sess456" / "workspace"
        workspace.mkdir(parents=True)

        configure_path_validator(
            session_id="sess456",
            workspace_path=workspace,
            username="testuser",
        )

        # Get validator as tools would
        validator = get_path_validator("sess456")

        # Verify protection is active
        with pytest.raises(PathValidationError, match="read-only"):
            validator.validate_path(".claude/anything", "write")


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


class TestDockerToDisplayPath:
    """Test docker_to_display_path reverse path translation."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        return workspace

    def test_workspace_relative_path(self, workspace: Path) -> None:
        """Paths under workspace return relative paths."""
        config = PathValidatorConfig(workspace_path=workspace)
        validator = Ag3ntumPathValidator(config)
        (workspace / "src").mkdir()
        result = validator.docker_to_display_path(workspace / "src" / "main.py")
        assert result == "src/main.py"

    def test_workspace_root(self, workspace: Path) -> None:
        """Workspace root returns '.'."""
        config = PathValidatorConfig(workspace_path=workspace)
        validator = Ag3ntumPathValidator(config)
        result = validator.docker_to_display_path(workspace)
        assert result == "."

    def test_persistent_path(self, tmp_path: Path, workspace: Path) -> None:
        """Persistent storage paths return persistent/... prefix."""
        persistent = tmp_path / "persistent"
        persistent.mkdir()
        config = PathValidatorConfig(
            workspace_path=workspace,
            persistent_path=persistent,
        )
        validator = Ag3ntumPathValidator(config)
        result = validator.docker_to_display_path(persistent / "data.txt")
        assert result == "persistent/data.txt"

    def test_persistent_root(self, tmp_path: Path, workspace: Path) -> None:
        """Persistent root returns 'persistent'."""
        persistent = tmp_path / "persistent"
        persistent.mkdir()
        config = PathValidatorConfig(
            workspace_path=workspace,
            persistent_path=persistent,
        )
        validator = Ag3ntumPathValidator(config)
        result = validator.docker_to_display_path(persistent)
        assert result == "persistent"

    def test_global_ro_mount(self, tmp_path: Path, workspace: Path) -> None:
        """Global RO mount paths return external/ro/{name}/... prefix."""
        mount_path = tmp_path / "mounts" / "global_var_log"
        mount_path.mkdir(parents=True)
        config = PathValidatorConfig(
            workspace_path=workspace,
            global_mounts_ro={"global_var_log": mount_path},
        )
        validator = Ag3ntumPathValidator(config)
        result = validator.docker_to_display_path(mount_path / "syslog")
        assert result == "external/ro/global_var_log/syslog"

    def test_global_rw_mount(self, tmp_path: Path, workspace: Path) -> None:
        """Global RW mount paths return external/rw/{name}/... prefix."""
        mount_path = tmp_path / "mounts" / "output"
        mount_path.mkdir(parents=True)
        config = PathValidatorConfig(
            workspace_path=workspace,
            global_mounts_rw={"output": mount_path},
        )
        validator = Ag3ntumPathValidator(config)
        result = validator.docker_to_display_path(mount_path / "result.csv")
        assert result == "external/rw/output/result.csv"

    def test_user_ro_mount(self, tmp_path: Path, workspace: Path) -> None:
        """Per-user RO mount paths return external/user-ro/{name}/... prefix."""
        mount_path = tmp_path / "mounts" / "docs"
        mount_path.mkdir(parents=True)
        config = PathValidatorConfig(
            workspace_path=workspace,
            user_mounts_ro={"docs": mount_path},
        )
        validator = Ag3ntumPathValidator(config)
        result = validator.docker_to_display_path(mount_path / "readme.md")
        assert result == "external/user-ro/docs/readme.md"

    def test_user_rw_mount(self, tmp_path: Path, workspace: Path) -> None:
        """Per-user RW mount paths return external/user-rw/{name}/... prefix."""
        mount_path = tmp_path / "mounts" / "work"
        mount_path.mkdir(parents=True)
        config = PathValidatorConfig(
            workspace_path=workspace,
            user_mounts_rw={"work": mount_path},
        )
        validator = Ag3ntumPathValidator(config)
        result = validator.docker_to_display_path(mount_path / "project")
        assert result == "external/user-rw/work/project"

    def test_dynamic_ro_mount(self, tmp_path: Path, workspace: Path) -> None:
        """Dynamic RO mount paths return dynamic/{alias}/... prefix."""
        mount_path = tmp_path / "mounts" / "logs"
        mount_path.mkdir(parents=True)
        config = PathValidatorConfig(
            workspace_path=workspace,
            dynamic_mounts_ro={"app_logs": mount_path},
        )
        validator = Ag3ntumPathValidator(config)
        result = validator.docker_to_display_path(mount_path / "app.log")
        assert result == "dynamic/app_logs/app.log"

    def test_original_path_ro_mount(self, tmp_path: Path, workspace: Path) -> None:
        """Original-path RO mounts return the original host path."""
        docker_mount = tmp_path / "mounts" / "paths" / "_var_log"
        docker_mount.mkdir(parents=True)
        config = PathValidatorConfig(
            workspace_path=workspace,
            original_path_mounts_ro={"/var/log": docker_mount},
        )
        validator = Ag3ntumPathValidator(config)
        result = validator.docker_to_display_path(docker_mount / "syslog")
        assert result == "/var/log/syslog"

    def test_fallback_returns_raw_path(self, workspace: Path) -> None:
        """Unknown paths return the raw Docker path as fallback."""
        config = PathValidatorConfig(workspace_path=workspace)
        validator = Ag3ntumPathValidator(config)
        unknown = Path("/some/unknown/path")
        result = validator.docker_to_display_path(unknown)
        assert result == "/some/unknown/path"

    def test_nested_mount_path(self, tmp_path: Path, workspace: Path) -> None:
        """Deeply nested paths in mounts are resolved correctly."""
        mount_path = tmp_path / "mounts" / "global_var_log"
        mount_path.mkdir(parents=True)
        config = PathValidatorConfig(
            workspace_path=workspace,
            global_mounts_ro={"global_var_log": mount_path},
        )
        validator = Ag3ntumPathValidator(config)
        result = validator.docker_to_display_path(mount_path / "apt" / "history.log")
        assert result == "external/ro/global_var_log/apt/history.log"


# =============================================================================
# Test: Path Encoding Attacks
# =============================================================================

class TestPathEncodingAttacks:
    """Test path validation against various encoding-based attacks.

    These tests verify that the PathValidator correctly handles
    URL-encoded, double-encoded, backslash, and mixed-encoding
    path traversal attempts. The validator works with filesystem
    paths (not URLs), so URL encoding is treated as literal
    characters and does not resolve to traversal.
    """

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

    def test_url_encoded_traversal(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """URL-encoded traversal: %2e%2e%2fetc%2fpasswd.

        The PathValidator operates on filesystem paths, not URLs.
        URL-encoded characters are treated as literal filename characters,
        not decoded to '../etc/passwd'. The path resolves within workspace
        as a file literally named '%2e%2e%2fetc%2fpasswd'.
        """
        # This should either:
        # 1. Resolve within workspace (as literal filename) - ALLOWED
        # 2. Be caught by boundary check if somehow decoded - BLOCKED
        # Either outcome is acceptable for security
        try:
            result = validator.validate_path("%2e%2e%2fetc%2fpasswd", "read")
            # If it passes, it must resolve within workspace
            assert str(result.normalized).startswith(str(validator.workspace))
        except PathValidationError:
            pass  # Also acceptable - blocked is safe

    def test_double_encoded_traversal(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Double-encoded traversal: %252e%252e%252f.

        Double encoding is a technique to bypass first-pass decoding.
        PathValidator treats these as literal characters.
        """
        try:
            result = validator.validate_path("%252e%252e%252f", "read")
            assert str(result.normalized).startswith(str(validator.workspace))
        except PathValidationError:
            pass  # Blocked is safe

    def test_backslash_traversal(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        r"""Backslash traversal: ..\etc\passwd.

        On Linux, backslash is a valid filename character, not a path
        separator. The path resolves within workspace as a file literally
        containing backslashes in its name.
        """
        try:
            result = validator.validate_path("..\\etc\\passwd", "read")
            # On Linux, this is treated as a literal filename with backslashes
            # It should still be within workspace
            assert str(result.normalized).startswith(str(validator.workspace))
        except PathValidationError:
            pass  # Also acceptable

    def test_mixed_encoding_traversal(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Mixed encoding: ..%2Fetc/passwd.

        Combines literal '..' with URL-encoded '/' and normal '/'.
        PathValidator treats '%2F' as a literal character (not decoded),
        so '..%2Fetc' is a single filename component that resolves
        within workspace. Traversal detection warns but path is safe.
        """
        # PathValidator doesn't URL-decode; '..%2Fetc' resolves
        # as literal filename in workspace/etc/passwd
        result = validator.validate_path("..%2Fetc/passwd", "read")
        assert str(result.normalized).startswith(
            str(validator.workspace)
        )

    def test_dot_dot_with_encoded_separator(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Path with literal '..' and encoded separator: ..%2f.

        PathValidator treats '%2f' as a literal character, so '..%2f'
        is a single filename component (not a traversal). The '..'
        prefix triggers a warning but the path resolves within workspace.
        """
        # PathValidator doesn't URL-decode; '..%2f' is a literal filename
        result = validator.validate_path("..%2f", "read")
        assert str(result.normalized).startswith(
            str(validator.workspace)
        )

    def test_unicode_slash_attack(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Unicode characters that look like slash: /../ etc.

        Test that unicode normalization doesn't create traversal.
        """
        # Fullwidth solidus (U+FF0F) looks like / but is a different character
        try:
            result = validator.validate_path(
                "..\uff0fetc\uff0fpasswd", "read"
            )
            # '..' triggers traversal via standard path resolution
            # but \uff0f is not a path separator on Linux
            # This should either resolve in workspace or be blocked
            if result:
                assert str(result.normalized).startswith(
                    str(validator.workspace)
                )
        except PathValidationError:
            pass  # Blocked is safe

    def test_null_byte_before_extension(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Null byte injection: file.txt%00.env.

        Null bytes are dangerous in C-based systems but Python handles them.
        The PathValidator should reject or safely handle null bytes.
        """
        with pytest.raises((PathValidationError, ValueError)):
            validator.validate_path("file.txt\x00.env", "read")

    def test_overlong_utf8_dot(
        self, validator: Ag3ntumPathValidator
    ) -> None:
        """Overlong UTF-8 encoding of dot character.

        In theory, overlong sequences could bypass dot-dot checks.
        Python's string handling normalizes these before they reach
        the filesystem, preventing the attack.
        """
        # This tests that the path with unusual dot-like chars
        # doesn't escape the workspace
        try:
            result = validator.validate_path(
                "\xc0\xae\xc0\xae/etc/passwd", "read"
            )
            if result:
                assert str(result.normalized).startswith(
                    str(validator.workspace)
                )
        except (PathValidationError, ValueError, UnicodeError):
            pass  # Any rejection is safe
