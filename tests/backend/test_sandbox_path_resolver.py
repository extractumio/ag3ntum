"""
Comprehensive tests for SandboxPathResolver.

Tests cover:
- Path normalization (relative, absolute, edge cases)
- Sandbox to Docker path translation
- Docker to sandbox path translation
- External mount handling (persistent, ro, rw, user mounts)
- Error handling and edge cases
- Mount type detection
- Writability checking
- Error message translation
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch

from src.core.sandbox_path_resolver import (
    SandboxPathContext,
    SandboxPathResolver,
    ExecutionContext,
    PathResolutionError,
    MountMapping,
    detect_execution_context,
    reset_context_cache,
    configure_sandbox_path_resolver,
    get_sandbox_path_resolver,
    cleanup_sandbox_path_resolver,
    has_sandbox_path_resolver,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def basic_context():
    """Create a basic SandboxPathContext for testing."""
    return SandboxPathContext(
        session_id="test-session-123",
        username="testuser",
    )


@pytest.fixture
def context_with_user_mounts():
    """Create a SandboxPathContext with user mounts."""
    return SandboxPathContext(
        session_id="test-session-456",
        username="testuser",
        user_mounts_ro={"downloads": "/mounts/user-ro/downloads"},
        user_mounts_rw={"projects": "/mounts/user-rw/projects"},
    )


@pytest.fixture
def resolver(basic_context):
    """Create a SandboxPathResolver with basic context."""
    return SandboxPathResolver(basic_context)


@pytest.fixture
def resolver_with_user_mounts(context_with_user_mounts):
    """Create a SandboxPathResolver with user mounts."""
    return SandboxPathResolver(context_with_user_mounts)


@pytest.fixture(autouse=True)
def reset_context():
    """Reset execution context cache before each test."""
    reset_context_cache()
    yield
    reset_context_cache()


@pytest.fixture(autouse=True)
def cleanup_resolvers():
    """Clean up any configured resolvers after each test."""
    yield
    # Clean up test sessions
    for session_id in ["test-session-123", "test-session-456", "test-session-789"]:
        if has_sandbox_path_resolver(session_id):
            cleanup_sandbox_path_resolver(session_id)


# =============================================================================
# SandboxPathContext Tests
# =============================================================================

class TestSandboxPathContext:
    """Tests for SandboxPathContext initialization and mount building."""

    def test_basic_initialization(self, basic_context):
        """Test basic context initialization with defaults."""
        ctx = basic_context
        assert ctx.session_id == "test-session-123"
        assert ctx.username == "testuser"
        assert ctx.workspace_sandbox == "/workspace"
        assert ctx.workspace_docker == "/users/testuser/sessions/test-session-123/workspace"
        assert ctx.venv_sandbox == "/venv"
        assert ctx.venv_docker == "/users/testuser/venv"
        # Agent sees persistent storage via symlink at /workspace/external/persistent
        # Docker path is the actual directory
        assert ctx.persistent_sandbox == "/workspace/external/persistent"
        assert ctx.persistent_docker == "/users/testuser/ag3ntum/persistent"

    def test_mounts_are_built(self, basic_context):
        """Test that mount mappings are built correctly."""
        ctx = basic_context
        assert len(ctx.mounts) > 0

        # Check workspace mount exists
        workspace_mount = ctx.find_mount_for_sandbox_path("/workspace/test.txt")
        assert workspace_mount is not None
        assert workspace_mount.mount_type == "workspace"
        assert workspace_mount.mode == "rw"

    def test_user_mounts_included(self, context_with_user_mounts):
        """Test that user mounts are included in mount list."""
        ctx = context_with_user_mounts

        # Check user-ro mount
        ro_mount = ctx.find_mount_for_sandbox_path("/workspace/external/user-ro/downloads/file.txt")
        assert ro_mount is not None
        assert ro_mount.mode == "ro"

        # Check user-rw mount
        rw_mount = ctx.find_mount_for_sandbox_path("/workspace/external/user-rw/projects/code.py")
        assert rw_mount is not None
        assert rw_mount.mode == "rw"

    def test_mounts_sorted_by_specificity(self, basic_context):
        """Test that mounts are sorted by path length (longest first)."""
        ctx = basic_context

        # The mounts should be sorted so that more specific paths come first
        prev_len = float('inf')
        for mount in ctx.mounts:
            # Allow equal lengths but not shorter before longer
            assert len(mount.sandbox_path) <= prev_len or len(mount.sandbox_path) == len(ctx.mounts[0].sandbox_path)
            prev_len = len(mount.sandbox_path)


# =============================================================================
# Path Normalization Tests
# =============================================================================

class TestPathNormalization:
    """Tests for path normalization."""

    def test_relative_path(self, resolver):
        """Test normalizing relative paths."""
        assert resolver.normalize("file.txt") == "/workspace/file.txt"
        assert resolver.normalize("./file.txt") == "/workspace/file.txt"
        assert resolver.normalize("dir/file.txt") == "/workspace/dir/file.txt"
        assert resolver.normalize("./dir/file.txt") == "/workspace/dir/file.txt"

    def test_absolute_workspace_path(self, resolver):
        """Test normalizing absolute workspace paths."""
        assert resolver.normalize("/workspace/file.txt") == "/workspace/file.txt"
        assert resolver.normalize("/workspace/dir/file.txt") == "/workspace/dir/file.txt"

    def test_external_mount_paths(self, resolver):
        """Test normalizing external mount paths."""
        # Relative external paths
        assert resolver.normalize("external/persistent/img.png") == "/workspace/external/persistent/img.png"
        assert resolver.normalize("./external/ro/data.csv") == "/workspace/external/ro/data.csv"

        # Absolute external paths
        assert resolver.normalize("/workspace/external/rw/file.txt") == "/workspace/external/rw/file.txt"

    def test_path_with_dots(self, resolver):
        """Test normalizing paths with . and .. components."""
        assert resolver.normalize("/workspace/./file.txt") == "/workspace/file.txt"
        assert resolver.normalize("/workspace/dir/../file.txt") == "/workspace/file.txt"
        assert resolver.normalize("/workspace/a/b/../c/./d.txt") == "/workspace/a/c/d.txt"

    def test_empty_path_raises_error(self, resolver):
        """Test that empty path raises error."""
        with pytest.raises(PathResolutionError) as exc_info:
            resolver.normalize("")
        assert exc_info.value.reason == "EMPTY_PATH"

    def test_null_bytes_raises_error(self, resolver):
        """Test that null bytes in path raises error."""
        with pytest.raises(PathResolutionError) as exc_info:
            resolver.normalize("/workspace/file\x00.txt")
        assert exc_info.value.reason == "NULL_BYTES"

    def test_whitespace_handling(self, resolver):
        """Test that leading/trailing whitespace is handled."""
        assert resolver.normalize("  file.txt  ") == "/workspace/file.txt"
        assert resolver.normalize("  /workspace/file.txt  ") == "/workspace/file.txt"


# =============================================================================
# Sandbox to Docker Translation Tests
# =============================================================================

class TestSandboxToDocker:
    """Tests for sandbox to Docker path translation."""

    def test_workspace_path(self, resolver):
        """Test translating workspace paths."""
        docker_path = resolver.sandbox_to_docker("/workspace/file.txt")
        assert docker_path == "/users/testuser/sessions/test-session-123/workspace/file.txt"

    def test_workspace_relative_path(self, resolver):
        """Test translating relative workspace paths."""
        docker_path = resolver.sandbox_to_docker("file.txt")
        assert docker_path == "/users/testuser/sessions/test-session-123/workspace/file.txt"

    def test_persistent_storage_path(self, resolver):
        """Test translating persistent storage paths."""
        docker_path = resolver.sandbox_to_docker("/workspace/external/persistent/img.png")
        assert docker_path == "/users/testuser/ag3ntum/persistent/img.png"

        # Also test with relative path
        docker_path = resolver.sandbox_to_docker("external/persistent/data.json")
        assert docker_path == "/users/testuser/ag3ntum/persistent/data.json"

    def test_external_ro_path(self, resolver):
        """Test translating external read-only mount paths."""
        docker_path = resolver.sandbox_to_docker("/workspace/external/ro/downloads/file.csv")
        assert docker_path == "/mounts/ro/downloads/file.csv"

    def test_external_rw_path(self, resolver):
        """Test translating external read-write mount paths."""
        docker_path = resolver.sandbox_to_docker("/workspace/external/rw/projects/code.py")
        assert docker_path == "/mounts/rw/projects/code.py"

    def test_venv_path(self, resolver):
        """Test translating venv paths."""
        docker_path = resolver.sandbox_to_docker("/venv/bin/python3")
        assert docker_path == "/users/testuser/venv/bin/python3"

    def test_skills_path(self, resolver):
        """Test translating skills paths (same in both contexts)."""
        docker_path = resolver.sandbox_to_docker("/skills/.claude/skills/test/script.py")
        assert docker_path == "/skills/.claude/skills/test/script.py"

    def test_user_ro_mount(self, resolver_with_user_mounts):
        """Test translating user read-only mount paths."""
        docker_path = resolver_with_user_mounts.sandbox_to_docker(
            "/workspace/external/user-ro/downloads/file.txt"
        )
        assert docker_path == "/mounts/user-ro/downloads/file.txt"

    def test_user_rw_mount(self, resolver_with_user_mounts):
        """Test translating user read-write mount paths."""
        docker_path = resolver_with_user_mounts.sandbox_to_docker(
            "/workspace/external/user-rw/projects/code.py"
        )
        assert docker_path == "/mounts/user-rw/projects/code.py"

    def test_unknown_mount_raises_error(self, resolver_with_user_mounts):
        """Test that unknown user mount raises error."""
        with pytest.raises(PathResolutionError) as exc_info:
            resolver_with_user_mounts.sandbox_to_docker(
                "/workspace/external/user-ro/nonexistent/file.txt"
            )
        assert exc_info.value.reason == "UNKNOWN_MOUNT"

    def test_path_outside_mounts_raises_error(self, resolver):
        """Test that path outside all mounts raises error."""
        with pytest.raises(PathResolutionError) as exc_info:
            resolver.sandbox_to_docker("/etc/passwd")
        assert exc_info.value.reason == "OUTSIDE_MOUNTS"


# =============================================================================
# Docker to Sandbox Translation Tests
# =============================================================================

class TestDockerToSandbox:
    """Tests for Docker to sandbox path translation."""

    def test_workspace_path(self, resolver):
        """Test translating Docker workspace paths to sandbox."""
        sandbox_path = resolver.docker_to_sandbox(
            "/users/testuser/sessions/test-session-123/workspace/file.txt"
        )
        assert sandbox_path == "/workspace/file.txt"

    def test_persistent_storage_path(self, resolver):
        """Test translating Docker persistent storage paths to sandbox."""
        sandbox_path = resolver.docker_to_sandbox(
            "/users/testuser/ag3ntum/persistent/img.png"
        )
        assert sandbox_path == "/workspace/external/persistent/img.png"

    def test_venv_path(self, resolver):
        """Test translating Docker venv paths to sandbox."""
        sandbox_path = resolver.docker_to_sandbox(
            "/users/testuser/venv/bin/python3"
        )
        assert sandbox_path == "/venv/bin/python3"

    def test_path_outside_mounts_raises_error(self, resolver):
        """Test that Docker path outside mounts raises error."""
        with pytest.raises(PathResolutionError) as exc_info:
            resolver.docker_to_sandbox("/some/random/path")
        assert exc_info.value.reason == "OUTSIDE_MOUNTS"


# =============================================================================
# Mount Type Detection Tests
# =============================================================================

class TestMountTypeDetection:
    """Tests for mount type detection."""

    def test_workspace_mount_type(self, resolver):
        """Test detecting workspace mount type."""
        assert resolver.get_mount_type("/workspace/file.txt") == "workspace"
        assert resolver.get_mount_type("file.txt") == "workspace"

    def test_persistent_mount_type(self, resolver):
        """Test detecting persistent storage mount type."""
        assert resolver.get_mount_type("/workspace/external/persistent/img.png") == "persistent"
        assert resolver.get_mount_type("external/persistent/data.json") == "persistent"

    def test_external_ro_mount_type(self, resolver):
        """Test detecting external read-only mount type."""
        assert resolver.get_mount_type("/workspace/external/ro/file.csv") == "external_ro"

    def test_external_rw_mount_type(self, resolver):
        """Test detecting external read-write mount type."""
        assert resolver.get_mount_type("/workspace/external/rw/file.txt") == "external_rw"

    def test_venv_mount_type(self, resolver):
        """Test detecting venv mount type."""
        assert resolver.get_mount_type("/venv/bin/python3") == "venv"

    def test_invalid_path_returns_none(self, resolver):
        """Test that invalid path returns None for mount type."""
        assert resolver.get_mount_type("") is None


# =============================================================================
# Writability Tests
# =============================================================================

class TestWritability:
    """Tests for path writability checking."""

    def test_workspace_is_writable(self, resolver):
        """Test that workspace is writable."""
        assert resolver.is_path_writable("/workspace/file.txt") is True

    def test_persistent_is_writable(self, resolver):
        """Test that persistent storage is writable."""
        assert resolver.is_path_writable("external/persistent/img.png") is True

    def test_external_rw_is_writable(self, resolver):
        """Test that external rw mount is writable."""
        assert resolver.is_path_writable("/workspace/external/rw/file.txt") is True

    def test_external_ro_is_not_writable(self, resolver):
        """Test that external ro mount is not writable."""
        assert resolver.is_path_writable("/workspace/external/ro/file.csv") is False

    def test_venv_is_not_writable(self, resolver):
        """Test that venv is not writable."""
        assert resolver.is_path_writable("/venv/bin/python3") is False

    def test_skills_is_not_writable(self, resolver):
        """Test that skills is not writable."""
        assert resolver.is_path_writable("/skills/.claude/skills/test/script.py") is False

    def test_invalid_path_is_not_writable(self, resolver):
        """Test that invalid path returns False for writability."""
        assert resolver.is_path_writable("") is False


# =============================================================================
# Error Message Translation Tests
# =============================================================================

class TestErrorMessageTranslation:
    """Tests for translating Docker paths in error messages."""

    def test_translate_workspace_path_in_error(self, resolver):
        """Test translating workspace path in error message."""
        error = "File not found: /users/testuser/sessions/test-session-123/workspace/missing.txt"
        translated = resolver.translate_error_paths(error)
        assert "/workspace/missing.txt" in translated
        assert "/users/testuser/sessions" not in translated

    def test_translate_persistent_path_in_error(self, resolver):
        """Test translating persistent path in error message."""
        error = "Permission denied: /users/testuser/ag3ntum/persistent/file.png"
        translated = resolver.translate_error_paths(error)
        assert "/workspace/external/persistent/file.png" in translated

    def test_preserve_non_path_text(self, resolver):
        """Test that non-path text is preserved."""
        error = "Error occurred: something went wrong"
        translated = resolver.translate_error_paths(error)
        assert translated == error


# =============================================================================
# Context-Aware Resolution Tests
# =============================================================================

class TestContextAwareResolution:
    """Tests for context-aware path resolution."""

    def test_resolve_in_docker_context(self, resolver):
        """Test resolve() in Docker context returns Docker path."""
        with patch.object(resolver, '_execution_context', ExecutionContext.DOCKER):
            path = resolver.resolve("/workspace/file.txt")
            assert path == "/users/testuser/sessions/test-session-123/workspace/file.txt"

    def test_resolve_in_sandbox_context(self, resolver):
        """Test resolve() in sandbox context returns sandbox path."""
        with patch.object(resolver, '_execution_context', ExecutionContext.SANDBOX):
            path = resolver.resolve("/workspace/file.txt")
            assert path == "/workspace/file.txt"

    def test_resolve_to_docker_always_returns_docker(self, resolver):
        """Test resolve_to_docker() always returns Docker path."""
        with patch.object(resolver, '_execution_context', ExecutionContext.SANDBOX):
            path = resolver.resolve_to_docker("/workspace/file.txt")
            assert path == "/users/testuser/sessions/test-session-123/workspace/file.txt"


# =============================================================================
# Execution Context Detection Tests
# =============================================================================

class TestExecutionContextDetection:
    """Tests for execution context detection."""

    def test_sandbox_context_from_env(self):
        """Test detection of sandbox context from environment variable."""
        with patch.dict(os.environ, {"AG3NTUM_CONTEXT": "sandbox"}):
            reset_context_cache()
            context = detect_execution_context()
            assert context == ExecutionContext.SANDBOX

    def test_docker_context_default(self):
        """Test that Docker context is default."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove AG3NTUM_CONTEXT if it exists
            if "AG3NTUM_CONTEXT" in os.environ:
                del os.environ["AG3NTUM_CONTEXT"]
            reset_context_cache()
            context = detect_execution_context()
            assert context == ExecutionContext.DOCKER


# =============================================================================
# Session Resolver Management Tests
# =============================================================================

class TestSessionResolverManagement:
    """Tests for session-scoped resolver management."""

    def test_configure_and_get_resolver(self):
        """Test configuring and getting a resolver."""
        resolver = configure_sandbox_path_resolver(
            session_id="test-session-789",
            username="testuser",
        )
        assert resolver is not None

        # Get it again
        retrieved = get_sandbox_path_resolver("test-session-789")
        assert retrieved is resolver

    def test_has_resolver(self):
        """Test checking if resolver exists."""
        assert has_sandbox_path_resolver("nonexistent") is False

        configure_sandbox_path_resolver(
            session_id="test-session-789",
            username="testuser",
        )
        assert has_sandbox_path_resolver("test-session-789") is True

    def test_cleanup_resolver(self):
        """Test cleaning up a resolver."""
        configure_sandbox_path_resolver(
            session_id="test-session-789",
            username="testuser",
        )
        assert has_sandbox_path_resolver("test-session-789") is True

        cleanup_sandbox_path_resolver("test-session-789")
        assert has_sandbox_path_resolver("test-session-789") is False

    def test_get_unconfigured_resolver_raises(self):
        """Test that getting unconfigured resolver raises error."""
        with pytest.raises(RuntimeError) as exc_info:
            get_sandbox_path_resolver("nonexistent-session")
        assert "not configured" in str(exc_info.value)


# =============================================================================
# MountMapping Tests
# =============================================================================

class TestMountMapping:
    """Tests for MountMapping class."""

    def test_matches_sandbox_path(self):
        """Test sandbox path matching."""
        mount = MountMapping(
            sandbox_path="/workspace",
            docker_path="/users/test/sessions/123/workspace",
            mode="rw",
        )
        assert mount.matches_sandbox_path("/workspace") is True
        assert mount.matches_sandbox_path("/workspace/file.txt") is True
        assert mount.matches_sandbox_path("/workspaceother") is False
        assert mount.matches_sandbox_path("/other") is False

    def test_matches_docker_path(self):
        """Test docker path matching."""
        mount = MountMapping(
            sandbox_path="/workspace",
            docker_path="/users/test/sessions/123/workspace",
            mode="rw",
        )
        assert mount.matches_docker_path("/users/test/sessions/123/workspace") is True
        assert mount.matches_docker_path("/users/test/sessions/123/workspace/file.txt") is True
        assert mount.matches_docker_path("/users/test/sessions/123/workspaceother") is False

    def test_sandbox_to_docker_conversion(self):
        """Test sandbox to docker path conversion."""
        mount = MountMapping(
            sandbox_path="/workspace",
            docker_path="/users/test/sessions/123/workspace",
            mode="rw",
        )
        assert mount.sandbox_to_docker("/workspace") == "/users/test/sessions/123/workspace"
        assert mount.sandbox_to_docker("/workspace/file.txt") == "/users/test/sessions/123/workspace/file.txt"

    def test_docker_to_sandbox_conversion(self):
        """Test docker to sandbox path conversion."""
        mount = MountMapping(
            sandbox_path="/workspace",
            docker_path="/users/test/sessions/123/workspace",
            mode="rw",
        )
        assert mount.docker_to_sandbox("/users/test/sessions/123/workspace") == "/workspace"
        assert mount.docker_to_sandbox("/users/test/sessions/123/workspace/file.txt") == "/workspace/file.txt"


# =============================================================================
# Edge Cases and Corner Cases
# =============================================================================

class TestEdgeCases:
    """Tests for edge cases and corner cases."""

    def test_deeply_nested_path(self, resolver):
        """Test handling deeply nested paths."""
        path = "/workspace/a/b/c/d/e/f/g/h/i/j/file.txt"
        docker_path = resolver.sandbox_to_docker(path)
        assert docker_path.endswith("/a/b/c/d/e/f/g/h/i/j/file.txt")

    def test_path_with_spaces(self, resolver):
        """Test handling paths with spaces."""
        path = "/workspace/my file.txt"
        docker_path = resolver.sandbox_to_docker(path)
        assert "my file.txt" in docker_path

    def test_path_with_special_chars(self, resolver):
        """Test handling paths with special characters."""
        path = "/workspace/file-name_v2.0.txt"
        docker_path = resolver.sandbox_to_docker(path)
        assert "file-name_v2.0.txt" in docker_path

    def test_path_with_unicode(self, resolver):
        """Test handling paths with unicode characters."""
        path = "/workspace/文件.txt"
        docker_path = resolver.sandbox_to_docker(path)
        assert "文件.txt" in docker_path

    def test_persistent_root_path(self, resolver):
        """Test handling persistent storage root path."""
        docker_path = resolver.sandbox_to_docker("/workspace/external/persistent")
        assert docker_path == "/users/testuser/ag3ntum/persistent"

    def test_external_ro_root_path(self, resolver):
        """Test handling external ro root path."""
        docker_path = resolver.sandbox_to_docker("/workspace/external/ro")
        assert docker_path == "/mounts/ro"
        # Also verify mount type and writability for root path
        assert resolver.get_mount_type("/workspace/external/ro") == "external_ro"
        assert resolver.is_path_writable("/workspace/external/ro") is False

    def test_external_rw_root_path(self, resolver):
        """Test handling external rw root path."""
        docker_path = resolver.sandbox_to_docker("/workspace/external/rw")
        assert docker_path == "/mounts/rw"
        # Also verify mount type and writability for root path
        assert resolver.get_mount_type("/workspace/external/rw") == "external_rw"
        assert resolver.is_path_writable("/workspace/external/rw") is True

    def test_multiple_dot_components(self, resolver):
        """Test handling multiple dot components."""
        path = "/workspace/./a/./b/./file.txt"
        normalized = resolver.normalize(path)
        assert normalized == "/workspace/a/b/file.txt"

    def test_multiple_dotdot_components(self, resolver):
        """Test handling multiple parent directory components."""
        path = "/workspace/a/b/c/../../file.txt"
        normalized = resolver.normalize(path)
        assert normalized == "/workspace/a/file.txt"

    def test_dotdot_at_root(self, resolver):
        """Test that .. at root doesn't escape."""
        path = "/workspace/../../../file.txt"
        normalized = resolver.normalize(path)
        # Should stay at root level
        assert normalized == "/file.txt"


# =============================================================================
# Integration Tests
# =============================================================================

class TestIntegration:
    """Integration tests for full path resolution flow."""

    def test_full_flow_workspace_file(self, resolver):
        """Test complete flow for workspace file."""
        # Start with relative path
        sandbox_path = "src/main.py"

        # Normalize
        normalized = resolver.normalize(sandbox_path)
        assert normalized == "/workspace/src/main.py"

        # Translate to Docker
        docker_path = resolver.sandbox_to_docker(normalized)
        assert "/users/testuser/sessions/test-session-123/workspace/src/main.py" == docker_path

        # Translate back
        back_to_sandbox = resolver.docker_to_sandbox(docker_path)
        assert back_to_sandbox == normalized

        # Check properties
        assert resolver.get_mount_type(normalized) == "workspace"
        assert resolver.is_path_writable(normalized) is True

    def test_full_flow_persistent_storage(self, resolver):
        """Test complete flow for persistent storage file."""
        # Start with relative external path
        sandbox_path = "external/persistent/generated_image.png"

        # Normalize
        normalized = resolver.normalize(sandbox_path)
        assert normalized == "/workspace/external/persistent/generated_image.png"

        # Translate to Docker
        docker_path = resolver.sandbox_to_docker(normalized)
        assert docker_path == "/users/testuser/ag3ntum/persistent/generated_image.png"

        # Check properties
        assert resolver.get_mount_type(normalized) == "persistent"
        assert resolver.is_path_writable(normalized) is True

    def test_full_flow_readonly_external(self, resolver):
        """Test complete flow for read-only external mount."""
        sandbox_path = "/workspace/external/ro/datasets/data.csv"

        # Normalize
        normalized = resolver.normalize(sandbox_path)
        assert normalized == sandbox_path

        # Translate to Docker
        docker_path = resolver.sandbox_to_docker(normalized)
        assert docker_path == "/mounts/ro/datasets/data.csv"

        # Check properties
        assert resolver.get_mount_type(normalized) == "external_ro"
        assert resolver.is_path_writable(normalized) is False
