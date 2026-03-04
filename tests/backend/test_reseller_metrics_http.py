"""
HTTP integration tests for reseller usage metrics and export endpoints.

Covers:
- GET /reseller/usage/metrics — WHMCS MetricProvider format
- GET /reseller/usage/export — JSON and CSV download
- Scope enforcement (usage:read required)
- Period selection (current_month, last_month)
"""
import pytest


# =============================================================================
# WHMCS Metrics Endpoint
# =============================================================================

class TestUsageMetrics:
    """Tests for GET /reseller/usage/metrics."""

    @pytest.mark.integration
    def test_metrics_returns_whmcs_format(
        self, client, reseller_auth_headers
    ):
        """GET /reseller/usage/metrics returns metrics + usage dicts."""
        resp = client.get(
            "/api/v1/reseller/usage/metrics",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "metrics" in data
        assert "usage" in data
        # Check metric definitions
        assert "sessions" in data["metrics"]
        assert "tokens" in data["metrics"]
        assert "cost" in data["metrics"]
        assert data["metrics"]["sessions"]["type"] == "snapcount"

    @pytest.mark.integration
    def test_metrics_with_last_month(
        self, client, reseller_auth_headers
    ):
        """GET /reseller/usage/metrics?period=last_month works."""
        resp = client.get(
            "/api/v1/reseller/usage/metrics?period=last_month",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "metrics" in data
        assert "usage" in data

    @pytest.mark.integration
    def test_metrics_empty_usage(
        self, client, reseller_auth_headers
    ):
        """Metrics returns empty usage dict when no records exist."""
        resp = client.get(
            "/api/v1/reseller/usage/metrics",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["usage"] == {}

    @pytest.mark.integration
    def test_metrics_non_reseller_forbidden(self, client, auth_headers):
        """Regular user cannot access metrics endpoint."""
        resp = client.get(
            "/api/v1/reseller/usage/metrics",
            headers=auth_headers,
        )
        assert resp.status_code == 403


# =============================================================================
# Usage Export Endpoint
# =============================================================================

class TestUsageExport:
    """Tests for GET /reseller/usage/export."""

    @pytest.mark.integration
    def test_export_json_default(
        self, client, reseller_auth_headers
    ):
        """GET /reseller/usage/export returns JSON by default."""
        resp = client.get(
            "/api/v1/reseller/usage/export",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "records" in data
        assert "count" in data
        assert "period" in data
        assert isinstance(data["records"], list)

    @pytest.mark.integration
    def test_export_json_content_disposition(
        self, client, reseller_auth_headers
    ):
        """JSON export includes Content-Disposition header."""
        resp = client.get(
            "/api/v1/reseller/usage/export?format=json",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        assert "content-disposition" in resp.headers
        assert "usage_current_month.json" in resp.headers["content-disposition"]

    @pytest.mark.integration
    def test_export_csv(self, client, reseller_auth_headers):
        """GET /reseller/usage/export?format=csv returns CSV."""
        resp = client.get(
            "/api/v1/reseller/usage/export?format=csv",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert "content-disposition" in resp.headers
        assert "usage_current_month.csv" in resp.headers["content-disposition"]

    @pytest.mark.integration
    def test_export_last_month(
        self, client, reseller_auth_headers
    ):
        """Export with last_month period works."""
        resp = client.get(
            "/api/v1/reseller/usage/export?period=last_month",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200

    @pytest.mark.integration
    def test_export_non_reseller_forbidden(self, client, auth_headers):
        """Regular user cannot export usage."""
        resp = client.get(
            "/api/v1/reseller/usage/export",
            headers=auth_headers,
        )
        assert resp.status_code == 403
