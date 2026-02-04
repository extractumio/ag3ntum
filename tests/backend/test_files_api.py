"""
Tests for Files API path normalization and security validation.

These tests ensure that sandbox-format paths from agent messages
(e.g., /workspace/persistent/file.txt) are correctly
normalized before being processed by the file API.
"""
import pytest
from pathlib import Path
import tempfile
import shutil

# Import the functions we're testing
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.api.routes.files import (
    normalize_path_for_mount_check,
    validate_path_security,
    get_mount_info,
    _load_mount_host_paths,
)


class TestNormalizePathForMountCheck:
    """Tests for normalize_path_for_mount_check function."""

    def test_relative_path_unchanged(self):
        """Relative paths without workspace prefix should be unchanged."""
        assert normalize_path_for_mount_check("file.txt") == "file.txt"
        assert normalize_path_for_mount_check("subdir/file.txt") == "subdir/file.txt"

    def test_sandbox_format_workspace_prefix_stripped(self):
        """Sandbox format /workspace/... should have prefix stripped."""
        assert normalize_path_for_mount_check("/workspace/file.txt") == "file.txt"
        assert normalize_path_for_mount_check("/workspace/subdir/file.txt") == "subdir/file.txt"

    def test_workspace_prefix_without_leading_slash(self):
        """workspace/... without leading slash should be stripped."""
        assert normalize_path_for_mount_check("workspace/file.txt") == "file.txt"
        assert normalize_path_for_mount_check("workspace/subdir/file.txt") == "subdir/file.txt"

    def test_external_paths_normalized(self):
        """External mount paths in sandbox format should be normalized."""
        # These are the common cases from agent messages
        assert normalize_path_for_mount_check("/workspace/external/persistent/image.png") == "external/persistent/image.png"
        assert normalize_path_for_mount_check("/workspace/external/ro/downloads/file.txt") == "external/ro/downloads/file.txt"
        assert normalize_path_for_mount_check("/workspace/external/rw/data/output.csv") == "external/rw/data/output.csv"

    def test_just_workspace_returns_empty(self):
        """Just 'workspace' or '/workspace' should return empty string."""
        assert normalize_path_for_mount_check("workspace") == ""
        assert normalize_path_for_mount_check("/workspace") == ""

    def test_multiple_leading_slashes_stripped(self):
        """Multiple leading slashes should all be stripped."""
        assert normalize_path_for_mount_check("///workspace/file.txt") == "file.txt"
        assert normalize_path_for_mount_check("//file.txt") == "file.txt"

    def test_backslash_normalized(self):
        """Backslashes should be converted to forward slashes."""
        assert normalize_path_for_mount_check("\\workspace\\file.txt") == "file.txt"
        assert normalize_path_for_mount_check("workspace\\subdir\\file.txt") == "subdir/file.txt"

    def test_external_root_normalized(self):
        """Just 'external' should be preserved after normalization."""
        assert normalize_path_for_mount_check("external") == "external"
        assert normalize_path_for_mount_check("/workspace/external") == "external"


class TestGetMountInfo:
    """Tests for get_mount_info function with sandbox-format paths."""

    def test_regular_file_not_external(self):
        """Regular workspace files should not be marked as external."""
        is_external, is_readonly, mount_type = get_mount_info("file.txt")
        assert is_external is False
        assert mount_type is None

    def test_sandbox_format_regular_file(self):
        """Sandbox format regular files should not be marked as external."""
        is_external, is_readonly, mount_type = get_mount_info("/workspace/file.txt")
        assert is_external is False
        assert mount_type is None

    def test_sandbox_format_persistent_mount(self):
        """Sandbox format persistent paths should be detected."""
        is_external, is_readonly, mount_type = get_mount_info("/workspace/external/persistent/image.png")
        assert is_external is True
        assert is_readonly is False
        assert mount_type == "persistent"

    def test_sandbox_format_ro_mount(self):
        """Sandbox format read-only mount paths should be detected."""
        is_external, is_readonly, mount_type = get_mount_info("/workspace/external/ro/downloads/file.txt")
        assert is_external is True
        assert is_readonly is True
        assert mount_type == "ro"

    def test_sandbox_format_rw_mount(self):
        """Sandbox format read-write mount paths should be detected."""
        is_external, is_readonly, mount_type = get_mount_info("/workspace/external/rw/data/output.csv")
        assert is_external is True
        assert is_readonly is False
        assert mount_type == "rw"

    def test_sandbox_format_user_ro_mount(self):
        """Sandbox format user read-only mount paths should be detected."""
        is_external, is_readonly, mount_type = get_mount_info("/workspace/external/user-ro/mydata/file.txt")
        assert is_external is True
        assert is_readonly is True
        assert mount_type == "user-ro"

    def test_sandbox_format_user_rw_mount(self):
        """Sandbox format user read-write mount paths should be detected."""
        is_external, is_readonly, mount_type = get_mount_info("/workspace/external/user-rw/mydata/file.txt")
        assert is_external is True
        assert is_readonly is False
        assert mount_type == "user-rw"

    def test_relative_external_paths(self):
        """Relative external paths should also be detected correctly."""
        is_external, is_readonly, mount_type = get_mount_info("external/persistent/file.txt")
        assert is_external is True
        assert mount_type == "persistent"


class TestGetMountInfoDynamic:
    """Tests for get_mount_info with dynamic mounts (workspace_root symlinks)."""

    @pytest.fixture
    def workspace_with_dynamic_mount(self):
        """Create workspace with a dynamic mount symlink pointing outside."""
        temp_dir = Path(tempfile.mkdtemp(prefix="test_workspace_"))
        session_dir = temp_dir / "session"
        session_dir.mkdir()
        workspace = session_dir / "workspace"
        workspace.mkdir()

        # Create a mount target outside workspace
        mount_target = temp_dir / "mounts" / "logs"
        mount_target.mkdir(parents=True)
        (mount_target / "test.log").write_text("log data")

        # Create symlink at workspace root pointing outside
        (workspace / "var-log").symlink_to(mount_target)

        # Write metadata file
        import json
        meta = {"var-log": {"mode": "ro", "source_base": "logs", "source_subpath": None, "host_path": "/var/log"}}
        (session_dir / ".dynamic-mounts.json").write_text(json.dumps(meta))

        yield workspace
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_dynamic_mount_detected(self, workspace_with_dynamic_mount):
        """Dynamic mount symlink at workspace root detected as external."""
        is_external, is_readonly, mount_type = get_mount_info(
            "var-log/test.log",
            workspace_root=workspace_with_dynamic_mount,
        )
        assert is_external is True
        assert mount_type == "dynamic"

    def test_dynamic_mount_mode_from_metadata(self, workspace_with_dynamic_mount):
        """Dynamic mount reads mode from .dynamic-mounts.json."""
        is_external, is_readonly, mount_type = get_mount_info(
            "var-log",
            workspace_root=workspace_with_dynamic_mount,
        )
        assert is_external is True
        assert is_readonly is True  # mode=ro in metadata

    def test_regular_dir_not_dynamic(self, workspace_with_dynamic_mount):
        """Regular directories (non-symlinks) are not detected as dynamic mounts."""
        (workspace_with_dynamic_mount / "regular-dir").mkdir()
        is_external, is_readonly, mount_type = get_mount_info(
            "regular-dir",
            workspace_root=workspace_with_dynamic_mount,
        )
        assert is_external is False
        assert mount_type is None

    def test_no_workspace_root_skips_dynamic_check(self):
        """Without workspace_root, dynamic mount detection is skipped."""
        is_external, is_readonly, mount_type = get_mount_info("var-log/test.log")
        assert is_external is False
        assert mount_type is None


class TestValidatePathSecurity:
    """Tests for validate_path_security with sandbox-format paths."""

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace directory."""
        temp_dir = Path(tempfile.mkdtemp(prefix="test_workspace_"))
        workspace = temp_dir / "workspace"
        workspace.mkdir()
        # Create some test files
        (workspace / "test.txt").write_text("test content")
        (workspace / "subdir").mkdir()
        (workspace / "subdir" / "nested.txt").write_text("nested content")
        yield workspace
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_relative_path(self, temp_workspace):
        """Relative paths should work correctly."""
        result = validate_path_security("test.txt", temp_workspace)
        # Use resolve() on both sides to handle macOS /var vs /private/var symlink
        assert result.resolve() == (temp_workspace / "test.txt").resolve()

    def test_sandbox_format_path(self, temp_workspace):
        """Sandbox format paths should have /workspace/ prefix stripped."""
        result = validate_path_security("/workspace/test.txt", temp_workspace)
        assert result.resolve() == (temp_workspace / "test.txt").resolve()

    def test_sandbox_format_nested_path(self, temp_workspace):
        """Sandbox format nested paths should work."""
        result = validate_path_security("/workspace/subdir/nested.txt", temp_workspace)
        assert result.resolve() == (temp_workspace / "subdir" / "nested.txt").resolve()

    def test_workspace_prefix_without_slash(self, temp_workspace):
        """Paths starting with workspace/ (no leading /) should work."""
        result = validate_path_security("workspace/test.txt", temp_workspace)
        assert result.resolve() == (temp_workspace / "test.txt").resolve()

    def test_path_traversal_blocked(self, temp_workspace):
        """Path traversal attempts should be blocked."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            validate_path_security("../../../etc/passwd", temp_workspace)
        # Check for either "path traversal" or "traversal" in the error message
        assert "traversal" in exc_info.value.detail.lower()

    def test_sandbox_format_path_traversal_blocked(self, temp_workspace):
        """Path traversal in sandbox format should be blocked."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            validate_path_security("/workspace/../../../etc/passwd", temp_workspace)
        assert "traversal" in exc_info.value.detail.lower()


class TestPathNormalizationRegression:
    """
    Regression tests for the sandbox path normalization bug.

    The original bug: When clicking "View in File Explorer" on a file,
    the path came from agent messages in sandbox format (e.g., /workspace/tests/file.py).
    The validation code only stripped the leading '/', leaving 'workspace/tests/file.py'.
    This was then appended to workspace_root, creating an invalid double-workspace path.

    These tests verify the fix works correctly.
    """

    def test_double_workspace_path_not_created(self):
        """Ensure paths like /workspace/file.txt don't create workspace/workspace/file.txt."""
        normalized = normalize_path_for_mount_check("/workspace/tests/backend/conftest.py")
        assert normalized == "tests/backend/conftest.py"
        assert not normalized.startswith("workspace/")

    def test_agent_message_external_path_normalized(self):
        """Paths from agent messages like /workspace/external/... should be normalized."""
        # This is the exact format that caused the original bug
        path = "/workspace/external/persistent/screenshot.png"
        normalized = normalize_path_for_mount_check(path)

        assert normalized == "external/persistent/screenshot.png"
        assert not normalized.startswith("workspace/")

        # Verify it's correctly detected as external
        is_external, _, mount_type = get_mount_info(path)
        assert is_external is True
        assert mount_type == "persistent"

    def test_various_sandbox_path_formats(self):
        """Test various sandbox path formats that might come from agent messages."""
        test_cases = [
            # (input, expected_normalized)
            ("/workspace/file.txt", "file.txt"),
            ("/workspace/subdir/file.txt", "subdir/file.txt"),
            ("workspace/file.txt", "file.txt"),
            ("/workspace/external/ro/name/file.txt", "external/ro/name/file.txt"),
            ("./file.txt", "./file.txt"),  # Current dir prefix preserved
            ("file.txt", "file.txt"),  # Plain relative path unchanged
        ]

        for input_path, expected in test_cases:
            result = normalize_path_for_mount_check(input_path)
            assert result == expected, f"Failed for input '{input_path}': expected '{expected}', got '{result}'"


# =============================================================================
# On-Demand Resolver Configuration Tests
# =============================================================================

class TestOnDemandResolverConfiguration:
    """
    Tests for on-demand SandboxPathResolver configuration.

    The SandboxPathResolver is stored in memory and gets cleared on server restart.
    The File Explorer API now configures the resolver on-demand when needed.
    """

    @pytest.fixture(autouse=True)
    def cleanup_resolvers(self):
        """Clean up any configured resolvers after each test."""
        from src.core.sandbox_path_resolver import (
            cleanup_sandbox_path_resolver,
            has_sandbox_path_resolver,
        )
        yield
        # Clean up test sessions
        for session_id in ["test-on-demand-123", "test-on-demand-456"]:
            if has_sandbox_path_resolver(session_id):
                cleanup_sandbox_path_resolver(session_id)

    def test_configure_sandbox_path_resolver_if_needed_creates_resolver(self):
        """Test that configure_sandbox_path_resolver_if_needed creates a resolver."""
        from src.api.deps import configure_sandbox_path_resolver_if_needed
        from src.core.sandbox_path_resolver import (
            has_sandbox_path_resolver,
            get_sandbox_path_resolver,
        )

        session_id = "test-on-demand-123"
        assert has_sandbox_path_resolver(session_id) is False

        configure_sandbox_path_resolver_if_needed(
            session_id=session_id,
            username="testuser",
            workspace_docker="/users/testuser/sessions/test-on-demand-123/workspace",
        )

        assert has_sandbox_path_resolver(session_id) is True

        # Verify the resolver works correctly
        resolver = get_sandbox_path_resolver(session_id)
        docker_path = resolver.sandbox_to_docker("/workspace/test.txt")
        assert docker_path == "/users/testuser/sessions/test-on-demand-123/workspace/test.txt"

    def test_configure_sandbox_path_resolver_if_needed_is_idempotent(self):
        """Test that calling configure_sandbox_path_resolver_if_needed twice is safe."""
        from src.api.deps import configure_sandbox_path_resolver_if_needed
        from src.core.sandbox_path_resolver import (
            has_sandbox_path_resolver,
            get_sandbox_path_resolver,
        )

        session_id = "test-on-demand-456"
        configure_sandbox_path_resolver_if_needed(
            session_id=session_id,
            username="testuser",
            workspace_docker="/users/testuser/sessions/test-on-demand-456/workspace",
        )

        resolver1 = get_sandbox_path_resolver(session_id)

        # Call again - should not raise or change the resolver
        configure_sandbox_path_resolver_if_needed(
            session_id=session_id,
            username="testuser",
            workspace_docker="/users/testuser/sessions/test-on-demand-456/workspace",
        )

        resolver2 = get_sandbox_path_resolver(session_id)
        assert resolver1 is resolver2

    def test_validate_and_resolve_path_with_on_demand_resolver(self):
        """Test validate_and_resolve_path_for_session configures resolver on-demand."""
        from src.api.routes.files import validate_and_resolve_path_for_session
        from src.core.sandbox_path_resolver import (
            has_sandbox_path_resolver,
            cleanup_sandbox_path_resolver,
        )
        from fastapi import HTTPException

        session_id = "test-on-demand-123"

        # Ensure resolver doesn't exist
        if has_sandbox_path_resolver(session_id):
            cleanup_sandbox_path_resolver(session_id)
        assert has_sandbox_path_resolver(session_id) is False

        # Call without required info should raise 500
        with pytest.raises(HTTPException) as exc_info:
            validate_and_resolve_path_for_session(session_id, "./test.txt")
        assert exc_info.value.status_code == 500
        assert "not configured" in exc_info.value.detail

    def test_validate_and_resolve_path_configures_on_demand(self):
        """Test that validate_and_resolve_path_for_session configures resolver when given info."""
        from src.api.routes.files import validate_and_resolve_path_for_session
        from src.core.sandbox_path_resolver import (
            has_sandbox_path_resolver,
            cleanup_sandbox_path_resolver,
        )
        import tempfile
        from pathlib import Path

        session_id = "test-on-demand-123"

        # Ensure resolver doesn't exist
        if has_sandbox_path_resolver(session_id):
            cleanup_sandbox_path_resolver(session_id)

        # Create a temp workspace for the test
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            (workspace / "test.txt").write_text("test content")

            # Call with required info - should configure resolver
            docker_path, is_external, mount_type = validate_and_resolve_path_for_session(
                session_id,
                "./test.txt",
                workspace_docker=str(workspace),
                username="testuser",
            )

            # Resolver should now exist
            assert has_sandbox_path_resolver(session_id) is True

            # Path should be resolved correctly
            assert docker_path == workspace / "test.txt"
            assert mount_type == "workspace"


class TestValidateAndResolvePathForSession:
    """Tests for validate_and_resolve_path_for_session function."""

    @pytest.fixture(autouse=True)
    def cleanup_resolvers(self):
        """Clean up any configured resolvers after each test."""
        from src.core.sandbox_path_resolver import (
            cleanup_sandbox_path_resolver,
            has_sandbox_path_resolver,
        )
        yield
        for session_id in ["test-varp-123"]:
            if has_sandbox_path_resolver(session_id):
                cleanup_sandbox_path_resolver(session_id)

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace with test files."""
        temp_dir = Path(tempfile.mkdtemp(prefix="test_varp_"))
        workspace = temp_dir / "workspace"
        workspace.mkdir()
        (workspace / "file.txt").write_text("content")
        (workspace / "subdir").mkdir()
        (workspace / "subdir" / "nested.txt").write_text("nested")
        yield workspace
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_resolves_relative_path(self, temp_workspace):
        """Test resolving a relative path like ./file.txt."""
        from src.api.routes.files import validate_and_resolve_path_for_session

        session_id = "test-varp-123"

        docker_path, is_external, mount_type = validate_and_resolve_path_for_session(
            session_id,
            "./file.txt",
            workspace_docker=str(temp_workspace),
            username="testuser",
        )

        assert docker_path == temp_workspace / "file.txt"
        assert is_external is False
        assert mount_type == "workspace"

    def test_resolves_plain_relative_path(self, temp_workspace):
        """Test resolving a plain relative path without ./."""
        from src.api.routes.files import validate_and_resolve_path_for_session

        session_id = "test-varp-123"

        docker_path, is_external, mount_type = validate_and_resolve_path_for_session(
            session_id,
            "file.txt",
            workspace_docker=str(temp_workspace),
            username="testuser",
        )

        assert docker_path == temp_workspace / "file.txt"

    def test_resolves_nested_path(self, temp_workspace):
        """Test resolving a nested path."""
        from src.api.routes.files import validate_and_resolve_path_for_session

        session_id = "test-varp-123"

        docker_path, is_external, mount_type = validate_and_resolve_path_for_session(
            session_id,
            "./subdir/nested.txt",
            workspace_docker=str(temp_workspace),
            username="testuser",
        )

        assert docker_path == temp_workspace / "subdir" / "nested.txt"

    def test_handles_empty_path_error(self, temp_workspace):
        """Test that empty path raises appropriate error."""
        from src.api.routes.files import validate_and_resolve_path_for_session
        from fastapi import HTTPException

        session_id = "test-varp-123"

        with pytest.raises(HTTPException) as exc_info:
            validate_and_resolve_path_for_session(
                session_id,
                "",
                workspace_docker=str(temp_workspace),
                username="testuser",
            )
        assert exc_info.value.status_code == 400
        assert "Empty path" in exc_info.value.detail

    def test_handles_null_bytes_error(self, temp_workspace):
        """Test that null bytes in path raise appropriate error."""
        from src.api.routes.files import validate_and_resolve_path_for_session
        from fastapi import HTTPException

        session_id = "test-varp-123"

        with pytest.raises(HTTPException) as exc_info:
            validate_and_resolve_path_for_session(
                session_id,
                "./file\x00.txt",
                workspace_docker=str(temp_workspace),
                username="testuser",
            )
        assert exc_info.value.status_code == 400
        assert "null bytes" in exc_info.value.detail


class TestResolverAfterServerRestart:
    """
    Tests that simulate server restart behavior.

    After a server restart, the in-memory resolver cache is empty.
    The File Explorer API should configure the resolver on-demand.
    """

    @pytest.fixture(autouse=True)
    def cleanup_resolvers(self):
        """Clean up any configured resolvers before and after each test."""
        from src.core.sandbox_path_resolver import (
            cleanup_sandbox_path_resolver,
            has_sandbox_path_resolver,
        )
        test_sessions = ["restart-test-123", "restart-test-456"]
        # Cleanup before
        for session_id in test_sessions:
            if has_sandbox_path_resolver(session_id):
                cleanup_sandbox_path_resolver(session_id)
        yield
        # Cleanup after
        for session_id in test_sessions:
            if has_sandbox_path_resolver(session_id):
                cleanup_sandbox_path_resolver(session_id)

    def test_resolver_cleared_simulates_restart(self):
        """Test that clearing resolver simulates server restart."""
        from src.core.sandbox_path_resolver import (
            configure_sandbox_path_resolver,
            cleanup_sandbox_path_resolver,
            has_sandbox_path_resolver,
        )

        session_id = "restart-test-123"

        # Configure resolver
        configure_sandbox_path_resolver(
            session_id=session_id,
            username="testuser",
            workspace_docker="/users/testuser/sessions/restart-test-123/workspace",
        )
        assert has_sandbox_path_resolver(session_id) is True

        # "Restart" by cleaning up
        cleanup_sandbox_path_resolver(session_id)
        assert has_sandbox_path_resolver(session_id) is False

    def test_on_demand_config_after_simulated_restart(self):
        """Test on-demand configuration after simulated restart."""
        from src.core.sandbox_path_resolver import (
            configure_sandbox_path_resolver,
            cleanup_sandbox_path_resolver,
            has_sandbox_path_resolver,
        )
        from src.api.deps import configure_sandbox_path_resolver_if_needed

        session_id = "restart-test-456"

        # Simulate initial session creation
        configure_sandbox_path_resolver(
            session_id=session_id,
            username="testuser",
            workspace_docker="/users/testuser/sessions/restart-test-456/workspace",
        )

        # Simulate server restart
        cleanup_sandbox_path_resolver(session_id)
        assert has_sandbox_path_resolver(session_id) is False

        # On-demand configuration should work
        configure_sandbox_path_resolver_if_needed(
            session_id=session_id,
            username="testuser",
            workspace_docker="/users/testuser/sessions/restart-test-456/workspace",
        )
        assert has_sandbox_path_resolver(session_id) is True

    def test_file_path_resolution_after_simulated_restart(self):
        """Test complete file path resolution after simulated restart."""
        from src.core.sandbox_path_resolver import (
            configure_sandbox_path_resolver,
            cleanup_sandbox_path_resolver,
            has_sandbox_path_resolver,
        )
        from src.api.routes.files import validate_and_resolve_path_for_session
        import tempfile
        from pathlib import Path

        session_id = "restart-test-123"

        # Create temp workspace
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            (workspace / "memory_tracker.py").write_text("# test file")

            # Initial configuration (simulating session creation)
            configure_sandbox_path_resolver(
                session_id=session_id,
                username="testuser",
                workspace_docker=str(workspace),
            )

            # Verify resolution works
            docker_path, _, _ = validate_and_resolve_path_for_session(
                session_id, "./memory_tracker.py"
            )
            assert docker_path == workspace / "memory_tracker.py"

            # Simulate server restart
            cleanup_sandbox_path_resolver(session_id)

            # Resolution should still work with on-demand config
            docker_path, _, _ = validate_and_resolve_path_for_session(
                session_id,
                "./memory_tracker.py",
                workspace_docker=str(workspace),
                username="testuser",
            )
            assert docker_path == workspace / "memory_tracker.py"


class TestPathFormatCompatibility:
    """
    Tests for path format compatibility.

    The File Explorer receives paths in various formats from the frontend.
    These tests ensure all formats are handled correctly.
    """

    @pytest.fixture(autouse=True)
    def cleanup_resolvers(self):
        """Clean up after each test."""
        from src.core.sandbox_path_resolver import (
            cleanup_sandbox_path_resolver,
            has_sandbox_path_resolver,
        )
        yield
        if has_sandbox_path_resolver("path-format-test"):
            cleanup_sandbox_path_resolver("path-format-test")

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace."""
        temp_dir = Path(tempfile.mkdtemp(prefix="test_path_format_"))
        workspace = temp_dir / "workspace"
        workspace.mkdir()
        (workspace / "file.py").write_text("# python")
        (workspace / "subdir").mkdir()
        (workspace / "subdir" / "nested.txt").write_text("nested")
        yield workspace
        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.mark.parametrize("path_format,expected_suffix", [
        ("./file.py", "file.py"),
        ("file.py", "file.py"),
        ("./subdir/nested.txt", "subdir/nested.txt"),
        ("subdir/nested.txt", "subdir/nested.txt"),
    ])
    def test_various_path_formats(self, temp_workspace, path_format, expected_suffix):
        """Test that various path formats are resolved correctly."""
        from src.api.routes.files import validate_and_resolve_path_for_session

        session_id = "path-format-test"

        docker_path, is_external, mount_type = validate_and_resolve_path_for_session(
            session_id,
            path_format,
            workspace_docker=str(temp_workspace),
            username="testuser",
        )

        assert docker_path == temp_workspace / expected_suffix
        assert is_external is False
        assert mount_type == "workspace"


class TestLoadMountHostPaths:
    """Tests for _load_mount_host_paths helper that reads auto-generated manifest."""

    def _write_manifest(self, tmp_path: Path, content: str) -> Path:
        """Write a YAML manifest file and return its path."""
        manifest_path = tmp_path / "auto-generated-mounts.yaml"
        manifest_path.write_text(content, encoding="utf-8")
        return manifest_path

    def test_reads_ro_mounts(self, tmp_path):
        """Reads host_path from ro section."""
        manifest = self._write_manifest(tmp_path, """
mounts:
  ro:
    - name: global_var_log
      host_path: /var/log
""")
        result = _load_mount_host_paths(manifest_path=manifest)
        assert result == {"global_var_log": "/var/log"}

    def test_reads_all_sections(self, tmp_path):
        """Reads host_path from all mount sections (ro, rw, user-ro, user-rw)."""
        manifest = self._write_manifest(tmp_path, """
mounts:
  ro:
    - name: global_var_log
      host_path: /var/log
  rw:
    - name: product_docs
      host_path: /Users/greg/PRODUCT
  user-ro:
    - name: all_documents
      host_path: /Users/{username}/Documents
  user-rw:
    - name: user_data
      host_path: /Users/{username}/Data
""")
        result = _load_mount_host_paths(manifest_path=manifest)
        assert result == {
            "global_var_log": "/var/log",
            "product_docs": "/Users/greg/PRODUCT",
            "all_documents": "/Users/{username}/Documents",
            "user_data": "/Users/{username}/Data",
        }

    def test_missing_manifest_returns_empty(self, tmp_path):
        """Returns empty dict when manifest file doesn't exist."""
        nonexistent = tmp_path / "nonexistent" / "auto-generated-mounts.yaml"
        result = _load_mount_host_paths(manifest_path=nonexistent)
        assert result == {}

    def test_skips_entries_without_name(self, tmp_path):
        """Entries missing 'name' field are skipped."""
        manifest = self._write_manifest(tmp_path, """
mounts:
  ro:
    - host_path: /var/log
    - name: valid_mount
      host_path: /opt/data
""")
        result = _load_mount_host_paths(manifest_path=manifest)
        assert result == {"valid_mount": "/opt/data"}

    def test_skips_entries_without_host_path(self, tmp_path):
        """Entries missing 'host_path' field are skipped."""
        manifest = self._write_manifest(tmp_path, """
mounts:
  ro:
    - name: no_path_mount
    - name: valid_mount
      host_path: /opt/data
""")
        result = _load_mount_host_paths(manifest_path=manifest)
        assert result == {"valid_mount": "/opt/data"}

    def test_empty_manifest_returns_empty(self, tmp_path):
        """Empty manifest file returns empty dict."""
        manifest = self._write_manifest(tmp_path, "")
        result = _load_mount_host_paths(manifest_path=manifest)
        assert result == {}

    def test_malformed_yaml_returns_empty(self, tmp_path):
        """Malformed YAML returns empty dict gracefully."""
        manifest = self._write_manifest(tmp_path, "{{invalid yaml: [")
        result = _load_mount_host_paths(manifest_path=manifest)
        assert result == {}

    def test_skips_non_dict_entries(self, tmp_path):
        """Non-dict entries in mount lists are skipped."""
        manifest = self._write_manifest(tmp_path, """
mounts:
  ro:
    - just_a_string
    - 42
    - name: valid_mount
      host_path: /opt/data
""")
        result = _load_mount_host_paths(manifest_path=manifest)
        assert result == {"valid_mount": "/opt/data"}
