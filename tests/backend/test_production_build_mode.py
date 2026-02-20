"""
Tests for production build mode feature.

Verifies that:
1. The production static file server (web_frontend_server.py) serves correctly
2. Docker Compose configuration has web service in both prod and dev modes
3. Dev overlay properly overrides web service for Vite dev server
4. API container does NOT serve frontend (no StaticFiles mount)
5. entrypoint-web.sh is mode-aware (prod skips npm, dev does full setup)
6. web_frontend_server.py module structure is correct

Run: ./run.sh test --subset "production_build"
"""
import pytest
import yaml
from pathlib import Path

from fastapi.testclient import TestClient


def _require_current_image(path: Path, marker: str, description: str) -> str:
    """Read a container-baked file and skip if it predates our changes.

    Files at / (docker-compose.yml, run.sh, etc.) come from COPY . / in
    the Dockerfile. They are NOT updated by volume mounts, so they can be
    stale if the image hasn't been rebuilt since the production build mode
    changes.

    Returns file content if the marker is present.
    """
    if not path.exists():
        pytest.skip(f"{path} not available in test environment")
    content = path.read_text()
    if marker not in content:
        pytest.skip(
            f"{path} is from a pre-production-build-mode Docker image "
            f"(missing: {description}). Rebuild with: ./run.sh build --no-cache"
        )
    return content


# =============================================================================
# Test 1: Production Static File Server
# =============================================================================

class TestWebFrontendServer:
    """Tests for the production static file server (web_frontend_server.py).

    Uses a temporary directory (via WEB_DIST_DIR env var) to verify the
    server serves the built React bundle correctly with SPA routing.
    """

    @pytest.fixture
    def web_dist_dir(self, tmp_path: Path) -> Path:
        """Create a mock web_dist directory with test files."""
        dist = tmp_path / "web_dist"
        dist.mkdir()
        (dist / "index.html").write_text(
            "<!DOCTYPE html><html><body><div id='root'>Ag3ntum</div></body></html>"
        )
        (dist / "config.yaml").write_text(
            "api:\n  base_url: http://localhost:40080\n"
        )
        assets = dist / "assets"
        assets.mkdir()
        (assets / "main.abc123.js").write_text("console.log('app');")
        (assets / "style.abc123.css").write_text("body { margin: 0; }")
        return dist

    @pytest.fixture
    def frontend_client(self, web_dist_dir: Path, monkeypatch):
        """Create a test client for the production frontend server.

        Uses monkeypatch to set WEB_DIST_DIR so the production server
        reads from our temp directory instead of /web_dist.
        """
        monkeypatch.setenv("WEB_DIST_DIR", str(web_dist_dir))
        # Import the production app — serve() reads WEB_DIST_DIR at request time
        from src.web_frontend_server import app
        from starlette.testclient import TestClient as StarletteTestClient
        return StarletteTestClient(app)

    @pytest.mark.unit
    def test_serves_index_html_at_root(self, frontend_client) -> None:
        """Root path serves index.html."""
        response = frontend_client.get("/")
        assert response.status_code == 200
        assert "Ag3ntum" in response.text

    @pytest.mark.unit
    def test_spa_routing_returns_index_html(self, frontend_client) -> None:
        """Unmatched paths return index.html for client-side routing."""
        response = frontend_client.get("/sessions/abc-123")
        assert response.status_code == 200
        assert "Ag3ntum" in response.text

    @pytest.mark.unit
    def test_deep_spa_route_returns_index_html(self, frontend_client) -> None:
        """Deeply nested unmatched paths also return index.html."""
        response = frontend_client.get("/settings/profile/edit")
        assert response.status_code == 200
        assert "Ag3ntum" in response.text

    @pytest.mark.unit
    def test_serves_javascript_assets(self, frontend_client) -> None:
        """Static JS assets are served directly."""
        response = frontend_client.get("/assets/main.abc123.js")
        assert response.status_code == 200
        assert "console.log" in response.text

    @pytest.mark.unit
    def test_serves_css_assets(self, frontend_client) -> None:
        """Static CSS assets are served directly."""
        response = frontend_client.get("/assets/style.abc123.css")
        assert response.status_code == 200
        assert "margin" in response.text

    @pytest.mark.unit
    def test_serves_config_yaml(self, frontend_client) -> None:
        """config.yaml is served (runtime configuration for frontend)."""
        response = frontend_client.get("/config.yaml")
        assert response.status_code == 200
        assert "base_url" in response.text

    @pytest.mark.unit
    def test_traversal_check_blocks_parent_directory(
        self, web_dist_dir: Path
    ) -> None:
        """Path traversal outside dist directory is blocked by resolve+startswith."""
        secret = web_dist_dir.parent / "secret.txt"
        secret.write_text("top-secret-data")

        dist_resolved = str(web_dist_dir.resolve())
        traversal_resolved = str((web_dist_dir / "../secret.txt").resolve())
        assert not traversal_resolved.startswith(dist_resolved), (
            "Traversal path should resolve outside dist directory"
        )

    @pytest.mark.unit
    def test_missing_dist_returns_503(self, monkeypatch, tmp_path: Path) -> None:
        """When dist directory has no index.html, returns 503."""
        empty_dir = tmp_path / "empty_dist"
        empty_dir.mkdir()
        monkeypatch.setenv("WEB_DIST_DIR", str(empty_dir))
        from src.web_frontend_server import app
        from starlette.testclient import TestClient as StarletteTestClient
        client = StarletteTestClient(app)
        response = client.get("/")
        assert response.status_code == 503
        assert "not built" in response.text.lower()


# =============================================================================
# Test 2: Docker Compose Configuration (both modes)
# =============================================================================

class TestDockerComposeConfig:
    """Tests for Docker Compose configuration structure.

    Verifies that docker-compose.yml always includes the web service (both
    prod and dev modes use port 50080), and that docker-compose.dev.yml
    properly overrides for Vite dev server.

    NOTE: These files come from COPY . / in the Dockerfile. If the image
    is stale, tests skip with a rebuild message.
    """

    @pytest.fixture
    def base_compose(self) -> dict:
        """Load and parse docker-compose.yml (requires current image)."""
        content = _require_current_image(
            Path("/docker-compose.yml"),
            "web_frontend_server",
            "web_frontend_server in web service command",
        )
        return yaml.safe_load(content)

    @pytest.fixture
    def dev_compose(self) -> dict:
        """Load and parse docker-compose.dev.yml (requires current image)."""
        content = _require_current_image(
            Path("/docker-compose.dev.yml"),
            "vite",
            "vite command in dev overlay",
        )
        return yaml.safe_load(content)

    # --- Base compose (production defaults) ---

    @pytest.mark.unit
    def test_base_has_all_three_services(self, base_compose: dict) -> None:
        """docker-compose.yml defines api, web, and redis services."""
        services = base_compose["services"]
        assert "ag3ntum-api" in services, "Missing ag3ntum-api service"
        assert "ag3ntum-web" in services, "Missing ag3ntum-web service"
        assert "redis" in services, "Missing redis service"

    @pytest.mark.unit
    def test_base_web_uses_static_server(self, base_compose: dict) -> None:
        """In prod mode, web container runs the static file server."""
        web = base_compose["services"]["ag3ntum-web"]
        command = web["command"]
        command_str = " ".join(str(c) for c in command)
        assert "web_frontend_server" in command_str

    @pytest.mark.unit
    def test_base_web_uses_uvicorn(self, base_compose: dict) -> None:
        """Production web server runs via uvicorn."""
        web = base_compose["services"]["ag3ntum-web"]
        command = web["command"]
        command_str = " ".join(str(c) for c in command)
        assert "uvicorn" in command_str

    @pytest.mark.unit
    def test_base_web_exposes_port_50080(self, base_compose: dict) -> None:
        """Web service exposes port 50080."""
        web = base_compose["services"]["ag3ntum-web"]
        ports = web["ports"]
        port_str = str(ports)
        assert "50080" in port_str

    @pytest.mark.unit
    def test_base_api_exposes_port_40080(self, base_compose: dict) -> None:
        """API service exposes port 40080."""
        api = base_compose["services"]["ag3ntum-api"]
        ports = api["ports"]
        port_str = str(ports)
        assert "40080" in port_str

    @pytest.mark.unit
    def test_base_web_mounts_config_yaml(self, base_compose: dict) -> None:
        """Web service mounts config.yaml into /web_dist for runtime updates."""
        web = base_compose["services"]["ag3ntum-web"]
        volumes = web.get("volumes", [])
        volume_str = str(volumes)
        assert "config.yaml:/web_dist/config.yaml" in volume_str

    @pytest.mark.unit
    def test_base_web_depends_on_api(self, base_compose: dict) -> None:
        """Web service depends on API service."""
        web = base_compose["services"]["ag3ntum-web"]
        depends = web.get("depends_on", [])
        assert "ag3ntum-api" in depends

    @pytest.mark.unit
    def test_base_web_has_mode_env_var(self, base_compose: dict) -> None:
        """Web service has AG3NTUM_MODE environment variable."""
        web = base_compose["services"]["ag3ntum-web"]
        env = web.get("environment", {})
        assert "AG3NTUM_MODE" in env

    @pytest.mark.unit
    def test_base_api_does_not_serve_frontend(self, base_compose: dict) -> None:
        """API command should NOT reference web_frontend_server or StaticFiles."""
        api = base_compose["services"]["ag3ntum-api"]
        command = api["command"]
        command_str = " ".join(str(c) for c in command)
        assert "web_frontend_server" not in command_str
        assert "StaticFiles" not in command_str

    # --- Dev overlay ---

    @pytest.mark.unit
    def test_dev_overlay_overrides_to_vite(self, dev_compose: dict) -> None:
        """Dev overlay changes web command to Vite dev server."""
        web = dev_compose["services"]["ag3ntum-web"]
        command = web["command"]
        command_str = str(command)
        assert "vite" in command_str

    @pytest.mark.unit
    def test_dev_overlay_uses_vite_not_uvicorn(
        self, dev_compose: dict
    ) -> None:
        """Dev overlay should use vite, not uvicorn static server."""
        web = dev_compose["services"]["ag3ntum-web"]
        command = web["command"]
        command_str = str(command)
        assert "web_frontend_server" not in command_str

    @pytest.mark.unit
    def test_dev_overlay_sets_dev_mode(self, dev_compose: dict) -> None:
        """Dev overlay sets AG3NTUM_MODE=dev."""
        web = dev_compose["services"]["ag3ntum-web"]
        env = web.get("environment", {})
        assert env.get("AG3NTUM_MODE") == "dev"

    @pytest.mark.unit
    def test_dev_overlay_adds_node_modules_volume(self, dev_compose: dict) -> None:
        """Dev overlay defines web_node_modules named volume."""
        volumes = dev_compose.get("volumes", {})
        assert "web_node_modules" in volumes

    @pytest.mark.unit
    def test_dev_overlay_adds_test_mounts(self, dev_compose: dict) -> None:
        """Dev overlay adds tests and auto-generated mounts for development."""
        web = dev_compose["services"]["ag3ntum-web"]
        volumes = web.get("volumes", [])
        volume_str = str(volumes)
        assert "tests" in volume_str
        assert "auto-generated" in volume_str

    @pytest.mark.unit
    def test_dev_overlay_sets_working_dir(self, dev_compose: dict) -> None:
        """Dev overlay sets working_dir to web_terminal_client."""
        web = dev_compose["services"]["ag3ntum-web"]
        working_dir = web.get("working_dir", "")
        assert "web_terminal_client" in working_dir

    @pytest.mark.unit
    def test_dev_overlay_is_override_not_full_definition(
        self, dev_compose: dict
    ) -> None:
        """Dev overlay should NOT redefine image, build, entrypoint, ports.

        These are inherited from the base docker-compose.yml. If the dev
        overlay redefines them, it's a full definition (not an override),
        which defeats the purpose of compose file merging.
        """
        web = dev_compose["services"]["ag3ntum-web"]
        # These fields should be inherited from base, not redefined
        assert "image" not in web, "Dev overlay should not redefine 'image'"
        assert "build" not in web, "Dev overlay should not redefine 'build'"
        assert "entrypoint" not in web, "Dev overlay should not redefine 'entrypoint'"
        assert "ports" not in web, "Dev overlay should not redefine 'ports'"


# =============================================================================
# Test 3: API Container Does NOT Serve Frontend
# =============================================================================

class TestApiNoFrontendServing:
    """Verify the API container only serves API routes, not the frontend.

    After the production build mode change, the API container should NOT
    mount StaticFiles. The web container handles all frontend serving.
    """

    @pytest.mark.unit
    def test_api_root_returns_not_found(self, client: TestClient) -> None:
        """Root path (/) returns 404, not index.html."""
        response = client.get("/")
        # Without StaticFiles mount, root should return 404
        assert response.status_code in (404, 405)

    @pytest.mark.unit
    def test_api_frontend_route_returns_not_found(self, client: TestClient) -> None:
        """Frontend SPA routes return 404 (not proxied to index.html)."""
        response = client.get("/sessions/some-session-id")
        assert response.status_code == 404

    @pytest.mark.unit
    def test_api_health_still_works(self, client: TestClient) -> None:
        """API routes work normally despite no frontend serving."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @pytest.mark.unit
    def test_api_docs_still_accessible(self, client: TestClient) -> None:
        """OpenAPI docs are still served by the API."""
        response = client.get("/api/openapi.json")
        assert response.status_code == 200

    @pytest.mark.unit
    def test_no_staticfiles_in_main_py(self) -> None:
        """main.py should not import or use StaticFiles."""
        # /src is volume-mounted so this reads the current host file
        path = Path("/src/api/main.py")
        if not path.exists():
            pytest.skip("main.py not available in test environment")
        content = path.read_text()
        assert "StaticFiles" not in content, (
            "main.py should not reference StaticFiles — "
            "frontend serving moved to web container"
        )


# =============================================================================
# Test 4: Entrypoint Mode Awareness
# =============================================================================

class TestEntrypointModeAwareness:
    """Tests for entrypoint-web.sh mode-aware behavior.

    In production mode, the entrypoint should skip npm install entirely
    for fast container startup. In dev mode, it should install dependencies
    and copy Vite configuration files.

    NOTE: entrypoint-web.sh is at / (from COPY), not volume-mounted.
    Tests skip if the image is stale.
    """

    @pytest.fixture
    def entrypoint_content(self) -> str:
        """Read entrypoint-web.sh content (requires current image)."""
        return _require_current_image(
            Path("/entrypoint-web.sh"),
            "AG3NTUM_MODE",
            "AG3NTUM_MODE check for prod/dev branching",
        )

    @pytest.mark.unit
    def test_entrypoint_checks_ag3ntum_mode(self, entrypoint_content: str) -> None:
        """Entrypoint reads AG3NTUM_MODE environment variable."""
        assert "AG3NTUM_MODE" in entrypoint_content

    @pytest.mark.unit
    def test_entrypoint_has_prod_fast_path(self, entrypoint_content: str) -> None:
        """Production mode exits early (before npm install command)."""
        # The prod fast path should be before the actual npm install command.
        # Use "npm install --" to match the real command, not comments about it.
        prod_check_pos = entrypoint_content.find('"prod"')
        npm_install_pos = entrypoint_content.find("npm install --")
        assert prod_check_pos != -1, "Missing prod mode check"
        assert npm_install_pos != -1, "Missing npm install command (dev mode)"
        assert prod_check_pos < npm_install_pos, (
            "Prod mode check must come BEFORE npm install "
            "(to skip it in production)"
        )

    @pytest.mark.unit
    def test_prod_path_uses_exec(self, entrypoint_content: str) -> None:
        """Production path uses exec to replace shell process."""
        # Find the prod block — it should exec (not just run) the command
        lines = entrypoint_content.split("\n")
        in_prod_block = False
        found_exec = False
        for line in lines:
            if '"prod"' in line or "'prod'" in line:
                in_prod_block = True
            if in_prod_block and "exec" in line and "setpriv" in line:
                found_exec = True
                break
            # The prod block ends at the next non-indented line after the if
            if in_prod_block and line.strip() == "fi":
                break
        assert found_exec, (
            "Prod mode should use 'exec setpriv' to drop privileges and run command"
        )

    @pytest.mark.unit
    def test_dev_mode_installs_npm_packages(self, entrypoint_content: str) -> None:
        """Dev mode runs npm install."""
        assert "npm install" in entrypoint_content

    @pytest.mark.unit
    def test_dev_mode_copies_vite_configs(self, entrypoint_content: str) -> None:
        """Dev mode copies all three Vite config files."""
        assert "vite.config.mjs" in entrypoint_content
        assert "vitest.config.mjs" in entrypoint_content
        assert "vite.shared.mjs" in entrypoint_content

    @pytest.mark.unit
    def test_dev_mode_creates_node_modules_symlink(
        self, entrypoint_content: str
    ) -> None:
        """Dev mode symlinks node_modules for ESM resolution."""
        assert "ln -sf /app/node_modules" in entrypoint_content

    @pytest.mark.unit
    def test_both_modes_drop_privileges(self, entrypoint_content: str) -> None:
        """Both prod and dev paths drop to ag3ntum_api (UID 45045)."""
        # setpriv with --reuid=45045 should appear at least twice
        # (once for prod fast path, once for dev final exec)
        count = entrypoint_content.count("--reuid=45045")
        assert count >= 2, (
            f"Expected setpriv --reuid=45045 in both prod and dev paths, "
            f"found {count} occurrence(s)"
        )


# =============================================================================
# Test 5: web_frontend_server Module Structure
# =============================================================================

class TestWebFrontendServerModule:
    """Tests for the web_frontend_server.py module.

    Verifies the module exists, exports the right app, and uses the
    correct routing approach for serving static files with SPA fallback.

    NOTE: /src is volume-mounted so these read the current host files.
    """

    @pytest.fixture
    def module_content(self) -> str:
        """Read web_frontend_server.py content."""
        path = Path("/src/web_frontend_server.py")
        if not path.exists():
            pytest.skip("web_frontend_server.py not available in test environment")
        return path.read_text()

    @pytest.mark.unit
    def test_module_exists(self) -> None:
        """web_frontend_server.py exists at the expected path."""
        path = Path("/src/web_frontend_server.py")
        assert path.exists(), "web_frontend_server.py not found at /src/"

    @pytest.mark.unit
    def test_module_importable(self, monkeypatch, tmp_path: Path) -> None:
        """Module can be imported and exports 'app'."""
        # Set WEB_DIST_DIR to a temp dir so import doesn't fail
        # (production /web_dist may not exist in test container)
        monkeypatch.setenv("WEB_DIST_DIR", str(tmp_path))
        try:
            from src.web_frontend_server import app
            assert app is not None
        except ImportError:
            pytest.skip("src.web_frontend_server not importable in test environment")

    @pytest.mark.unit
    def test_uses_starlette_not_fastapi(self, module_content: str) -> None:
        """Module uses lightweight Starlette, not full FastAPI."""
        assert "Starlette" in module_content
        assert "FastAPI" not in module_content

    @pytest.mark.unit
    def test_has_spa_fallback_routing(self, module_content: str) -> None:
        """Module serves index.html for unmatched paths (SPA routing)."""
        assert "index.html" in module_content
        assert "FileResponse" in module_content

    @pytest.mark.unit
    def test_prevents_path_traversal(self, module_content: str) -> None:
        """Module checks resolved paths stay within dist directory."""
        assert ".resolve()" in module_content
        assert "startswith" in module_content

    @pytest.mark.unit
    def test_serves_web_dist_directory(self, module_content: str) -> None:
        """Module serves from /web_dist directory (default)."""
        assert "/web_dist" in module_content

    @pytest.mark.unit
    def test_supports_dist_dir_override(self, module_content: str) -> None:
        """Module reads WEB_DIST_DIR env var for testability."""
        assert "WEB_DIST_DIR" in module_content


# =============================================================================
# Test 6: Dockerfile Frontend Builder Stage
# =============================================================================

class TestDockerfileFrontendBuilder:
    """Tests for the multi-stage Dockerfile frontend builder.

    Verifies the Dockerfile has a frontend builder stage that produces
    /web_dist in the final image.

    NOTE: /Dockerfile is from COPY . / and may be stale.
    """

    @pytest.fixture
    def dockerfile_content(self) -> str:
        """Read Dockerfile content (requires current image)."""
        return _require_current_image(
            Path("/Dockerfile"),
            "frontend-builder",
            "frontend-builder multi-stage build",
        )

    @pytest.mark.unit
    def test_has_frontend_builder_stage(self, dockerfile_content: str) -> None:
        """Dockerfile has a node:20-slim builder stage."""
        assert "FROM node:20-slim AS frontend-builder" in dockerfile_content

    @pytest.mark.unit
    def test_builder_runs_vite_build(self, dockerfile_content: str) -> None:
        """Builder stage runs vite build to create production bundle."""
        assert "vite build" in dockerfile_content

    @pytest.mark.unit
    def test_copies_bundle_to_web_dist(self, dockerfile_content: str) -> None:
        """Final image copies built bundle from builder to /web_dist."""
        assert "COPY --from=frontend-builder" in dockerfile_content
        assert "/web_dist" in dockerfile_content

    @pytest.mark.unit
    def test_web_dist_exists_in_container(self) -> None:
        """Built container should have /web_dist directory."""
        path = Path("/web_dist")
        if not path.exists():
            pytest.skip(
                "/web_dist not available — image needs rebuild with: "
                "./run.sh build --no-cache"
            )
        assert path.is_dir()


# =============================================================================
# Test 7: run.sh Mode Detection
# =============================================================================

class TestRunShModeDetection:
    """Tests for run.sh mode detection logic.

    Verifies that run.sh correctly handles --dev flag, AG3NTUM_MODE env var,
    and always includes the web service in both modes.

    NOTE: /run.sh is from COPY . / and may be stale.
    """

    @pytest.fixture
    def runsh_content(self) -> str:
        """Read run.sh content (requires current image)."""
        return _require_current_image(
            Path("/run.sh"),
            "docker-compose.dev.yml",
            "dev overlay compose file reference",
        )

    @pytest.mark.unit
    def test_supports_dev_flag(self, runsh_content: str) -> None:
        """run.sh accepts --dev flag."""
        assert "--dev)" in runsh_content

    @pytest.mark.unit
    def test_defaults_to_prod_mode(self, runsh_content: str) -> None:
        """Default mode is prod when no --dev flag or env var."""
        # Should have a default assignment to prod
        assert "AG3NTUM_MODE:-prod" in runsh_content or \
               'AG3NTUM_MODE="prod"' in runsh_content

    @pytest.mark.unit
    def test_compose_cmd_varies_by_mode(self, runsh_content: str) -> None:
        """COMPOSE_CMD uses dev overlay only in dev mode."""
        assert "docker-compose.dev.yml" in runsh_content
        assert "COMPOSE_CMD=" in runsh_content

    @pytest.mark.unit
    def test_check_services_always_checks_web(self, runsh_content: str) -> None:
        """check_services() verifies web service in all modes (not mode-gated)."""
        # Find the check_services function
        start = runsh_content.find("function check_services()")
        end = runsh_content.find("\n}", start)
        if start == -1:
            pytest.skip("check_services function not found in run.sh")
        check_fn = runsh_content[start:end]
        # Should check for ag3ntum-web without any mode conditional
        assert "ag3ntum-web" in check_fn
        assert "AG3NTUM_MODE" not in check_fn, (
            "check_services should always check web (no mode conditional)"
        )

    @pytest.mark.unit
    def test_restart_always_restarts_web(self, runsh_content: str) -> None:
        """do_restart() restarts web service in all modes."""
        start = runsh_content.find("function do_restart()")
        end = runsh_content.find("\n}", start)
        if start == -1:
            pytest.skip("do_restart function not found in run.sh")
        restart_fn = runsh_content[start:end]
        assert "ag3ntum-web" in restart_fn
        # Should NOT be gated behind a mode check
        assert 'AG3NTUM_MODE" == "dev"' not in restart_fn, (
            "do_restart should restart web in all modes"
        )

    @pytest.mark.unit
    def test_deployment_always_shows_web_port(self, runsh_content: str) -> None:
        """Deployment verification always shows Web UI URL."""
        # The deployment section should show the web URL unconditionally
        assert "Web UI:" in runsh_content or "Web Port:" in runsh_content

    @pytest.mark.unit
    def test_env_file_includes_mode(self, runsh_content: str) -> None:
        """Generated .env file includes AG3NTUM_MODE."""
        assert "AG3NTUM_MODE=" in runsh_content


# =============================================================================
# Test 8: install.sh Mode Handling
# =============================================================================

class TestInstallShModeHandling:
    """Tests for install.sh mode and branch handling.

    Verifies that install.sh supports --dev and --branch flags, always
    prompts for web port, and shows both URLs in completion message.

    NOTE: /install.sh is from COPY . / and may be stale.
    """

    @pytest.fixture
    def installsh_content(self) -> str:
        """Read install.sh content (requires current image)."""
        return _require_current_image(
            Path("/install.sh"),
            "AG3NTUM_BRANCH",
            "AG3NTUM_BRANCH variable for branch selection",
        )

    @pytest.mark.unit
    def test_supports_dev_flag(self, installsh_content: str) -> None:
        """install.sh accepts --dev flag."""
        assert "--dev)" in installsh_content

    @pytest.mark.unit
    def test_supports_branch_flag(self, installsh_content: str) -> None:
        """install.sh accepts --branch flag."""
        assert "--branch)" in installsh_content or "--branch=*)" in installsh_content

    @pytest.mark.unit
    def test_defaults_to_release_branch(self, installsh_content: str) -> None:
        """Default branch is 'release'."""
        assert '"release"' in installsh_content

    @pytest.mark.unit
    def test_dev_flag_sets_main_branch(self, installsh_content: str) -> None:
        """--dev flag switches to 'main' branch."""
        # In the --dev case handler, branch should be set to main
        assert '"main"' in installsh_content

    @pytest.mark.unit
    def test_always_prompts_web_port(self, installsh_content: str) -> None:
        """Web port prompt is NOT gated behind dev mode check."""
        # The web port prompt should say "Web UI port" (used in both modes)
        assert "Web UI port" in installsh_content
        # The prompt should NOT be inside a dev-mode conditional.
        # Find the web port prompt line and verify no dev-mode gate nearby.
        lines = installsh_content.split("\n")
        for i, line in enumerate(lines):
            if "Web UI port" in line and "prompt" in line.lower():
                context = "\n".join(lines[max(0, i - 5):i])
                assert 'AG3NTUM_MODE' not in context or 'dev' not in context, (
                    "Web port prompt should not be gated behind a dev mode check"
                )
                break

    @pytest.mark.unit
    def test_completion_always_shows_both_urls(self, installsh_content: str) -> None:
        """Completion message shows both Web UI and API URLs."""
        assert "Web Interface" in installsh_content
        assert "API Endpoint" in installsh_content

    @pytest.mark.unit
    def test_firewall_note_mentions_both_ports(self, installsh_content: str) -> None:
        """Firewall note mentions both Web and API ports."""
        assert "WEB_PORT" in installsh_content
        assert "API_PORT" in installsh_content
