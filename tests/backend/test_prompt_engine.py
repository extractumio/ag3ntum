"""
Tests for prompt_engine.py.

Tests the new ${VARIABLE} syntax template engine:
- Simple variable substitution
- Function calls
- Object property access
- Ternary conditionals
- Comparison operators
- Array operations (join, length)
- JSON stringify
- Metadata parsing
- File caching (mtime-based)
- Edge cases and error handling
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.prompt_engine import (
    PromptTemplateEngine,
    PromptContext,
    PromptMetadata,
)


@pytest.fixture
def engine():
    """Create a fresh engine for each test."""
    return PromptTemplateEngine()


@pytest.fixture
def context():
    """Create a basic context for testing."""
    ctx = PromptContext()
    ctx.tool_names = {
        "AG3NTUM_READ_TOOL": "mcp__ag3ntum__Read",
        "AG3NTUM_BASH_TOOL": "mcp__ag3ntum__Bash",
    }
    ctx.environment = {
        "WORKSPACE_PATH": "/workspace",
        "MODEL_NAME": "claude-sonnet-4-20250514",
        "SESSION_ID": "test-session-123",
    }
    ctx.flags = {
        "ENABLE_SKILLS": True,
        "ENABLE_MOUNTS": False,
    }
    ctx.strings = {
        "ROLE_CONTENT": "You are a helpful assistant.",
        "GREETING": "Hello",
    }
    ctx.objects = {
        "CONFIG": {"name": "test", "version": "1.0", "nested": {"key": "value"}},
    }
    ctx.arrays = {
        "TOOLS": ["Read", "Write", "Bash"],
        "EMPTY_LIST": [],
    }
    ctx.functions = {
        "MAX_LINES": lambda: 2000,
        "CURRENT_TIME": lambda: "12:00:00",
    }
    return ctx


# ---------------------------------------------------------------------------
# Simple Variable Substitution
# ---------------------------------------------------------------------------
class TestSimpleVariables:
    """Tests for ${VARIABLE} substitution."""

    @pytest.mark.unit
    def test_simple_variable(self, engine, context):
        result = engine.render("Hello ${GREETING}", context)
        assert result == "Hello Hello"

    @pytest.mark.unit
    def test_tool_name_variable(self, engine, context):
        result = engine.render("Use ${AG3NTUM_READ_TOOL} to read files.", context)
        assert result == "Use mcp__ag3ntum__Read to read files."

    @pytest.mark.unit
    def test_environment_variable(self, engine, context):
        result = engine.render("Model: ${MODEL_NAME}", context)
        assert result == "Model: claude-sonnet-4-20250514"

    @pytest.mark.unit
    def test_missing_variable_preserved(self, engine, context):
        """Missing variables should be preserved as-is."""
        result = engine.render("Missing: ${NONEXISTENT}", context)
        assert result == "Missing: ${NONEXISTENT}"

    @pytest.mark.unit
    def test_multiple_variables(self, engine, context):
        result = engine.render(
            "${GREETING} from ${WORKSPACE_PATH} using ${MODEL_NAME}",
            context,
        )
        assert result == "Hello from /workspace using claude-sonnet-4-20250514"

    @pytest.mark.unit
    def test_no_variables(self, engine, context):
        """Template with no variables should pass through unchanged."""
        text = "No variables here."
        result = engine.render(text, context)
        assert result == text

    @pytest.mark.unit
    def test_priority_order(self, engine):
        """Tool names should take priority over environment."""
        ctx = PromptContext()
        ctx.tool_names = {"FOO": "tool_value"}
        ctx.environment = {"FOO": "env_value"}
        result = engine.render("${FOO}", ctx)
        assert result == "tool_value"


# ---------------------------------------------------------------------------
# Function Calls
# ---------------------------------------------------------------------------
class TestFunctionCalls:
    """Tests for ${FUNCTION()} syntax."""

    @pytest.mark.unit
    def test_function_call(self, engine, context):
        result = engine.render("Max lines: ${MAX_LINES()}", context)
        assert result == "Max lines: 2000"

    @pytest.mark.unit
    def test_missing_function(self, engine, context):
        result = engine.render("${MISSING_FUNC()}", context)
        assert result == ""

    @pytest.mark.unit
    def test_function_returning_string(self, engine, context):
        result = engine.render("Time: ${CURRENT_TIME()}", context)
        assert result == "Time: 12:00:00"


# ---------------------------------------------------------------------------
# Object Property Access
# ---------------------------------------------------------------------------
class TestObjectAccess:
    """Tests for ${OBJECT.property} syntax."""

    @pytest.mark.unit
    def test_simple_property(self, engine, context):
        result = engine.render("Name: ${CONFIG.name}", context)
        assert result == "Name: test"

    @pytest.mark.unit
    def test_nested_property(self, engine, context):
        result = engine.render("Key: ${CONFIG.nested.key}", context)
        assert result == "Key: value"

    @pytest.mark.unit
    def test_missing_property(self, engine, context):
        result = engine.render("Missing: ${CONFIG.nonexistent}", context)
        assert result == "Missing: "

    @pytest.mark.unit
    def test_missing_object(self, engine, context):
        result = engine.render("Missing: ${UNKNOWN.prop}", context)
        assert result == "Missing: "


# ---------------------------------------------------------------------------
# Ternary Conditionals
# ---------------------------------------------------------------------------
class TestConditionals:
    """Tests for ${COND?if_true:if_false} syntax."""

    @pytest.mark.unit
    def test_true_condition(self, engine, context):
        result = engine.render("${ENABLE_SKILLS?Skills ON:Skills OFF}", context)
        assert result == "Skills ON"

    @pytest.mark.unit
    def test_false_condition(self, engine, context):
        result = engine.render("${ENABLE_MOUNTS?Mounts ON:Mounts OFF}", context)
        assert result == "Mounts OFF"

    @pytest.mark.unit
    def test_empty_false_branch(self, engine, context):
        result = engine.render("${ENABLE_SKILLS?Skills section:}", context)
        assert result == "Skills section"

    @pytest.mark.unit
    def test_empty_true_branch(self, engine, context):
        result = engine.render("${ENABLE_MOUNTS?:No mounts}", context)
        assert result == "No mounts"

    @pytest.mark.unit
    def test_multiline_content(self, engine, context):
        template = "${ENABLE_SKILLS?## Skills\nThis is the skills section.:}"
        result = engine.render(template, context)
        assert "## Skills" in result
        assert "This is the skills section." in result

    @pytest.mark.unit
    def test_undefined_flag_is_falsy(self, engine, context):
        result = engine.render("${UNKNOWN_FLAG?yes:no}", context)
        assert result == "no"


# ---------------------------------------------------------------------------
# Comparison Operators
# ---------------------------------------------------------------------------
class TestComparisons:
    """Tests for ${VAR!==null?"text":"other"} syntax."""

    @pytest.mark.unit
    def test_not_null(self, engine, context):
        result = engine.render('${GREETING!==null?"has value":"no value"}', context)
        assert result == "has value"

    @pytest.mark.unit
    def test_is_null(self, engine, context):
        result = engine.render('${UNKNOWN!==null?"exists":"missing"}', context)
        assert result == "missing"

    @pytest.mark.unit
    def test_equals_null(self, engine, context):
        result = engine.render('${UNKNOWN===null?"is null":"not null"}', context)
        assert result == "is null"


# ---------------------------------------------------------------------------
# Array Operations
# ---------------------------------------------------------------------------
class TestArrayOperations:
    """Tests for array join and length operations."""

    @pytest.mark.unit
    def test_array_join(self, engine, context):
        result = engine.render('${TOOLS.join(", ")}', context)
        assert result == "Read, Write, Bash"

    @pytest.mark.unit
    def test_array_join_empty_separator(self, engine, context):
        result = engine.render("${TOOLS.join()}", context)
        assert result == "ReadWriteBash"

    @pytest.mark.unit
    def test_array_length_gt_true(self, engine, context):
        result = engine.render('${TOOLS.length>0?has tools:no tools}', context)
        assert result == "has tools"

    @pytest.mark.unit
    def test_array_length_gt_false(self, engine, context):
        result = engine.render('${EMPTY_LIST.length>0?has items:empty}', context)
        assert result == "empty"

    @pytest.mark.unit
    def test_array_join_missing(self, engine, context):
        result = engine.render('${MISSING_ARR.join(",")}', context)
        assert result == ""


# ---------------------------------------------------------------------------
# JSON Stringify
# ---------------------------------------------------------------------------
class TestJsonStringify:
    """Tests for ${JSON_STRINGIFY_FN(obj)} syntax."""

    @pytest.mark.unit
    def test_json_stringify(self, engine, context):
        result = engine.render("${JSON_STRINGIFY_FN(CONFIG)}", context)
        parsed = json.loads(result)
        assert parsed["name"] == "test"
        assert parsed["version"] == "1.0"

    @pytest.mark.unit
    def test_json_stringify_null(self, engine, context):
        result = engine.render("${JSON_STRINGIFY_FN(MISSING)}", context)
        assert result == "null"


# ---------------------------------------------------------------------------
# Metadata Parsing
# ---------------------------------------------------------------------------
class TestMetadataParsing:
    """Tests for parse_metadata()."""

    @pytest.mark.unit
    def test_full_metadata(self, engine):
        content = """<!--
name: 'System Prompt: Identity'
description: Agent identity and role
version: 2.0.0
variables:
  - ROLE_CONTENT
  - MODEL_NAME
override_allowed: true
-->

Body content here."""
        metadata, body = engine.parse_metadata(content)
        assert metadata.name == "System Prompt: Identity"
        assert metadata.description == "Agent identity and role"
        assert metadata.version == "2.0.0"
        assert metadata.variables == ["ROLE_CONTENT", "MODEL_NAME"]
        assert metadata.override_allowed is True
        assert body.strip() == "Body content here."

    @pytest.mark.unit
    def test_no_metadata(self, engine):
        content = "Just plain text."
        metadata, body = engine.parse_metadata(content)
        assert metadata.name == "Unknown"
        assert body == content

    @pytest.mark.unit
    def test_empty_variables(self, engine):
        content = """<!--
name: 'Test'
description: Test
variables: []
override_allowed: false
-->

Body."""
        metadata, body = engine.parse_metadata(content)
        assert metadata.variables == []
        assert metadata.override_allowed is False


# ---------------------------------------------------------------------------
# File Loading and Caching
# ---------------------------------------------------------------------------
class TestFileLoadingAndCaching:
    """Tests for load_and_render() with file caching."""

    @pytest.mark.unit
    def test_load_and_render(self, engine, context, tmp_path):
        """Test basic file loading and rendering."""
        prompt_file = tmp_path / "test.md"
        prompt_file.write_text(
            "<!--\nname: 'Test'\ndescription: Test\nvariables: []\noverride_allowed: false\n-->\n\n"
            "Hello ${GREETING}"
        )
        result = engine.load_and_render(prompt_file, context)
        assert "Hello Hello" in result

    @pytest.mark.unit
    def test_cache_hit(self, engine, context, tmp_path):
        """Test that subsequent loads use cache."""
        prompt_file = tmp_path / "cached.md"
        prompt_file.write_text(
            "<!--\nname: 'Test'\ndescription: Test\nvariables: []\noverride_allowed: false\n-->\n\n"
            "Version ${MODEL_NAME}"
        )
        # First load
        result1 = engine.load_and_render(prompt_file, context)
        # Second load should use cache
        result2 = engine.load_and_render(prompt_file, context)
        assert result1 == result2
        assert str(prompt_file) in engine._cache

    @pytest.mark.unit
    def test_cache_invalidated_on_mtime_change(self, engine, context, tmp_path):
        """Test that cache is invalidated when file changes."""
        import time
        prompt_file = tmp_path / "changing.md"
        prompt_file.write_text(
            "<!--\nname: 'Test'\ndescription: Test\nvariables: []\noverride_allowed: false\n-->\n\n"
            "V1"
        )
        result1 = engine.load_and_render(prompt_file, context)
        assert "V1" in result1

        # Modify file (ensure mtime changes)
        time.sleep(0.05)
        prompt_file.write_text(
            "<!--\nname: 'Test'\ndescription: Test\nvariables: []\noverride_allowed: false\n-->\n\n"
            "V2"
        )
        result2 = engine.load_and_render(prompt_file, context)
        assert "V2" in result2

    @pytest.mark.unit
    def test_clear_cache(self, engine, context, tmp_path):
        """Test cache clearing."""
        prompt_file = tmp_path / "clear.md"
        prompt_file.write_text(
            "<!--\nname: 'Test'\ndescription: Test\nvariables: []\noverride_allowed: false\n-->\n\n"
            "content"
        )
        engine.load_and_render(prompt_file, context)
        assert len(engine._cache) == 1
        cleared = engine.clear_cache()
        assert cleared == 1
        assert len(engine._cache) == 0

    @pytest.mark.unit
    def test_no_cache(self, engine, context, tmp_path):
        """Test loading without cache."""
        prompt_file = tmp_path / "nocache.md"
        prompt_file.write_text(
            "<!--\nname: 'Test'\ndescription: Test\nvariables: []\noverride_allowed: false\n-->\n\n"
            "no cache"
        )
        engine.load_and_render(prompt_file, context, use_cache=False)
        # Cache should still be populated for future use
        assert str(prompt_file) in engine._cache


# ---------------------------------------------------------------------------
# PromptContext
# ---------------------------------------------------------------------------
class TestPromptContext:
    """Tests for PromptContext.get() and related methods."""

    @pytest.mark.unit
    def test_get_priority_order(self):
        """Tool names > environment > flags > strings > objects > arrays."""
        ctx = PromptContext()
        ctx.tool_names = {"X": "tool"}
        ctx.environment = {"X": "env"}
        ctx.flags = {"X": True}
        assert ctx.get("X") == "tool"

        ctx2 = PromptContext()
        ctx2.environment = {"X": "env"}
        ctx2.flags = {"X": True}
        assert ctx2.get("X") == "env"

    @pytest.mark.unit
    def test_get_returns_none_for_missing(self):
        ctx = PromptContext()
        assert ctx.get("MISSING") is None

    @pytest.mark.unit
    def test_get_nested_dict(self):
        ctx = PromptContext()
        ctx.objects = {"OBJ": {"a": {"b": "deep"}}}
        assert ctx.get_nested("OBJ", "a.b") == "deep"

    @pytest.mark.unit
    def test_get_nested_missing(self):
        ctx = PromptContext()
        ctx.objects = {"OBJ": {"a": 1}}
        assert ctx.get_nested("OBJ", "b.c") is None

    @pytest.mark.unit
    def test_call_function(self):
        ctx = PromptContext()
        ctx.functions = {"FN": lambda: 42}
        assert ctx.call("FN") == 42

    @pytest.mark.unit
    def test_call_missing_function(self):
        ctx = PromptContext()
        assert ctx.call("MISSING") is None


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------
class TestEdgeCases:
    """Edge cases and security tests."""

    @pytest.mark.unit
    def test_dollar_sign_without_braces(self, engine, context):
        """Dollar sign without braces should pass through."""
        result = engine.render("Price: $100", context)
        assert result == "Price: $100"

    @pytest.mark.unit
    def test_escaped_looking_syntax(self, engine, context):
        """Lowercase variable names should not match."""
        result = engine.render("${lowercase_var}", context)
        assert result == "${lowercase_var}"

    @pytest.mark.unit
    def test_empty_template(self, engine, context):
        result = engine.render("", context)
        assert result == ""

    @pytest.mark.unit
    def test_nested_dollar_braces(self, engine, context):
        """${${VAR}} should not crash."""
        result = engine.render("${${GREETING}}", context)
        # Inner should be replaced first, outer is not valid syntax
        assert "Hello" in result or "${" in result

    @pytest.mark.unit
    def test_context_security_no_secrets(self):
        """Context should never contain API keys."""
        ctx = PromptContext()
        ctx.strings["API_KEY"] = "sk-ant-test"
        # This is just a documentation test - the context allows any strings
        # but callers should never put secrets in it
        assert ctx.get("API_KEY") == "sk-ant-test"


# ---------------------------------------------------------------------------
# Include Directive Resolution
# ---------------------------------------------------------------------------
class TestIncludeDirectives:
    """Tests for {% include 'path' %} resolution."""

    @pytest.mark.unit
    def test_simple_include(self, context, tmp_path):
        """Include directive should inline file contents."""
        modules = tmp_path / "modules"
        modules.mkdir()
        (modules / "greeting.j2").write_text("Hello from included module!")

        engine = PromptTemplateEngine(base_dir=tmp_path)
        content = "Before\n{% include 'modules/greeting.j2' %}\nAfter"
        result = engine._resolve_includes(content, base_dir=tmp_path)
        assert "Hello from included module!" in result
        assert "Before" in result
        assert "After" in result
        assert "{% include" not in result

    @pytest.mark.unit
    def test_include_strips_metadata(self, context, tmp_path):
        """Included files should have their metadata headers stripped."""
        modules = tmp_path / "modules"
        modules.mkdir()
        (modules / "meta.j2").write_text(
            "<!--\nname: 'Test'\ndescription: Test\n-->\n\nBody content only"
        )

        engine = PromptTemplateEngine(base_dir=tmp_path)
        content = "{% include 'modules/meta.j2' %}"
        result = engine._resolve_includes(content, base_dir=tmp_path)
        assert "Body content only" in result
        assert "<!--" not in result

    @pytest.mark.unit
    def test_nested_includes(self, context, tmp_path):
        """Includes should resolve recursively."""
        modules = tmp_path / "modules"
        modules.mkdir()
        (modules / "inner.j2").write_text("Inner content")
        (modules / "outer.j2").write_text(
            "Outer start\n{% include 'modules/inner.j2' %}\nOuter end"
        )

        engine = PromptTemplateEngine(base_dir=tmp_path)
        content = "{% include 'modules/outer.j2' %}"
        result = engine._resolve_includes(content, base_dir=tmp_path)
        assert "Outer start" in result
        assert "Inner content" in result
        assert "Outer end" in result

    @pytest.mark.unit
    def test_circular_include_protection(self, context, tmp_path):
        """Circular includes should be detected and stopped."""
        modules = tmp_path / "modules"
        modules.mkdir()
        (modules / "a.j2").write_text("A includes B: {% include 'modules/b.j2' %}")
        (modules / "b.j2").write_text("B includes A: {% include 'modules/a.j2' %}")

        engine = PromptTemplateEngine(base_dir=tmp_path)
        content = "{% include 'modules/a.j2' %}"
        result = engine._resolve_includes(content, base_dir=tmp_path)
        assert "circular include" in result

    @pytest.mark.unit
    def test_max_depth_protection(self, context, tmp_path):
        """Deep nesting should be stopped at MAX_INCLUDE_DEPTH."""
        modules = tmp_path / "modules"
        modules.mkdir()
        for i in range(7):
            if i < 6:
                (modules / f"d{i}.j2").write_text(
                    f"Depth {i}\n{{% include 'modules/d{i+1}.j2' %}}"
                )
            else:
                (modules / f"d{i}.j2").write_text(f"Depth {i}")

        engine = PromptTemplateEngine(base_dir=tmp_path)
        content = "{% include 'modules/d0.j2' %}"
        result = engine._resolve_includes(content, base_dir=tmp_path)
        assert "Depth 0" in result
        assert "Depth 4" in result

    @pytest.mark.unit
    def test_missing_include_file(self, context, tmp_path):
        """Missing include file should produce a comment, not crash."""
        engine = PromptTemplateEngine(base_dir=tmp_path)
        content = "{% include 'nonexistent.j2' %}"
        result = engine._resolve_includes(content, base_dir=tmp_path)
        assert "include not found" in result

    @pytest.mark.unit
    def test_multiple_includes(self, context, tmp_path):
        """Multiple includes in the same file should all resolve."""
        modules = tmp_path / "modules"
        modules.mkdir()
        (modules / "a.j2").write_text("Content A")
        (modules / "b.j2").write_text("Content B")

        engine = PromptTemplateEngine(base_dir=tmp_path)
        content = "{% include 'modules/a.j2' %}\n---\n{% include 'modules/b.j2' %}"
        result = engine._resolve_includes(content, base_dir=tmp_path)
        assert "Content A" in result
        assert "Content B" in result

    @pytest.mark.unit
    def test_no_base_dir_passthrough(self, context):
        """Without base_dir, includes should pass through unchanged."""
        engine = PromptTemplateEngine()
        content = "{% include 'modules/test.j2' %}"
        result = engine._resolve_includes(content)
        assert result == content


# ---------------------------------------------------------------------------
# Jinja2 Comment Stripping
# ---------------------------------------------------------------------------
class TestJinja2Comments:
    """Tests for {# ... #} comment stripping."""

    @pytest.mark.unit
    def test_single_line_comment(self):
        engine = PromptTemplateEngine()
        result = engine._strip_jinja2_comments("before {# comment #} after")
        assert result == "before  after"

    @pytest.mark.unit
    def test_multiline_comment(self):
        engine = PromptTemplateEngine()
        result = engine._strip_jinja2_comments(
            "before\n{# multi\nline\ncomment #}\nafter"
        )
        assert "before" in result
        assert "after" in result
        assert "multi" not in result

    @pytest.mark.unit
    def test_multiple_comments(self):
        engine = PromptTemplateEngine()
        result = engine._strip_jinja2_comments("{# a #} text {# b #}")
        assert result == " text "


# ---------------------------------------------------------------------------
# Jinja2 Conditional Processing
# ---------------------------------------------------------------------------
class TestJinja2Conditionals:
    """Tests for {% if VAR %}...{% endif %} processing."""

    @pytest.mark.unit
    def test_if_true(self, context):
        engine = PromptTemplateEngine()
        content = "{% if ENABLE_SKILLS %}skills section{% endif %}"
        result = engine._process_conditionals(content, context)
        assert result == "skills section"

    @pytest.mark.unit
    def test_if_false(self, context):
        engine = PromptTemplateEngine()
        content = "{% if ENABLE_MOUNTS %}mounts section{% endif %}"
        result = engine._process_conditionals(content, context)
        assert result == ""

    @pytest.mark.unit
    def test_if_else_true(self, context):
        engine = PromptTemplateEngine()
        content = "{% if ENABLE_SKILLS %}yes{% else %}no{% endif %}"
        result = engine._process_conditionals(content, context)
        assert result == "yes"

    @pytest.mark.unit
    def test_if_else_false(self, context):
        engine = PromptTemplateEngine()
        content = "{% if ENABLE_MOUNTS %}yes{% else %}no{% endif %}"
        result = engine._process_conditionals(content, context)
        assert result == "no"

    @pytest.mark.unit
    def test_undefined_var_is_falsy(self, context):
        engine = PromptTemplateEngine()
        content = "{% if UNDEFINED_VAR %}shown{% else %}hidden{% endif %}"
        result = engine._process_conditionals(content, context)
        assert result == "hidden"

    @pytest.mark.unit
    def test_multiline_if_body(self, context):
        engine = PromptTemplateEngine()
        content = "{% if ENABLE_SKILLS %}\n# Skills\nSkills are enabled.\n{% endif %}"
        result = engine._process_conditionals(content, context)
        assert "# Skills" in result
        assert "Skills are enabled." in result


# ---------------------------------------------------------------------------
# Full Pipeline: load_and_render with includes + conditionals + variables
# ---------------------------------------------------------------------------
class TestFullPipeline:
    """Tests for the complete load_and_render pipeline with all features."""

    @pytest.mark.unit
    def test_load_j2_with_includes_and_vars(self, context, tmp_path):
        """Full pipeline: include + comment stripping + variable substitution."""
        modules = tmp_path / "modules"
        modules.mkdir()
        (modules / "security.j2").write_text(
            "{# Security module #}\n# Security\nUse ${AG3NTUM_READ_TOOL} safely."
        )

        main_template = tmp_path / "prompt.j2"
        main_template.write_text(
            "{# Main prompt #}\n"
            "# Identity\n"
            "{% include 'modules/security.j2' %}\n"
            "Model: ${MODEL_NAME}"
        )

        engine = PromptTemplateEngine(base_dir=tmp_path)
        result = engine.load_and_render(main_template, context)
        assert "# Identity" in result
        assert "# Security" in result
        assert "mcp__ag3ntum__Read" in result
        assert "claude-sonnet-4-20250514" in result
        assert "{#" not in result
        assert "{% include" not in result

    @pytest.mark.unit
    def test_load_j2_with_conditionals(self, context, tmp_path):
        """Conditionals should be processed in load_and_render."""
        template = tmp_path / "cond.j2"
        template.write_text(
            "{% if ENABLE_SKILLS %}Skills: ON{% else %}Skills: OFF{% endif %}\n"
            "{% if ENABLE_MOUNTS %}Mounts: ON{% else %}Mounts: OFF{% endif %}"
        )

        engine = PromptTemplateEngine(base_dir=tmp_path)
        result = engine.load_and_render(template, context)
        assert "Skills: ON" in result
        assert "Mounts: OFF" in result

    @pytest.mark.unit
    def test_md_files_unaffected(self, context, tmp_path):
        """.md files without include directives should render normally."""
        md_file = tmp_path / "test.md"
        md_file.write_text(
            "<!--\nname: 'Test'\ndescription: Test\nvariables: []\n"
            "override_allowed: false\n-->\n\nHello ${GREETING}"
        )

        engine = PromptTemplateEngine(base_dir=tmp_path)
        result = engine.load_and_render(md_file, context)
        assert "Hello Hello" in result

    @pytest.mark.unit
    def test_core_principles_in_system_prompts(self):
        """Verify core principles file loads correctly as a system prompt."""
        principles_file = Path(__file__).parent.parent.parent / (
            "prompts/system-prompts/03-core-principles.md"
        )
        if not principles_file.exists():
            pytest.skip("Core principles file not found")

        engine = PromptTemplateEngine()
        ctx = PromptContext()
        result = engine.load_and_render(principles_file, ctx)
        assert "Core Operating Principles" in result
        assert "Backup Before You Touch" in result
        assert "Uptime Is Sacred" in result
        assert "Timing Is Part of the Operation" in result

    @pytest.mark.unit
    def test_core_principles_module_loadable(self):
        """Verify core principles module can be included."""
        module_file = Path(__file__).parent.parent.parent / (
            "prompts/modules/core_principles.md"
        )
        if not module_file.exists():
            pytest.skip("Core principles module not found")

        content = module_file.read_text(encoding="utf-8")
        assert "Core Operating Principles" in content
        assert "Backup Before You Touch" in content

    @pytest.mark.unit
    def test_subagent_prompt_includes_core_principles(self, tmp_path):
        """Verify subagent prompt resolves core_principles include."""
        # Simulate prompts directory structure
        modules = tmp_path / "modules"
        modules.mkdir()
        (modules / "security.j2").write_text(
            "{# Security #}\n# Security Rules"
        )
        (modules / "core_principles.j2").write_text(
            "{# Core Principles #}\n# Core Operating Principles\n"
            "1. **Backup Before You Touch**"
        )

        subagents = tmp_path / "subagents" / "test-agent"
        subagents.mkdir(parents=True)
        (subagents / "identity.j2").write_text("# Test Agent Identity")
        (subagents / "output.j2").write_text("# Output Rules")
        (subagents / "prompt.j2").write_text(
            "{% include 'subagents/test-agent/identity.j2' %}\n"
            "{% include 'modules/security.j2' %}\n"
            "{% include 'modules/core_principles.j2' %}\n"
            "{% include 'subagents/test-agent/output.j2' %}"
        )

        engine = PromptTemplateEngine(base_dir=tmp_path)
        ctx = PromptContext()
        result = engine.load_and_render(subagents / "prompt.j2", ctx)
        assert "# Test Agent Identity" in result
        assert "# Security Rules" in result
        assert "# Core Operating Principles" in result
        assert "Backup Before You Touch" in result
        assert "# Output Rules" in result
