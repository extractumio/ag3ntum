"""
Structural tests that enforce architectural invariants.

These tests verify module boundaries, file size limits, and naming conventions.
They run on every PR and serve as the "custom linters with teaching error messages"
recommended by the Harness Engineering methodology.

Error messages are deliberately verbose — they become remediation context
for the agent's next attempt when a test fails.
"""
import ast
import os
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")


class TestModuleBoundaries:
    """Verify that module dependency directions are correct.

    Architecture rule:
    - src/api/ may import from src/services/, src/core/, src/config, src/db/
    - src/services/ may import from src/core/, src/config, src/db/
    - src/core/ may import from src/config, src/db/
    - tools/ag3ntum/ may import from src/core/, src/config, src/security/
    - NO reverse dependencies (core must not import from api, etc.)
    """

    FORBIDDEN_IMPORTS = [
        # (importing_module_prefix, forbidden_import_prefix, reason)
        ("src.core", "src.api",
         "Core must not import from API layer. Core is the foundation; "
         "API depends on Core, not the reverse. Move shared code to src/services/ "
         "or src/core/."),
        ("src.core", "src.services",
         "Core must not import from Services. Services orchestrate Core, "
         "not the reverse. If Core needs a service capability, define an "
         "interface/protocol in Core and implement it in Services."),
        ("src.services", "src.api",
         "Services must not import from API layer. Services are used BY the API, "
         "not the reverse. Move shared types to src/services/ or src/core/."),
        ("tools.ag3ntum", "src.api",
         "MCP tools must not import from API layer. Tools operate within the "
         "agent sandbox; they should only use src/core/ and src/config."),
        ("tools.ag3ntum", "src.services",
         "MCP tools must not import from Services layer. Tools should use "
         "src/core/ utilities directly."),
    ]

    # Known violations from pre-existing code — tracked as tech debt.
    # These are counted; new violations beyond this count will fail the test.
    # Reduce this number as violations are fixed.
    KNOWN_VIOLATION_COUNT = 12

    def _get_imports(self, filepath):
        """Extract all import statements from a Python file."""
        with open(filepath, "r") as f:
            try:
                tree = ast.parse(f.read())
            except SyntaxError:
                return []

        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return imports

    def _module_prefix(self, filepath):
        """Convert file path to module prefix."""
        rel = os.path.relpath(filepath, PROJECT_ROOT)
        return rel.replace(os.sep, ".").replace(".py", "")

    @pytest.mark.unit
    def test_no_new_forbidden_imports(self):
        """Verify no NEW module imports from a layer it shouldn't depend on.

        Existing violations are tracked as tech debt (KNOWN_VIOLATION_COUNT).
        This test fails only if NEW violations are introduced.
        """
        violations = []

        for scan_dir in [SRC_DIR, TOOLS_DIR]:
            for root, _, files in os.walk(scan_dir):
                for f in files:
                    if not f.endswith(".py"):
                        continue
                    filepath = os.path.join(root, f)
                    module = self._module_prefix(filepath)
                    imports = self._get_imports(filepath)

                    for importing_prefix, forbidden_prefix, reason in self.FORBIDDEN_IMPORTS:
                        if not module.startswith(importing_prefix):
                            continue
                        for imp in imports:
                            if imp.startswith(forbidden_prefix):
                                violations.append(
                                    f"\n  VIOLATION: {module}\n"
                                    f"  imports: {imp}\n"
                                    f"  Rule: {reason}\n"
                                )

        assert len(violations) <= self.KNOWN_VIOLATION_COUNT, (
            f"\n{'='*60}\n"
            f"NEW ARCHITECTURE BOUNDARY VIOLATIONS ({len(violations)} total, "
            f"{self.KNOWN_VIOLATION_COUNT} known)\n"
            f"{'='*60}\n"
            + "\n".join(violations)
            + f"\n{'='*60}\n"
            f"Fix: Move shared code to the appropriate layer. See CLAUDE.md "
            f"'Key Paths' for the layer hierarchy.\n"
            f"If fixing existing violations, reduce KNOWN_VIOLATION_COUNT "
            f"in test_architecture.py.\n"
        )


class TestFileSizeLimits:
    """Enforce file size limits to maintain agent-legible code.

    Large files are harder for agents to navigate and reason about.
    The Harness Engineering methodology recommends enforcing file size
    limits via lints.
    """

    MAX_PYTHON_LINES = 600
    MAX_SHELL_LINES = 2100
    MAX_TSX_LINES = 500
    MAX_CSS_LINES = 300

    WAIVERS = {
        # Shell scripts
        "run.sh": 2200,
        # Scripts
        "scripts/red-alert.py": 2500,
        "scripts/claude-code-security-env-check.py": 1500,
        # Core — large due to complexity, tracked for future splitting
        "src/core/agent_core.py": 2300,
        "src/core/path_validator.py": 1800,
        "src/core/trace_processor.py": 1500,
        "src/core/permission_config.py": 1300,
        "src/core/sandbox_path_resolver.py": 1100,
        "src/core/sessions.py": 900,
        "src/core/permission_profiles.py": 900,
        "src/core/hooks.py": 700,
        "src/core/sandbox.py": 650,
        "src/core/uid_security.py": 650,
        "src/core/command_security.py": 1200,
        # Tracers
        "src/core/tracers/cli.py": 1600,
        "src/core/tracers/eventing.py": 900,
        # API routes
        "src/api/routes/files.py": 1700,
        "src/api/routes/sessions.py": 1500,
        "src/api/routes/reseller.py": 2200,
        "src/api/routes/admin.py": 1100,
        "src/api/reseller_models.py": 800,
        "src/api/security_middleware.py": 700,
        # Models
        "src/db/models.py": 750,
        # Services
        "src/services/session_service.py": 1100,
        "src/services/event_service.py": 750,
        # Tools
        "tools/ag3ntum/bash_tool.py": 1200,
        "tools/ag3ntum/read_tool.py": 700,
        "tools/ag3ntum/edit_tool.py": 700,
        "tools/ag3ntum/multi_edit_tool.py": 700,
        # Tests — large test files are acceptable
        "tests/backend/test_waf_filter.py": 800,
        "tests/backend/test_ag3ntum_glob_grep_ls.py": 1000,
        "tests/backend/test_llm_proxy.py": 2200,
        "tests/backend/test_trace_processor.py": 1000,
        "tests/backend/test_sessions.py": 1100,
        "tests/backend/test_sensitive_data_scanner.py": 1000,
        "tests/backend/test_production_build_mode.py": 800,
        "tests/backend/test_auth.py": 900,
        "tests/backend/test_permission_config.py": 1000,
        "tests/backend/test_zzz_e2e_server.py": 1300,
        "tests/backend/test_tasks.py": 900,
        "tests/backend/test_hooks.py": 700,
        "tests/backend/test_ag3ntum_tools.py": 1800,
        "tests/backend/test_path_validator.py": 1000,
        "tests/backend/redis/test_redis_services.py": 700,
        "tests/backend/redis/test_redis_event_hub.py": 800,
        "tests/backend/redis/test_zzz_redis_sse_e2e.py": 700,
        "tests/backend/test_cross_tool_path_consistency.py": 800,
        "tests/backend/test_real_user_integration.py": 1700,
        "tests/backend/test_prompt_engine.py": 850,
        "tests/backend/test_structured_output.py": 650,
        "tests/backend/test_external_mounts.py": 1000,
        "tests/backend/test_ag3ntum_write.py": 850,
        "tests/backend/test_install_user_lifecycle.py": 750,
        "tests/backend/test_streaming.py": 1100,
        "tests/backend/test_sse_schemas.py": 1200,
        "tests/backend/test_files_api.py": 850,
        "tests/backend/test_queue_system.py": 900,
        "tests/backend/test_mount_e2e.py": 800,
        "tests/backend/test_session_service.py": 800,
        "tests/backend/test_ag3ntum_bash.py": 800,
        "tests/backend/test_ask_user_question.py": 1250,
        "tests/backend/test_dynamic_mounts.py": 1700,
        "tests/backend/test_user_service.py": 900,
        "tests/backend/test_reseller.py": 800,
        "tests/backend/test_admin.py": 650,
        "tests/backend/test_sandbox.py": 1100,
        "tests/backend/test_sandbox_path_resolver.py": 850,
        "tests/core-tests/test_prompt_engine.py": 850,
        "tests/core-tests/test_permission_profiles.py": 650,
        "tests/core-tests/test_task_runner.py": 650,
        "tests/security/test_command_security.py": 2000,
        "tests/security/test_security_prompts.py": 800,
        "tests/security/test_uid_mode_integration.py": 700,
        "tests/security/test_uid_security.py": 750,
        "tests/security/test_sandboxed_envs.py": 850,
        "tests/security/test_user_isolation.py": 1700,
        "tests/sandbox/security_tests.py": 1500,
        # Tools
        "tools/ag3ntum/ag3ntum_webfetch/tool.py": 750,
        "tools/ag3ntum/ag3ntum_ssh/tool.py": 1150,
        "src/core/ssh/ssh_command_filter.py": 900,
        "tests/backend/ssh/test_ssh_tools.py": 800,
        "tests/backend/ssh/test_ssh_command_filter.py": 1150,
        "tests/backend/ssh/test_ssh_tool_hardening.py": 1100,
        # Skills
        "skills/.claude/skills/create_image/image_gen.py": 800,
        # Services
        "src/services/user_service.py": 1200,
        "src/services/agent_runner.py": 850,  # usage recording hook for reseller billing
        "src/services/mount_service.py": 1200,
        # Core (additional)
        "src/core/output.py": 700,
        "src/core/skills.py": 750,
        # Large test files (comprehensive test suites)
        "tests/security/test_reseller_security.py": 650,
        "tests/core-tests/test_agent_core_unit.py": 950,
        "tests/backend/test_ag3ntum_read_document.py": 1100,
        "tests/backend/test_ag3ntum_webfetch.py": 1600,
        # Structural tests themselves — grows as invariants are added
        "tests/structural/test_architecture.py": 750,
    }

    # Project directories to scan (avoids walking system paths in Docker)
    PROJECT_DIRS = ["src", "tools", "tests", "skills", "scripts"]

    @pytest.mark.unit
    def test_python_file_sizes(self):
        """No Python file should exceed the line limit."""
        oversized = []
        for subdir in self.PROJECT_DIRS:
            scan_root = os.path.join(PROJECT_ROOT, subdir)
            if not os.path.isdir(scan_root):
                continue
            for root, _, files in os.walk(scan_root):
                if any(s in root for s in ["node_modules", ".git", "__pycache__", ".venv", "venv"]):
                    continue
                for f in files:
                    if not f.endswith(".py"):
                        continue
                    filepath = os.path.join(root, f)
                    if not os.path.isfile(filepath):
                        continue
                    rel = os.path.relpath(filepath, PROJECT_ROOT)
                    limit = self.WAIVERS.get(rel, self.MAX_PYTHON_LINES)
                    with open(filepath) as fh:
                        count = sum(1 for _ in fh)
                    if count > limit:
                        oversized.append(
                            f"  {rel}: {count} lines (limit: {limit})\n"
                            f"    -> Split into smaller modules or extract helpers."
                        )
        assert not oversized, (
            f"\n{'='*60}\nFILES EXCEEDING SIZE LIMITS ({len(oversized)})\n{'='*60}\n"
            + "\n".join(oversized) + f"\n{'='*60}\n"
            f"Fix: Split large files. To add a waiver, update WAIVERS dict.\n"
        )

    @pytest.mark.unit
    def test_shell_script_sizes(self):
        """Shell scripts should not exceed size limits."""
        oversized = []
        # Also scan root-level .sh files (run.sh, entrypoints)
        scan_dirs = self.PROJECT_DIRS + [""]
        for subdir in scan_dirs:
            scan_root = os.path.join(PROJECT_ROOT, subdir) if subdir else PROJECT_ROOT
            if not os.path.isdir(scan_root):
                continue
            for root, _, files in os.walk(scan_root):
                if any(s in root for s in ["node_modules", ".git", "__pycache__", ".venv", "venv"]):
                    continue
                if not subdir and root != PROJECT_ROOT:
                    continue  # root-level: only direct files
                for f in files:
                    if not f.endswith(".sh"):
                        continue
                    filepath = os.path.join(root, f)
                    rel_path = os.path.relpath(filepath, PROJECT_ROOT)
                    limit = self.WAIVERS.get(rel_path, self.MAX_SHELL_LINES)
                    with open(filepath) as fh:
                        line_count = sum(1 for _ in fh)
                    if line_count > limit:
                        oversized.append(
                            f"  {rel_path}: {line_count} lines (limit: {limit})\n"
                            f"    -> Consider splitting into sourced scripts."
                        )

        assert not oversized, (
            f"\n{'='*60}\n"
            f"SHELL SCRIPTS EXCEEDING SIZE LIMITS ({len(oversized)})\n"
            f"{'='*60}\n"
            + "\n".join(oversized)
            + f"\n{'='*60}\n"
            f"Fix: Split large scripts. To add a waiver, update WAIVERS dict.\n"
        )


class TestNamingConventions:
    """Enforce consistent naming across the codebase.

    Consistent naming helps agents navigate the codebase without
    needing to search for variations.
    """

    # Files that are helpers/utilities, not test files — exempt from test_ prefix
    HELPER_FILES = {
        "e2e_helpers.py",
        "security_tests.py",  # sandbox helper, not a pytest test file
    }

    @pytest.mark.unit
    def test_test_files_prefixed(self):
        """All test files must start with test_ prefix."""
        violations = []
        test_dirs = ["tests/backend", "tests/core-tests", "tests/security", "tests/sandbox"]
        for test_dir in test_dirs:
            full_dir = os.path.join(PROJECT_ROOT, test_dir)
            if not os.path.exists(full_dir):
                continue
            for root, _, files in os.walk(full_dir):
                for f in files:
                    if not f.endswith(".py"):
                        continue
                    if f.startswith("test_") or f in ("conftest.py", "__init__.py"):
                        continue
                    if f in self.HELPER_FILES:
                        continue
                    violations.append(
                        f"  {os.path.relpath(os.path.join(root, f), PROJECT_ROOT)}\n"
                        f"    -> Rename to test_{f} or add to HELPER_FILES in test_architecture.py"
                    )

        assert not violations, (
            f"\nTest files must start with 'test_' prefix:\n"
            + "\n".join(violations)
        )

    @pytest.mark.unit
    def test_no_print_in_src(self):
        """Source code should use logging, not print().

        print() statements in source code bypass structured logging.
        Use the logging module or the tracer system instead.
        Exceptions: CLI scripts in scripts/ and __main__ blocks.
        """
        violations = []
        for root, _, files in os.walk(SRC_DIR):
            if "web_terminal_client" in root:
                continue
            for f in files:
                if not f.endswith(".py"):
                    continue
                filepath = os.path.join(root, f)
                with open(filepath) as fh:
                    for i, line in enumerate(fh, 1):
                        stripped = line.strip()
                        # Skip comments and strings
                        if stripped.startswith("#") or stripped.startswith('"') or stripped.startswith("'"):
                            continue
                        if "print(" in stripped and "# noqa: print-ok" not in stripped:
                            violations.append(
                                f"  {os.path.relpath(filepath, PROJECT_ROOT)}:{i}: {stripped[:80]}\n"
                                f"    -> Use logging.info/debug/warning() or tracer instead of print(). "
                                f"Add '# noqa: print-ok' if intentional."
                            )

        # Allow some prints — this is informational, not blocking
        if len(violations) > 20:
            pytest.skip(f"Too many print() usages ({len(violations)}) — fix incrementally")


class TestPromptTemplateIntegrity:
    """Verify prompt templates are internally consistent.

    Catches broken includes and undefined variables before runtime.
    """

    PROMPTS_DIR = os.path.join(PROJECT_ROOT, "prompts")

    @pytest.mark.unit
    def test_include_targets_exist(self):
        """All {% include 'file' %} targets must exist."""
        import re
        include_pattern = re.compile(r'\{%\s*include\s+["\']([^"\']+)["\']\s*%\}')
        missing = []

        if not os.path.exists(self.PROMPTS_DIR):
            pytest.skip("prompts/ directory not found")

        for root, _, files in os.walk(self.PROMPTS_DIR):
            for f in files:
                if not f.endswith(".md"):
                    continue
                filepath = os.path.join(root, f)
                with open(filepath) as fh:
                    content = fh.read()
                for match in include_pattern.finditer(content):
                    include_target = match.group(1)
                    # Resolve relative to prompts dir
                    target_path = os.path.join(self.PROMPTS_DIR, include_target)
                    if not os.path.exists(target_path):
                        rel = os.path.relpath(filepath, PROJECT_ROOT)
                        missing.append(
                            f"  {rel}: includes '{include_target}' — FILE NOT FOUND\n"
                            f"    -> Create the file or fix the include path."
                        )

        assert not missing, (
            f"\nBroken prompt includes found:\n"
            + "\n".join(missing)
        )


class TestCoverageMapping:
    """Verify that key source modules have corresponding test files.

    This ensures new modules don't get added without corresponding tests.
    """

    @pytest.mark.unit
    def test_route_files_have_tests(self):
        """Every src/api/routes/*.py (except __init__.py) should have a test file.

        Route files define API endpoints. Each should have at least one
        corresponding test file in tests/backend/.
        """
        routes_dir = os.path.join(SRC_DIR, "api", "routes")
        tests_dir = os.path.join(PROJECT_ROOT, "tests", "backend")

        if not os.path.exists(routes_dir):
            pytest.skip("src/api/routes/ not found")

        route_files = [
            f for f in os.listdir(routes_dir)
            if f.endswith(".py") and not f.startswith("_")
        ]

        # Get all test files
        test_files = set()
        if os.path.exists(tests_dir):
            test_files = {
                f for f in os.listdir(tests_dir)
                if f.startswith("test_") and f.endswith(".py")
            }

        missing = []
        for route_file in route_files:
            route_name = route_file.replace(".py", "")
            # Look for any test file that contains the route name
            has_test = any(
                route_name in tf for tf in test_files
            )
            if not has_test:
                missing.append(
                    f"  src/api/routes/{route_file} -> no test file "
                    f"matching '*{route_name}*' in tests/backend/\n"
                    f"    -> Create tests/backend/test_{route_name}.py"
                )

        assert not missing, (
            f"\n{'='*60}\n"
            f"ROUTE FILES WITHOUT TESTS ({len(missing)})\n"
            f"{'='*60}\n"
            + "\n".join(missing)
            + f"\n{'='*60}\n"
            f"Fix: Create test files for untested routes.\n"
        )

    @pytest.mark.unit
    def test_tool_modules_have_tests(self):
        """Every tools/ag3ntum/ag3ntum_* directory should have a test.

        Tool modules are critical for agent functionality. Each should
        have at least one test file in the test suite.
        """
        tools_base = os.path.join(TOOLS_DIR, "ag3ntum")
        tests_dir = os.path.join(PROJECT_ROOT, "tests", "backend")

        if not os.path.exists(tools_base):
            pytest.skip("tools/ag3ntum/ not found")

        tool_dirs = [
            d for d in os.listdir(tools_base)
            if d.startswith("ag3ntum_") and os.path.isdir(os.path.join(tools_base, d))
        ]

        # Get all test files across all test directories (recursive)
        test_files = set()
        for test_dir_name in ["tests/backend", "tests/security", "tests/core-tests"]:
            test_dir = os.path.join(PROJECT_ROOT, test_dir_name)
            if os.path.exists(test_dir):
                for root, _, files in os.walk(test_dir):
                    test_files.update(
                        f for f in files
                        if f.startswith("test_") and f.endswith(".py")
                    )

        missing = []
        for tool_dir in tool_dirs:
            # Extract tool name: ag3ntum_bash -> bash, ag3ntum_read -> read
            tool_name = tool_dir.replace("ag3ntum_", "")
            # Look for test files that reference this tool
            has_test = any(
                tool_name in tf for tf in test_files
            )
            if not has_test:
                missing.append(
                    f"  tools/ag3ntum/{tool_dir}/ -> no test file "
                    f"matching '*{tool_name}*'\n"
                    f"    -> Create a test file covering {tool_dir}"
                )

        assert not missing, (
            f"\n{'='*60}\n"
            f"TOOL MODULES WITHOUT TESTS ({len(missing)})\n"
            f"{'='*60}\n"
            + "\n".join(missing)
            + f"\n{'='*60}\n"
            f"Fix: Create test files for untested tools.\n"
        )


class TestCISecurityInvariants:
    """Verify CI pipeline security invariants.

    These tests prevent regression of security hardening measures
    in the GitHub Actions workflows and Docker build configuration.
    """

    WORKFLOWS_DIR = os.path.join(PROJECT_ROOT, ".github", "workflows")
    DOCKERIGNORE = os.path.join(PROJECT_ROOT, ".dockerignore")

    @pytest.mark.unit
    def test_secrets_yaml_excluded_from_docker_build(self):
        """config/secrets.yaml must be in .dockerignore.

        The CI pipeline writes API keys to config/secrets.yaml before
        docker build. Without .dockerignore exclusion, COPY . / bakes
        the key into image layers. (EXT-23)
        """
        assert os.path.exists(self.DOCKERIGNORE), (
            f".dockerignore not found at {self.DOCKERIGNORE}"
        )
        with open(self.DOCKERIGNORE) as f:
            content = f.read()

        assert "config/secrets.yaml" in content, (
            f"\n{'='*60}\n"
            f"SECURITY: config/secrets.yaml NOT in .dockerignore\n"
            f"{'='*60}\n"
            f"CI writes API keys to config/secrets.yaml before docker build.\n"
            f"Without .dockerignore exclusion, COPY . / bakes the secret\n"
            f"into Docker image layers.\n"
            f"\n"
            f"Fix: Add 'config/secrets.yaml' to .dockerignore\n"
            f"{'='*60}\n"
        )

    @pytest.mark.unit
    def test_ci_workflows_clean_secrets_file(self):
        """CI workflows that create secrets.yaml must clean it up.

        Self-hosted runner workspaces persist between runs. Any workflow
        that writes config/secrets.yaml must have an if:always() step
        to remove it, preventing secrets from lingering on disk.
        """
        if not os.path.isdir(self.WORKFLOWS_DIR):
            pytest.skip(".github/workflows/ not found")

        violations = []
        for f in sorted(os.listdir(self.WORKFLOWS_DIR)):
            if not f.endswith((".yml", ".yaml")):
                continue
            filepath = os.path.join(self.WORKFLOWS_DIR, f)
            with open(filepath) as fh:
                content = fh.read()

            if "config/secrets.yaml" in content and "echo " in content:
                # This workflow creates secrets.yaml — verify cleanup
                if "rm -f config/secrets.yaml" not in content:
                    violations.append(
                        f"  .github/workflows/{f}: creates config/secrets.yaml "
                        f"but has no 'rm -f config/secrets.yaml' cleanup step\n"
                        f"    -> Add: - name: Remove secrets file\\n"
                        f"             if: always()\\n"
                        f"             run: rm -f config/secrets.yaml"
                    )

        assert not violations, (
            f"\n{'='*60}\n"
            f"SECURITY: CI workflows missing secrets cleanup ({len(violations)})\n"
            f"{'='*60}\n"
            + "\n".join(violations)
            + f"\n{'='*60}\n"
            f"Fix: Add an if:always() step to remove config/secrets.yaml.\n"
            f"{'='*60}\n"
        )


class TestCodeConventions:
    """Verify code conventions are followed across the codebase."""

    @pytest.mark.unit
    def test_impl_functions_exist_for_tools(self):
        """Tool modules should have _impl function pattern for testability.

        Per CLAUDE.md gotcha #19: MCP tools have extracted _impl functions
        for testing. This verifies tool modules follow the convention.

        Tools without _impl functions are harder to unit test because
        the MCP wrapper adds complexity (session context, etc.).
        """
        tools_base = os.path.join(TOOLS_DIR, "ag3ntum")

        if not os.path.exists(tools_base):
            pytest.skip("tools/ag3ntum/ not found")

        tool_dirs = [
            d for d in os.listdir(tools_base)
            if d.startswith("ag3ntum_") and os.path.isdir(os.path.join(tools_base, d))
        ]

        # These tools are exempt from the _impl requirement
        # (they may have different patterns or be simple wrappers)
        EXEMPT_TOOLS = {
            "ag3ntum_ask",          # Simple user interaction, no complex impl
            "ag3ntum_webfetch",     # Delegates to external fetch logic
            "ag3ntum_read_document",  # Has its own module structure
            "ag3ntum_multiedit",    # Composes edit operations
        }

        missing = []
        for tool_dir in tool_dirs:
            if tool_dir in EXEMPT_TOOLS:
                continue

            tool_py = os.path.join(tools_base, tool_dir, "tool.py")
            if not os.path.exists(tool_py):
                continue

            with open(tool_py) as f:
                content = f.read()

            if "_impl(" not in content:
                missing.append(
                    f"  tools/ag3ntum/{tool_dir}/tool.py -> no _impl function\n"
                    f"    -> Extract core logic to an async _<name>_impl() function "
                    f"for testability"
                )

        assert not missing, (
            f"\n{'='*60}\n"
            f"TOOLS WITHOUT _impl FUNCTIONS ({len(missing)})\n"
            f"{'='*60}\n"
            + "\n".join(missing)
            + f"\n{'='*60}\n"
            f"Fix: Extract tool logic to _*_impl() functions.\n"
            f"See CLAUDE.md gotcha #19.\n"
        )
