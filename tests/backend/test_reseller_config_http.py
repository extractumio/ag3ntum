"""
HTTP integration tests for reseller user configuration, spending, skills,
and usage endpoints.

Covers QA playbook sections 6-11.
Uses FastAPI TestClient with in-memory SQLite — no Docker or Redis required.
"""
import uuid

import pytest


# =============================================================================
# Sections 6-8 — User Configuration
# =============================================================================

class TestUserConfigHTTP:
    """Get and update user config, security, SSH filters, and env vars."""

    @pytest.mark.integration
    def test_get_user_config(
        self, client, reseller_auth_headers, reseller_user
    ):
        """GET /reseller/users/{id}/config returns config."""
        resp = client.get(
            f"/api/v1/reseller/users/{reseller_user['id']}/config",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "settings_mode" in data
        assert "features" in data

    @pytest.mark.integration
    def test_update_user_config(
        self, client, reseller_auth_headers, reseller_user
    ):
        """PUT /reseller/users/{id}/config updates settings_mode."""
        resp = client.put(
            f"/api/v1/reseller/users/{reseller_user['id']}/config",
            headers=reseller_auth_headers,
            json={
                "settings_mode": "configurable",
                "allowed_overrides": ["prompts", "env_vars"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["settings_mode"] == "configurable"

    @pytest.mark.integration
    def test_get_and_update_security_config(
        self, client, reseller_auth_headers, reseller_user
    ):
        """GET + PUT /reseller/users/{id}/security round-trips."""
        user_id = reseller_user["id"]
        # Get initial
        get_resp = client.get(
            f"/api/v1/reseller/users/{user_id}/security",
            headers=reseller_auth_headers,
        )
        assert get_resp.status_code == 200

        # Update
        put_resp = client.put(
            f"/api/v1/reseller/users/{user_id}/security",
            headers=reseller_auth_headers,
            json={
                "disabled_tools": ["WebFetch"],
                "network_blocked_domains": ["evil.com"],
            },
        )
        assert put_resp.status_code == 200
        data = put_resp.json()
        assert "WebFetch" in data.get("security", data).get(
            "disabled_tools", []
        )

    @pytest.mark.integration
    def test_get_and_update_ssh_filters(
        self, client, reseller_auth_headers, reseller_user
    ):
        """GET + PUT /reseller/users/{id}/ssh-filters round-trips."""
        user_id = reseller_user["id"]
        get_resp = client.get(
            f"/api/v1/reseller/users/{user_id}/ssh-filters",
            headers=reseller_auth_headers,
        )
        assert get_resp.status_code == 200

        put_resp = client.put(
            f"/api/v1/reseller/users/{user_id}/ssh-filters",
            headers=reseller_auth_headers,
            json={
                "blocked_hosts": ["internal.corp"],
                "max_connections": 5,
            },
        )
        assert put_resp.status_code == 200

    @pytest.mark.integration
    def test_set_and_get_env_vars(
        self, client, reseller_auth_headers, reseller_user
    ):
        """PUT /reseller/users/{id}/env-vars then GET returns names only."""
        user_id = reseller_user["id"]
        put_resp = client.put(
            f"/api/v1/reseller/users/{user_id}/env-vars",
            headers=reseller_auth_headers,
            json={"env_vars": {"MY_VAR": "hello", "API_URL": "https://x.com"}},
        )
        assert put_resp.status_code == 200

        get_resp = client.get(
            f"/api/v1/reseller/users/{user_id}/env-vars",
            headers=reseller_auth_headers,
        )
        assert get_resp.status_code == 200
        data = get_resp.json()
        names = data.get("env_var_names", data.get("names", []))
        assert "MY_VAR" in names
        assert "API_URL" in names

    @pytest.mark.integration
    def test_delete_env_var(
        self, client, reseller_auth_headers, reseller_user
    ):
        """DELETE /reseller/users/{id}/env-vars/{name} removes the var."""
        user_id = reseller_user["id"]
        # Set a var first
        client.put(
            f"/api/v1/reseller/users/{user_id}/env-vars",
            headers=reseller_auth_headers,
            json={"env_vars": {"TO_DELETE": "bye"}},
        )
        # Delete it
        del_resp = client.delete(
            f"/api/v1/reseller/users/{user_id}/env-vars/TO_DELETE",
            headers=reseller_auth_headers,
        )
        assert del_resp.status_code == 200


# =============================================================================
# Section 9 — Spending
# =============================================================================

class TestSpendingHTTP:
    """User spending endpoint."""

    @pytest.mark.integration
    def test_get_user_spending(
        self, client, reseller_auth_headers, reseller_user
    ):
        """GET /reseller/users/{id}/spending returns spending data."""
        resp = client.get(
            f"/api/v1/reseller/users/{reseller_user['id']}/spending",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] in ("ok", "warning", "exceeded")


# =============================================================================
# Section 10 — Skills Management
# =============================================================================

class TestSkillsHTTP:
    """Skill library upload, user assignment, enable/disable, removal."""

    @pytest.mark.integration
    def test_upload_skill_and_list_library(
        self, client, reseller_auth_headers
    ):
        """Upload a skill, then verify it appears in the library."""
        skill_name = f"skill_{uuid.uuid4().hex[:8]}"
        upload_resp = client.post(
            "/api/v1/reseller/skill-library",
            headers=reseller_auth_headers,
            json={
                "name": skill_name,
                "description": "Test skill",
                "content": "You are a test skill.",
            },
        )
        assert upload_resp.status_code == 201

        list_resp = client.get(
            "/api/v1/reseller/skill-library",
            headers=reseller_auth_headers,
        )
        assert list_resp.status_code == 200
        names = [s["name"] for s in list_resp.json()["skills"]]
        assert skill_name in names

    @pytest.mark.integration
    def test_assign_and_list_user_skills(
        self, client, reseller_auth_headers, reseller_user
    ):
        """Assign a library skill to a user, then list user skills."""
        skill_name = f"skill_{uuid.uuid4().hex[:8]}"
        # Upload to library
        client.post(
            "/api/v1/reseller/skill-library",
            headers=reseller_auth_headers,
            json={"name": skill_name, "content": "Skill content."},
        )
        # Assign to user
        assign_resp = client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/skills",
            headers=reseller_auth_headers,
            json={"name": skill_name},
        )
        assert assign_resp.status_code == 201

        # List user skills
        list_resp = client.get(
            f"/api/v1/reseller/users/{reseller_user['id']}/skills",
            headers=reseller_auth_headers,
        )
        assert list_resp.status_code == 200
        names = [s["name"] for s in list_resp.json()["skills"]]
        assert skill_name in names

    @pytest.mark.integration
    def test_disable_and_enable_skill(
        self, client, reseller_auth_headers, reseller_user
    ):
        """Disable then re-enable a user skill."""
        skill_name = f"skill_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/reseller/skill-library",
            headers=reseller_auth_headers,
            json={"name": skill_name, "content": "Content."},
        )
        client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/skills",
            headers=reseller_auth_headers,
            json={"name": skill_name},
        )

        # Disable
        dis_resp = client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}"
            f"/skills/{skill_name}/disable",
            headers=reseller_auth_headers,
        )
        assert dis_resp.status_code == 200

        # Enable
        en_resp = client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}"
            f"/skills/{skill_name}/enable",
            headers=reseller_auth_headers,
        )
        assert en_resp.status_code == 200

    @pytest.mark.integration
    def test_duplicate_assignment_409(
        self, client, reseller_auth_headers, reseller_user
    ):
        """Assigning the same skill twice → 409."""
        skill_name = f"skill_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/reseller/skill-library",
            headers=reseller_auth_headers,
            json={"name": skill_name, "content": "Content."},
        )
        # First assign
        client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/skills",
            headers=reseller_auth_headers,
            json={"name": skill_name},
        )
        # Second assign — duplicate
        dup_resp = client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/skills",
            headers=reseller_auth_headers,
            json={"name": skill_name},
        )
        assert dup_resp.status_code == 409

    @pytest.mark.integration
    def test_remove_user_skill_and_library_skill(
        self, client, reseller_auth_headers, reseller_user
    ):
        """Remove a skill from a user, then from the library."""
        skill_name = f"skill_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/reseller/skill-library",
            headers=reseller_auth_headers,
            json={"name": skill_name, "content": "Content."},
        )
        client.post(
            f"/api/v1/reseller/users/{reseller_user['id']}/skills",
            headers=reseller_auth_headers,
            json={"name": skill_name},
        )

        # Remove from user
        rm_user_resp = client.delete(
            f"/api/v1/reseller/users/{reseller_user['id']}"
            f"/skills/{skill_name}",
            headers=reseller_auth_headers,
        )
        assert rm_user_resp.status_code == 200

        # Remove from library
        rm_lib_resp = client.delete(
            f"/api/v1/reseller/skill-library/{skill_name}",
            headers=reseller_auth_headers,
        )
        assert rm_lib_resp.status_code == 200


# =============================================================================
# Section 11 — Usage & Reporting
# =============================================================================

class TestUsageHTTP:
    """Reseller-level and user-level usage endpoints."""

    @pytest.mark.integration
    def test_get_reseller_usage(self, client, reseller_auth_headers):
        """GET /reseller/usage returns period and totals."""
        resp = client.get(
            "/api/v1/reseller/usage",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "period" in data
        assert "totals" in data

    @pytest.mark.integration
    def test_get_user_usage(
        self, client, reseller_auth_headers, reseller_user
    ):
        """GET /reseller/users/{id}/usage returns user usage."""
        resp = client.get(
            f"/api/v1/reseller/users/{reseller_user['id']}/usage",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == reseller_user["id"]
        assert "totals" in data
