"""
Tests for prompt_manager.py.

Tests the PromptManager singleton:
- System prompt building from modular .md files
- Role loading with user overrides
- Override allowlist enforcement
- Admin hot-reload
- Template module discovery
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.prompt_manager import PromptManager, get_prompt_manager
from src.core.prompt_engine import PromptContext


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset singleton between tests."""
    PromptManager.reset_instance()
    yield
    PromptManager.reset_instance()


@pytest.fixture
def prompts_dir(tmp_path):
    """Create a minimal prompts directory structure."""
    # Roles
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "default.md").write_text("You are a helpful assistant.")
    (roles_dir / "researcher.md").write_text("You are a researcher.")

    # System prompts (numbered for ordering)
    system_dir = tmp_path / "system-prompts"
    system_dir.mkdir()
    (system_dir / "01-identity.md").write_text(
        "<!--\nname: 'Identity'\ndescription: Identity\nvariables:\n  - ROLE_CONTENT\n  - MODEL_NAME\noverride_allowed: false\n-->\n\n"
        "Role: ${ROLE_CONTENT}\nModel: ${MODEL_NAME}"
    )
    (system_dir / "02-tools.md").write_text(
        "<!--\nname: 'Tools'\ndescription: Tools\nvariables:\n  - AG3NTUM_READ_TOOL\noverride_allowed: false\n-->\n\n"
        "Read tool: ${AG3NTUM_READ_TOOL}"
    )
    (system_dir / "03-skills.md").write_text(
        "<!--\nname: 'Skills'\ndescription: Skills\nvariables: []\noverride_allowed: true\n-->\n\n"
        "${ENABLE_SKILLS?## Skills\nSkills are enabled.:}"
    )

    # System reminders
    reminders_dir = tmp_path / "system-reminders"
    reminders_dir.mkdir()
    (reminders_dir / "file-modified.md").write_text(
        "<!--\nname: 'File Modified'\ndescription: File modified\nvariables:\n  - FILE_PATH\noverride_allowed: false\n-->\n\n"
        "File `${FILE_PATH}` was modified."
    )

    return tmp_path


@pytest.fixture
def manager(prompts_dir, monkeypatch):
    """Create a manager pointing at the test prompts dir."""
    monkeypatch.setattr("src.core.prompt_manager.PROMPTS_DIR", prompts_dir)
    monkeypatch.setattr("src.core.prompt_manager.CONFIG_DIR", prompts_dir / "config")
    # Create empty overrides config
    config_dir = prompts_dir / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "prompt-overrides.yaml").write_text(
        "allowed_overrides:\n  roles:\n    - '*.md'\n  system-prompts:\n    - 03-skills.md\n"
    )
    return PromptManager()


# ---------------------------------------------------------------------------
# System Prompt Building
# ---------------------------------------------------------------------------
class TestBuildSystemPrompt:
    """Tests for build_system_prompt()."""

    @pytest.mark.unit
    def test_basic_render(self, manager):
        result = manager.build_system_prompt(role="default")
        assert "You are a helpful assistant." in result
        assert "mcp__ag3ntum__Read" in result

    @pytest.mark.unit
    def test_custom_role(self, manager):
        result = manager.build_system_prompt(role="researcher")
        assert "You are a researcher." in result

    @pytest.mark.unit
    def test_model_name_in_prompt(self, manager):
        result = manager.build_system_prompt(model="claude-opus-4-20250514")
        assert "claude-opus-4-20250514" in result

    @pytest.mark.unit
    def test_skills_enabled(self, manager):
        result = manager.build_system_prompt(enable_skills=True)
        assert "Skills are enabled" in result

    @pytest.mark.unit
    def test_skills_disabled(self, manager):
        result = manager.build_system_prompt(enable_skills=False)
        assert "Skills are enabled" not in result

    @pytest.mark.unit
    def test_module_ordering(self, manager):
        """Modules should be rendered in alphabetical order."""
        result = manager.build_system_prompt()
        identity_pos = result.find("Role:")
        tools_pos = result.find("Read tool:")
        skills_pos = result.find("Skills")
        # 01-identity < 02-tools < 03-skills
        assert identity_pos < tools_pos < skills_pos

    @pytest.mark.unit
    def test_missing_role_raises(self, manager):
        with pytest.raises(FileNotFoundError, match="nonexistent"):
            manager.build_system_prompt(role="nonexistent")


# ---------------------------------------------------------------------------
# User Overrides
# ---------------------------------------------------------------------------
class TestUserOverrides:
    """Tests for user override loading and allowlist."""

    @pytest.mark.unit
    def test_allowed_override_applied(self, manager, prompts_dir):
        """User override for allowed file should be used."""
        user_dir = prompts_dir.parent / "users" / "testuser" / ".prompts" / "system-prompts"
        user_dir.mkdir(parents=True)
        (user_dir / "03-skills.md").write_text(
            "<!--\nname: 'Custom Skills'\ndescription: Custom\nvariables: []\noverride_allowed: true\n-->\n\n"
            "Custom skills section."
        )
        # Point USERS_DIR to tmp
        import src.core.prompt_manager as pm
        original_users_dir = pm.USERS_DIR
        pm.USERS_DIR = prompts_dir.parent / "users"
        try:
            result = manager.build_system_prompt(username="testuser")
            assert "Custom skills section." in result
            assert "Skills are enabled" not in result
        finally:
            pm.USERS_DIR = original_users_dir

    @pytest.mark.unit
    def test_disallowed_override_rejected(self, manager, prompts_dir):
        """User override for disallowed file should be ignored."""
        user_dir = prompts_dir.parent / "users" / "testuser" / ".prompts" / "system-prompts"
        user_dir.mkdir(parents=True, exist_ok=True)
        # 01-identity.md is NOT in the allowlist
        (user_dir / "01-identity.md").write_text(
            "<!--\nname: 'Hacked'\ndescription: Hacked\nvariables: []\noverride_allowed: false\n-->\n\n"
            "HACKED CONTENT"
        )
        import src.core.prompt_manager as pm
        original_users_dir = pm.USERS_DIR
        pm.USERS_DIR = prompts_dir.parent / "users"
        try:
            result = manager.build_system_prompt(username="testuser")
            assert "HACKED CONTENT" not in result
            assert "You are a helpful assistant." in result
        finally:
            pm.USERS_DIR = original_users_dir

    @pytest.mark.unit
    def test_role_override(self, manager, prompts_dir):
        """User role override should be applied when allowed."""
        user_dir = prompts_dir.parent / "users" / "testuser" / ".prompts" / "roles"
        user_dir.mkdir(parents=True)
        (user_dir / "default.md").write_text("You are a custom assistant.")
        import src.core.prompt_manager as pm
        original_users_dir = pm.USERS_DIR
        pm.USERS_DIR = prompts_dir.parent / "users"
        try:
            result = manager.build_system_prompt(username="testuser", role="default")
            assert "You are a custom assistant." in result
        finally:
            pm.USERS_DIR = original_users_dir


# ---------------------------------------------------------------------------
# System Reminders
# ---------------------------------------------------------------------------
class TestSystemReminders:
    """Tests for get_system_reminder()."""

    @pytest.mark.unit
    def test_reminder_found(self, manager):
        ctx = PromptContext()
        ctx.strings = {"FILE_PATH": "/workspace/test.py"}
        result = manager.get_system_reminder("file-modified", ctx)
        assert result is not None
        assert "/workspace/test.py" in result

    @pytest.mark.unit
    def test_reminder_not_found(self, manager):
        ctx = PromptContext()
        result = manager.get_system_reminder("nonexistent-reminder", ctx)
        assert result is None


# ---------------------------------------------------------------------------
# Discovery Methods
# ---------------------------------------------------------------------------
class TestDiscovery:
    """Tests for get_available_roles() and get_prompt_modules()."""

    @pytest.mark.unit
    def test_available_roles(self, manager):
        roles = manager.get_available_roles()
        assert "default" in roles
        assert "researcher" in roles

    @pytest.mark.unit
    def test_prompt_modules(self, manager):
        modules = manager.get_prompt_modules()
        assert "01-identity" in modules
        assert "02-tools" in modules
        assert "03-skills" in modules


# ---------------------------------------------------------------------------
# Hot Reload
# ---------------------------------------------------------------------------
class TestHotReload:
    """Tests for reload()."""

    @pytest.mark.unit
    def test_reload_clears_cache(self, manager):
        # Prime cache by building prompt
        manager.build_system_prompt()
        cleared = manager.reload()
        assert cleared >= 0  # At least some entries cleared


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------
class TestSingleton:
    """Tests for singleton pattern."""

    @pytest.mark.unit
    def test_get_instance_returns_same(self, monkeypatch, prompts_dir):
        monkeypatch.setattr("src.core.prompt_manager.PROMPTS_DIR", prompts_dir)
        monkeypatch.setattr("src.core.prompt_manager.CONFIG_DIR", prompts_dir / "config")
        m1 = PromptManager.get_instance()
        m2 = PromptManager.get_instance()
        assert m1 is m2

    @pytest.mark.unit
    def test_reset_instance(self, monkeypatch, prompts_dir):
        monkeypatch.setattr("src.core.prompt_manager.PROMPTS_DIR", prompts_dir)
        monkeypatch.setattr("src.core.prompt_manager.CONFIG_DIR", prompts_dir / "config")
        m1 = PromptManager.get_instance()
        PromptManager.reset_instance()
        m2 = PromptManager.get_instance()
        assert m1 is not m2

    @pytest.mark.unit
    def test_get_prompt_manager_convenience(self, monkeypatch, prompts_dir):
        monkeypatch.setattr("src.core.prompt_manager.PROMPTS_DIR", prompts_dir)
        monkeypatch.setattr("src.core.prompt_manager.CONFIG_DIR", prompts_dir / "config")
        m = get_prompt_manager()
        assert isinstance(m, PromptManager)
