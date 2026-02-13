"""
Cross-tool path consistency tests.

Verifies that ALL Ag3ntum tools (Read, Write, Edit, Glob, Grep, LS, Bash)
resolve the same agent-provided paths to the same Docker filesystem paths.

Architecture summary:
- File tools (Read, Write, Edit, Glob, Grep, LS) all call
  validator.validate_path() → ValidatedPath.normalized (Docker path)
- Bash uses bubblewrap OS-level bind mounts to provide the same paths
  at the same locations inside the sandbox subprocess

These tests verify the property: given the same agent path, all tools
resolve to the same underlying file, through different mechanisms.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.path_validator import (
    Ag3ntumPathValidator,
    PathValidatorConfig,
    PathValidationError,
    configure_path_validator,
    cleanup_path_validator,
    get_path_validator,
    DEFAULT_BLOCKLIST,
    DEFAULT_READONLY_PREFIXES,
)
from src.core.sandbox import SandboxConfig, SandboxExecutor, SandboxMount

# Tool _impl imports
from tools.ag3ntum.ag3ntum_read.tool import _read_impl
from tools.ag3ntum.ag3ntum_write.tool import _write_impl
from tools.ag3ntum.ag3ntum_edit.tool import _edit_impl
from tools.ag3ntum.ag3ntum_glob.tool import _glob_impl
from tools.ag3ntum.ag3ntum_grep.tool import _grep_impl
from tools.ag3ntum.ag3ntum_ls.tool import _ls_impl

SESSION_ID = "test-cross-tool-session"
USERNAME = "testuser"


# ============================================================================
# Shared Fixtures
# ============================================================================


@pytest.fixture
def workspace_layout(tmp_path):
    """
    Create a realistic Docker filesystem layout for testing.

    Mimics the structure:
        /users/{username}/sessions/{session_id}/workspace/
        /users/{username}/ag3ntum/persistent/
        /mounts/datasets/         (global RO mount)
        /mounts/shared/           (global RW mount)
        /mounts/user_docs/        (per-user RO mount)
        /mounts/paths/_var_log/   (original-path RO mount)
    """
    # Session workspace
    workspace = tmp_path / "users" / USERNAME / "sessions" / SESSION_ID / "workspace"
    workspace.mkdir(parents=True)

    # Create files in workspace
    (workspace / "file.txt").write_text("hello world")
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text("def main(): pass")
    subdir = workspace / "subdir" / "nested"
    subdir.mkdir(parents=True)
    (subdir / "deep.txt").write_text("deep file")

    # Skills / .claude directories (read-only in workspace)
    claude_dir = workspace / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text('{"theme": "dark"}')
    skills_dir = workspace / "skills"
    skills_dir.mkdir()
    (skills_dir / "script.py").write_text("# skill script")

    # Persistent storage
    persistent = tmp_path / "users" / USERNAME / "ag3ntum" / "persistent"
    persistent.mkdir(parents=True)
    (persistent / "data.txt").write_text("persistent data")
    (persistent / "subdir").mkdir()
    (persistent / "subdir" / "file.csv").write_text("a,b,c")

    # External mounts (flattened structure at /mounts/{name})
    datasets_mount = tmp_path / "mounts" / "datasets"
    datasets_mount.mkdir(parents=True)
    (datasets_mount / "data.csv").write_text("col1,col2")
    (datasets_mount / "subdir").mkdir()
    (datasets_mount / "subdir" / "nested.csv").write_text("x,y")

    shared_mount = tmp_path / "mounts" / "shared"
    shared_mount.mkdir(parents=True)
    (shared_mount / "output.txt").write_text("shared output")

    user_docs_mount = tmp_path / "mounts" / "user_docs"
    user_docs_mount.mkdir(parents=True)
    (user_docs_mount / "readme.md").write_text("# Docs")

    # Original-path mount: /var/log → /mounts/paths/_var_log
    var_log_mount = tmp_path / "mounts" / "paths" / "_var_log"
    var_log_mount.mkdir(parents=True)
    (var_log_mount / "syslog").write_text("log entry 1\nlog entry 2")
    (var_log_mount / "subdir").mkdir()
    (var_log_mount / "subdir" / "app.log").write_text("app log")

    return {
        "tmp_path": tmp_path,
        "workspace": workspace,
        "persistent": persistent,
        "datasets_mount": datasets_mount,
        "shared_mount": shared_mount,
        "user_docs_mount": user_docs_mount,
        "var_log_mount": var_log_mount,
    }


@pytest.fixture
def validator(workspace_layout):
    """Create a real PathValidator with known workspace, mounts, and original-path mounts."""
    layout = workspace_layout
    config = PathValidatorConfig(
        workspace_path=layout["workspace"],
        persistent_path=layout["persistent"],
        global_mounts_ro={"datasets": layout["datasets_mount"]},
        global_mounts_rw={"shared": layout["shared_mount"]},
        user_mounts_ro={"user_docs": layout["user_docs_mount"]},
        original_path_mounts_ro={"/var/log": layout["var_log_mount"]},
    )
    return Ag3ntumPathValidator(config)


@pytest.fixture
def configured_session(workspace_layout):
    """
    Register validator for SESSION_ID so tool _impl functions can find it.

    Yields the validator, then cleans up after the test.
    """
    layout = workspace_layout
    v = configure_path_validator(
        session_id=SESSION_ID,
        workspace_path=layout["workspace"],
        username=USERNAME,
        persistent_path=layout["persistent"],
        global_mounts_ro={"datasets": layout["datasets_mount"]},
        global_mounts_rw={"shared": layout["shared_mount"]},
        user_mounts_ro={"user_docs": layout["user_docs_mount"]},
        original_path_mounts_ro={"/var/log": layout["var_log_mount"]},
    )
    yield v
    cleanup_path_validator(SESSION_ID)


# ============================================================================
# 1. Workspace Relative Paths
# ============================================================================


class TestWorkspaceRelativePaths:
    """Verify that different agent path formats resolve to the same Docker path."""

    def test_dotslash_and_bare_resolve_same(self, validator, workspace_layout):
        """./file.txt and file.txt resolve to the same normalized path."""
        r1 = validator.validate_path("./file.txt", operation="read")
        r2 = validator.validate_path("file.txt", operation="read")
        assert r1.normalized == r2.normalized
        assert r1.normalized == (workspace_layout["workspace"] / "file.txt").resolve()

    def test_absolute_workspace_resolves_same(self, validator, workspace_layout):
        """/workspace/file.txt resolves to the same path as ./file.txt."""
        r1 = validator.validate_path("/workspace/file.txt", operation="read")
        r2 = validator.validate_path("./file.txt", operation="read")
        assert r1.normalized == r2.normalized

    def test_nested_relative_path(self, validator, workspace_layout):
        """./subdir/nested/deep.txt resolves correctly."""
        r1 = validator.validate_path("./subdir/nested/deep.txt", operation="read")
        r2 = validator.validate_path("subdir/nested/deep.txt", operation="read")
        r3 = validator.validate_path("/workspace/subdir/nested/deep.txt", operation="read")
        assert r1.normalized == r2.normalized == r3.normalized
        expected = (workspace_layout["workspace"] / "subdir" / "nested" / "deep.txt").resolve()
        assert r1.normalized == expected

    def test_current_dir_resolves_to_workspace(self, validator, workspace_layout):
        """'.' resolves to workspace directory."""
        r = validator.validate_path(".", operation="list", allow_directory=True)
        assert r.normalized == workspace_layout["workspace"].resolve()

    @pytest.mark.asyncio
    async def test_tools_use_same_normalized_path(self, configured_session, workspace_layout):
        """Read, Glob, Grep, LS all resolve ./file.txt to the same Docker path."""
        ws = workspace_layout["workspace"]

        # Patch sessions.chown_to_session_user to be a no-op (no sandbox user in test)
        with patch("tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled", return_value=False):
            read_result = await _read_impl({"file_path": "./file.txt"}, session_id=SESSION_ID)
        assert "is_error" not in read_result or not read_result["is_error"]
        assert "hello world" in read_result["content"][0]["text"]

        # Glob should find file.txt
        glob_result = await _glob_impl({"pattern": "file.txt"}, session_id=SESSION_ID)
        assert "is_error" not in glob_result or not glob_result["is_error"]
        assert "file.txt" in glob_result["content"][0]["text"]

        # Grep should find content in file.txt
        grep_result = await _grep_impl(
            {"pattern": "hello", "path": ".", "include": "file.txt"}, session_id=SESSION_ID
        )
        assert "is_error" not in grep_result or not grep_result["is_error"]
        assert "hello" in grep_result["content"][0]["text"]


# ============================================================================
# 2. Persistent Storage Paths
# ============================================================================


class TestPersistentStoragePaths:
    """Verify persistent storage paths resolve consistently across tools."""

    def test_persistent_relative_formats(self, validator, workspace_layout):
        """persistent/data.txt, ./persistent/data.txt, /workspace/persistent/data.txt all resolve same."""
        r1 = validator.validate_path("persistent/data.txt", operation="read")
        r2 = validator.validate_path("./persistent/data.txt", operation="read")
        r3 = validator.validate_path("/workspace/persistent/data.txt", operation="read")
        assert r1.normalized == r2.normalized == r3.normalized
        expected = (workspace_layout["persistent"] / "data.txt").resolve()
        assert r1.normalized == expected

    def test_persistent_nested_path(self, validator, workspace_layout):
        """persistent/subdir/file.csv resolves correctly."""
        r = validator.validate_path("persistent/subdir/file.csv", operation="read")
        expected = (workspace_layout["persistent"] / "subdir" / "file.csv").resolve()
        assert r.normalized == expected

    def test_persistent_directory_listing(self, validator, workspace_layout):
        """persistent/ directory resolves for listing."""
        r = validator.validate_path("persistent", operation="list", allow_directory=True)
        expected = workspace_layout["persistent"].resolve()
        assert r.normalized == expected

    def test_persistent_is_writable(self, validator):
        """persistent/ is a read-write area."""
        r = validator.validate_path("persistent/data.txt", operation="write")
        assert not r.is_readonly


# ============================================================================
# 3. External Mount Paths
# ============================================================================


class TestExternalMountPaths:
    """Verify external mounts resolve consistently across tools."""

    def test_global_ro_mount_resolves(self, validator, workspace_layout):
        """external/ro/datasets/data.csv → /mounts/datasets/data.csv."""
        r = validator.validate_path("external/ro/datasets/data.csv", operation="read")
        expected = (workspace_layout["datasets_mount"] / "data.csv").resolve()
        assert r.normalized == expected

    def test_global_ro_mount_nested(self, validator, workspace_layout):
        """external/ro/datasets/subdir/nested.csv resolves correctly."""
        r = validator.validate_path("external/ro/datasets/subdir/nested.csv", operation="read")
        expected = (workspace_layout["datasets_mount"] / "subdir" / "nested.csv").resolve()
        assert r.normalized == expected

    def test_global_rw_mount_resolves(self, validator, workspace_layout):
        """external/rw/shared/output.txt → /mounts/shared/output.txt."""
        r = validator.validate_path("external/rw/shared/output.txt", operation="read")
        expected = (workspace_layout["shared_mount"] / "output.txt").resolve()
        assert r.normalized == expected

    def test_user_ro_mount_resolves(self, validator, workspace_layout):
        """external/user-ro/user_docs/readme.md → /mounts/user_docs/readme.md."""
        r = validator.validate_path("external/user-ro/user_docs/readme.md", operation="read")
        expected = (workspace_layout["user_docs_mount"] / "readme.md").resolve()
        assert r.normalized == expected

    def test_ro_mount_write_blocked(self, validator):
        """Write to external/ro/* is blocked."""
        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_path("external/ro/datasets/data.csv", operation="write")
        assert "read-only" in str(exc_info.value).lower() or "BLOCKLIST" in str(exc_info.value)


# ============================================================================
# 4. Original-Path Mounts
# ============================================================================


class TestOriginalPathMounts:
    """Verify original-path mounts (e.g., /var/log) resolve consistently."""

    def test_original_path_file_resolves(self, validator, workspace_layout):
        """/var/log/syslog → /mounts/paths/_var_log/syslog."""
        r = validator.validate_path("/var/log/syslog", operation="read")
        expected = (workspace_layout["var_log_mount"] / "syslog").resolve()
        assert r.normalized == expected

    def test_original_path_directory_resolves(self, validator, workspace_layout):
        """/var/log/ directory listing."""
        r = validator.validate_path("/var/log", operation="list", allow_directory=True)
        expected = workspace_layout["var_log_mount"].resolve()
        assert r.normalized == expected

    def test_original_path_nested(self, validator, workspace_layout):
        """/var/log/subdir/app.log resolves correctly."""
        r = validator.validate_path("/var/log/subdir/app.log", operation="read")
        expected = (workspace_layout["var_log_mount"] / "subdir" / "app.log").resolve()
        assert r.normalized == expected

    def test_original_path_ro_write_blocked(self, validator):
        """Write to read-only original-path mount is blocked."""
        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_path("/var/log/syslog", operation="write")
        assert "read-only" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_read_tool_reads_original_path(self, configured_session):
        """Read tool can read /var/log/syslog via original-path mount."""
        with patch("tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled", return_value=False):
            result = await _read_impl({"file_path": "/var/log/syslog"}, session_id=SESSION_ID)
        assert "is_error" not in result or not result["is_error"]
        assert "log entry" in result["content"][0]["text"]


# ============================================================================
# 5. Path Traversal Blocking
# ============================================================================


class TestPathTraversalBlocking:
    """Verify path traversal attacks are blocked identically by all tool paths."""

    def test_parent_traversal_blocked(self, validator):
        """../../../etc/passwd is blocked."""
        with pytest.raises(PathValidationError):
            validator.validate_path("../../../etc/passwd", operation="read")

    def test_workspace_traversal_blocked(self, validator):
        """/workspace/../../../etc/shadow is blocked."""
        with pytest.raises(PathValidationError):
            validator.validate_path("/workspace/../../../etc/shadow", operation="read")

    def test_external_mount_traversal_blocked(self, validator):
        """Traversal through a known mount is blocked by mount boundary check."""
        # Use a known mount name (datasets), then traverse out of the mount dir
        with pytest.raises(PathValidationError):
            validator.validate_path(
                "external/ro/datasets/../../../../../../../etc/passwd", operation="read"
            )

    def test_absolute_escape_blocked(self, validator):
        """/etc/passwd (outside any mount) is blocked."""
        with pytest.raises(PathValidationError):
            validator.validate_path("/etc/passwd", operation="read")

    @pytest.mark.asyncio
    async def test_all_tools_block_traversal(self, configured_session):
        """Read, Write, Edit, Glob, Grep, LS all block path traversal."""
        traversal_path = "../../../etc/passwd"

        # Read
        with patch("tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled", return_value=False):
            result = await _read_impl({"file_path": traversal_path}, session_id=SESSION_ID)
        assert result.get("is_error") is True

        # Write
        with patch("tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled", return_value=False), \
             patch("tools.ag3ntum.ag3ntum_write.tool.chown_to_session_user"):
            result = await _write_impl(SESSION_ID, traversal_path, "hack")
        assert result.get("is_error") is True

        # Edit
        result = await _edit_impl(
            {"file_path": traversal_path, "old_string": "x", "new_string": "y"},
            session_id=SESSION_ID,
        )
        assert result.get("is_error") is True

        # Glob
        result = await _glob_impl(
            {"pattern": "*", "path": traversal_path}, session_id=SESSION_ID
        )
        assert result.get("is_error") is True

        # Grep
        result = await _grep_impl(
            {"pattern": "root", "path": traversal_path}, session_id=SESSION_ID
        )
        assert result.get("is_error") is True

        # LS
        result = await _ls_impl({"path": traversal_path}, session_id=SESSION_ID)
        assert result.get("is_error") is True


# ============================================================================
# 6. Blocklist Enforcement
# ============================================================================


class TestBlocklistEnforcement:
    """Verify blocklist patterns are enforced consistently."""

    def test_dotenv_blocked(self, validator, workspace_layout):
        """.env file is blocked for read."""
        # Create a .env file in workspace
        env_file = workspace_layout["workspace"] / ".env"
        env_file.write_text("SECRET=123")
        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_path(".env", operation="read")
        assert "BLOCKLIST" in exc_info.value.reason

    def test_pem_file_blocked(self, validator, workspace_layout):
        """*.pem file is blocked."""
        pem_file = workspace_layout["workspace"] / "server.pem"
        pem_file.write_text("-----BEGIN CERTIFICATE-----")
        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_path("server.pem", operation="read")
        assert "BLOCKLIST" in exc_info.value.reason

    def test_p12_file_blocked(self, validator, workspace_layout):
        """*.p12 file is blocked."""
        p12_file = workspace_layout["workspace"] / "cert.p12"
        p12_file.write_bytes(b"\x00")
        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_path("cert.p12", operation="read")
        assert "BLOCKLIST" in exc_info.value.reason

    def test_pyc_file_blocked(self, validator, workspace_layout):
        """__pycache__/*.pyc is blocked."""
        pycache = workspace_layout["workspace"] / "__pycache__"
        pycache.mkdir()
        (pycache / "module.pyc").write_bytes(b"\x00")
        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_path("__pycache__/module.pyc", operation="read")
        assert "BLOCKLIST" in exc_info.value.reason


# ============================================================================
# 7. Read-Only Enforcement
# ============================================================================


class TestReadOnlyEnforcement:
    """Verify read-only paths allow reads but block writes across all tools."""

    def test_claude_dir_read_allowed(self, validator):
        """.claude/settings.json can be read."""
        r = validator.validate_path(".claude/settings.json", operation="read")
        assert r.is_readonly

    def test_claude_dir_write_blocked(self, validator):
        """.claude/settings.json cannot be written."""
        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_path(".claude/settings.json", operation="write")
        assert "read-only" in str(exc_info.value).lower()

    def test_claude_dir_edit_blocked(self, validator):
        """.claude/settings.json cannot be edited."""
        with pytest.raises(PathValidationError) as exc_info:
            validator.validate_path(".claude/settings.json", operation="edit")
        assert "read-only" in str(exc_info.value).lower()

    def test_skills_read_allowed(self, validator):
        """skills/script.py can be read."""
        r = validator.validate_path("skills/script.py", operation="read")
        assert r.is_readonly

    def test_skills_write_blocked(self, validator):
        """skills/script.py cannot be written."""
        with pytest.raises(PathValidationError):
            validator.validate_path("skills/script.py", operation="write")

    def test_external_ro_read_allowed_write_blocked(self, validator):
        """external/ro mount: read allowed, write blocked."""
        r = validator.validate_path("external/ro/datasets/data.csv", operation="read")
        assert r.is_readonly

        with pytest.raises(PathValidationError):
            validator.validate_path("external/ro/datasets/data.csv", operation="write")

    def test_user_ro_read_allowed_write_blocked(self, validator):
        """external/user-ro mount: read allowed, write blocked."""
        r = validator.validate_path("external/user-ro/user_docs/readme.md", operation="read")
        assert r.is_readonly

        with pytest.raises(PathValidationError):
            validator.validate_path("external/user-ro/user_docs/readme.md", operation="write")


# ============================================================================
# 8. Display Path Consistency
# ============================================================================


class TestDisplayPathConsistency:
    """Verify docker_to_display_path returns consistent paths for all tools."""

    def test_workspace_display_path(self, validator, workspace_layout):
        """Workspace file → workspace-relative display path."""
        docker_path = workspace_layout["workspace"] / "src" / "main.py"
        display = validator.docker_to_display_path(docker_path)
        assert display == "src/main.py"

    def test_persistent_display_path(self, validator, workspace_layout):
        """Persistent file → persistent/... display path."""
        docker_path = workspace_layout["persistent"] / "data.txt"
        display = validator.docker_to_display_path(docker_path)
        assert display == "persistent/data.txt"

    def test_global_ro_mount_display_path(self, validator, workspace_layout):
        """Global RO mount file → external/ro/name/... display path."""
        docker_path = workspace_layout["datasets_mount"] / "data.csv"
        display = validator.docker_to_display_path(docker_path)
        assert display == "external/ro/datasets/data.csv"

    def test_global_rw_mount_display_path(self, validator, workspace_layout):
        """Global RW mount file → external/rw/name/... display path."""
        docker_path = workspace_layout["shared_mount"] / "output.txt"
        display = validator.docker_to_display_path(docker_path)
        assert display == "external/rw/shared/output.txt"

    def test_user_ro_mount_display_path(self, validator, workspace_layout):
        """Per-user RO mount → external/user-ro/name/... display path."""
        docker_path = workspace_layout["user_docs_mount"] / "readme.md"
        display = validator.docker_to_display_path(docker_path)
        assert display == "external/user-ro/user_docs/readme.md"

    def test_original_path_mount_display_path(self, validator, workspace_layout):
        """Original-path mount → original path display."""
        docker_path = workspace_layout["var_log_mount"] / "syslog"
        display = validator.docker_to_display_path(docker_path)
        assert display == "/var/log/syslog"

    def test_original_path_mount_nested_display_path(self, validator, workspace_layout):
        """Nested original-path mount → correct display path."""
        docker_path = workspace_layout["var_log_mount"] / "subdir" / "app.log"
        display = validator.docker_to_display_path(docker_path)
        assert display == "/var/log/subdir/app.log"

    def test_display_path_roundtrip(self, validator, workspace_layout):
        """validate_path → normalized → docker_to_display_path produces sensible result."""
        validated = validator.validate_path("src/main.py", operation="read")
        display = validator.docker_to_display_path(validated.normalized)
        assert display == "src/main.py"


# ============================================================================
# 9. Bash Path Equivalence
# ============================================================================


class TestBashPathEquivalence:
    """
    Verify SandboxExecutor.build_bwrap_command() includes bind mounts that
    match what PathValidator resolves for file tools.

    The key property: if PathValidator resolves /var/log/syslog →
    /mounts/paths/_var_log/syslog, then bwrap must mount
    /mounts/paths/_var_log → /var/log (so scripts inside bwrap see /var/log/syslog).
    """

    @pytest.fixture
    def sandbox_executor(self, workspace_layout):
        """Create a SandboxExecutor with matching mount configuration."""
        layout = workspace_layout
        config = SandboxConfig(
            enabled=True,
            bwrap_path="bwrap",
            use_tmpfs_root=False,
            static_mounts={},
            session_mounts={
                "workspace": SandboxMount(
                    source=str(layout["workspace"]),
                    target="/workspace",
                    mode="rw",
                ),
            },
            dynamic_mounts=[
                SandboxMount(
                    source=str(layout["datasets_mount"]),
                    target=str(layout["datasets_mount"]),
                    mode="ro",
                ),
                SandboxMount(
                    source=str(layout["shared_mount"]),
                    target=str(layout["shared_mount"]),
                    mode="rw",
                ),
            ],
            original_path_mounts=[
                SandboxMount(
                    source=str(layout["var_log_mount"]),
                    target="/var/log",
                    mode="ro",
                ),
            ],
        )
        return SandboxExecutor(config, linux_uid=59990, linux_gid=59990)

    def test_workspace_mount_present(self, sandbox_executor, workspace_layout):
        """Bwrap command includes workspace bind mount."""
        cmd = sandbox_executor.build_bwrap_command(
            ["echo", "test"], allow_network=False
        )
        cmd_str = " ".join(cmd)
        ws_path = str(workspace_layout["workspace"])
        assert ws_path in cmd_str
        assert "/workspace" in cmd_str

    def test_original_path_mount_present(self, sandbox_executor, workspace_layout):
        """Bwrap command includes original-path mount: /mounts/paths/_var_log → /var/log."""
        cmd = sandbox_executor.build_bwrap_command(
            ["cat", "/var/log/syslog"], allow_network=False
        )
        cmd_str = " ".join(cmd)
        mount_source = str(workspace_layout["var_log_mount"])
        assert mount_source in cmd_str
        assert "/var/log" in cmd_str

    def test_original_path_mount_is_readonly(self, sandbox_executor, workspace_layout):
        """Original-path RO mount uses --ro-bind (not --bind)."""
        cmd = sandbox_executor.build_bwrap_command(
            ["ls", "/var/log"], allow_network=False
        )
        mount_source = str(workspace_layout["var_log_mount"])
        # Find the index of the mount source in the command
        for i, arg in enumerate(cmd):
            if arg == mount_source:
                # The flag before source should be --ro-bind
                assert cmd[i - 1] == "--ro-bind", (
                    f"Expected --ro-bind before {mount_source}, "
                    f"got {cmd[i - 1]}"
                )
                break
        else:
            pytest.fail(f"Mount source {mount_source} not found in bwrap command")

    def test_rw_mount_uses_bind(self, sandbox_executor, workspace_layout):
        """RW mounts use --bind (not --ro-bind)."""
        cmd = sandbox_executor.build_bwrap_command(
            ["ls", "/workspace"], allow_network=False
        )
        ws_path = str(workspace_layout["workspace"])
        for i, arg in enumerate(cmd):
            if arg == ws_path:
                assert cmd[i - 1] == "--bind", (
                    f"Expected --bind before {ws_path}, got {cmd[i - 1]}"
                )
                break
        else:
            pytest.fail(f"Workspace path {ws_path} not found in bwrap command")

    def test_dynamic_ro_mount_present(self, sandbox_executor, workspace_layout):
        """Bwrap command includes dynamic RO mounts with --ro-bind."""
        cmd = sandbox_executor.build_bwrap_command(
            ["ls"], allow_network=False
        )
        datasets_source = str(workspace_layout["datasets_mount"])
        for i, arg in enumerate(cmd):
            if arg == datasets_source:
                assert cmd[i - 1] == "--ro-bind"
                break
        else:
            pytest.fail(f"Datasets mount {datasets_source} not found in bwrap command")


# ============================================================================
# 10. Edge Cases
# ============================================================================


class TestEdgeCases:
    """Verify edge cases are handled consistently across tools."""

    def test_unicode_filename(self, validator, workspace_layout):
        """Unicode filenames resolve consistently."""
        # Create file with unicode name
        unicode_file = workspace_layout["workspace"] / "données.txt"
        unicode_file.write_text("data")
        r1 = validator.validate_path("données.txt", operation="read")
        r2 = validator.validate_path("./données.txt", operation="read")
        assert r1.normalized == r2.normalized
        assert r1.normalized == unicode_file.resolve()

    def test_deeply_nested_path(self, validator, workspace_layout):
        """Deeply nested paths resolve correctly."""
        nested = workspace_layout["workspace"]
        for segment in "a/b/c/d/e/f/g".split("/"):
            nested = nested / segment
        nested.mkdir(parents=True)
        (nested / "file.txt").write_text("deep")

        r1 = validator.validate_path("a/b/c/d/e/f/g/file.txt", operation="read")
        r2 = validator.validate_path("./a/b/c/d/e/f/g/file.txt", operation="read")
        r3 = validator.validate_path("/workspace/a/b/c/d/e/f/g/file.txt", operation="read")
        assert r1.normalized == r2.normalized == r3.normalized
        assert r1.normalized == (nested / "file.txt").resolve()

    def test_path_with_spaces(self, validator, workspace_layout):
        """Paths with spaces resolve consistently."""
        spaced = workspace_layout["workspace"] / "my dir"
        spaced.mkdir()
        (spaced / "my file.txt").write_text("data")

        r1 = validator.validate_path("my dir/my file.txt", operation="read")
        r2 = validator.validate_path("./my dir/my file.txt", operation="read")
        assert r1.normalized == r2.normalized

    def test_case_sensitivity_preserved(self, validator, workspace_layout):
        """Case is preserved in path resolution."""
        ws = workspace_layout["workspace"]
        (ws / "CamelCase.txt").write_text("data")

        r = validator.validate_path("CamelCase.txt", operation="read")
        assert "CamelCase.txt" in str(r.normalized)

    def test_multiple_mount_types_independent(self, validator, workspace_layout):
        """Workspace, persistent, external, and original-path mounts resolve independently."""
        # These should all resolve to different Docker paths
        ws_result = validator.validate_path("file.txt", operation="read")
        persistent_result = validator.validate_path("persistent/data.txt", operation="read")
        external_result = validator.validate_path("external/ro/datasets/data.csv", operation="read")
        original_result = validator.validate_path("/var/log/syslog", operation="read")

        # All should be different paths
        paths = {
            ws_result.normalized,
            persistent_result.normalized,
            external_result.normalized,
            original_result.normalized,
        }
        assert len(paths) == 4, "All mount types should resolve to different Docker paths"

    @pytest.mark.asyncio
    async def test_write_then_read_same_file(self, configured_session, workspace_layout):
        """Write via _write_impl, then Read via _read_impl — same file."""
        test_path = "test_write_read.txt"
        test_content = "cross-tool test content"

        # Write
        with patch("tools.ag3ntum.ag3ntum_write.tool.is_scanner_enabled", return_value=False), \
             patch("tools.ag3ntum.ag3ntum_write.tool.chown_to_session_user"), \
             patch("tools.ag3ntum.ag3ntum_write.tool.get_resolver_for_session", return_value=None):
            write_result = await _write_impl(SESSION_ID, test_path, test_content)
        assert "is_error" not in write_result or not write_result["is_error"]

        # Read back
        with patch("tools.ag3ntum.ag3ntum_read.tool.is_scanner_enabled", return_value=False):
            read_result = await _read_impl({"file_path": test_path}, session_id=SESSION_ID)
        assert "is_error" not in read_result or not read_result["is_error"]
        assert test_content in read_result["content"][0]["text"]
