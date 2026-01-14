"""
Tests for session management endpoints.

Comprehensive coverage of all session endpoints including:
- Response structure validation
- Edge cases and error scenarios
- Pagination and filtering
"""
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


class TestSessionRun:
    """Tests for POST /api/v1/sessions/run (unified endpoint)."""

    @pytest.mark.unit
    def test_run_task_creates_and_starts(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Run endpoint creates session and starts task."""
        response = client.post(
            "/api/v1/sessions/run",
            headers=auth_headers,
            json={"task": "Test task"}
        )

        assert response.status_code == 201
        data = response.json()
        
        # Validate response structure (TaskStartedResponse)
        assert "session_id" in data
        assert data["status"] == "running"
        assert data["message"] == "Task execution started"
        assert "resumed_from" in data  # Can be None
        
        # Validate session_id format: YYYYMMDD_HHMMSS_uuid8
        session_id = data["session_id"]
        parts = session_id.split("_")
        assert len(parts) == 3
        assert len(parts[0]) == 8  # Date
        assert len(parts[1]) == 6  # Time
        assert len(parts[2]) == 8  # UUID fragment

    @pytest.mark.unit
    def test_run_task_with_config_overrides(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Can pass config overrides to run endpoint."""
        response = client.post(
            "/api/v1/sessions/run",
            headers=auth_headers,
            json={
                "task": "Test task",
                "working_dir": "/tmp",
                "config": {
                    "model": "claude-sonnet-4-5-20250929",
                    "max_turns": 50,
                    "enable_skills": False
                }
            }
        )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "running"

    @pytest.mark.unit
    def test_run_task_with_resume(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Can specify session to resume."""
        response = client.post(
            "/api/v1/sessions/run",
            headers=auth_headers,
            json={
                "task": "Continue the task",
                "resume_session_id": "20260101_120000_abc12345",
                "fork_session": True
            }
        )

        assert response.status_code == 201
        data = response.json()
        assert data["resumed_from"] == "20260101_120000_abc12345"

    @pytest.mark.unit
    def test_run_task_requires_task(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Task field is required."""
        response = client.post(
            "/api/v1/sessions/run",
            headers=auth_headers,
            json={}
        )

        assert response.status_code == 422  # Validation error
        data = response.json()
        assert "detail" in data

    @pytest.mark.unit
    def test_run_task_with_additional_dirs(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Can specify additional directories."""
        response = client.post(
            "/api/v1/sessions/run",
            headers=auth_headers,
            json={
                "task": "Multi-dir task",
                "working_dir": "/project",
                "additional_dirs": ["/lib", "/data"]
            }
        )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "running"

    @pytest.mark.unit
    def test_run_task_requires_auth(self, client: TestClient) -> None:
        """Run endpoint requires authentication."""
        response = client.post(
            "/api/v1/sessions/run",
            json={"task": "Test task"}
        )
        assert response.status_code == 401


class TestSessionCreate:
    """Tests for POST /api/v1/sessions."""

    @pytest.mark.unit
    def test_create_session(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Can create a new session."""
        response = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Test task", "working_dir": "/tmp"}
        )

        assert response.status_code == 201
        data = response.json()
        
        # Validate full SessionResponse structure
        assert "id" in data
        assert data["status"] == "pending"
        assert data["task"] == "Test task"
        assert "model" in data  # Can be None
        assert "created_at" in data
        assert "updated_at" in data
        assert "completed_at" in data  # Can be None
        assert data["num_turns"] == 0
        assert "duration_ms" in data  # Can be None
        assert "total_cost_usd" in data  # Can be None
        assert data["cancel_requested"] is False

    @pytest.mark.unit
    def test_create_session_generates_id(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Created session has a valid ID format."""
        response = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Test task"}
        )

        session_id = response.json()["id"]
        # Session ID format: YYYYMMDD_HHMMSS_uuid8
        parts = session_id.split("_")
        assert len(parts) == 3
        assert len(parts[0]) == 8  # Date
        assert len(parts[1]) == 6  # Time
        assert len(parts[2]) == 8  # UUID fragment

    @pytest.mark.unit
    def test_create_session_with_model(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Can specify model when creating session."""
        response = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={
                "task": "Test task",
                "model": "claude-sonnet-4-5-20250929"
            }
        )

        assert response.status_code == 201
        data = response.json()
        assert data["model"] == "claude-sonnet-4-5-20250929"

    @pytest.mark.unit
    def test_create_session_requires_task(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Task field is required."""
        response = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={}
        )

        assert response.status_code == 422  # Validation error

    @pytest.mark.unit
    def test_create_session_timestamps(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Session has valid timestamp fields."""
        response = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Timestamp test"}
        )

        data = response.json()
        
        # Validate created_at is ISO format
        created_at = data["created_at"]
        assert "T" in created_at
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        
        # updated_at should also be valid
        updated_at = data["updated_at"]
        datetime.fromisoformat(updated_at.replace("Z", "+00:00"))

    @pytest.mark.unit
    def test_create_session_requires_auth(self, client: TestClient) -> None:
        """Create endpoint requires authentication."""
        response = client.post(
            "/api/v1/sessions",
            json={"task": "Test task"}
        )
        assert response.status_code == 401


class TestSessionList:
    """Tests for GET /api/v1/sessions."""

    @pytest.mark.unit
    def test_list_sessions_empty(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """List returns empty when no sessions exist."""
        response = client.get("/api/v1/sessions", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        
        # Validate SessionListResponse structure
        assert "sessions" in data
        assert isinstance(data["sessions"], list)
        assert "total" in data
        assert data["sessions"] == []
        assert data["total"] == 0

    @pytest.mark.unit
    def test_list_sessions_after_create(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Created sessions appear in list."""
        # Create a session
        client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Task 1"}
        )

        # List sessions
        response = client.get("/api/v1/sessions", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert len(data["sessions"]) == 1
        assert data["total"] == 1
        assert data["sessions"][0]["task"] == "Task 1"

    @pytest.mark.unit
    def test_list_sessions_pagination_limit(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """List respects limit parameter."""
        # Create 5 sessions
        for i in range(5):
            client.post(
                "/api/v1/sessions",
                headers=auth_headers,
                json={"task": f"Task {i}"}
            )

        # Request only 2
        response = client.get(
            "/api/v1/sessions",
            headers=auth_headers,
            params={"limit": 2}
        )

        data = response.json()
        assert len(data["sessions"]) == 2
        assert data["total"] == 5  # Total count still accurate

    @pytest.mark.unit
    def test_list_sessions_pagination_offset(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """List respects offset parameter."""
        # Create 5 sessions
        for i in range(5):
            client.post(
                "/api/v1/sessions",
                headers=auth_headers,
                json={"task": f"Task {i}"}
            )

        # Skip first 3
        response = client.get(
            "/api/v1/sessions",
            headers=auth_headers,
            params={"offset": 3}
        )

        data = response.json()
        assert len(data["sessions"]) == 2  # 5 - 3 = 2 remaining
        assert data["total"] == 5

    @pytest.mark.unit
    def test_list_sessions_pagination_combined(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """List works with limit and offset together."""
        # Create 10 sessions
        for i in range(10):
            client.post(
                "/api/v1/sessions",
                headers=auth_headers,
                json={"task": f"Task {i}"}
            )

        # Get page 2 (items 3-4) with limit 2, offset 2
        response = client.get(
            "/api/v1/sessions",
            headers=auth_headers,
            params={"limit": 2, "offset": 2}
        )

        data = response.json()
        assert len(data["sessions"]) == 2
        assert data["total"] == 10

    @pytest.mark.unit
    def test_sessions_isolated_by_user(
        self,
        client: TestClient,
        auth_headers: dict,
        second_auth_headers: dict
    ) -> None:
        """Users can only see their own sessions."""
        # Create session with user 1
        client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "User 1 task"}
        )

        # Create session with user 2
        client.post(
            "/api/v1/sessions",
            headers=second_auth_headers,
            json={"task": "User 2 task"}
        )

        # User 1 should only see their session
        list1 = client.get("/api/v1/sessions", headers=auth_headers)
        assert list1.json()["total"] == 1
        assert list1.json()["sessions"][0]["task"] == "User 1 task"

        # User 2 should only see their session
        list2 = client.get("/api/v1/sessions", headers=second_auth_headers)
        assert list2.json()["total"] == 1
        assert list2.json()["sessions"][0]["task"] == "User 2 task"

    @pytest.mark.unit
    def test_list_sessions_requires_auth(self, client: TestClient) -> None:
        """List endpoint requires authentication."""
        response = client.get("/api/v1/sessions")
        assert response.status_code == 401

    @pytest.mark.unit
    def test_list_sessions_ordered_by_created_at(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Sessions are ordered by creation date (newest first)."""
        # Create sessions
        for i in range(3):
            client.post(
                "/api/v1/sessions",
                headers=auth_headers,
                json={"task": f"Task {i}"}
            )

        response = client.get("/api/v1/sessions", headers=auth_headers)
        data = response.json()
        
        # Newest first
        assert data["sessions"][0]["task"] == "Task 2"
        assert data["sessions"][1]["task"] == "Task 1"
        assert data["sessions"][2]["task"] == "Task 0"


class TestSessionGet:
    """Tests for GET /api/v1/sessions/{id}."""

    @pytest.mark.unit
    def test_get_session(
        self,
        client: TestClient,
        auth_headers: dict,
        created_session: dict
    ) -> None:
        """Can get a specific session by ID."""
        session_id = created_session["id"]

        response = client.get(
            f"/api/v1/sessions/{session_id}",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        
        # Validate full SessionResponse structure
        assert data["id"] == session_id
        assert "status" in data
        assert "task" in data
        assert "model" in data
        assert "created_at" in data
        assert "updated_at" in data
        assert "completed_at" in data
        assert "num_turns" in data
        assert "duration_ms" in data
        assert "total_cost_usd" in data
        assert "cancel_requested" in data

    @pytest.mark.unit
    def test_get_session_not_found(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Returns 404 for non-existent session (invalid format)."""
        response = client.get(
            "/api/v1/sessions/nonexistent-session-id",
            headers=auth_headers
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        # Error message mentions the invalid session ID
        assert "nonexistent-session-id" in data["detail"]

    @pytest.mark.unit
    def test_get_session_not_found_valid_format(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Returns 404 for non-existent session with valid ID format."""
        response = client.get(
            "/api/v1/sessions/20250101_000000_deadbeef",
            headers=auth_headers
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    @pytest.mark.unit
    def test_get_session_wrong_user(
        self,
        client: TestClient,
        auth_headers: dict,
        second_auth_headers: dict
    ) -> None:
        """Cannot get another user's session."""
        # Create session with user 1
        create = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Private task"}
        )
        session_id = create.json()["id"]

        # Try to get with user 2
        response = client.get(
            f"/api/v1/sessions/{session_id}",
            headers=second_auth_headers
        )

        assert response.status_code == 404

    @pytest.mark.unit
    def test_get_session_requires_auth(self, client: TestClient) -> None:
        """Get endpoint requires authentication."""
        response = client.get("/api/v1/sessions/any-session-id")
        assert response.status_code == 401


class TestSessionTask:
    """Tests for POST /api/v1/sessions/{id}/task."""

    @pytest.mark.unit
    def test_start_task(
        self,
        client: TestClient,
        auth_headers: dict,
        created_session: dict
    ) -> None:
        """Can start a task for a session."""
        session_id = created_session["id"]

        response = client.post(
            f"/api/v1/sessions/{session_id}/task",
            headers=auth_headers,
            json={"task": "Execute: echo hello"}
        )

        assert response.status_code == 200
        data = response.json()
        
        # Validate TaskStartedResponse structure
        assert data["status"] == "running"
        assert data["session_id"] == session_id
        assert "message" in data
        assert "resumed_from" in data

    @pytest.mark.unit
    def test_start_task_with_empty_body(
        self,
        client: TestClient,
        auth_headers: dict,
        created_session: dict
    ) -> None:
        """Can start task with empty body, uses session's task."""
        session_id = created_session["id"]

        response = client.post(
            f"/api/v1/sessions/{session_id}/task",
            headers=auth_headers,
            json={}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"

    @pytest.mark.unit
    def test_start_task_with_config_overrides(
        self,
        client: TestClient,
        auth_headers: dict,
        created_session: dict
    ) -> None:
        """Can pass config overrides when starting task."""
        session_id = created_session["id"]

        response = client.post(
            f"/api/v1/sessions/{session_id}/task",
            headers=auth_headers,
            json={
                "config": {
                    "model": "claude-haiku-4-5-20251001",
                    "max_turns": 10
                }
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"

    @pytest.mark.unit
    def test_start_task_not_found(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Returns 404 for non-existent session (invalid format)."""
        response = client.post(
            "/api/v1/sessions/nonexistent/task",
            headers=auth_headers,
            json={"task": "Test"}
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    @pytest.mark.unit
    def test_start_task_not_found_valid_format(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Returns 404 for non-existent session with valid ID format."""
        response = client.post(
            "/api/v1/sessions/20250101_000000_deadbeef/task",
            headers=auth_headers,
            json={"task": "Test"}
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    @pytest.mark.unit
    def test_start_task_conflict_already_running(
        self,
        client: TestClient,
        auth_headers: dict,
        created_session: dict,
        mock_agent_runner
    ) -> None:
        """Returns 409 when task is already running."""
        session_id = created_session["id"]
        
        # Mock the agent runner to say task is already running
        mock_agent_runner.is_running.return_value = True

        with patch("src.api.routes.sessions.agent_runner", mock_agent_runner):
            response = client.post(
                f"/api/v1/sessions/{session_id}/task",
                headers=auth_headers,
                json={}
            )

        assert response.status_code == 409
        data = response.json()
        assert "already running" in data["detail"].lower()

    @pytest.mark.unit
    def test_start_task_requires_auth(self, client: TestClient) -> None:
        """Start task endpoint requires authentication."""
        response = client.post(
            "/api/v1/sessions/any-id/task",
            json={"task": "Test"}
        )
        assert response.status_code == 401


class TestSessionCancel:
    """Tests for POST /api/v1/sessions/{id}/cancel."""

    @pytest.mark.unit
    def test_cancel_session(
        self,
        client: TestClient,
        auth_headers: dict,
        created_session: dict
    ) -> None:
        """Can request cancellation of a session."""
        session_id = created_session["id"]

        response = client.post(
            f"/api/v1/sessions/{session_id}/cancel",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        
        # Validate CancelResponse structure
        assert data["session_id"] == session_id
        assert "status" in data
        assert "message" in data

    @pytest.mark.unit
    def test_cancel_not_running_session(
        self,
        client: TestClient,
        auth_headers: dict,
        created_session: dict
    ) -> None:
        """Cancelling a not-running session returns appropriate message."""
        session_id = created_session["id"]

        response = client.post(
            f"/api/v1/sessions/{session_id}/cancel",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert "not running" in data["message"].lower()

    @pytest.mark.unit
    def test_cancel_not_found(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Returns 404 for non-existent session (invalid format)."""
        response = client.post(
            "/api/v1/sessions/nonexistent/cancel",
            headers=auth_headers
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    @pytest.mark.unit
    def test_cancel_not_found_valid_format(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Returns 404 for non-existent session with valid ID format."""
        response = client.post(
            "/api/v1/sessions/20250101_000000_deadbeef/cancel",
            headers=auth_headers
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    @pytest.mark.unit
    def test_cancel_requires_auth(self, client: TestClient) -> None:
        """Cancel endpoint requires authentication."""
        response = client.post("/api/v1/sessions/any-id/cancel")
        assert response.status_code == 401


class TestSessionResult:
    """Tests for GET /api/v1/sessions/{id}/result."""

    @pytest.mark.unit
    def test_get_result(
        self,
        client: TestClient,
        auth_headers: dict,
        created_session: dict
    ) -> None:
        """Can get result for a session."""
        session_id = created_session["id"]

        response = client.get(
            f"/api/v1/sessions/{session_id}/result",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        
        # Validate ResultResponse structure
        assert data["session_id"] == session_id
        assert "status" in data
        assert "error" in data
        assert "comments" in data
        assert "output" in data
        assert "result_files" in data
        assert isinstance(data["result_files"], list)
        assert "metrics" in data

    @pytest.mark.unit
    def test_get_result_metrics_structure(
        self,
        client: TestClient,
        auth_headers: dict,
        created_session: dict
    ) -> None:
        """Result metrics have correct structure."""
        session_id = created_session["id"]

        response = client.get(
            f"/api/v1/sessions/{session_id}/result",
            headers=auth_headers
        )

        data = response.json()
        metrics = data["metrics"]
        
        # Validate ResultMetrics structure
        assert "duration_ms" in metrics
        assert "num_turns" in metrics
        assert "total_cost_usd" in metrics
        assert "model" in metrics
        assert "usage" in metrics

    @pytest.mark.unit
    def test_get_result_not_found(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Returns 404 for non-existent session (invalid format)."""
        response = client.get(
            "/api/v1/sessions/nonexistent/result",
            headers=auth_headers
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    @pytest.mark.unit
    def test_get_result_not_found_valid_format(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Returns 404 for non-existent session with valid ID format."""
        response = client.get(
            "/api/v1/sessions/20250101_000000_deadbeef/result",
            headers=auth_headers
        )

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    @pytest.mark.unit
    def test_get_result_requires_auth(self, client: TestClient) -> None:
        """Result endpoint requires authentication."""
        response = client.get("/api/v1/sessions/any-id/result")
        assert response.status_code == 401

    @pytest.mark.unit
    def test_get_result_wrong_user(
        self,
        client: TestClient,
        auth_headers: dict,
        second_auth_headers: dict
    ) -> None:
        """Cannot get result for another user's session."""
        # Create session with user 1
        create = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Private task"}
        )
        session_id = create.json()["id"]

        # Try to get result with user 2
        response = client.get(
            f"/api/v1/sessions/{session_id}/result",
            headers=second_auth_headers
        )

        assert response.status_code == 404


class TestSessionResumability:
    """Tests for session resumability after cancellation."""

    @pytest.mark.unit
    def test_session_response_includes_resumable_field(
        self,
        client: TestClient,
        auth_headers: dict,
        created_session: dict
    ) -> None:
        """Session response includes resumable field."""
        session_id = created_session["id"]

        response = client.get(
            f"/api/v1/sessions/{session_id}",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        
        # resumable field should be present (can be None for non-cancelled)
        assert "resumable" in data

    @pytest.mark.unit
    def test_new_session_resumable_is_none(
        self,
        client: TestClient,
        auth_headers: dict,
        created_session: dict
    ) -> None:
        """New sessions have resumable=None (not cancelled)."""
        session_id = created_session["id"]

        response = client.get(
            f"/api/v1/sessions/{session_id}",
            headers=auth_headers
        )

        data = response.json()
        # For non-cancelled sessions, resumable is None
        assert data["status"] == "pending"
        assert data.get("resumable") is None


class TestSessionStatusTransitions:
    """Tests for session status transitions."""

    @pytest.mark.unit
    def test_session_status_running_after_start(
        self,
        client: TestClient,
        auth_headers: dict,
        created_session: dict
    ) -> None:
        """Session status transitions to running after starting task."""
        session_id = created_session["id"]

        # Start task
        response = client.post(
            f"/api/v1/sessions/{session_id}/task",
            headers=auth_headers,
            json={}
        )

        assert response.status_code == 200
        assert response.json()["status"] == "running"

        # Verify via GET
        get_response = client.get(
            f"/api/v1/sessions/{session_id}",
            headers=auth_headers
        )
        assert get_response.json()["status"] == "running"

    @pytest.mark.unit
    def test_session_status_flow_pending_to_running(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Session follows pending -> running status flow."""
        # Create session (status: pending)
        create_response = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Status flow test"}
        )
        session_id = create_response.json()["id"]
        assert create_response.json()["status"] == "pending"

        # Start task (status: running)
        start_response = client.post(
            f"/api/v1/sessions/{session_id}/task",
            headers=auth_headers,
            json={}
        )
        assert start_response.json()["status"] == "running"


class TestCancelledEventStructure:
    """Tests for cancelled event structure in API responses."""

    @pytest.mark.unit
    def test_cancel_response_structure(
        self,
        client: TestClient,
        auth_headers: dict,
        created_session: dict
    ) -> None:
        """Cancel response has correct structure."""
        session_id = created_session["id"]

        response = client.post(
            f"/api/v1/sessions/{session_id}/cancel",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()

        # Validate CancelResponse fields
        assert "session_id" in data
        assert "status" in data
        assert "message" in data
        assert data["session_id"] == session_id


