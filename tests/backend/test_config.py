"""
Tests for the config endpoints.

Tests the /api/v1/config/system_prompt admin-only endpoint.
Tests the /api/v1/config/prompts/reload admin-only endpoint.
"""
import pytest
from unittest.mock import patch, MagicMock


class TestSystemPromptEndpoint:
    """Tests for GET /api/v1/config/system_prompt."""

    def test_admin_can_access_system_prompt(
        self, client, admin_auth_headers
    ):
        """Admin users should be able to access the system prompt."""
        with patch("src.api.routes.config.get_prompt_manager") as mock_get_manager:
            mock_manager = MagicMock()
            mock_manager.build_system_prompt.return_value = "Test system prompt content"
            mock_manager.get_available_roles.return_value = ["default", "researcher"]
            mock_manager.get_prompt_modules.return_value = ["identity", "security", "output"]
            mock_get_manager.return_value = mock_manager

            response = client.get(
                "/api/v1/config/system_prompt",
                headers=admin_auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert "prompt" in data
            assert data["prompt"] == "Test system prompt content"
            assert data["role"] == "default"
            assert data["model"] == "claude-sonnet-4-20250514"
            assert "available_roles" in data
            assert "prompt_modules" in data

    def test_regular_user_gets_403(self, client, auth_headers):
        """Regular users should get 403 Forbidden."""
        response = client.get(
            "/api/v1/config/system_prompt",
            headers=auth_headers,
        )

        assert response.status_code == 403
        assert "admin" in response.json()["detail"].lower()

    def test_unauthenticated_gets_401(self, client):
        """Unauthenticated requests should get 401."""
        response = client.get("/api/v1/config/system_prompt")

        # FastAPI returns 403 for missing bearer token with auto_error=True
        assert response.status_code in [401, 403]

    def test_invalid_token_gets_401(self, client):
        """Invalid tokens should get 401."""
        response = client.get(
            "/api/v1/config/system_prompt",
            headers={"Authorization": "Bearer invalid-token"},
        )

        assert response.status_code == 401

    def test_custom_role_parameter(self, client, admin_auth_headers):
        """Admin can request system prompt with custom role."""
        with patch("src.api.routes.config.get_prompt_manager") as mock_get_manager:
            mock_manager = MagicMock()
            mock_manager.build_system_prompt.return_value = "Custom role prompt"
            mock_manager.get_available_roles.return_value = ["default", "researcher"]
            mock_manager.get_prompt_modules.return_value = ["identity"]
            mock_get_manager.return_value = mock_manager

            response = client.get(
                "/api/v1/config/system_prompt?role=researcher",
                headers=admin_auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert data["role"] == "researcher"

            # Verify the manager was called with the custom role
            mock_manager.build_system_prompt.assert_called_once()
            call_kwargs = mock_manager.build_system_prompt.call_args.kwargs
            assert call_kwargs["role"] == "researcher"

    def test_custom_model_parameter(self, client, admin_auth_headers):
        """Admin can request system prompt with custom model."""
        with patch("src.api.routes.config.get_prompt_manager") as mock_get_manager:
            mock_manager = MagicMock()
            mock_manager.build_system_prompt.return_value = "Custom model prompt"
            mock_manager.get_available_roles.return_value = ["default"]
            mock_manager.get_prompt_modules.return_value = ["identity"]
            mock_get_manager.return_value = mock_manager

            response = client.get(
                "/api/v1/config/system_prompt?model=claude-opus-4-20250514",
                headers=admin_auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert data["model"] == "claude-opus-4-20250514"

    def test_enable_skills_parameter(self, client, admin_auth_headers):
        """Admin can toggle skills in the prompt."""
        with patch("src.api.routes.config.get_prompt_manager") as mock_get_manager:
            mock_manager = MagicMock()
            mock_manager.build_system_prompt.return_value = "No skills prompt"
            mock_manager.get_available_roles.return_value = ["default"]
            mock_manager.get_prompt_modules.return_value = ["identity"]
            mock_get_manager.return_value = mock_manager

            response = client.get(
                "/api/v1/config/system_prompt?enable_skills=false",
                headers=admin_auth_headers,
            )

            assert response.status_code == 200

            # Verify the manager was called with skills disabled
            mock_manager.build_system_prompt.assert_called_once()
            call_kwargs = mock_manager.build_system_prompt.call_args.kwargs
            assert call_kwargs["enable_skills"] is False


class TestReloadPromptsEndpoint:
    """Tests for POST /api/v1/config/prompts/reload."""

    def test_admin_can_reload(self, client, admin_auth_headers):
        """Admin users should be able to reload prompts."""
        with patch("src.api.routes.config.get_prompt_manager") as mock_get_manager:
            mock_manager = MagicMock()
            mock_manager.reload.return_value = 5
            mock_get_manager.return_value = mock_manager

            response = client.post(
                "/api/v1/config/prompts/reload",
                headers=admin_auth_headers,
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"
            assert data["cache_entries_cleared"] == 5

    def test_regular_user_gets_403(self, client, auth_headers):
        """Regular users should get 403 Forbidden."""
        response = client.post(
            "/api/v1/config/prompts/reload",
            headers=auth_headers,
        )

        assert response.status_code == 403


class TestRequireAdminDependency:
    """Tests for the require_admin dependency."""

    def test_admin_user_passes(self, client, admin_auth_headers):
        """Admin user should pass the admin check."""
        with patch("src.api.routes.config.get_prompt_manager") as mock_get_manager:
            mock_manager = MagicMock()
            mock_manager.build_system_prompt.return_value = "Test"
            mock_manager.get_available_roles.return_value = []
            mock_manager.get_prompt_modules.return_value = []
            mock_get_manager.return_value = mock_manager

            response = client.get(
                "/api/v1/config/system_prompt",
                headers=admin_auth_headers,
            )

            # Should not be 403 for admin
            assert response.status_code == 200

    def test_regular_user_fails(self, client, auth_headers):
        """Regular user should fail the admin check."""
        response = client.get(
            "/api/v1/config/system_prompt",
            headers=auth_headers,
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Admin access required"


class TestPromptBuilderService:
    """Tests for the PromptBuilder service."""

    def test_build_system_prompt_with_defaults(self):
        """PromptBuilder should render prompt with default values."""
        from src.services.prompt_builder import PromptBuilder
        from src.core.prompt_manager import PromptManager
        from pathlib import Path

        # Get the real prompts directory
        prompts_dir = Path(__file__).parent.parent.parent / "prompts"

        if not prompts_dir.exists():
            pytest.skip("Prompts directory not found")

        PromptManager.reset_instance()
        builder = PromptBuilder(prompts_dir)
        prompt = builder.build_system_prompt()

        # Verify the prompt contains expected sections
        assert len(prompt) > 0

    def test_get_available_roles(self):
        """PromptBuilder should list available roles."""
        from src.services.prompt_builder import PromptBuilder
        from src.core.prompt_manager import PromptManager
        from pathlib import Path

        prompts_dir = Path(__file__).parent.parent.parent / "prompts"

        if not prompts_dir.exists():
            pytest.skip("Prompts directory not found")

        PromptManager.reset_instance()
        builder = PromptBuilder(prompts_dir)
        roles = builder.get_available_roles()

        # Should have at least the default role
        assert isinstance(roles, list)
        if (prompts_dir / "roles" / "default.md").exists():
            assert "default" in roles

    def test_get_template_modules(self):
        """PromptBuilder should list template modules."""
        from src.services.prompt_builder import PromptBuilder
        from src.core.prompt_manager import PromptManager
        from pathlib import Path

        prompts_dir = Path(__file__).parent.parent.parent / "prompts"

        if not prompts_dir.exists():
            pytest.skip("Prompts directory not found")

        PromptManager.reset_instance()
        builder = PromptBuilder(prompts_dir)
        modules = builder.get_template_modules()

        # Should have some modules
        assert isinstance(modules, list)

    def test_invalid_role_raises_error(self):
        """PromptBuilder should raise error for invalid role."""
        from src.services.prompt_builder import PromptBuilder
        from src.core.prompt_manager import PromptManager
        from pathlib import Path

        prompts_dir = Path(__file__).parent.parent.parent / "prompts"

        if not prompts_dir.exists():
            pytest.skip("Prompts directory not found")

        PromptManager.reset_instance()
        builder = PromptBuilder(prompts_dir)

        with pytest.raises(FileNotFoundError, match="Role file not found"):
            builder.build_system_prompt(role="nonexistent_role_12345")
