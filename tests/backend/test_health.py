"""
Tests for the health check endpoint.

Validates response structure and content for GET /api/v1/health.
"""
import re
from datetime import datetime

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoint:
    """Tests for GET /api/v1/health."""

    @pytest.mark.unit
    def test_health_response(self, client: TestClient) -> None:
        """Health endpoint returns 200 with complete HealthResponse structure."""
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()

        # Validate all HealthResponse fields
        assert data["status"] == "ok"
        assert "version" in data
        assert "timestamp" in data

    @pytest.mark.unit
    def test_health_returns_version(self, client: TestClient) -> None:
        """Health endpoint returns API version."""
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        # Version should be semver (X.Y.Z) or "dev"
        version = data["version"]
        assert version == "dev" or re.match(r"^\d+\.\d+\.\d+$", version), (
            f"Version should be semver or 'dev', got: {version}"
        )

    @pytest.mark.unit
    def test_health_returns_valid_timestamp(self, client: TestClient) -> None:
        """Health endpoint returns a valid ISO format timestamp."""
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert "timestamp" in data

        # Timestamp should be a valid ISO format string
        timestamp = data["timestamp"]
        assert "T" in timestamp

        # Should be parseable as datetime
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        assert parsed is not None

    @pytest.mark.unit
    def test_health_returns_json(self, client: TestClient) -> None:
        """Health endpoint returns JSON content type."""
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        assert "application/json" in response.headers.get("content-type", "")
