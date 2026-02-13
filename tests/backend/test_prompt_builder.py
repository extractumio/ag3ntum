"""
Tests for prompt_builder.py.

Tests the PromptBuilder wrapper that delegates to PromptManager:
- build_system_prompt delegation
- get_available_roles delegation
- get_template_modules delegation
- Singleton management
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.prompt_builder import (
    PromptBuilder,
    get_prompt_builder,
)
from src.core.prompt_manager import PromptManager


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset singletons between tests."""
    PromptManager.reset_instance()
    # Reset prompt_builder singleton
    import src.services.prompt_builder as pb
    pb._prompt_builder = None
    yield
    PromptManager.reset_instance()
    pb._prompt_builder = None


@pytest.fixture
def prompts_dir(tmp_path):
    """Create a minimal prompts directory structure."""
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "default.md").write_text("You are a helpful assistant.")
    (roles_dir / "researcher.md").write_text("You are a research assistant.")

    system_dir = tmp_path / "system-prompts"
    system_dir.mkdir()
    (system_dir / "01-identity.md").write_text(
        "<!--\nname: 'Identity'\ndescription: Identity\nvariables:\n  - ROLE_CONTENT\n  - MODEL_NAME\noverride_allowed: false\n-->\n\n"
        "Role: ${ROLE_CONTENT}\nModel: ${MODEL_NAME}"
    )

    return tmp_path


# ---------------------------------------------------------------------------
# PromptBuilder Initialization
# ---------------------------------------------------------------------------
class TestPromptBuilderInit:
    """Tests for PromptBuilder initialization."""

    @pytest.mark.unit
    def test_init_with_no_args(self):
        """Test that PromptBuilder initializes without arguments."""
        builder = PromptBuilder()
        assert builder is not None

    @pytest.mark.unit
    def test_init_with_prompts_dir_ignored(self, tmp_path):
        """Test that prompts_dir argument is accepted but ignored."""
        builder = PromptBuilder(prompts_dir=tmp_path)
        assert builder is not None


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------
class TestBuildSystemPrompt:
    """Tests for PromptBuilder.build_system_prompt delegation."""

    @pytest.mark.unit
    def test_basic_render(self, prompts_dir, monkeypatch):
        monkeypatch.setattr("src.core.prompt_manager.PROMPTS_DIR", prompts_dir)
        monkeypatch.setattr("src.core.prompt_manager.CONFIG_DIR", prompts_dir / "config")
        builder = PromptBuilder()
        result = builder.build_system_prompt()
        assert "You are a helpful assistant." in result

    @pytest.mark.unit
    def test_custom_role(self, prompts_dir, monkeypatch):
        monkeypatch.setattr("src.core.prompt_manager.PROMPTS_DIR", prompts_dir)
        monkeypatch.setattr("src.core.prompt_manager.CONFIG_DIR", prompts_dir / "config")
        builder = PromptBuilder()
        result = builder.build_system_prompt(role="researcher")
        assert "You are a research assistant." in result

    @pytest.mark.unit
    def test_custom_model(self, prompts_dir, monkeypatch):
        monkeypatch.setattr("src.core.prompt_manager.PROMPTS_DIR", prompts_dir)
        monkeypatch.setattr("src.core.prompt_manager.CONFIG_DIR", prompts_dir / "config")
        builder = PromptBuilder()
        result = builder.build_system_prompt(model="claude-opus-4-20250514")
        assert "claude-opus-4-20250514" in result

    @pytest.mark.unit
    def test_missing_role_raises(self, prompts_dir, monkeypatch):
        monkeypatch.setattr("src.core.prompt_manager.PROMPTS_DIR", prompts_dir)
        monkeypatch.setattr("src.core.prompt_manager.CONFIG_DIR", prompts_dir / "config")
        builder = PromptBuilder()
        with pytest.raises(FileNotFoundError, match="nonexistent"):
            builder.build_system_prompt(role="nonexistent")


# ---------------------------------------------------------------------------
# get_available_roles
# ---------------------------------------------------------------------------
class TestGetAvailableRoles:
    """Tests for PromptBuilder.get_available_roles."""

    @pytest.mark.unit
    def test_finds_roles(self, prompts_dir, monkeypatch):
        monkeypatch.setattr("src.core.prompt_manager.PROMPTS_DIR", prompts_dir)
        monkeypatch.setattr("src.core.prompt_manager.CONFIG_DIR", prompts_dir / "config")
        builder = PromptBuilder()
        roles = builder.get_available_roles()
        assert "default" in roles
        assert "researcher" in roles

    @pytest.mark.unit
    def test_no_roles_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.core.prompt_manager.PROMPTS_DIR", tmp_path)
        monkeypatch.setattr("src.core.prompt_manager.CONFIG_DIR", tmp_path / "config")
        builder = PromptBuilder()
        roles = builder.get_available_roles()
        assert roles == []


# ---------------------------------------------------------------------------
# get_template_modules
# ---------------------------------------------------------------------------
class TestGetTemplateModules:
    """Tests for PromptBuilder.get_template_modules delegation."""

    @pytest.mark.unit
    def test_finds_modules(self, prompts_dir, monkeypatch):
        monkeypatch.setattr("src.core.prompt_manager.PROMPTS_DIR", prompts_dir)
        monkeypatch.setattr("src.core.prompt_manager.CONFIG_DIR", prompts_dir / "config")
        builder = PromptBuilder()
        modules = builder.get_template_modules()
        assert "01-identity" in modules

    @pytest.mark.unit
    def test_no_modules_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.core.prompt_manager.PROMPTS_DIR", tmp_path)
        monkeypatch.setattr("src.core.prompt_manager.CONFIG_DIR", tmp_path / "config")
        builder = PromptBuilder()
        modules = builder.get_template_modules()
        assert modules == []


# ---------------------------------------------------------------------------
# Singleton / get_prompt_builder
# ---------------------------------------------------------------------------
class TestGetPromptBuilder:
    """Tests for get_prompt_builder singleton."""

    @pytest.mark.unit
    def test_returns_prompt_builder_instance(self):
        builder = get_prompt_builder()
        assert isinstance(builder, PromptBuilder)

    @pytest.mark.unit
    def test_returns_same_instance(self):
        builder1 = get_prompt_builder()
        builder2 = get_prompt_builder()
        assert builder1 is builder2
