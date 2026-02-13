"""
Prompt Template Engine for Ag3ntum.

Provides Claude Code-compatible ${VARIABLE} syntax parsing and rendering.
Replaces Jinja2 for prompt template processing.

Supported syntax (Claude Code v2.1.39 compatible):
- ${VARIABLE_NAME}           - Simple variable substitution
- ${FUNCTION_NAME()}         - Function call
- ${OBJECT.property}         - Object property access
- ${COND?if_true:if_false}   - Simple ternary conditional
- ${VAR!==null?"a":"b"}      - Comparison ternary
- ${ARRAY.join(sep)}         - Array join
- ${ARRAY.length>0?a:b}     - Array length conditional
- ${JSON_STRINGIFY_FN(obj)}  - JSON serialization

Jinja2-compatible directives (for subagent and module templates):
- {% include 'path/to/file.md' %}  - File inclusion with recursive resolution
- {% if VAR %}...{% endif %}        - Conditional blocks
- {% if VAR %}...{% else %}...{% endif %} - Conditional with else
- {# comment #}                     - Block comments (stripped)
"""
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Regex patterns for template syntax (Claude Code v2.1.39 compatible)
# Order matters: more specific patterns must be checked before simpler ones.

# JSON stringify: ${JSON_STRINGIFY_FN(obj)}
JSON_STRINGIFY_PATTERN = re.compile(
    r'\$\{JSON_STRINGIFY_FN\(([^)]+)\)\}'
)
# Array join: ${ARRAY.join(separator)}
ARRAY_JOIN_PATTERN = re.compile(
    r'\$\{([A-Z_][A-Z0-9_]*)\.join\(([^)]*)\)\}'
)
# Array length check: ${ARRAY.length>0?content:""}
ARRAY_LENGTH_PATTERN = re.compile(
    r'\$\{([A-Z_][A-Z0-9_]*)\.length(>|>=|<|<=|===)(\d+)\?([^:]*):([^}]*)\}'
)
# Comparison ternary: ${VAR!==null?"text":"other"} or ${VAR>0?...}
COMPARISON_PATTERN = re.compile(
    r'\$\{([A-Z_][A-Z0-9_]*)(!==|===|>|<|>=|<=)([^?]+)\?([^:]*):([^}]*)\}'
)
# Object access: ${OBJECT.property} or ${OBJECT.nested.property}
OBJECT_ACCESS_PATTERN = re.compile(
    r'\$\{([A-Z_][A-Z0-9_]*)\.([a-zA-Z_][a-zA-Z0-9_.]*)\}'
)
# Function call: ${FUNCTION_NAME()}
FUNCTION_PATTERN = re.compile(
    r'\$\{([A-Z_][A-Z0-9_]*)\(\)\}'
)
# Simple ternary: ${CONDITION?if_true:if_false}
CONDITIONAL_PATTERN = re.compile(
    r'\$\{([A-Z_][A-Z0-9_]*)\?([^:]*):([^}]*)\}'
)
# Simple variable: ${VARIABLE_NAME}
VARIABLE_PATTERN = re.compile(
    r'\$\{([A-Z_][A-Z0-9_]*)\}'
)


@dataclass
class PromptMetadata:
    """Metadata extracted from prompt file header."""
    name: str
    description: str
    version: str = "1.0.0"
    variables: list[str] = field(default_factory=list)
    override_allowed: bool = False


@dataclass
class PromptContext:
    """
    Context for prompt template rendering.

    Contains all variables and functions available during rendering.
    Security: Never include secrets (API keys, passwords) in context.
    """
    # Tool name registry - maps placeholder to full MCP tool name
    tool_names: dict[str, str] = field(default_factory=dict)

    # Environment variables
    environment: dict[str, str] = field(default_factory=dict)

    # Configuration functions (callable)
    functions: dict[str, Callable[[], Any]] = field(default_factory=dict)

    # Boolean flags
    flags: dict[str, bool] = field(default_factory=dict)

    # Static strings
    strings: dict[str, str] = field(default_factory=dict)

    # Objects (dictionaries with nested access)
    objects: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Arrays (lists for join/length operations)
    arrays: dict[str, list[Any]] = field(default_factory=dict)

    def get(self, name: str) -> Any:
        """Get a variable value by name, checking registries in priority order."""
        if name in self.tool_names:
            return self.tool_names[name]
        if name in self.environment:
            return self.environment[name]
        if name in self.flags:
            return self.flags[name]
        if name in self.strings:
            return self.strings[name]
        if name in self.objects:
            return self.objects[name]
        if name in self.arrays:
            return self.arrays[name]
        if name in self.functions:
            # Allow functions to be accessed as values (not just called)
            return self.functions[name]
        return None

    def get_nested(self, name: str, path: str) -> Any:
        """Get a nested property from an object. E.g., OBJECT.property.nested"""
        obj = self.get(name)
        if obj is None:
            return None
        for key in path.split('.'):
            if isinstance(obj, dict) and key in obj:
                obj = obj[key]
            elif hasattr(obj, key):
                obj = getattr(obj, key)
            else:
                return None
        return obj

    def call(self, name: str) -> Any:
        """Call a function by name."""
        if name in self.functions:
            return self.functions[name]()
        return None


# Jinja2-compatible directive patterns
INCLUDE_PATTERN = re.compile(
    r"\{%\s*include\s+['\"]([^'\"]+)['\"]\s*%\}"
)
JINJA2_COMMENT_PATTERN = re.compile(
    r"\{#.*?#\}", re.DOTALL
)
# {% if VAR %}...{% else %}...{% endif %} (with optional else)
JINJA2_IF_PATTERN = re.compile(
    r"\{%\s*if\s+(\w+)\s*%\}(.*?)(?:\{%\s*else\s*%\}(.*?))?\{%\s*endif\s*%\}",
    re.DOTALL,
)

# Maximum include depth to prevent circular includes
MAX_INCLUDE_DEPTH = 5


class PromptTemplateEngine:
    """
    Template engine for Ag3ntum prompts.

    Parses and renders prompts with ${VARIABLE}, ${FUNCTION()}, and
    ${CONDITION?if_true:if_false} syntax. Also supports Jinja2-compatible
    {% include %}, {% if %}, and {# comment #} directives for all templates.
    Uses regex-based parsing (no eval/exec) for security.
    """

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._cache: dict[str, tuple[str, float]] = {}  # path -> (content, mtime)
        self._base_dir = base_dir

    def _resolve_includes(
        self,
        content: str,
        base_dir: Optional[Path] = None,
        depth: int = 0,
        _seen: Optional[set[str]] = None,
    ) -> str:
        """
        Resolve {% include 'path' %} directives by inlining file contents.

        Args:
            content: Template content with possible include directives
            base_dir: Directory for resolving relative include paths
            depth: Current recursion depth (prevents circular includes)
            _seen: Set of already-included file paths (prevents cycles)

        Returns:
            Content with all includes resolved
        """
        if depth >= MAX_INCLUDE_DEPTH:
            logger.warning(
                "Max include depth (%d) reached — possible circular include",
                MAX_INCLUDE_DEPTH,
            )
            return content

        resolve_dir = base_dir or self._base_dir
        if resolve_dir is None:
            return content

        if _seen is None:
            _seen = set()

        def _replace_include(match: re.Match) -> str:
            include_path = match.group(1)
            full_path = resolve_dir / include_path

            # Circular include guard
            canonical = str(full_path.resolve())
            if canonical in _seen:
                logger.warning("Circular include detected: %s", include_path)
                return f"<!-- circular include: {include_path} -->"

            if not full_path.exists():
                logger.warning("Include file not found: %s", full_path)
                return f"<!-- include not found: {include_path} -->"

            try:
                included = full_path.read_text(encoding="utf-8")
            except OSError as e:
                logger.error("Failed to read include %s: %s", full_path, e)
                return f"<!-- include error: {include_path} -->"

            # Strip metadata header from included file
            _, body = self.parse_metadata(included)

            # Recursively resolve includes in the included content
            seen_copy = _seen | {canonical}
            return self._resolve_includes(
                body, base_dir=resolve_dir, depth=depth + 1, _seen=seen_copy
            )

        return INCLUDE_PATTERN.sub(_replace_include, content)

    def _strip_jinja2_comments(self, content: str) -> str:
        """Strip {# ... #} Jinja2-style block comments."""
        return JINJA2_COMMENT_PATTERN.sub("", content)

    def _process_conditionals(
        self, content: str, context: "PromptContext"
    ) -> str:
        """
        Process {% if VAR %}...{% else %}...{% endif %} blocks.

        Evaluates VAR as a boolean from the context (truthy/falsy).
        Supports optional {% else %} clause.
        """
        def _replace_if(match: re.Match) -> str:
            var_name = match.group(1)
            if_body = match.group(2)
            else_body = match.group(3) or ""

            value = context.get(var_name)
            if value:
                return if_body
            return else_body

        # Process iteratively in case of nested conditionals (up to 5 passes)
        result = content
        for _ in range(5):
            new_result = JINJA2_IF_PATTERN.sub(_replace_if, result)
            if new_result == result:
                break
            result = new_result

        return result

    def parse_metadata(self, content: str) -> tuple[PromptMetadata, str]:
        """
        Extract metadata from prompt file header.

        Args:
            content: Raw prompt file content

        Returns:
            Tuple of (metadata, body_content)
        """
        # Match HTML comment header
        header_match = re.match(
            r'^<!--\s*\n(.*?)\n-->\s*\n(.*)$',
            content,
            re.DOTALL,
        )

        if not header_match:
            return PromptMetadata(name="Unknown", description=""), content

        header_text, body = header_match.groups()

        # Parse YAML-like header
        metadata = PromptMetadata(name="Unknown", description="")
        in_variables = False
        for line in header_text.split('\n'):
            stripped = line.strip()
            if stripped.startswith('name:'):
                metadata.name = stripped.split(':', 1)[1].strip().strip("'\"")
                in_variables = False
            elif stripped.startswith('description:'):
                metadata.description = stripped.split(':', 1)[1].strip()
                in_variables = False
            elif stripped.startswith('version:'):
                metadata.version = stripped.split(':', 1)[1].strip()
                in_variables = False
            elif stripped.startswith('override_allowed:'):
                metadata.override_allowed = stripped.split(':', 1)[1].strip().lower() == 'true'
                in_variables = False
            elif stripped.startswith('variables:'):
                val = stripped.split(':', 1)[1].strip()
                if val == '[]':
                    in_variables = False
                else:
                    in_variables = True
            elif stripped.startswith('- ') and in_variables:
                metadata.variables.append(stripped[2:].strip())

        return metadata, body

    def render(self, template: str, context: PromptContext) -> str:
        """
        Render a template with the given context.

        Processing order is from most specific patterns to least specific
        to avoid partial matches.

        Args:
            template: Template string with ${VAR} placeholders
            context: PromptContext with variable values

        Returns:
            Rendered string with all placeholders resolved
        """
        result = template

        # 1. Replace JSON stringify ${JSON_STRINGIFY_FN(obj)}
        def replace_json_stringify(match: re.Match) -> str:
            obj_expr = match.group(1)
            value = context.get(obj_expr)
            return json.dumps(value) if value is not None else "null"

        result = JSON_STRINGIFY_PATTERN.sub(replace_json_stringify, result)

        # 2. Replace array join ${ARRAY.join(separator)}
        def replace_array_join(match: re.Match) -> str:
            arr_name = match.group(1)
            separator = match.group(2).strip('"\'') or ""
            arr = context.get(arr_name)
            if isinstance(arr, list):
                return separator.join(str(x) for x in arr)
            return ""

        result = ARRAY_JOIN_PATTERN.sub(replace_array_join, result)

        # 3. Replace array length conditionals ${ARRAY.length>0?content:""}
        def replace_array_length(match: re.Match) -> str:
            arr_name = match.group(1)
            operator = match.group(2)
            threshold = int(match.group(3))
            if_true = match.group(4).strip('"\'')
            if_false = match.group(5).strip('"\'')
            arr = context.get(arr_name)
            length = len(arr) if isinstance(arr, list) else 0

            condition = False
            if operator == ">":
                condition = length > threshold
            elif operator == ">=":
                condition = length >= threshold
            elif operator == "<":
                condition = length < threshold
            elif operator == "<=":
                condition = length <= threshold
            elif operator == "===":
                condition = length == threshold

            return if_true if condition else if_false

        result = ARRAY_LENGTH_PATTERN.sub(replace_array_length, result)

        # 4. Replace comparison conditionals ${VAR!==null?"text":"other"}
        def replace_comparison(match: re.Match) -> str:
            var_name = match.group(1)
            operator = match.group(2)
            compare_val_raw = match.group(3).strip().strip('"\'')
            if_true = match.group(4).strip('"\'')
            if_false = match.group(5).strip('"\'')
            value = context.get(var_name)

            # Handle null comparisons
            compare_val: Any = compare_val_raw
            if compare_val_raw == "null":
                compare_val = None
            elif compare_val_raw.isdigit():
                compare_val = int(compare_val_raw)

            condition = False
            if operator == "!==":
                condition = value != compare_val
            elif operator == "===":
                condition = value == compare_val
            elif operator == ">":
                condition = value > compare_val if value is not None and compare_val is not None else False
            elif operator == "<":
                condition = value < compare_val if value is not None and compare_val is not None else False
            elif operator == ">=":
                condition = value >= compare_val if value is not None and compare_val is not None else False
            elif operator == "<=":
                condition = value <= compare_val if value is not None and compare_val is not None else False

            return if_true if condition else if_false

        result = COMPARISON_PATTERN.sub(replace_comparison, result)

        # 5. Replace object access ${OBJECT.property}
        def replace_object_access(match: re.Match) -> str:
            obj_name = match.group(1)
            property_path = match.group(2)
            value = context.get_nested(obj_name, property_path)
            return str(value) if value is not None else ""

        result = OBJECT_ACCESS_PATTERN.sub(replace_object_access, result)

        # 6. Replace function calls ${FUNCTION()}
        def replace_function(match: re.Match) -> str:
            func_name = match.group(1)
            value = context.call(func_name)
            return str(value) if value is not None else ""

        result = FUNCTION_PATTERN.sub(replace_function, result)

        # 7. Replace simple conditionals ${COND?if_true:if_false}
        def replace_conditional(match: re.Match) -> str:
            cond_name = match.group(1)
            if_true = match.group(2)
            if_false = match.group(3)
            value = context.get(cond_name)
            return if_true if value else if_false

        result = CONDITIONAL_PATTERN.sub(replace_conditional, result)

        # 8. Replace simple variables ${VAR} (last, to avoid partial matches)
        def replace_variable(match: re.Match) -> str:
            var_name = match.group(1)
            value = context.get(var_name)
            if value is None:
                return f"${{{var_name}}}"
            return str(value)

        result = VARIABLE_PATTERN.sub(replace_variable, result)

        return result

    def load_and_render(
        self,
        file_path: Path,
        context: PromptContext,
        use_cache: bool = True,
    ) -> str:
        """
        Load a prompt file and render it.

        Processing order:
        1. Load file and strip metadata header
        2. Resolve {% include %} directives (recursive)
        3. Strip {# ... #} Jinja2 comments
        4. Process {% if VAR %}...{% endif %} conditionals
        5. Render ${VARIABLE} substitutions

        Args:
            file_path: Path to the prompt file
            context: PromptContext for rendering
            use_cache: Whether to use file cache

        Returns:
            Rendered prompt string
        """
        cache_key = str(file_path)
        if use_cache and cache_key in self._cache:
            cached_content, cached_mtime = self._cache[cache_key]
            try:
                current_mtime = file_path.stat().st_mtime
                if current_mtime == cached_mtime:
                    # Cached content already has includes resolved and comments stripped
                    processed = self._process_conditionals(cached_content, context)
                    return self.render(processed, context)
            except OSError:
                pass  # File may have been deleted; re-read

        content = file_path.read_text(encoding="utf-8")
        _, body = self.parse_metadata(content)

        # Resolve includes relative to the file's parent or base_dir
        resolve_dir = self._base_dir or file_path.parent
        body = self._resolve_includes(body, base_dir=resolve_dir)

        # Strip Jinja2-style block comments
        body = self._strip_jinja2_comments(body)

        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        self._cache[cache_key] = (body, mtime)

        # Process conditionals (context-dependent, not cached)
        processed = self._process_conditionals(body, context)
        return self.render(processed, context)

    def clear_cache(self) -> int:
        """Clear the template cache. Returns number of entries cleared."""
        count = len(self._cache)
        self._cache.clear()
        return count
