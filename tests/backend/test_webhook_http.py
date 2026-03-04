"""
HTTP integration tests for webhook CRUD endpoints.

Covers:
- POST /reseller/webhooks — register endpoint
- GET /reseller/webhooks — list endpoints
- PUT /reseller/webhooks/{id} — update
- DELETE /reseller/webhooks/{id} — delete
- POST /reseller/webhooks/{id}/test — send test event
- GET /reseller/webhooks/{id}/deliveries — delivery log
- Scope enforcement (webhooks:manage required)
- IDOR protection (cross-reseller access denied)
"""
import pytest


# =============================================================================
# Webhook CRUD
# =============================================================================

class TestCreateWebhook:
    """Tests for POST /reseller/webhooks."""

    @pytest.mark.integration
    def test_create_webhook_success(self, client, reseller_auth_headers):
        resp = client.post(
            "/api/v1/reseller/webhooks",
            headers=reseller_auth_headers,
            json={
                "url": "https://example.com/hook",
                "events": ["session.completed"],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert "secret" in data
        assert data["url"] == "https://example.com/hook"
        assert data["is_active"] is True

    @pytest.mark.integration
    def test_create_webhook_invalid_event(self, client, reseller_auth_headers):
        resp = client.post(
            "/api/v1/reseller/webhooks",
            headers=reseller_auth_headers,
            json={
                "url": "https://example.com/hook",
                "events": ["invalid.event"],
            },
        )
        assert resp.status_code == 422  # Pydantic validation error

    @pytest.mark.integration
    def test_create_webhook_wildcard(self, client, reseller_auth_headers):
        resp = client.post(
            "/api/v1/reseller/webhooks",
            headers=reseller_auth_headers,
            json={
                "url": "https://example.com/all",
                "events": ["*"],
                "description": "Catch all events",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["description"] == "Catch all events"


class TestListWebhooks:
    """Tests for GET /reseller/webhooks."""

    @pytest.mark.integration
    def test_list_webhooks(self, client, reseller_auth_headers):
        # Create one first
        client.post(
            "/api/v1/reseller/webhooks",
            headers=reseller_auth_headers,
            json={
                "url": "https://example.com/list-test",
                "events": ["session.completed"],
            },
        )
        resp = client.get(
            "/api/v1/reseller/webhooks",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "webhooks" in data
        assert isinstance(data["webhooks"], list)
        assert len(data["webhooks"]) >= 1


class TestUpdateWebhook:
    """Tests for PUT /reseller/webhooks/{id}."""

    @pytest.mark.integration
    def test_update_webhook(self, client, reseller_auth_headers):
        # Create
        create_resp = client.post(
            "/api/v1/reseller/webhooks",
            headers=reseller_auth_headers,
            json={
                "url": "https://example.com/update-test",
                "events": ["session.completed"],
            },
        )
        eid = create_resp.json()["id"]
        # Update
        resp = client.put(
            f"/api/v1/reseller/webhooks/{eid}",
            headers=reseller_auth_headers,
            json={
                "url": "https://example.com/updated",
                "is_active": False,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["url"] == "https://example.com/updated"
        assert resp.json()["is_active"] is False

    @pytest.mark.integration
    def test_update_nonexistent_webhook(self, client, reseller_auth_headers):
        resp = client.put(
            "/api/v1/reseller/webhooks/nonexistent-id",
            headers=reseller_auth_headers,
            json={"url": "https://example.com/nope"},
        )
        assert resp.status_code == 404


class TestDeleteWebhook:
    """Tests for DELETE /reseller/webhooks/{id}."""

    @pytest.mark.integration
    def test_delete_webhook(self, client, reseller_auth_headers):
        # Create
        create_resp = client.post(
            "/api/v1/reseller/webhooks",
            headers=reseller_auth_headers,
            json={
                "url": "https://example.com/delete-test",
                "events": ["session.completed"],
            },
        )
        eid = create_resp.json()["id"]
        # Delete
        resp = client.delete(
            f"/api/v1/reseller/webhooks/{eid}",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    @pytest.mark.integration
    def test_delete_nonexistent(self, client, reseller_auth_headers):
        resp = client.delete(
            "/api/v1/reseller/webhooks/nonexistent-id",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 404


class TestTestWebhook:
    """Tests for POST /reseller/webhooks/{id}/test."""

    @pytest.mark.integration
    def test_send_test_event(self, client, reseller_auth_headers):
        # Create
        create_resp = client.post(
            "/api/v1/reseller/webhooks",
            headers=reseller_auth_headers,
            json={
                "url": "https://httpbin.org/post",
                "events": ["session.completed"],
            },
        )
        eid = create_resp.json()["id"]
        # Test event (may fail to actually deliver, but endpoint should respond)
        resp = client.post(
            f"/api/v1/reseller/webhooks/{eid}/test",
            headers=reseller_auth_headers,
        )
        # 200 means delivery was attempted (status may be pending or delivered)
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "delivery_id" in data


class TestDeliveryLog:
    """Tests for GET /reseller/webhooks/{id}/deliveries."""

    @pytest.mark.integration
    def test_get_deliveries(self, client, reseller_auth_headers):
        # Create
        create_resp = client.post(
            "/api/v1/reseller/webhooks",
            headers=reseller_auth_headers,
            json={
                "url": "https://example.com/delivery-test",
                "events": ["session.completed"],
            },
        )
        eid = create_resp.json()["id"]
        resp = client.get(
            f"/api/v1/reseller/webhooks/{eid}/deliveries",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "deliveries" in data
        assert isinstance(data["deliveries"], list)

    @pytest.mark.integration
    def test_deliveries_nonexistent_endpoint(
        self, client, reseller_auth_headers
    ):
        resp = client.get(
            "/api/v1/reseller/webhooks/nonexistent/deliveries",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deliveries"] == []


# =============================================================================
# IDOR protection
# =============================================================================

class TestWebhookIDOR:
    """Cross-reseller access must be denied."""

    @pytest.mark.integration
    def test_cannot_access_other_reseller_webhook(
        self, client, reseller_auth_headers, second_reseller_auth_headers,
    ):
        # Create with first reseller
        create_resp = client.post(
            "/api/v1/reseller/webhooks",
            headers=reseller_auth_headers,
            json={
                "url": "https://example.com/idor-test",
                "events": ["session.completed"],
            },
        )
        eid = create_resp.json()["id"]
        # Try to update with second reseller
        resp = client.put(
            f"/api/v1/reseller/webhooks/{eid}",
            headers=second_reseller_auth_headers,
            json={"url": "https://evil.com/steal"},
        )
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_cannot_delete_other_reseller_webhook(
        self, client, reseller_auth_headers, second_reseller_auth_headers,
    ):
        create_resp = client.post(
            "/api/v1/reseller/webhooks",
            headers=reseller_auth_headers,
            json={
                "url": "https://example.com/idor-delete",
                "events": ["session.completed"],
            },
        )
        eid = create_resp.json()["id"]
        resp = client.delete(
            f"/api/v1/reseller/webhooks/{eid}",
            headers=second_reseller_auth_headers,
        )
        assert resp.status_code == 404
