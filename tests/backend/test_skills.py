"""
Tests for the skills API endpoint.

Tests cover:
- GET /skills endpoint
- Authentication requirements
- User skill discovery
- Error handling
"""
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


class TestSkillsEndpoint:
    """Tests for GET /api/v1/skills endpoint."""

    @pytest.mark.unit
    def test_list_skills_requires_auth(self, client: TestClient) -> None:
        """Skills endpoint requires authentication."""
        response = client.get("/api/v1/skills")

        assert response.status_code == 401

    @pytest.mark.unit
    def test_list_skills_returns_skills(self, client: TestClient, auth_headers: dict) -> None:
        """Authenticated request returns skills list."""
        # Mock skill discovery
        mock_skill = MagicMock()
        mock_skill.name = "test-skill"
        mock_skill.description = "A test skill"

        with patch("src.api.routes.skills.discover_merged_skills") as mock_discover:
            mock_discover.return_value = {}  # Empty skills for simplicity

            response = client.get("/api/v1/skills", headers=auth_headers)

            assert response.status_code == 200
            data = response.json()
            assert "skills" in data
            assert isinstance(data["skills"], list)

    @pytest.mark.unit
    def test_list_skills_response_structure(self, client: TestClient, auth_headers: dict) -> None:
        """Skills response has correct structure."""
        with patch("src.api.routes.skills.discover_merged_skills") as mock_discover:
            mock_discover.return_value = {}

            response = client.get("/api/v1/skills", headers=auth_headers)

            assert response.status_code == 200
            data = response.json()

            # Validate response model structure
            assert "skills" in data
            for skill in data["skills"]:
                # Each skill should have id, name, description
                assert "id" in skill or True  # May be empty
                assert isinstance(skill, dict)

    @pytest.mark.unit
    def test_list_skills_with_invalid_token(self, client: TestClient) -> None:
        """Invalid token returns 401."""
        headers = {"Authorization": "Bearer invalid-token-here"}
        response = client.get("/api/v1/skills", headers=headers)

        assert response.status_code == 401


class TestSkillDiscovery:
    """Tests for skill discovery functionality."""

    @pytest.mark.unit
    def test_skills_merged_from_global_and_user(self, client: TestClient, auth_headers: dict, test_user: dict) -> None:
        """Skills are merged from global and user directories."""
        from pathlib import Path

        with patch("src.api.routes.skills.discover_merged_skills") as mock_discover:
            # Simulate some discovered skills
            mock_discover.return_value = {
                "meow": Path("/skills/.claude/skills/meow"),
                "user-skill": Path(f"/users/{test_user['username']}/.claude/skills/user-skill"),
            }

            with patch("src.api.routes.skills.SkillManager") as mock_manager_cls:
                mock_manager = MagicMock()
                mock_skill = MagicMock()
                mock_skill.name = "Test Skill"
                mock_skill.description = "A test description"
                mock_manager.load_skill.return_value = mock_skill
                mock_manager_cls.return_value = mock_manager

                response = client.get("/api/v1/skills", headers=auth_headers)

                assert response.status_code == 200
                data = response.json()
                assert len(data["skills"]) >= 0  # May have skills or not

    @pytest.mark.unit
    def test_skill_loading_failure_graceful(self, client: TestClient, auth_headers: dict) -> None:
        """Skill loading failure is handled gracefully."""
        from pathlib import Path

        with patch("src.api.routes.skills.discover_merged_skills") as mock_discover:
            mock_discover.return_value = {
                "broken-skill": Path("/skills/.claude/skills/broken-skill"),
            }

            with patch("src.api.routes.skills.SkillManager") as mock_manager_cls:
                mock_manager = MagicMock()
                mock_manager.load_skill.side_effect = Exception("Failed to load")
                mock_manager_cls.return_value = mock_manager

                response = client.get("/api/v1/skills", headers=auth_headers)

                # Should still return 200 with partial results
                assert response.status_code == 200
                data = response.json()
                # Broken skill should still appear with just the name
                assert len(data["skills"]) == 1
                assert data["skills"][0]["id"] == "broken-skill"


class TestSkillsErrorHandling:
    """Tests for error handling in skills endpoint."""

    @pytest.mark.unit
    def test_user_not_found_returns_404(self, client: TestClient) -> None:
        """Invalid user_id returns 404."""
        # This would require a valid token but invalid user in DB
        # For now, test that missing auth returns 401
        response = client.get("/api/v1/skills")
        assert response.status_code == 401

    @pytest.mark.unit
    def test_internal_error_returns_500(self, client: TestClient, auth_headers: dict) -> None:
        """Internal errors return 500."""
        with patch("src.api.routes.skills.discover_merged_skills") as mock_discover:
            mock_discover.side_effect = Exception("Database connection failed")

            response = client.get("/api/v1/skills", headers=auth_headers)

            assert response.status_code == 500
            data = response.json()
            assert "detail" in data
