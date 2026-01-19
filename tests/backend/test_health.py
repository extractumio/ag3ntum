"""
Tests for the health check endpoint.

Validates response structure and content for GET /api/v1/health.
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoint:
    """Tests for GET /api/v1/health."""

    @pytest.mark.unit
    def test_health_returns_ok(self, client: TestClient) -> None:
        """Health endpoint returns status ok."""
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    @pytest.mark.unit
    def test_health_response_structure(self, client: TestClient) -> None:
        """Health endpoint returns complete HealthResponse structure."""
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        
        # Validate all HealthResponse fields
        assert "status" in data
        assert "version" in data
        assert "timestamp" in data

    @pytest.mark.unit
    def test_health_returns_version(self, client: TestClient) -> None:
        """Health endpoint returns API version."""
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        assert data["version"] == "1.0.0"

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
    def test_health_no_auth_required(self, client: TestClient) -> None:
        """Health endpoint doesn't require authentication."""
        # No Authorization header
        response = client.get("/api/v1/health")
        assert response.status_code == 200

    @pytest.mark.unit
    def test_health_returns_json(self, client: TestClient) -> None:
        """Health endpoint returns JSON content type."""
        response = client.get("/api/v1/health")
        
        assert response.status_code == 200
        assert "application/json" in response.headers.get("content-type", "")
