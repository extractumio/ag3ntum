"""
HTTP integration tests for admin platform configuration endpoints.

Covers:
- GET /admin/config — returns effective platform defaults
- PUT /admin/config — updates platform defaults (features, quotas, spending)
- Null values in PUT body reset keys to hardcoded defaults
- Non-admin users cannot access config endpoints
"""
import pytest


# =============================================================================
# GET /admin/config
# =============================================================================

class TestGetPlatformConfig:
    """Tests for GET /admin/config endpoint."""

    @pytest.mark.integration
    def test_get_config_returns_all_sections(self, client, admin_auth_headers):
        """GET /admin/config returns features, quotas, spending."""
        resp = client.get(
            "/api/v1/admin/config",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "default_features" in data
        assert "default_quotas" in data
        assert "default_spending_limits" in data
        assert "default_settings_mode" in data

    @pytest.mark.integration
    def test_get_config_has_expected_features(self, client, admin_auth_headers):
        """GET /admin/config features include key defaults."""
        resp = client.get(
            "/api/v1/admin/config",
            headers=admin_auth_headers,
        )
        data = resp.json()
        features = data["default_features"]
        assert "ssh_enabled" in features
        assert "max_session_minutes" in features
        assert "allowed_models" in features
        assert features["ssh_enabled"] is False
        assert features["max_session_minutes"] == 30

    @pytest.mark.integration
    def test_get_config_has_expected_quotas(self, client, admin_auth_headers):
        """GET /admin/config quotas include concurrent and daily limits."""
        resp = client.get(
            "/api/v1/admin/config",
            headers=admin_auth_headers,
        )
        data = resp.json()
        quotas = data["default_quotas"]
        assert quotas["global_max_concurrent"] == 4
        assert quotas["per_user_max_concurrent"] == 2
        assert quotas["per_user_daily_limit"] == 50

    @pytest.mark.integration
    def test_get_config_non_admin_forbidden(self, client, auth_headers):
        """Regular user cannot access GET /admin/config."""
        resp = client.get(
            "/api/v1/admin/config",
            headers=auth_headers,
        )
        assert resp.status_code == 403


# =============================================================================
# PUT /admin/config
# =============================================================================

class TestUpdatePlatformConfig:
    """Tests for PUT /admin/config endpoint."""

    @pytest.mark.integration
    def test_update_features(self, client, admin_auth_headers):
        """PUT /admin/config with features updates feature defaults."""
        resp = client.put(
            "/api/v1/admin/config",
            headers=admin_auth_headers,
            json={
                "features": {"max_session_minutes": 60, "web_fetch_enabled": True},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["default_features"]["max_session_minutes"] == 60
        assert data["default_features"]["web_fetch_enabled"] is True
        # Unchanged values remain at defaults
        assert data["default_features"]["ssh_enabled"] is False

    @pytest.mark.integration
    def test_update_quotas(self, client, admin_auth_headers):
        """PUT /admin/config with quotas updates quota defaults."""
        resp = client.put(
            "/api/v1/admin/config",
            headers=admin_auth_headers,
            json={
                "quotas": {"per_user_daily_limit": 100},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["default_quotas"]["per_user_daily_limit"] == 100
        assert data["default_quotas"]["global_max_concurrent"] == 4

    @pytest.mark.integration
    def test_update_spending(self, client, admin_auth_headers):
        """PUT /admin/config with spending updates spending defaults."""
        resp = client.put(
            "/api/v1/admin/config",
            headers=admin_auth_headers,
            json={
                "spending": {"user_daily_usd": 50.0},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["default_spending_limits"]["user_daily_usd"] == 50.0

    @pytest.mark.integration
    def test_null_value_resets_to_default(self, client, admin_auth_headers):
        """Setting a value then setting it to null resets to hardcoded default."""
        # First, set a custom value
        client.put(
            "/api/v1/admin/config",
            headers=admin_auth_headers,
            json={"features": {"max_session_minutes": 120}},
        )

        # Then reset it to null
        resp = client.put(
            "/api/v1/admin/config",
            headers=admin_auth_headers,
            json={"features": {"max_session_minutes": None}},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Should be back to hardcoded default (30)
        assert data["default_features"]["max_session_minutes"] == 30

    @pytest.mark.integration
    def test_update_multiple_sections(self, client, admin_auth_headers):
        """PUT /admin/config can update all sections at once."""
        resp = client.put(
            "/api/v1/admin/config",
            headers=admin_auth_headers,
            json={
                "features": {"vault_enabled": False},
                "quotas": {"global_max_concurrent": 8},
                "spending": {"reseller_monthly_usd": 500.0},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["default_features"]["vault_enabled"] is False
        assert data["default_quotas"]["global_max_concurrent"] == 8
        assert data["default_spending_limits"]["reseller_monthly_usd"] == 500.0

    @pytest.mark.integration
    def test_get_reflects_put_changes(self, client, admin_auth_headers):
        """GET /admin/config reflects values set by PUT."""
        client.put(
            "/api/v1/admin/config",
            headers=admin_auth_headers,
            json={"features": {"custom_skills_enabled": True}},
        )

        resp = client.get(
            "/api/v1/admin/config",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["default_features"]["custom_skills_enabled"] is True

    @pytest.mark.integration
    def test_update_non_admin_forbidden(self, client, auth_headers):
        """Regular user cannot access PUT /admin/config."""
        resp = client.put(
            "/api/v1/admin/config",
            headers=auth_headers,
            json={"features": {"ssh_enabled": False}},
        )
        assert resp.status_code == 403

    @pytest.mark.integration
    def test_empty_body_no_change(self, client, admin_auth_headers):
        """PUT /admin/config with empty body (no sections) is a no-op."""
        resp = client.put(
            "/api/v1/admin/config",
            headers=admin_auth_headers,
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Should still have all sections
        assert "default_features" in data
        assert "default_quotas" in data
        assert "default_spending_limits" in data
