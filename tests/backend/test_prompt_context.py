"""
Tests for prompt_context.py.

Tests the PromptContext builder:
- Tool name registry
- Security strings
- Environment variables
- Configuration flags
- Functions
- Objects and arrays
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.prompt_context import (
    TOOL_NAME_REGISTRY,
    SECURITY_STRINGS,
    build_prompt_context,
)
from src.core.prompt_engine import PromptContext


# ---------------------------------------------------------------------------
# Tool Name Registry
# ---------------------------------------------------------------------------
class TestToolNameRegistry:
    """Tests for TOOL_NAME_REGISTRY constant."""

    @pytest.mark.unit
    def test_all_ag3ntum_tools_present(self):
        expected = [
            "AG3NTUM_BASH_TOOL",
            "AG3NTUM_READ_TOOL",
            "AG3NTUM_WRITE_TOOL",
            "AG3NTUM_EDIT_TOOL",
            "AG3NTUM_GLOB_TOOL",
            "AG3NTUM_GREP_TOOL",
            "AG3NTUM_LS_TOOL",
            "AG3NTUM_READDOCUMENT_TOOL",
            "AG3NTUM_ASKUSER_TOOL",
        ]
        for name in expected:
            assert name in TOOL_NAME_REGISTRY

    @pytest.mark.unit
    def test_ag3ntum_tools_have_prefix(self):
        for name, value in TOOL_NAME_REGISTRY.items():
            if name.startswith("AG3NTUM_"):
                assert value.startswith("mcp__ag3ntum__"), f"{name}: {value}"

    @pytest.mark.unit
    def test_native_sdk_tools(self):
        assert TOOL_NAME_REGISTRY["TODOWRITE_TOOL"] == "TodoWrite"
        assert TOOL_NAME_REGISTRY["TASK_TOOL"] == "Task"
        assert TOOL_NAME_REGISTRY["SKILL_TOOL"] == "Skill"

    @pytest.mark.unit
    def test_registry_has_12_entries(self):
        assert len(TOOL_NAME_REGISTRY) == 12


# ---------------------------------------------------------------------------
# Security Strings
# ---------------------------------------------------------------------------
class TestSecurityStrings:
    """Tests for SECURITY_STRINGS constant."""

    @pytest.mark.unit
    def test_has_disclosure_response(self):
        assert "SECURITY_DISCLOSURE_RESPONSE" in SECURITY_STRINGS

    @pytest.mark.unit
    def test_has_denial_response(self):
        assert "SECURITY_DENIAL_RESPONSE" in SECURITY_STRINGS

    @pytest.mark.unit
    def test_no_implementation_details(self):
        """Security strings should not reveal internal implementation."""
        for key, value in SECURITY_STRINGS.items():
            assert "jinja" not in value.lower()
            assert "api_key" not in value.lower()
            assert "sk-ant" not in value.lower()


# ---------------------------------------------------------------------------
# build_prompt_context
# ---------------------------------------------------------------------------
class TestBuildPromptContext:
    """Tests for build_prompt_context() function."""

    @pytest.mark.unit
    def test_returns_prompt_context(self):
        ctx = build_prompt_context()
        assert isinstance(ctx, PromptContext)

    @pytest.mark.unit
    def test_tool_names_populated(self):
        ctx = build_prompt_context()
        assert ctx.tool_names == TOOL_NAME_REGISTRY

    @pytest.mark.unit
    def test_workspace_always_workspace(self):
        """Agent should always see /workspace as workspace regardless of docker path."""
        ctx = build_prompt_context(docker_workspace_path="/var/lib/ag3ntum/sessions/123/workspace")
        assert ctx.environment["WORKSPACE_PATH"] == "/workspace"

    @pytest.mark.unit
    def test_session_id_default(self):
        ctx = build_prompt_context()
        assert ctx.environment["SESSION_ID"] == "preview"

    @pytest.mark.unit
    def test_session_id_custom(self):
        ctx = build_prompt_context(session_id="abc-123")
        assert ctx.environment["SESSION_ID"] == "abc-123"

    @pytest.mark.unit
    def test_model_name(self):
        ctx = build_prompt_context(model="claude-opus-4-20250514")
        assert ctx.environment["MODEL_NAME"] == "claude-opus-4-20250514"

    @pytest.mark.unit
    def test_role_content(self):
        ctx = build_prompt_context(role_content="You are a coder.")
        assert ctx.environment["ROLE_CONTENT"] == "You are a coder."

    @pytest.mark.unit
    def test_datetime_populated(self):
        ctx = build_prompt_context()
        assert "CURRENT_DATETIME" in ctx.environment
        assert "CURRENT_YEAR" in ctx.environment
        assert len(ctx.environment["CURRENT_YEAR"]) == 4

    @pytest.mark.unit
    def test_skills_flag_enabled(self):
        ctx = build_prompt_context(enable_skills=True)
        assert ctx.flags["ENABLE_SKILLS"] is True

    @pytest.mark.unit
    def test_skills_flag_disabled(self):
        ctx = build_prompt_context(enable_skills=False)
        assert ctx.flags["ENABLE_SKILLS"] is False

    @pytest.mark.unit
    def test_mounts_flag_from_bool(self):
        ctx = build_prompt_context(enable_external_mounts=True)
        assert ctx.flags["ENABLE_EXTERNAL_MOUNTS"] is True

    @pytest.mark.unit
    def test_mounts_flag_from_dict(self):
        ctx = build_prompt_context(external_mounts={"ro": ["/data"]})
        assert ctx.flags["ENABLE_EXTERNAL_MOUNTS"] is True

    @pytest.mark.unit
    def test_has_external_mounts_flag(self):
        ctx = build_prompt_context(external_mounts={"ro": ["/data"]})
        assert ctx.flags["HAS_EXTERNAL_MOUNTS"] is True

    @pytest.mark.unit
    def test_no_external_mounts_flag(self):
        ctx = build_prompt_context(external_mounts=None)
        assert ctx.flags["HAS_EXTERNAL_MOUNTS"] is False

    @pytest.mark.unit
    def test_permissions_in_context(self):
        perms = {"name": "default", "allow": [], "deny": []}
        ctx = build_prompt_context(permissions=perms)
        assert ctx.objects["PERMISSIONS"] == perms
        assert ctx.flags["HAS_PERMISSIONS"] is True

    @pytest.mark.unit
    def test_no_permissions(self):
        ctx = build_prompt_context(permissions=None)
        assert ctx.flags["HAS_PERMISSIONS"] is False

    @pytest.mark.unit
    def test_security_strings_included(self):
        ctx = build_prompt_context()
        assert "SECURITY_DISCLOSURE_RESPONSE" in ctx.strings
        assert "SECURITY_DENIAL_RESPONSE" in ctx.strings

    @pytest.mark.unit
    def test_functions_populated(self):
        ctx = build_prompt_context()
        assert callable(ctx.functions["MAX_OUTPUT_CHARS"])
        assert ctx.functions["MAX_OUTPUT_CHARS"]() == 30000

    @pytest.mark.unit
    def test_explore_agent_object(self):
        ctx = build_prompt_context()
        assert ctx.objects["EXPLORE_AGENT"]["agentType"] == "Explore"

    @pytest.mark.unit
    def test_dynamic_mounts_array(self):
        mounts = [{"alias": "data", "host_path": "/data"}]
        ctx = build_prompt_context(dynamic_mounts=mounts)
        assert ctx.arrays["DYNAMIC_MOUNTS"] == mounts

    @pytest.mark.unit
    def test_permission_strings(self):
        perms = {
            "name": "secure",
            "description": "Secure profile",
            "enabled_tools": ["Read", "Write"],
        }
        ctx = build_prompt_context(permissions=perms)
        assert ctx.strings["PERMISSION_PROFILE_NAME"] == "secure"
        assert ctx.strings["PERMISSION_DESCRIPTION"] == "Secure profile"
        assert "Read" in ctx.strings["ENABLED_TOOLS"]
