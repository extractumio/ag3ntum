"""
Structural tests for the reseller feature.

These tests verify architectural invariants without Docker or a running server.
They read source files directly and enforce:

- IDOR scoping: all route handlers reference reseller_id or _get_owned_user
- No core contamination: src/core/ does not import reseller/admin modules
- API key prefix constants exist in api_key_service.py
- Swagger tags are set on reseller and admin routers
- VALID_SCOPES contains all 9 required base scopes
"""
import ast
import os

import pytest

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
CORE_DIR = os.path.join(SRC_DIR, "core")
RESELLER_ROUTE = os.path.join(SRC_DIR, "api", "routes", "reseller.py")
ADMIN_ROUTE = os.path.join(SRC_DIR, "api", "routes", "admin.py")
API_KEY_SERVICE = os.path.join(SRC_DIR, "services", "api_key_service.py")
RESELLER_MODELS = os.path.join(SRC_DIR, "api", "reseller_models.py")
ROUTES_INIT = os.path.join(SRC_DIR, "api", "routes", "__init__.py")


def _read_source(path: str) -> str:
    """Read a source file as a string."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _get_async_function_sources(source: str) -> dict[str, str]:
    """Extract {function_name: source_lines} for all async def functions in source.

    Uses AST parsing for accuracy, then extracts line ranges from the raw source.
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    functions = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            # Collect source lines for this function
            start = node.lineno - 1
            end = node.end_lineno
            func_source = "\n".join(lines[start:end])
            functions[node.name] = func_source

    return functions


def _get_top_level_async_functions(source: str) -> dict[str, str]:
    """Extract only module-level async def functions (not nested)."""
    tree = ast.parse(source)
    lines = source.splitlines()
    functions = {}

    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef):
            start = node.lineno - 1
            end = node.end_lineno
            func_source = "\n".join(lines[start:end])
            functions[node.name] = func_source

    return functions


def _get_all_imports(filepath: str) -> list[str]:
    """Extract all import module names from a Python file."""
    with open(filepath, "r", encoding="utf-8") as f:
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


# =============================================================================
# IDOR Scoping Enforcement
# =============================================================================

class TestResellerScopingEnforcement:
    """Every route handler in reseller.py must reference reseller_id isolation.

    This test ensures no new route is added without scoping it to the
    authenticated reseller, which is the primary IDOR prevention mechanism.
    """

    # Private helpers and utility functions that do not need scoping themselves.
    # Also includes thin delegation wrappers that call a scoped private helper.
    EXCLUDED_FUNCTIONS = {
        "_get_owned_user",    # is the scoping helper itself
        "_get_reseller",      # fetches the reseller record by auth.reseller_id
        "_period_bounds",     # pure datetime utility
        "_api_key_to_response",  # pure serialization helper
        "_read_version",      # reads a file, no DB access
        "_set_skill_enabled",  # internal helper called by enable/disable
        "enable_user_skill",  # delegates entirely to _set_skill_enabled
        "disable_user_skill",  # delegates entirely to _set_skill_enabled
    }

    SCOPING_MARKERS = [
        "reseller_id",
        "auth.reseller_id",
        "_get_owned_user",
        "_get_reseller",
        "_set_skill_enabled",
    ]

    @pytest.mark.unit
    def test_reseller_route_file_exists(self):
        """reseller.py route file must exist at the expected path."""
        assert os.path.isfile(RESELLER_ROUTE), (
            f"Expected reseller route file at {RESELLER_ROUTE}. "
            "This structural test requires the file to exist."
        )

    @pytest.mark.unit
    def test_all_handlers_reference_reseller_scope(self):
        """Each route handler must reference reseller_id or scoping helpers.

        This prevents IDOR by ensuring every handler that accesses data
        is anchored to the authenticated reseller's context.

        If a new handler is added without scoping, this test will fail and
        the implementor must add explicit reseller scoping before merging.
        """
        source = _read_source(RESELLER_ROUTE)
        functions = _get_top_level_async_functions(source)

        violations = []
        for name, func_source in functions.items():
            if name in self.EXCLUDED_FUNCTIONS:
                continue
            if name.startswith("_"):
                continue  # Skip other private helpers

            has_scoping = any(
                marker in func_source for marker in self.SCOPING_MARKERS
            )
            if not has_scoping:
                violations.append(name)

        assert not violations, (
            f"\n{'=' * 60}\n"
            f"RESELLER SCOPING VIOLATION: {len(violations)} handler(s) lack "
            f"reseller_id scoping:\n"
            + "\n".join(f"  - {v}" for v in violations)
            + f"\n{'=' * 60}\n"
            "Fix: Every handler must call _get_owned_user(), _get_reseller(), "
            "or filter by auth.reseller_id / reseller_id. "
            "This prevents IDOR attacks across reseller boundaries.\n"
        )


# =============================================================================
# No Core Module Contamination
# =============================================================================

class TestNoCoreContamination:
    """src/core/ must not import from reseller or admin feature modules.

    The reseller feature is additive — it lives in src/api/ and src/services/.
    Core agent functionality must remain unaware of the reseller domain.
    """

    FORBIDDEN_FROM_CORE = [
        "src.services.reseller_service",
        "src.services.api_key_service",
        "src.api.routes.reseller",
        "src.api.routes.admin",
        "services.reseller_service",
        "services.api_key_service",
    ]

    @pytest.mark.unit
    def test_core_files_do_not_import_reseller_modules(self):
        """No file in src/core/ imports reseller or admin modules."""
        violations = []

        for root, _dirs, files in os.walk(CORE_DIR):
            for filename in files:
                if not filename.endswith(".py"):
                    continue
                filepath = os.path.join(root, filename)
                imports = _get_all_imports(filepath)

                for imp in imports:
                    for forbidden in self.FORBIDDEN_FROM_CORE:
                        if imp == forbidden or imp.startswith(forbidden):
                            rel = os.path.relpath(filepath, PROJECT_ROOT)
                            violations.append(f"  {rel} imports {imp}")

        assert not violations, (
            f"\n{'=' * 60}\n"
            f"CORE CONTAMINATION: src/core/ files import reseller/admin modules:\n"
            + "\n".join(violations)
            + f"\n{'=' * 60}\n"
            "Fix: The reseller feature must be additive. Core must not depend on\n"
            "reseller_service, api_key_service, or reseller/admin routes.\n"
            "Move shared code to src/core/ if Core needs it, or keep it in\n"
            "src/services/ and access it only from src/api/ layer.\n"
        )


# =============================================================================
# API Key Prefix Constants
# =============================================================================

class TestAPIKeyPrefixConstants:
    """api_key_service.py must define the standard key prefixes as constants."""

    @pytest.mark.unit
    def test_reseller_prefix_constant_exists(self):
        """APIKeyService must define PREFIX_RESELLER = 'ag3_res_'."""
        source = _read_source(API_KEY_SERVICE)
        has_dq = 'PREFIX_RESELLER = "ag3_res_"' in source
        has_sq = "PREFIX_RESELLER = 'ag3_res_'" in source
        assert has_dq or has_sq, (
            "api_key_service.py must define the constant: "
            'PREFIX_RESELLER = "ag3_res_"\n'
            "This ensures key prefixes are single-source-of-truth and "
            "cannot silently diverge across the codebase."
        )

    @pytest.mark.unit
    def test_admin_prefix_constant_exists(self):
        """APIKeyService must define PREFIX_ADMIN = 'ag3_adm_'."""
        source = _read_source(API_KEY_SERVICE)
        has_dq = 'PREFIX_ADMIN = "ag3_adm_"' in source
        has_sq = "PREFIX_ADMIN = 'ag3_adm_'" in source
        assert has_dq or has_sq, (
            "api_key_service.py must define the constant: "
            'PREFIX_ADMIN = "ag3_adm_"\n'
            "Admin keys must use a distinct prefix from reseller keys "
            "to prevent privilege confusion during validation."
        )

    @pytest.mark.unit
    def test_prefix_constants_are_used_in_key_creation(self):
        """The prefix constants must be referenced when constructing raw keys."""
        source = _read_source(API_KEY_SERVICE)
        # The key creation must use the prefix variables, not hardcoded strings
        assert "PREFIX_RESELLER" in source
        assert "PREFIX_ADMIN" in source
        assert "ag3_res_" in source  # also present as the constant value

    @pytest.mark.unit
    def test_raw_key_uses_prefix(self):
        """Key generation line must incorporate the prefix variable."""
        source = _read_source(API_KEY_SERVICE)
        # Verify that key construction references the prefix variable
        assert "prefix" in source
        assert "{prefix}" in source or "prefix +" in source or "f\"{prefix}" in source, (
            "The raw_key must be constructed using the prefix variable so that "
            "PREFIX_RESELLER and PREFIX_ADMIN constants are actually used in keys."
        )


# =============================================================================
# Swagger Tags
# =============================================================================

class TestSwaggerTags:
    """Reseller and admin routers must declare OpenAPI tags for grouping."""

    @pytest.mark.unit
    def test_reseller_router_has_tag(self):
        """reseller.py router must declare tags=['reseller']."""
        source = _read_source(RESELLER_ROUTE)
        assert 'tags=["reseller"]' in source or "tags=['reseller']" in source, (
            "The reseller APIRouter must declare tags=[\"reseller\"]. "
            "Without this, reseller endpoints appear ungrouped in Swagger UI, "
            "making the API hard to navigate and document."
        )

    @pytest.mark.unit
    def test_admin_router_has_tag(self):
        """admin.py router must declare tags=['admin']."""
        source = _read_source(ADMIN_ROUTE)
        assert 'tags=["admin"]' in source or "tags=['admin']" in source, (
            "The admin APIRouter must declare tags=[\"admin\"]. "
            "Without this, admin endpoints appear ungrouped in Swagger UI."
        )

    @pytest.mark.unit
    def test_reseller_router_exported_from_init(self):
        """reseller_router must be exported from routes/__init__.py."""
        source = _read_source(ROUTES_INIT)
        assert "reseller_router" in source, (
            "reseller_router must be exported from src/api/routes/__init__.py. "
            "Without this export, FastAPI cannot register the routes."
        )

    @pytest.mark.unit
    def test_admin_router_exported_from_init(self):
        """admin_router must be exported from routes/__init__.py."""
        source = _read_source(ROUTES_INIT)
        assert "admin_router" in source, (
            "admin_router must be exported from src/api/routes/__init__.py."
        )


# =============================================================================
# VALID_SCOPES Completeness
# =============================================================================

class TestValidScopesCompleteness:
    """VALID_SCOPES in reseller_models.py must include all 9 base scopes."""

    REQUIRED_SCOPES = {
        "users:create",
        "users:read",
        "users:update",
        "users:suspend",
        "users:delete",
        "users:password",
        "sessions:read",
        "usage:read",
        "keys:manage",
    }

    def _extract_valid_scopes(self) -> set:
        """Parse VALID_SCOPES from reseller_models.py source using AST.

        This avoids requiring Docker or a running application — we read the
        set literal directly from the source file.
        """
        source = _read_source(RESELLER_MODELS)
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not (isinstance(target, ast.Name) and target.id == "VALID_SCOPES"):
                    continue
                # The value should be a Set literal
                if isinstance(node.value, ast.Set):
                    return {
                        elt.value for elt in node.value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    }
        return set()

    @pytest.mark.unit
    def test_valid_scopes_constant_exists(self):
        """VALID_SCOPES must be defined in reseller_models.py."""
        source = _read_source(RESELLER_MODELS)
        assert "VALID_SCOPES" in source, (
            "VALID_SCOPES set must be defined in src/api/reseller_models.py. "
            "This is the single source of truth for permitted API key scopes."
        )

    @pytest.mark.unit
    def test_valid_scopes_contains_all_required_base_scopes(self):
        """VALID_SCOPES must contain all 9 required base scopes from the spec."""
        valid_scopes = self._extract_valid_scopes()
        assert valid_scopes, (
            "Could not parse VALID_SCOPES from reseller_models.py. "
            "Ensure it is defined as a set literal: VALID_SCOPES = {...}"
        )

        missing = self.REQUIRED_SCOPES - valid_scopes
        assert not missing, (
            f"\n{'=' * 60}\n"
            f"VALID_SCOPES is missing required base scopes:\n"
            + "\n".join(f"  - {s}" for s in sorted(missing))
            + f"\n{'=' * 60}\n"
            "Fix: Add the missing scopes to VALID_SCOPES in reseller_models.py.\n"
            "These 9 scopes are the minimum required by the reseller API spec.\n"
            "VALID_SCOPES may contain additional scopes beyond these 9.\n"
        )

    @pytest.mark.unit
    def test_valid_scopes_is_a_set_literal(self):
        """VALID_SCOPES must be defined as a set literal in source."""
        source = _read_source(RESELLER_MODELS)
        tree = ast.parse(source)

        found_as_set = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "VALID_SCOPES":
                    if isinstance(node.value, ast.Set):
                        found_as_set = True

        assert found_as_set, (
            "VALID_SCOPES must be defined as a set literal in reseller_models.py, "
            "e.g.: VALID_SCOPES = {\"users:create\", \"users:read\", ...}\n"
            "A set provides O(1) membership checks during scope validation."
        )

    @pytest.mark.unit
    def test_valid_scopes_has_no_typos(self):
        """All scopes follow the 'resource:action' naming convention."""
        valid_scopes = self._extract_valid_scopes()
        assert valid_scopes, (
            "Could not parse VALID_SCOPES from reseller_models.py."
        )

        malformed = [s for s in valid_scopes if ":" not in s]
        assert not malformed, (
            "All scopes must follow 'resource:action' format. "
            f"Malformed scopes found: {malformed}\n"
            "Example valid scope: 'users:create', 'sessions:read'."
        )
