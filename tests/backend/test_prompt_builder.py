"""
Tests for prompt_builder.py.

Tests the prompt builder service:
- Jinja2 template rendering with configurable context
- Role file loading and validation
- Custom filter registration (select_startswith, contains)
- Template module discovery
- Available roles listing
- Error handling for missing roles/templates
- Singleton instance management
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
    _filter_startswith,
    _filter_contains,
)


# ---------------------------------------------------------------------------
# Jinja2 Filters
# ---------------------------------------------------------------------------
class TestFilterStartswith:
    """Tests for _filter_startswith Jinja2 filter."""

    @pytest.mark.unit
    def test_matching_items(self):
        """Test filtering items that start with prefix."""
        items = ["mcp__ag3ntum__Read", "mcp__ag3ntum__Write", "Bash", "Edit"]
        result = _filter_startswith(items, "mcp__ag3ntum__")
        assert result == ["mcp__ag3ntum__Read", "mcp__ag3ntum__Write"]

    @pytest.mark.unit
    def test_no_matches(self):
        """Test when no items match prefix."""
        items = ["Read", "Write", "Bash"]
        result = _filter_startswith(items, "mcp__")
        assert result == []

    @pytest.mark.unit
    def test_empty_list(self):
        """Test with empty input list."""
        result = _filter_startswith([], "prefix")
        assert result == []

    @pytest.mark.unit
    def test_empty_prefix(self):
        """Test that empty prefix matches all items."""
        items = ["a", "b", "c"]
        result = _filter_startswith(items, "")
        assert items == result

    @pytest.mark.unit
    def test_all_match(self):
        """Test when all items match."""
        items = ["test_a", "test_b", "test_c"]
        result = _filter_startswith(items, "test_")
        assert result == items


class TestFilterContains:
    """Tests for _filter_contains Jinja2 filter."""

    @pytest.mark.unit
    def test_value_present(self):
        """Test when value is in list."""
        assert _filter_contains(["a", "b", "c"], "b") is True

    @pytest.mark.unit
    def test_value_absent(self):
        """Test when value is not in list."""
        assert _filter_contains(["a", "b", "c"], "d") is False

    @pytest.mark.unit
    def test_empty_list(self):
        """Test with empty list."""
        assert _filter_contains([], "a") is False


# ---------------------------------------------------------------------------
# PromptBuilder Initialization
# ---------------------------------------------------------------------------
class TestPromptBuilderInit:
    """Tests for PromptBuilder initialization."""

    @pytest.mark.unit
    def test_default_prompts_dir(self):
        """Test that default prompts dir comes from config."""
        with patch("src.services.prompt_builder.PROMPTS_DIR", Path("/fake/prompts")):
            builder = PromptBuilder()
            assert builder._prompts_dir == Path("/fake/prompts")

    @pytest.mark.unit
    def test_custom_prompts_dir(self, tmp_path):
        """Test that custom prompts dir is used when provided."""
        builder = PromptBuilder(prompts_dir=tmp_path)
        assert builder._prompts_dir == tmp_path

    @pytest.mark.unit
    def test_jinja_env_created(self, tmp_path):
        """Test that Jinja2 environment is created with correct settings."""
        builder = PromptBuilder(prompts_dir=tmp_path)
        env = builder._jinja_env
        assert env.trim_blocks is True
        assert env.lstrip_blocks is True

    @pytest.mark.unit
    def test_custom_filters_registered(self, tmp_path):
        """Test that custom Jinja2 filters are registered."""
        builder = PromptBuilder(prompts_dir=tmp_path)
        assert "select_startswith" in builder._jinja_env.filters
        assert "contains" in builder._jinja_env.filters


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------
class TestBuildSystemPrompt:
    """Tests for PromptBuilder.build_system_prompt."""

    @pytest.fixture
    def prompts_dir(self, tmp_path):
        """Create a minimal prompts directory structure."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()

        # Create a minimal role
        (roles_dir / "default.md").write_text("You are a helpful assistant.")
        (roles_dir / "researcher.md").write_text("You are a research assistant.")

        # Create a minimal system template
        (tmp_path / "system.j2").write_text(
            "Role: {{ role_content }}\n"
            "Model: {{ model }}\n"
            "Session: {{ session_id }}\n"
            "Workspace: {{ workspace_path }}\n"
            "Date: {{ current_date }}\n"
            "Skills: {{ enable_skills }}\n"
        )

        return tmp_path

    @pytest.mark.unit
    def test_basic_render(self, prompts_dir):
        """Test basic system prompt rendering with defaults."""
        builder = PromptBuilder(prompts_dir=prompts_dir)
        result = builder.build_system_prompt()

        assert "You are a helpful assistant." in result
        assert "claude-sonnet-4-20250514" in result
        assert "preview" in result  # default session_id
        assert "/workspace" in result

    @pytest.mark.unit
    def test_custom_role(self, prompts_dir):
        """Test rendering with a custom role."""
        builder = PromptBuilder(prompts_dir=prompts_dir)
        result = builder.build_system_prompt(role="researcher")

        assert "You are a research assistant." in result

    @pytest.mark.unit
    def test_custom_model(self, prompts_dir):
        """Test rendering with custom model name."""
        builder = PromptBuilder(prompts_dir=prompts_dir)
        result = builder.build_system_prompt(model="claude-opus-4-20250514")

        assert "claude-opus-4-20250514" in result

    @pytest.mark.unit
    def test_custom_session_id(self, prompts_dir):
        """Test rendering with custom session ID."""
        builder = PromptBuilder(prompts_dir=prompts_dir)
        result = builder.build_system_prompt(session_id="test-session-123")

        assert "test-session-123" in result

    @pytest.mark.unit
    def test_custom_workspace(self, prompts_dir):
        """Test rendering with custom workspace path."""
        builder = PromptBuilder(prompts_dir=prompts_dir)
        result = builder.build_system_prompt(workspace_path="/home/user/project")

        assert "/home/user/project" in result

    @pytest.mark.unit
    def test_no_session_id_defaults_to_preview(self, prompts_dir):
        """Test that None session_id defaults to 'preview'."""
        builder = PromptBuilder(prompts_dir=prompts_dir)
        result = builder.build_system_prompt(session_id=None)

        assert "preview" in result

    @pytest.mark.unit
    def test_skills_enabled(self, prompts_dir):
        """Test that skills flag is passed to template."""
        builder = PromptBuilder(prompts_dir=prompts_dir)
        result = builder.build_system_prompt(enable_skills=True)
        assert "True" in result

    @pytest.mark.unit
    def test_skills_disabled(self, prompts_dir):
        """Test that skills disabled flag is passed to template."""
        builder = PromptBuilder(prompts_dir=prompts_dir)
        result = builder.build_system_prompt(enable_skills=False)
        assert "False" in result

    @pytest.mark.unit
    def test_permissions_passed_to_template(self, prompts_dir):
        """Test that permissions dict is available in template context."""
        # Update template to use permissions
        (prompts_dir / "system.j2").write_text(
            "{% if permissions %}Has permissions{% else %}No permissions{% endif %}"
        )
        builder = PromptBuilder(prompts_dir=prompts_dir)

        result_with = builder.build_system_prompt(
            permissions={"tools": {"enabled": ["Read"]}}
        )
        assert "Has permissions" in result_with

        result_without = builder.build_system_prompt(permissions=None)
        assert "No permissions" in result_without

    @pytest.mark.unit
    def test_external_mounts_default_empty(self, prompts_dir):
        """Test that external_mounts defaults to empty dict."""
        (prompts_dir / "system.j2").write_text(
            "Mounts: {{ external_mounts | length }}"
        )
        builder = PromptBuilder(prompts_dir=prompts_dir)
        result = builder.build_system_prompt()
        assert "Mounts: 0" in result

    @pytest.mark.unit
    def test_external_mounts_passed(self, prompts_dir):
        """Test that external_mounts are available in template."""
        (prompts_dir / "system.j2").write_text(
            "Mounts: {{ external_mounts | length }}"
        )
        builder = PromptBuilder(prompts_dir=prompts_dir)
        result = builder.build_system_prompt(
            external_mounts={"data": {"path": "/data", "mode": "ro"}}
        )
        assert "Mounts: 1" in result

    @pytest.mark.unit
    def test_current_date_included(self, prompts_dir):
        """Test that current date is rendered in prompt."""
        builder = PromptBuilder(prompts_dir=prompts_dir)
        result = builder.build_system_prompt()
        # Date format is like "Saturday, February 08, 2026"
        assert "Date:" in result
        # Should have some date-like content (not empty)
        date_line = [l for l in result.split("\n") if l.startswith("Date:")][0]
        assert len(date_line) > len("Date: ")

    @pytest.mark.unit
    def test_working_dir_matches_workspace(self, prompts_dir):
        """Test that working_dir is set to workspace_path."""
        (prompts_dir / "system.j2").write_text(
            "Working: {{ working_dir }}"
        )
        builder = PromptBuilder(prompts_dir=prompts_dir)
        result = builder.build_system_prompt(workspace_path="/custom/path")
        assert "Working: /custom/path" in result


# ---------------------------------------------------------------------------
# Error Handling
# ---------------------------------------------------------------------------
class TestBuildSystemPromptErrors:
    """Tests for error handling in build_system_prompt."""

    @pytest.mark.unit
    def test_missing_role_file(self, tmp_path):
        """Test FileNotFoundError when role file doesn't exist."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        # No role file created

        builder = PromptBuilder(prompts_dir=tmp_path)
        with pytest.raises(FileNotFoundError, match="Role file not found"):
            builder.build_system_prompt(role="nonexistent")

    @pytest.mark.unit
    def test_missing_role_file_message_includes_path(self, tmp_path):
        """Test that error message includes the expected path."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()

        builder = PromptBuilder(prompts_dir=tmp_path)
        with pytest.raises(FileNotFoundError, match="nonexistent.md"):
            builder.build_system_prompt(role="nonexistent")

    @pytest.mark.unit
    def test_missing_system_template(self, tmp_path):
        """Test ValueError when system.j2 template is missing."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "default.md").write_text("Role content")
        # No system.j2 created

        builder = PromptBuilder(prompts_dir=tmp_path)
        with pytest.raises(ValueError, match="Failed to render system prompt"):
            builder.build_system_prompt()

    @pytest.mark.unit
    def test_template_syntax_error(self, tmp_path):
        """Test ValueError when template has syntax errors."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "default.md").write_text("Role content")
        # Template with bad syntax
        (tmp_path / "system.j2").write_text("{% for x in %}")

        builder = PromptBuilder(prompts_dir=tmp_path)
        with pytest.raises(ValueError, match="Failed to render system prompt"):
            builder.build_system_prompt()

    @pytest.mark.unit
    def test_template_undefined_variable(self, tmp_path):
        """Test that undefined variables in template raise ValueError."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "default.md").write_text("Role content")
        # Template referencing undefined variable
        (tmp_path / "system.j2").write_text("{{ undefined_var.method() }}")

        builder = PromptBuilder(prompts_dir=tmp_path)
        with pytest.raises(ValueError, match="Failed to render system prompt"):
            builder.build_system_prompt()


# ---------------------------------------------------------------------------
# Custom Filters in Templates
# ---------------------------------------------------------------------------
class TestCustomFiltersInTemplates:
    """Tests for custom Jinja2 filters used within templates."""

    @pytest.mark.unit
    def test_select_startswith_in_template(self, tmp_path):
        """Test select_startswith filter works in template rendering."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "default.md").write_text("Role")

        (tmp_path / "system.j2").write_text(
            "{% set tools = ['mcp__ag3ntum__Read', 'mcp__ag3ntum__Write', 'Bash'] %}"
            "{% set mcp_tools = tools | select_startswith('mcp__') %}"
            "MCP tools: {{ mcp_tools | length }}"
        )

        builder = PromptBuilder(prompts_dir=tmp_path)
        result = builder.build_system_prompt()
        assert "MCP tools: 2" in result

    @pytest.mark.unit
    def test_contains_filter_in_template(self, tmp_path):
        """Test contains filter works in template rendering."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "default.md").write_text("Role")

        (tmp_path / "system.j2").write_text(
            "{% set items = ['Read', 'Write', 'Bash'] %}"
            "{% if items | contains('Bash') %}Has Bash{% endif %}"
        )

        builder = PromptBuilder(prompts_dir=tmp_path)
        result = builder.build_system_prompt()
        assert "Has Bash" in result


# ---------------------------------------------------------------------------
# get_available_roles
# ---------------------------------------------------------------------------
class TestGetAvailableRoles:
    """Tests for PromptBuilder.get_available_roles."""

    @pytest.mark.unit
    def test_finds_roles(self, tmp_path):
        """Test discovering role files."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "default.md").write_text("Default role")
        (roles_dir / "researcher.md").write_text("Research role")
        (roles_dir / "coder.md").write_text("Coder role")

        builder = PromptBuilder(prompts_dir=tmp_path)
        roles = builder.get_available_roles()

        assert sorted(roles) == ["coder", "default", "researcher"]

    @pytest.mark.unit
    def test_no_roles_dir(self, tmp_path):
        """Test returns empty list when roles dir doesn't exist."""
        builder = PromptBuilder(prompts_dir=tmp_path)
        roles = builder.get_available_roles()
        assert roles == []

    @pytest.mark.unit
    def test_empty_roles_dir(self, tmp_path):
        """Test returns empty list when roles dir is empty."""
        (tmp_path / "roles").mkdir()
        builder = PromptBuilder(prompts_dir=tmp_path)
        roles = builder.get_available_roles()
        assert roles == []

    @pytest.mark.unit
    def test_ignores_non_md_files(self, tmp_path):
        """Test that non-.md files are excluded."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "default.md").write_text("Role")
        (roles_dir / "notes.txt").write_text("Not a role")
        (roles_dir / "draft.j2").write_text("Not a role")

        builder = PromptBuilder(prompts_dir=tmp_path)
        roles = builder.get_available_roles()
        assert roles == ["default"]

    @pytest.mark.unit
    def test_ignores_directories(self, tmp_path):
        """Test that subdirectories are excluded."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "default.md").write_text("Role")
        (roles_dir / "subdir").mkdir()

        builder = PromptBuilder(prompts_dir=tmp_path)
        roles = builder.get_available_roles()
        assert roles == ["default"]

    @pytest.mark.unit
    def test_roles_sorted(self, tmp_path):
        """Test that roles are returned in sorted order."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "zebra.md").write_text("Z")
        (roles_dir / "alpha.md").write_text("A")
        (roles_dir / "middle.md").write_text("M")

        builder = PromptBuilder(prompts_dir=tmp_path)
        roles = builder.get_available_roles()
        assert roles == ["alpha", "middle", "zebra"]


# ---------------------------------------------------------------------------
# get_template_modules
# ---------------------------------------------------------------------------
class TestGetTemplateModules:
    """Tests for PromptBuilder.get_template_modules."""

    @pytest.mark.unit
    def test_finds_modules(self, tmp_path):
        """Test discovering template modules."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        (modules_dir / "identity.j2").write_text("Identity module")
        (modules_dir / "security.j2").write_text("Security module")
        (modules_dir / "tools.j2").write_text("Tools module")

        builder = PromptBuilder(prompts_dir=tmp_path)
        modules = builder.get_template_modules()

        assert sorted(modules) == ["identity", "security", "tools"]

    @pytest.mark.unit
    def test_no_modules_dir(self, tmp_path):
        """Test returns empty list when modules dir doesn't exist."""
        builder = PromptBuilder(prompts_dir=tmp_path)
        modules = builder.get_template_modules()
        assert modules == []

    @pytest.mark.unit
    def test_empty_modules_dir(self, tmp_path):
        """Test returns empty list when modules dir is empty."""
        (tmp_path / "modules").mkdir()
        builder = PromptBuilder(prompts_dir=tmp_path)
        modules = builder.get_template_modules()
        assert modules == []

    @pytest.mark.unit
    def test_ignores_non_j2_files(self, tmp_path):
        """Test that non-.j2 files are excluded."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        (modules_dir / "identity.j2").write_text("Module")
        (modules_dir / "readme.md").write_text("Not a module")
        (modules_dir / "config.yaml").write_text("Not a module")

        builder = PromptBuilder(prompts_dir=tmp_path)
        modules = builder.get_template_modules()
        assert modules == ["identity"]

    @pytest.mark.unit
    def test_modules_sorted(self, tmp_path):
        """Test that modules are returned in sorted order."""
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()
        (modules_dir / "z_final.j2").write_text("Z")
        (modules_dir / "a_first.j2").write_text("A")

        builder = PromptBuilder(prompts_dir=tmp_path)
        modules = builder.get_template_modules()
        assert modules == ["a_first", "z_final"]


# ---------------------------------------------------------------------------
# Singleton / get_prompt_builder
# ---------------------------------------------------------------------------
class TestGetPromptBuilder:
    """Tests for get_prompt_builder singleton."""

    @pytest.mark.unit
    def test_returns_prompt_builder_instance(self):
        """Test that get_prompt_builder returns a PromptBuilder."""
        with patch("src.services.prompt_builder._prompt_builder", None):
            builder = get_prompt_builder()
            assert isinstance(builder, PromptBuilder)

    @pytest.mark.unit
    def test_returns_same_instance(self):
        """Test that get_prompt_builder returns the same instance on repeated calls."""
        with patch("src.services.prompt_builder._prompt_builder", None):
            builder1 = get_prompt_builder()
            builder2 = get_prompt_builder()
            assert builder1 is builder2

    @pytest.mark.unit
    def test_returns_existing_instance(self):
        """Test that existing instance is reused."""
        mock_builder = MagicMock(spec=PromptBuilder)
        with patch("src.services.prompt_builder._prompt_builder", mock_builder):
            result = get_prompt_builder()
            assert result is mock_builder


# ---------------------------------------------------------------------------
# Template Include Integration
# ---------------------------------------------------------------------------
class TestTemplateIncludes:
    """Tests for template include functionality."""

    @pytest.mark.unit
    def test_include_module(self, tmp_path):
        """Test that templates can include module files."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()

        (roles_dir / "default.md").write_text("Role content")
        (modules_dir / "header.j2").write_text("== HEADER ==")
        (tmp_path / "system.j2").write_text(
            "{% include 'modules/header.j2' %}\n{{ role_content }}"
        )

        builder = PromptBuilder(prompts_dir=tmp_path)
        result = builder.build_system_prompt()

        assert "== HEADER ==" in result
        assert "Role content" in result

    @pytest.mark.unit
    def test_module_accesses_context(self, tmp_path):
        """Test that included modules can access template context variables."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        modules_dir = tmp_path / "modules"
        modules_dir.mkdir()

        (roles_dir / "default.md").write_text("Role")
        (modules_dir / "env.j2").write_text("Model={{ model }}, Session={{ session_id }}")
        (tmp_path / "system.j2").write_text("{% include 'modules/env.j2' %}")

        builder = PromptBuilder(prompts_dir=tmp_path)
        result = builder.build_system_prompt(
            model="test-model",
            session_id="sess-42",
        )

        assert "Model=test-model" in result
        assert "Session=sess-42" in result
