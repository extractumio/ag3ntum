"""
Tests for permission_profiles.py.

Tests the PermissionManager and related models:
- Profile loading (YAML and JSON)
- Tool enablement/disablement
- Session context and workspace sandboxing
- Permission checking (allow/deny rules)
- Profile validation and error handling
- Pydantic model validation
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.permission_profiles import (
    PermissionManager,
    PermissionProfile,
    ExtendedToolsConfig,
    ExtendedPermissionRules,
    SessionWorkspaceConfig,
    CheckpointingConfig,
    ProfileNotFoundError,
)
from src.core.permission_config import PermissionMode


class TestExtendedToolsConfig:
    """Tests for ExtendedToolsConfig model."""

    @pytest.mark.unit
    def test_default_empty_lists(self):
        """Test that defaults are empty lists."""
        config = ExtendedToolsConfig()
        assert config.enabled == []
        assert config.disabled == []
        assert config.permission_checked == []

    @pytest.mark.unit
    def test_with_tools(self):
        """Test creating with tool lists."""
        config = ExtendedToolsConfig(
            enabled=["Read", "Write", "Edit"],
            disabled=["Bash"],
            permission_checked=["Write"],
        )
        assert "Read" in config.enabled
        assert "Bash" in config.disabled
        assert "Write" in config.permission_checked

    @pytest.mark.unit
    def test_none_converts_to_empty_list(self):
        """Test that None values are converted to empty lists."""
        config = ExtendedToolsConfig(
            enabled=None,
            disabled=None,
            permission_checked=None,
        )
        assert config.enabled == []
        assert config.disabled == []
        assert config.permission_checked == []


class TestExtendedPermissionRules:
    """Tests for ExtendedPermissionRules model."""

    @pytest.mark.unit
    def test_default_empty_lists(self):
        """Test that defaults are empty lists."""
        rules = ExtendedPermissionRules()
        assert rules.allow == []
        assert rules.deny == []
        assert rules.ask == []
        assert rules.allowed_dirs == []

    @pytest.mark.unit
    def test_with_rules(self):
        """Test creating with rules."""
        rules = ExtendedPermissionRules(
            allow=["Read(*)", "Write(./workspace/*)"],
            deny=["Bash(rm -rf *)"],
            ask=["Write(/etc/*)"],
            allowed_dirs=["./workspace"],
        )
        assert len(rules.allow) == 2
        assert len(rules.deny) == 1
        assert len(rules.ask) == 1
        assert len(rules.allowed_dirs) == 1

    @pytest.mark.unit
    def test_none_converts_to_empty_list(self):
        """Test that None values are converted to empty lists."""
        rules = ExtendedPermissionRules(
            allow=None, deny=None, ask=None, allowed_dirs=None
        )
        assert rules.allow == []
        assert rules.deny == []
        assert rules.ask == []
        assert rules.allowed_dirs == []


class TestSessionWorkspaceConfig:
    """Tests for SessionWorkspaceConfig model."""

    @pytest.mark.unit
    def test_default_values(self):
        """Test default values."""
        config = SessionWorkspaceConfig()
        assert config.description == ""
        assert config.allow == []
        assert config.deny == []
        assert config.allowed_dirs == []

    @pytest.mark.unit
    def test_with_workspace_placeholder(self):
        """Test config with {workspace} placeholder."""
        config = SessionWorkspaceConfig(
            description="Session workspace rules",
            allow=["Read({workspace}/*)", "Write({workspace}/*)"],
            deny=["Bash(rm -rf {workspace})"],
            allowed_dirs=["{workspace}"],
        )
        assert "{workspace}" in config.allow[0]
        assert "{workspace}" in config.deny[0]
        assert "{workspace}" in config.allowed_dirs[0]


class TestCheckpointingConfig:
    """Tests for CheckpointingConfig model."""

    @pytest.mark.unit
    def test_default_tools(self):
        """Test default auto checkpoint tools."""
        config = CheckpointingConfig()
        assert "Write" in config.auto_checkpoint_tools
        assert "Edit" in config.auto_checkpoint_tools

    @pytest.mark.unit
    def test_custom_tools(self):
        """Test custom auto checkpoint tools."""
        config = CheckpointingConfig(
            auto_checkpoint_tools=["Write", "Edit", "Bash"]
        )
        assert len(config.auto_checkpoint_tools) == 3

    @pytest.mark.unit
    def test_none_gives_defaults(self):
        """Test that None gives default tools."""
        config = CheckpointingConfig(auto_checkpoint_tools=None)
        assert "Write" in config.auto_checkpoint_tools
        assert "Edit" in config.auto_checkpoint_tools


class TestPermissionProfile:
    """Tests for PermissionProfile model."""

    @pytest.mark.unit
    def test_minimal_profile(self):
        """Test creating a profile with minimal fields."""
        profile = PermissionProfile(name="test")
        assert profile.name == "test"
        assert profile.description == ""
        assert profile.defaultMode == PermissionMode.DEFAULT
        assert profile.tools.enabled == []
        assert profile.permissions is None
        assert profile.session_workspace is None
        assert profile.checkpointing is None
        assert profile.sandbox is None

    @pytest.mark.unit
    def test_full_profile(self):
        """Test creating a profile with all fields."""
        profile = PermissionProfile(
            name="user",
            description="User profile",
            defaultMode=PermissionMode.DEFAULT,
            tools=ExtendedToolsConfig(
                enabled=["Read", "Write"],
                disabled=["Bash"],
            ),
            permissions=ExtendedPermissionRules(
                allow=["Read(*)"],
                deny=["Write(/etc/*)"],
            ),
            session_workspace=SessionWorkspaceConfig(
                allow=["Read({workspace}/*)"],
            ),
            checkpointing=CheckpointingConfig(
                auto_checkpoint_tools=["Write"],
            ),
        )
        assert profile.name == "user"
        assert "Read" in profile.tools.enabled
        assert "Bash" in profile.tools.disabled
        assert len(profile.permissions.allow) == 1


class TestPermissionManagerLoading:
    """Tests for PermissionManager profile loading."""

    @pytest.fixture
    def yaml_profile(self, tmp_path):
        """Create a YAML profile file."""
        profile_data = {
            "name": "test-profile",
            "description": "Test profile for unit testing",
            "defaultMode": "default",
            "tools": {
                "enabled": ["Read", "Write", "Edit", "Bash"],
                "disabled": ["WebFetch"],
                "permission_checked": ["Bash"],
            },
            "permissions": {
                "allow": ["Read(*)", "Write(./workspace/*)"],
                "deny": ["Bash(rm -rf *)"],
                "ask": [],
                "allowed_dirs": ["./workspace"],
            },
            "session_workspace": {
                "description": "Session rules",
                "allow": ["Read({workspace}/*)", "Write({workspace}/*)"],
                "deny": [],
                "allowed_dirs": ["{workspace}"],
            },
            "checkpointing": {
                "auto_checkpoint_tools": ["Write", "Edit"],
            },
        }
        profile_file = tmp_path / "permissions.yaml"
        with open(profile_file, "w") as f:
            yaml.dump(profile_data, f)
        return profile_file

    @pytest.fixture
    def json_profile(self, tmp_path):
        """Create a JSON profile file."""
        profile_data = {
            "name": "json-profile",
            "description": "JSON format profile",
            "defaultMode": "default",
            "tools": {
                "enabled": ["Read", "Write"],
                "disabled": [],
            },
        }
        profile_file = tmp_path / "permissions.json"
        with open(profile_file, "w") as f:
            json.dump(profile_data, f)
        return profile_file

    @pytest.mark.unit
    def test_load_yaml_profile(self, yaml_profile):
        """Test loading a YAML profile."""
        manager = PermissionManager(profile_path=yaml_profile)
        profile = manager.profile

        assert profile.name == "test-profile"
        assert "Read" in profile.tools.enabled
        assert "WebFetch" in profile.tools.disabled

    @pytest.mark.unit
    def test_load_json_profile(self, json_profile):
        """Test loading a JSON profile."""
        manager = PermissionManager(profile_path=json_profile)
        profile = manager.profile

        assert profile.name == "json-profile"
        assert "Read" in profile.tools.enabled

    @pytest.mark.unit
    def test_profile_not_found_error(self, tmp_path):
        """Test that missing profile raises ProfileNotFoundError."""
        missing_path = tmp_path / "nonexistent.yaml"
        manager = PermissionManager(profile_path=missing_path)

        with pytest.raises(ProfileNotFoundError):
            _ = manager.profile

    @pytest.mark.unit
    def test_invalid_yaml_raises_error(self, tmp_path):
        """Test that invalid YAML raises ValueError."""
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("{{invalid yaml:: [}")

        manager = PermissionManager(profile_path=bad_file)

        with pytest.raises(ValueError):
            _ = manager.profile


class TestPermissionManagerToolAccess:
    """Tests for tool enablement/disablement."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create a manager with a test profile."""
        profile_data = {
            "name": "test",
            "tools": {
                "enabled": ["Read", "Write", "Edit", "Bash", "Glob"],
                "disabled": ["WebFetch", "AskUserQuestion"],
                "permission_checked": ["Bash"],
            },
        }
        profile_file = tmp_path / "permissions.yaml"
        with open(profile_file, "w") as f:
            yaml.dump(profile_data, f)
        return PermissionManager(profile_path=profile_file)

    @pytest.mark.unit
    def test_get_enabled_tools(self, manager):
        """Test getting enabled tools (excluding disabled)."""
        enabled = manager.get_enabled_tools()
        assert "Read" in enabled
        assert "Write" in enabled
        assert "WebFetch" not in enabled
        assert "AskUserQuestion" not in enabled

    @pytest.mark.unit
    def test_get_disabled_tools(self, manager):
        """Test getting disabled tools."""
        disabled = manager.get_disabled_tools()
        assert "WebFetch" in disabled
        assert "AskUserQuestion" in disabled
        assert "Read" not in disabled

    @pytest.mark.unit
    def test_get_permission_checked_tools(self, manager):
        """Test getting permission-checked tools."""
        checked = manager.get_permission_checked_tools()
        assert "Bash" in checked
        assert "Read" not in checked

    @pytest.mark.unit
    def test_get_pre_approved_tools(self, manager):
        """Test getting pre-approved tools (enabled - permission_checked - disabled)."""
        pre_approved = manager.get_pre_approved_tools()
        assert "Read" in pre_approved
        assert "Write" in pre_approved
        assert "Bash" not in pre_approved  # permission_checked
        assert "WebFetch" not in pre_approved  # disabled


class TestPermissionManagerSessionContext:
    """Tests for session context and workspace sandboxing."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create a manager with session workspace config."""
        profile_data = {
            "name": "test",
            "tools": {
                "enabled": ["Read", "Write"],
                "disabled": [],
            },
            "permissions": {
                "allow": ["Read(*)"],
                "deny": [],
            },
            "session_workspace": {
                "description": "Session workspace rules",
                "allow": [
                    "Read({workspace}/*)",
                    "Write({workspace}/*)",
                ],
                "deny": [
                    "Bash(rm -rf {workspace})",
                ],
                "allowed_dirs": ["{workspace}"],
            },
        }
        profile_file = tmp_path / "permissions.yaml"
        with open(profile_file, "w") as f:
            yaml.dump(profile_data, f)
        return PermissionManager(profile_path=profile_file)

    @pytest.mark.unit
    def test_set_session_context(self, manager):
        """Test setting session context creates session-specific profile."""
        manager.set_session_context(
            session_id="test-session",
            workspace_path="./sessions/test-session/workspace",
        )

        profile = manager.active_profile
        assert "test-session" in profile.name
        assert profile.permissions is not None

    @pytest.mark.unit
    def test_session_workspace_placeholder_replaced(self, manager):
        """Test that {workspace} placeholder is replaced with actual path."""
        workspace = "./sessions/my-session/workspace"
        manager.set_session_context(
            session_id="my-session",
            workspace_path=workspace,
        )

        profile = manager.active_profile
        assert profile.permissions is not None
        # Check that {workspace} was replaced
        for rule in profile.permissions.allow:
            assert "{workspace}" not in rule
            assert workspace in rule

    @pytest.mark.unit
    def test_clear_session_context(self, manager):
        """Test clearing session context reverts to base profile."""
        manager.set_session_context(
            session_id="test-session",
            workspace_path="./sessions/test-session/workspace",
        )
        assert "test-session" in manager.active_profile.name

        manager.clear_session_context()
        assert manager.active_profile.name == "test"

    @pytest.mark.unit
    def test_no_session_workspace_uses_base(self, tmp_path):
        """Test that missing session_workspace config uses base profile."""
        profile_data = {
            "name": "no-workspace",
            "tools": {"enabled": ["Read"], "disabled": []},
        }
        profile_file = tmp_path / "permissions.yaml"
        with open(profile_file, "w") as f:
            yaml.dump(profile_data, f)

        manager = PermissionManager(profile_path=profile_file)
        manager.set_session_context(
            session_id="test",
            workspace_path="./workspace",
        )

        # Should use base profile (no session-specific rules)
        assert manager.active_profile.name == "no-workspace"

    @pytest.mark.unit
    def test_session_allowed_dirs(self, manager):
        """Test that session context sets allowed_dirs correctly."""
        workspace = "./sessions/test/workspace"
        manager.set_session_context(
            session_id="test",
            workspace_path=workspace,
        )

        allowed_dirs = manager.get_allowed_dirs()
        assert workspace in allowed_dirs


class TestPermissionManagerProfileOperations:
    """Tests for profile save, reload, and validation."""

    @pytest.fixture
    def profile_file(self, tmp_path):
        """Create a profile file."""
        profile_data = {
            "name": "saveable",
            "tools": {"enabled": ["Read"], "disabled": []},
        }
        path = tmp_path / "permissions.yaml"
        with open(path, "w") as f:
            yaml.dump(profile_data, f)
        return path

    @pytest.mark.unit
    def test_save_profile_yaml(self, profile_file, tmp_path):
        """Test saving profile as YAML."""
        manager = PermissionManager(profile_path=profile_file)
        profile = manager.profile

        target = tmp_path / "saved.yaml"
        saved_path = manager.save_profile(profile, target_path=target)

        assert saved_path.exists()
        with open(saved_path) as f:
            data = yaml.safe_load(f)
        assert data["name"] == "saveable"

    @pytest.mark.unit
    def test_save_profile_json(self, profile_file, tmp_path):
        """Test saving profile as JSON."""
        manager = PermissionManager(profile_path=profile_file)
        profile = manager.profile

        target = tmp_path / "saved.json"
        saved_path = manager.save_profile(profile, target_path=target)

        assert saved_path.exists()
        with open(saved_path) as f:
            data = json.load(f)
        assert data["name"] == "saveable"

    @pytest.mark.unit
    def test_reload_profile(self, profile_file):
        """Test that reload_profile forces re-read from file."""
        manager = PermissionManager(profile_path=profile_file)
        _ = manager.profile  # First load

        # Modify the file
        with open(profile_file, "w") as f:
            yaml.dump({
                "name": "reloaded",
                "tools": {"enabled": ["Write"], "disabled": []},
            }, f)

        manager.reload_profile()
        assert manager.profile.name == "reloaded"

    @pytest.mark.unit
    def test_validate_profile_exists(self, profile_file):
        """Test validate_profile_exists with existing file."""
        manager = PermissionManager(profile_path=profile_file)
        path = manager.validate_profile_exists()
        assert path == profile_file

    @pytest.mark.unit
    def test_validate_profile_missing(self, tmp_path):
        """Test validate_profile_exists with missing file."""
        missing = tmp_path / "missing.yaml"
        manager = PermissionManager(profile_path=missing)

        with pytest.raises(ProfileNotFoundError):
            manager.validate_profile_exists()

    @pytest.mark.unit
    def test_activate_returns_profile(self, profile_file):
        """Test that activate returns the loaded profile."""
        manager = PermissionManager(profile_path=profile_file)
        profile = manager.activate()
        assert profile is not None
        assert profile.name == "saveable"

    @pytest.mark.unit
    def test_set_tracer(self, profile_file):
        """Test setting tracer on the manager."""
        manager = PermissionManager(profile_path=profile_file)
        mock_tracer = MagicMock()
        manager.set_tracer(mock_tracer)
        assert manager._tracer is mock_tracer


class TestPermissionManagerPatterns:
    """Tests for tool-specific pattern extraction."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create a manager with patterns."""
        profile_data = {
            "name": "patterns-test",
            "tools": {"enabled": ["Read", "Write", "Bash"], "disabled": []},
            "permissions": {
                "allow": [
                    "Read(./workspace/*)",
                    "Read(./config/*)",
                    "Write(./workspace/*)",
                    "Bash(ls *)",
                    "Bash(cat *)",
                ],
                "deny": [
                    "Bash(rm -rf *)",
                    "Write(/etc/*)",
                ],
            },
        }
        profile_file = tmp_path / "permissions.yaml"
        with open(profile_file, "w") as f:
            yaml.dump(profile_data, f)
        return PermissionManager(profile_path=profile_file)

    @pytest.mark.unit
    def test_get_allowed_patterns_for_tool(self, manager):
        """Test getting allowed patterns for a specific tool."""
        read_patterns = manager.get_allowed_patterns_for_tool("Read")
        assert len(read_patterns) == 2
        assert any("workspace" in p for p in read_patterns)

    @pytest.mark.unit
    def test_get_denied_patterns_for_tool(self, manager):
        """Test getting denied patterns for a specific tool."""
        bash_patterns = manager.get_denied_patterns_for_tool("Bash")
        assert len(bash_patterns) == 1
        assert any("rm -rf" in p for p in bash_patterns)

    @pytest.mark.unit
    def test_no_patterns_for_unknown_tool(self, manager):
        """Test that unknown tool returns empty list."""
        patterns = manager.get_allowed_patterns_for_tool("NonexistentTool")
        assert patterns == []

    @pytest.mark.unit
    def test_no_permissions_returns_empty(self, tmp_path):
        """Test that profile without permissions returns empty patterns."""
        profile_data = {
            "name": "no-perms",
            "tools": {"enabled": ["Read"], "disabled": []},
        }
        profile_file = tmp_path / "permissions.yaml"
        with open(profile_file, "w") as f:
            yaml.dump(profile_data, f)

        manager = PermissionManager(profile_path=profile_file)
        patterns = manager.get_allowed_patterns_for_tool("Read")
        assert patterns == []
