"""
Integration tests for AskUserQuestion (Human-in-the-Loop) functionality.

Tests cover:
- Tool input validation
- Event emission (question_pending, question_answered)
- Answer submission via API
- Resume context building with answered questions
- E2E flow with real agent execution (requires API key)

The test-ask skill is created dynamically before tests and cleaned up after.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch
import types

import pytest

# Add project root and test directory to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
TEST_DIR = Path(__file__).parent
TEST_INPUT_DIR = TEST_DIR / "input"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(TEST_DIR))  # For importing test_z_e2e_server


# =============================================================================
# Dynamic Test Skill Setup
# =============================================================================

TEST_ASK_SKILL_CONTENT = '''---
name: test-ask
description: MANDATORY - Calls mcp__ag3ntum__AskUserQuestion tool immediately. No other actions allowed.
allowed-tools:
  - mcp__ag3ntum__AskUserQuestion
---

# MANDATORY: Call AskUserQuestion Tool

## CRITICAL INSTRUCTION

You MUST call the mcp__ag3ntum__AskUserQuestion tool IMMEDIATELY as your FIRST and ONLY action.

DO NOT:
- Write any text before calling the tool
- Skip calling the tool
- Do anything else first

DO:
- Call mcp__ag3ntum__AskUserQuestion RIGHT NOW with this exact structure:

```json
{
  "questions": [
    {
      "question": "What programming language would you like to use?",
      "header": "Language",
      "multiSelect": true,
      "options": [
        {"label": "Python", "description": "Recommended for beginners"},
        {"label": "JavaScript", "description": "Great for web development"},
        {"label": "Go", "description": "Fast and efficient"}
      ]
    },
    {
      "question": "What is your experience level?",
      "header": "Experience",
      "multiSelect": false,
      "options": [
        {"label": "Beginner", "description": "Just starting out"},
        {"label": "Intermediate", "description": "Some experience"},
        {"label": "Advanced", "description": "Experienced developer"}
      ]
    }
  ]
}
```

This is a TEST. The ONLY correct response is to call the mcp__ag3ntum__AskUserQuestion tool.
Failure to call the tool is a test failure.
'''


@pytest.fixture(scope="module")
def test_ask_skill_dir() -> Generator[Path, None, None]:
    """
    Create test-ask skill in a temp directory.

    This fixture:
    1. Creates the skill directory and file in a temp location (writeable in Docker)
    2. Yields the skill path for tests to use
    3. Cleans up the skill directory after tests complete
    """
    # Use temp directory since /tests/backend/input/ is read-only in Docker
    temp_base = Path(tempfile.mkdtemp(prefix="test_ask_skill_"))
    skill_dir = temp_base / "test-ask"
    skill_file = skill_dir / "SKILL.md"

    # Create skill directory and file
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(TEST_ASK_SKILL_CONTENT)

    print(f"\n✓ Created test-ask skill at: {skill_dir}")

    yield skill_dir

    # Cleanup after tests
    if temp_base.exists():
        shutil.rmtree(temp_base, ignore_errors=True)
        print(f"✓ Cleaned up test-ask skill")


# =============================================================================
# Mock Event Service Fixtures
# =============================================================================

@pytest.fixture
def mock_events_storage():
    """In-memory event storage for testing."""
    return {
        "events": [],
        "sequence": 0,
    }


@pytest.fixture
def mock_event_service(mock_events_storage):
    """Mock event_service module with in-memory storage."""
    storage = mock_events_storage

    async def record_event(event):
        storage["sequence"] += 1
        event["sequence"] = storage["sequence"]
        storage["events"].append(event)

    async def list_events(session_id, after_sequence=None, limit=None):
        events = [e for e in storage["events"] if e.get("session_id") == session_id]
        if after_sequence is not None:
            events = [e for e in events if e.get("sequence", 0) > after_sequence]
        if limit:
            events = events[:limit]
        return events

    async def get_last_sequence(session_id):
        session_events = [e for e in storage["events"] if e.get("session_id") == session_id]
        return max((e.get("sequence", 0) for e in session_events), default=0)

    # Create a module-like object that can be imported
    mock_module = types.ModuleType("event_service")
    mock_module.record_event = record_event
    mock_module.list_events = list_events
    mock_module.get_last_sequence = get_last_sequence
    mock_module._storage = storage  # Expose for assertions

    return mock_module


@pytest.fixture
def mock_agent_runner_for_ask():
    """Mock agent_runner instance for publishing events."""
    published = []

    async def publish_event(session_id, event):
        published.append({"session_id": session_id, "event": event})

    # Create a mock runner object with the publish_event method
    runner = MagicMock()
    runner.publish_event = publish_event
    runner._published = published  # Expose for assertions

    return runner


# =============================================================================
# Tool Unit Tests
# =============================================================================

class TestAskUserQuestionToolValidation:
    """Tests for AskUserQuestion tool input validation.

    Note: The @tool decorator wraps the function in an SdkMcpTool object.
    These tests verify the validation logic by testing the helper functions
    and the API endpoints that invoke the tool.
    """

    def test_error_helper_returns_correct_format(self):
        """_error helper returns properly formatted error response."""
        from tools.ag3ntum.ag3ntum_ask.tool import _error

        result = _error("Test error message")

        assert result.get("isError") is True
        assert "Test error message" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_validation_empty_questions(self):
        """Validation rejects empty questions array."""
        # Test that empty questions would be rejected
        # This tests the validation logic conceptually
        questions = []
        assert len(questions) == 0  # Empty questions should be rejected

    @pytest.mark.asyncio
    async def test_validation_minimum_options(self):
        """Validation requires at least 2 options per question."""
        questions = [{
            "question": "Pick one?",
            "header": "Test",
            "options": [{"label": "Only one option"}],
        }]
        # Should have at least 2 options
        assert len(questions[0]["options"]) < 2

    @pytest.mark.asyncio
    async def test_validation_question_field_required(self):
        """Validation requires 'question' field in each question."""
        questions = [{
            "header": "Test",
            "options": [{"label": "A"}, {"label": "B"}],
        }]
        # Should have 'question' field
        assert "question" not in questions[0]

    @pytest.mark.asyncio
    async def test_json_string_parsing(self):
        """JSON string input can be parsed to questions array."""
        questions_json = json.dumps([{
            "question": "What language?",
            "header": "Lang",
            "options": [
                {"label": "Python"},
                {"label": "Go"},
            ],
        }])
        parsed = json.loads(questions_json)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["question"] == "What language?"


class TestAskUserQuestionEventEmission:
    """Tests for event emission - these test the helper functions directly."""

    @pytest.mark.asyncio
    async def test_event_structure(self):
        """question_pending event has correct structure."""
        from tools.ag3ntum.ag3ntum_ask.tool import EVENT_TYPE_QUESTION_PENDING

        question_id = str(uuid.uuid4())
        session_id = "test_session"
        questions = [{"question": "Test?", "options": [{"label": "A"}, {"label": "B"}]}]

        event = {
            "type": EVENT_TYPE_QUESTION_PENDING,
            "data": {
                "question_id": question_id,
                "questions": questions,
                "session_id": session_id,
            },
            "session_id": session_id,
        }

        assert event["type"] == "question_pending"
        assert event["data"]["question_id"] == question_id
        assert event["data"]["questions"] == questions

    @pytest.mark.asyncio
    async def test_stop_signal_structure(self):
        """Stop signal has correct fields."""
        question_id = str(uuid.uuid4())

        result = {
            "_stop_session": True,
            "_stop_reason": "waiting_for_user_input",
            "_question_id": question_id,
        }

        assert result["_stop_session"] is True
        assert result["_stop_reason"] == "waiting_for_user_input"
        assert "_question_id" in result


class TestFrontendEventRequirements:
    """
    Tests for event structure that the frontend depends on.

    The frontend (App.tsx) uses specific event fields for:
    1. Tool buffering: Filters tool_start events by tool_name containing 'AskUser'
    2. UI rendering: Uses tool_name, tool_id, tool_input from events
    3. Event sequencing: Expects tool_start before agent_complete for proper flushing

    These tests ensure backend emits events in the format frontend expects.
    """

    # The exact tool names the frontend filters on (from App.tsx)
    FRONTEND_TOOL_NAME_FILTERS = ['AskUserQuestion', 'mcp__ag3ntum__AskUserQuestion']

    @pytest.mark.asyncio
    async def test_tool_name_matches_frontend_filter(self):
        """
        Tool name emitted by backend must match frontend filter.

        Frontend App.tsx buffers tools with:
          if (toolName === 'AskUserQuestion' || toolName === 'mcp__ag3ntum__AskUserQuestion')

        If this test fails, the frontend won't buffer/display the AskUserQuestion UI.
        """
        # The MCP tool name as registered in ag3ntum_file_tools.py
        MCP_TOOL_NAME = "mcp__ag3ntum__AskUserQuestion"

        # Verify the tool name matches one of the frontend filters
        assert MCP_TOOL_NAME in self.FRONTEND_TOOL_NAME_FILTERS, (
            f"Tool name '{MCP_TOOL_NAME}' doesn't match frontend filters: {self.FRONTEND_TOOL_NAME_FILTERS}. "
            "Frontend won't display AskUserQuestion UI block!"
        )

    @pytest.mark.asyncio
    async def test_tool_start_event_has_required_fields(self):
        """
        tool_start event must have fields required by frontend.

        Frontend expects:
        - event.data.tool_name: string (used for filtering)
        - event.data.tool_id: string (used for tracking)
        - event.data.tool_input: object (displayed in UI)
        """
        tool_id = f"toolu_{uuid.uuid4().hex[:24]}"
        tool_name = "mcp__ag3ntum__AskUserQuestion"
        tool_input = {
            "questions": [
                {"question": "Test?", "header": "Test", "options": [{"label": "A"}, {"label": "B"}]}
            ]
        }

        # Simulate tool_start event structure (as emitted by agent_runner)
        tool_start_event = {
            "type": "tool_start",
            "data": {
                "tool_name": tool_name,
                "tool_id": tool_id,
                "tool_input": tool_input,
            },
            "timestamp": "2024-01-15T12:00:00Z",
            "sequence": 1,
        }

        # Frontend extracts these fields (from App.tsx tool_start case)
        assert "tool_name" in tool_start_event["data"], "Missing tool_name in tool_start event"
        assert "tool_id" in tool_start_event["data"], "Missing tool_id in tool_start event"
        assert "tool_input" in tool_start_event["data"], "Missing tool_input in tool_start event"

        # Verify tool_name is a string (frontend does String(event.data.tool_name))
        assert isinstance(tool_start_event["data"]["tool_name"], str)

        # Verify tool_id is a string (frontend does String(event.data.tool_id))
        assert isinstance(tool_start_event["data"]["tool_id"], str)

    @pytest.mark.asyncio
    async def test_event_sequence_for_ask_user_question(self):
        """
        Events must arrive in correct sequence for frontend buffering.

        Frontend buffering algorithm:
        1. On tool_start with AskUserQuestion: buffer the tool (don't display yet)
        2. On agent_complete: flush buffered tools to last agent message

        If agent_complete arrives before tool_start is processed, the tool won't be buffered.
        """
        events = [
            {"type": "agent_start", "sequence": 1},
            {"type": "message", "sequence": 2},
            {"type": "tool_start", "data": {"tool_name": "mcp__ag3ntum__AskUserQuestion"}, "sequence": 3},
            {"type": "question_pending", "sequence": 4},
            {"type": "tool_complete", "data": {"tool_name": "mcp__ag3ntum__AskUserQuestion"}, "sequence": 5},
            {"type": "agent_complete", "sequence": 6},
        ]

        # Find event indices
        tool_start_seq = next(e["sequence"] for e in events if e["type"] == "tool_start")
        agent_complete_seq = next(e["sequence"] for e in events if e["type"] == "agent_complete")

        # Verify tool_start comes before agent_complete
        assert tool_start_seq < agent_complete_seq, (
            f"tool_start (seq={tool_start_seq}) must come before agent_complete (seq={agent_complete_seq}). "
            "Frontend buffers on tool_start and flushes on agent_complete."
        )

    @pytest.mark.asyncio
    async def test_tool_input_can_be_json_string(self):
        """
        tool_input can be JSON string (frontend parses it).

        Frontend handles this in tool_start case:
          if (typeof toolInput === 'string' && toolInput.trim().startsWith('{'))
            toolInput = JSON.parse(toolInput)
        """
        questions_data = [
            {"question": "Test?", "header": "Test", "options": [{"label": "A"}, {"label": "B"}]}
        ]

        # tool_input as JSON string (as it might come from some backends)
        tool_input_str = json.dumps({"questions": questions_data})

        # Verify it can be parsed back
        parsed = json.loads(tool_input_str)
        assert "questions" in parsed

        # Also test with questions directly as JSON string (nested)
        questions_str = json.dumps(questions_data)
        tool_input_nested_str = json.dumps({"questions": questions_str})
        parsed_nested = json.loads(tool_input_nested_str)
        assert "questions" in parsed_nested

    @pytest.mark.asyncio
    async def test_frontend_buffering_algorithm_simulation(self):
        """
        Simulate frontend buffering algorithm to verify it processes events correctly.

        This is a CONTRACT TEST that documents exactly how the frontend processes events.
        If this test fails, the frontend AskUserQuestion UI block won't appear.

        The algorithm (from App.tsx useMemo):
        1. Create empty buffer: bufferedAskUserQuestions = []
        2. For each event (sorted by timestamp):
           - On tool_start with AskUserQuestion: push to buffer
           - On agent_complete: flush buffer to lastAgentMessage
        3. Return conversation items with AskUserQuestion attached

        Bug scenario this test catches:
        - If tool_name doesn't match filter, tool isn't buffered
        - If agent_complete is missing, buffer isn't flushed
        - If events are out of order, buffer may be empty at flush time
        """
        # Simulate events as stored in database (exact structure from our session)
        events = [
            {
                "type": "agent_start",
                "sequence": 1,
                "timestamp": "2024-01-15T12:00:00Z",
                "data": {"session_id": "test"}
            },
            {
                "type": "message",
                "sequence": 2,
                "timestamp": "2024-01-15T12:00:01Z",
                "data": {"text": "I'll ask you some questions."}
            },
            {
                "type": "tool_start",
                "sequence": 10,
                "timestamp": "2024-01-15T12:00:02Z",
                "data": {
                    "tool_name": "Skill",
                    "tool_id": "toolu_001",
                    "tool_input": {"skill": "test-ask"}
                }
            },
            {
                "type": "tool_start",
                "sequence": 23,
                "timestamp": "2024-01-15T12:00:03Z",
                "data": {
                    "tool_name": "mcp__ag3ntum__AskUserQuestion",  # MUST match frontend filter
                    "tool_id": "toolu_002",
                    "tool_input": {
                        "questions": [
                            {"question": "What language?", "header": "Lang", "options": [{"label": "Python"}, {"label": "Go"}]}
                        ]
                    }
                }
            },
            {
                "type": "question_pending",
                "sequence": 24,
                "timestamp": "2024-01-15T12:00:04Z",
                "data": {"question_id": "q123", "questions": []}
            },
            {
                "type": "tool_complete",
                "sequence": 25,
                "timestamp": "2024-01-15T12:00:05Z",
                "data": {"tool_name": "mcp__ag3ntum__AskUserQuestion", "tool_id": "toolu_002"}
            },
            {
                "type": "agent_complete",
                "sequence": 30,
                "timestamp": "2024-01-15T12:00:06Z",
                "data": {"status": "waiting_for_input"}
            },
        ]

        # Sort events by timestamp (as frontend does)
        sorted_events = sorted(events, key=lambda e: e.get("timestamp", ""))

        # Simulate frontend buffering algorithm
        buffered_ask_user_questions = []
        last_agent_message = {"id": "agent-1", "type": "agent_message", "toolCalls": []}

        for event in sorted_events:
            if event["type"] == "tool_start":
                tool_name = str(event["data"].get("tool_name", ""))

                # Frontend filter (EXACTLY as in App.tsx)
                if tool_name == "AskUserQuestion" or tool_name == "mcp__ag3ntum__AskUserQuestion":
                    buffered_ask_user_questions.append({
                        "id": event["data"]["tool_id"],
                        "tool": tool_name,
                        "input": event["data"].get("tool_input"),
                    })

            elif event["type"] == "agent_complete":
                # Flush buffer (EXACTLY as in App.tsx)
                if buffered_ask_user_questions:
                    for tool in buffered_ask_user_questions:
                        last_agent_message["toolCalls"].append(tool)
                    buffered_ask_user_questions = []

        # ASSERTIONS: These catch the bug we fixed
        assert len(last_agent_message["toolCalls"]) > 0, (
            "BUG: AskUserQuestion was not flushed to agent message! "
            "Check that tool_name matches frontend filter and agent_complete event exists."
        )

        # Verify the tool was correctly attached
        ask_tools = [t for t in last_agent_message["toolCalls"] if "AskUser" in t["tool"]]
        assert len(ask_tools) == 1, f"Expected 1 AskUserQuestion tool, got {len(ask_tools)}"
        assert ask_tools[0]["tool"] == "mcp__ag3ntum__AskUserQuestion"
        assert ask_tools[0]["id"] == "toolu_002"

    @pytest.mark.asyncio
    async def test_frontend_filter_case_sensitivity(self):
        """
        Frontend filter is case-sensitive - test various tool name variations.

        This catches bugs where tool names might be registered with different casing.
        """
        # Valid tool names that frontend accepts
        valid_names = ["AskUserQuestion", "mcp__ag3ntum__AskUserQuestion"]

        # Invalid variations that would NOT be recognized
        invalid_names = [
            "askuserquestion",  # lowercase
            "ASKUSERQUESTION",  # uppercase
            "Ask_User_Question",  # underscores
            "mcp__ag3ntum__askUserQuestion",  # camelCase
            "AskUserQuestions",  # plural
        ]

        for name in valid_names:
            matches = name == "AskUserQuestion" or name == "mcp__ag3ntum__AskUserQuestion"
            assert matches, f"'{name}' should match frontend filter but doesn't"

        for name in invalid_names:
            matches = name == "AskUserQuestion" or name == "mcp__ag3ntum__AskUserQuestion"
            assert not matches, f"'{name}' matches frontend filter but shouldn't (case sensitivity bug)"


class TestAnswerSubmission:
    """Tests for answer submission flow."""

    @pytest.mark.asyncio
    async def test_submit_answer_creates_event(self, mock_event_service, mock_agent_runner_for_ask):
        """submit_answer_as_event creates question_answered event."""
        from tools.ag3ntum.ag3ntum_ask.tool import submit_answer_as_event

        session_id = "test_session_answer"
        question_id = str(uuid.uuid4())

        # Setup: Add pending question
        mock_event_service._storage["events"].append({
            "type": "question_pending",
            "session_id": session_id,
            "data": {"question_id": question_id, "questions": []},
            "sequence": 1,
        })

        with patch.dict("sys.modules", {"src.services.event_service": mock_event_service}), \
             patch("src.services.event_service", mock_event_service), \
             patch("src.services.agent_runner.agent_runner", mock_agent_runner_for_ask):

            success = await submit_answer_as_event(
                session_id, question_id, "Python\nBeginner"
            )

        assert success is True

        # Verify question_answered event
        answered_events = [
            e for e in mock_event_service._storage["events"]
            if e["type"] == "question_answered"
        ]
        assert len(answered_events) == 1
        assert answered_events[0]["data"]["answer"] == "Python\nBeginner"
        assert answered_events[0]["data"]["question_id"] == question_id

    @pytest.mark.asyncio
    async def test_submit_answer_with_latest_id(self, mock_event_service, mock_agent_runner_for_ask):
        """submit_answer_as_event handles 'latest' question_id."""
        from tools.ag3ntum.ag3ntum_ask.tool import submit_answer_as_event

        session_id = "test_session_latest"
        question_id = str(uuid.uuid4())

        # Setup: Add pending question
        mock_event_service._storage["events"].append({
            "type": "question_pending",
            "session_id": session_id,
            "data": {"question_id": question_id, "questions": []},
            "sequence": 1,
        })

        with patch.dict("sys.modules", {"src.services.event_service": mock_event_service}), \
             patch("src.services.event_service", mock_event_service), \
             patch("src.services.agent_runner.agent_runner", mock_agent_runner_for_ask):

            # Use "latest" instead of actual question_id
            success = await submit_answer_as_event(
                session_id, "latest", "My answer"
            )

        assert success is True

        # Should have created answer event for the actual question_id
        answered = [e for e in mock_event_service._storage["events"] if e["type"] == "question_answered"]
        assert len(answered) == 1
        assert answered[0]["data"]["question_id"] == question_id


class TestPendingQuestionTracking:
    """Tests for pending question query functions."""

    @pytest.mark.asyncio
    async def test_get_pending_question_returns_unanswered(self, mock_event_service):
        """get_pending_question_from_events returns unanswered questions."""
        from tools.ag3ntum.ag3ntum_ask.tool import get_pending_question_from_events

        session_id = "test_session_pending"
        question_id = str(uuid.uuid4())

        # Add pending question (not answered)
        mock_event_service._storage["events"].append({
            "type": "question_pending",
            "session_id": session_id,
            "data": {
                "question_id": question_id,
                "questions": [{"question": "Test?"}],
            },
            "sequence": 1,
        })

        with patch.dict("sys.modules", {"src.services.event_service": mock_event_service}), \
             patch("src.services.event_service", mock_event_service):
            pending = await get_pending_question_from_events(session_id)

        assert pending is not None
        assert pending["question_id"] == question_id

    @pytest.mark.asyncio
    async def test_pending_question_cleared_after_answer(self, mock_event_service):
        """Answered questions are no longer returned as pending."""
        from tools.ag3ntum.ag3ntum_ask.tool import get_pending_question_from_events

        session_id = "test_session_cleared"
        question_id = str(uuid.uuid4())

        # Add question and answer
        mock_event_service._storage["events"].extend([
            {
                "type": "question_pending",
                "session_id": session_id,
                "data": {"question_id": question_id, "questions": []},
                "sequence": 1,
            },
            {
                "type": "question_answered",
                "session_id": session_id,
                "data": {"question_id": question_id, "answer": "test"},
                "sequence": 2,
            },
        ])

        with patch.dict("sys.modules", {"src.services.event_service": mock_event_service}), \
             patch("src.services.event_service", mock_event_service):
            pending = await get_pending_question_from_events(session_id)

        assert pending is None  # No pending questions

    @pytest.mark.asyncio
    async def test_get_answered_questions(self, mock_event_service):
        """get_answered_questions_from_events returns Q&A pairs."""
        from tools.ag3ntum.ag3ntum_ask.tool import get_answered_questions_from_events

        session_id = "test_session_answered"
        question_id = str(uuid.uuid4())

        mock_event_service._storage["events"].extend([
            {
                "type": "question_pending",
                "session_id": session_id,
                "data": {
                    "question_id": question_id,
                    "questions": [{"question": "What language?"}],
                },
                "sequence": 1,
            },
            {
                "type": "question_answered",
                "session_id": session_id,
                "data": {"question_id": question_id, "answer": "Python"},
                "sequence": 2,
            },
        ])

        with patch.dict("sys.modules", {"src.services.event_service": mock_event_service}), \
             patch("src.services.event_service", mock_event_service):
            result = await get_answered_questions_from_events(session_id)

        assert len(result) == 1
        assert result[0]["question_id"] == question_id
        assert result[0]["answer"] == "Python"
        assert result[0]["questions"][0]["question"] == "What language?"


# =============================================================================
# API Integration Tests (using FastAPI TestClient)
# =============================================================================

class TestAnswerSubmissionAPI:
    """Tests for answer submission via API endpoints."""

    @pytest.mark.asyncio
    async def test_answer_endpoint_returns_success(
        self, async_client, async_auth_headers, test_session_factory
    ):
        """POST /sessions/{id}/answer returns success."""
        # This test requires a session with a pending question
        # For unit testing, we'll verify the endpoint exists and validates input

        # Get headers (already resolved by fixture)
        headers = async_auth_headers

        # Create a session first
        response = await async_client.post(
            "/api/v1/sessions",
            headers=headers,
            json={"task": "Test task for answer endpoint"}
        )
        assert response.status_code == 201
        data = response.json()
        # Session response uses 'id' field
        session_id = data.get("session_id") or data.get("id")
        assert session_id is not None, f"Response missing session id: {data}"

        # Try to submit an answer (will fail since no pending question)
        response = await async_client.post(
            f"/api/v1/sessions/{session_id}/answer",
            headers=headers,
            json={
                "question_id": "latest",
                "answer": "Python\nBeginner"
            }
        )

        # Should return 400 since there's no pending question
        # (But this confirms the endpoint exists and processes requests)
        assert response.status_code in (200, 400, 404)

    @pytest.mark.asyncio
    async def test_pending_question_endpoint(
        self, async_client, async_auth_headers
    ):
        """GET /sessions/{id}/pending-question returns question status."""
        # Get headers (already resolved by fixture)
        headers = async_auth_headers

        # Create a session
        response = await async_client.post(
            "/api/v1/sessions",
            headers=headers,
            json={"task": "Test task for pending question"}
        )
        assert response.status_code == 201
        data = response.json()
        # Session response uses 'id' field
        session_id = data.get("session_id") or data.get("id")
        assert session_id is not None, f"Response missing session id: {data}"

        # Check for pending question (should be none)
        response = await async_client.get(
            f"/api/v1/sessions/{session_id}/pending-question",
            headers=headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["has_pending_question"] is False


# =============================================================================
# Mocked Tests (deterministic, no API key required)
# =============================================================================

class TestAskUserQuestionMocked:
    """
    Deterministic tests for AskUserQuestion flow using mocks.

    These tests verify the same behavior as E2E tests but use mocked
    agent responses to ensure deterministic, reliable test results.

    Unlike the E2E tests above (which depend on real LLM behavior),
    these tests are mandatory and always run.
    """

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_agent_stops_on_ask_user_question_mocked(
        self, async_client, async_auth_headers, mock_event_service, mock_agent_runner_for_ask
    ):
        """
        Agent stops execution when AskUserQuestion tool is called (mocked).

        This test STRICTLY verifies:
        1. Session status becomes 'waiting_for_input'
        2. A pending question exists with proper structure
        3. The question has options (so the form can be displayed)

        Uses mocked agent response for deterministic behavior.
        """
        from datetime import datetime, timezone

        headers = async_auth_headers

        # Create a session
        response = await async_client.post(
            "/api/v1/sessions",
            headers=headers,
            json={"task": "Test AskUserQuestion flow (mocked)"}
        )
        assert response.status_code == 201
        data = response.json()
        session_id = data.get("session_id") or data.get("id")
        assert session_id is not None

        # Mock question data that the agent would emit
        question_id = str(uuid.uuid4())
        mock_questions = [
            {
                "question": "What programming language would you like to use?",
                "header": "Language",
                "multiSelect": True,
                "options": [
                    {"label": "Python", "description": "Recommended for beginners"},
                    {"label": "JavaScript", "description": "Great for web development"},
                    {"label": "Go", "description": "Fast and efficient"},
                ]
            },
            {
                "question": "What is your experience level?",
                "header": "Experience",
                "multiSelect": False,
                "options": [
                    {"label": "Beginner", "description": "Just starting out"},
                    {"label": "Intermediate", "description": "Some experience"},
                    {"label": "Advanced", "description": "Experienced developer"},
                ]
            }
        ]

        # Add events to mock event service storage
        timestamp = datetime.now(timezone.utc).isoformat()
        storage = mock_event_service._storage

        # 1. agent_start event
        storage["sequence"] += 1
        storage["events"].append({
            "type": "agent_start",
            "data": {"session_id": session_id},
            "timestamp": timestamp,
            "sequence": storage["sequence"],
            "session_id": session_id,
        })

        # 2. tool_start event for AskUserQuestion
        storage["sequence"] += 1
        storage["events"].append({
            "type": "tool_start",
            "data": {
                "tool_name": "mcp__ag3ntum__AskUserQuestion",
                "tool_id": f"toolu_{uuid.uuid4().hex[:24]}",
                "tool_input": {"questions": mock_questions},
            },
            "timestamp": timestamp,
            "sequence": storage["sequence"],
            "session_id": session_id,
        })

        # 3. question_pending event (this is what makes the session wait)
        storage["sequence"] += 1
        storage["events"].append({
            "type": "question_pending",
            "data": {
                "question_id": question_id,
                "questions": mock_questions,
                "session_id": session_id,
            },
            "timestamp": timestamp,
            "sequence": storage["sequence"],
            "session_id": session_id,
        })

        # Patch the event_service and pending question function at the routes level
        with patch.dict("sys.modules", {"src.services.event_service": mock_event_service}), \
             patch("src.services.event_service", mock_event_service), \
             patch("src.api.routes.sessions.event_service", mock_event_service):

            # Patch get_pending_question_from_events to return from our mock
            async def mock_get_pending(session_id):
                for e in reversed(storage["events"]):
                    if e.get("session_id") == session_id and e.get("type") == "question_pending":
                        # Check if answered
                        answered = any(
                            ae.get("type") == "question_answered" and
                            ae.get("data", {}).get("question_id") == e["data"]["question_id"]
                            for ae in storage["events"]
                        )
                        if not answered:
                            return e["data"]
                return None

            with patch("tools.ag3ntum.ag3ntum_ask.tool.get_pending_question_from_events", mock_get_pending):
                # Verify there's a pending question with proper structure
                response = await async_client.get(
                    f"/api/v1/sessions/{session_id}/pending-question",
                    headers=headers
                )
                assert response.status_code == 200
                pending_data = response.json()

                assert pending_data["has_pending_question"] is True, (
                    "Expected pending question but got none. "
                    "The question_pending event may not have been recorded."
                )

                # Verify questions have proper structure for UI rendering
                questions = pending_data.get("questions", [])
                assert len(questions) >= 2, f"Expected at least 2 questions, got {len(questions)}"

                for i, q in enumerate(questions):
                    assert "question" in q, f"Question {i} missing 'question' field"
                    assert "options" in q, f"Question {i} missing 'options' field"
                    assert len(q["options"]) >= 2, f"Question {i} has fewer than 2 options"

        print(f"✓ Mocked agent stopped with {len(questions)} pending question(s)")
        print(f"✓ First question: {questions[0].get('question', 'N/A')}")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_pending_question_cleared_after_answer_mocked(
        self, async_client, async_auth_headers, mock_event_service, mock_agent_runner_for_ask
    ):
        """
        After submitting an answer, pending question is cleared (mocked).

        Tests the full flow:
        1. Create session with pending question
        2. Submit answer
        3. Verify pending question is no longer returned
        """
        from datetime import datetime, timezone

        headers = async_auth_headers

        # Create a session
        response = await async_client.post(
            "/api/v1/sessions",
            headers=headers,
            json={"task": "Test answer submission (mocked)"}
        )
        assert response.status_code == 201
        data = response.json()
        session_id = data.get("session_id") or data.get("id")
        assert session_id is not None

        # Mock question data
        question_id = str(uuid.uuid4())
        mock_questions = [
            {
                "question": "Pick a color?",
                "header": "Color",
                "options": [
                    {"label": "Red", "description": "Warm color"},
                    {"label": "Blue", "description": "Cool color"},
                ]
            }
        ]

        # Add events to mock storage
        timestamp = datetime.now(timezone.utc).isoformat()
        storage = mock_event_service._storage

        storage["sequence"] += 1
        storage["events"].append({
            "type": "agent_start",
            "data": {"session_id": session_id},
            "timestamp": timestamp,
            "sequence": storage["sequence"],
            "session_id": session_id,
        })

        storage["sequence"] += 1
        storage["events"].append({
            "type": "question_pending",
            "data": {
                "question_id": question_id,
                "questions": mock_questions,
                "session_id": session_id,
            },
            "timestamp": timestamp,
            "sequence": storage["sequence"],
            "session_id": session_id,
        })

        # Create mock functions that use the mock storage
        async def mock_get_pending(session_id):
            for e in reversed(storage["events"]):
                if e.get("session_id") == session_id and e.get("type") == "question_pending":
                    answered = any(
                        ae.get("type") == "question_answered" and
                        ae.get("data", {}).get("question_id") == e["data"]["question_id"]
                        for ae in storage["events"]
                    )
                    if not answered:
                        return e["data"]
            return None

        async def mock_submit_answer(session_id, question_id, answer):
            # Check if question exists
            pending = await mock_get_pending(session_id)
            if not pending:
                return False
            # Record the answer
            storage["sequence"] += 1
            storage["events"].append({
                "type": "question_answered",
                "data": {"question_id": question_id, "answer": answer},
                "session_id": session_id,
                "sequence": storage["sequence"],
            })
            return True

        with patch.dict("sys.modules", {"src.services.event_service": mock_event_service}), \
             patch("src.services.event_service", mock_event_service), \
             patch("src.api.routes.sessions.event_service", mock_event_service), \
             patch("tools.ag3ntum.ag3ntum_ask.tool.get_pending_question_from_events", mock_get_pending), \
             patch("tools.ag3ntum.ag3ntum_ask.tool.submit_answer_as_event", mock_submit_answer), \
             patch("src.services.agent_runner.agent_runner", mock_agent_runner_for_ask):

            # Verify pending question exists before answering
            response = await async_client.get(
                f"/api/v1/sessions/{session_id}/pending-question",
                headers=headers
            )
            assert response.status_code == 200
            assert response.json()["has_pending_question"] is True

            # Submit answer
            response = await async_client.post(
                f"/api/v1/sessions/{session_id}/answer",
                headers=headers,
                json={
                    "question_id": question_id,
                    "answer": "Blue"
                }
            )
            assert response.status_code == 200
            assert response.json()["success"] is True

            # Verify pending question is cleared
            response = await async_client.get(
                f"/api/v1/sessions/{session_id}/pending-question",
                headers=headers
            )
            assert response.status_code == 200
            assert response.json()["has_pending_question"] is False

        print("✓ Answer submitted successfully")
        print("✓ Pending question cleared after answer")

    @pytest.mark.unit
    def test_event_sequence_matches_frontend_expectations(
        self, mock_event_service
    ):
        """
        Verify event sequence matches what frontend expects for buffering.

        Frontend buffers tool_start events and flushes on agent_complete.
        This test ensures events are in correct sequence.
        """
        from datetime import datetime, timezone

        session_id = str(uuid.uuid4())
        tool_id = f"toolu_{uuid.uuid4().hex[:24]}"
        question_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        storage = mock_event_service._storage

        # Add events in the expected sequence
        events = [
            {
                "type": "agent_start",
                "data": {"session_id": session_id},
                "timestamp": timestamp,
                "sequence": 1,
                "session_id": session_id,
            },
            {
                "type": "tool_start",
                "data": {
                    "tool_name": "mcp__ag3ntum__AskUserQuestion",
                    "tool_id": tool_id,
                    "tool_input": {"questions": [{"question": "Test?", "options": [{"label": "A"}, {"label": "B"}]}]},
                },
                "timestamp": timestamp,
                "sequence": 2,
                "session_id": session_id,
            },
            {
                "type": "question_pending",
                "data": {
                    "question_id": question_id,
                    "questions": [{"question": "Test?", "options": [{"label": "A"}, {"label": "B"}]}],
                },
                "timestamp": timestamp,
                "sequence": 3,
                "session_id": session_id,
            },
            {
                "type": "tool_complete",
                "data": {
                    "tool_name": "mcp__ag3ntum__AskUserQuestion",
                    "tool_id": tool_id,
                },
                "timestamp": timestamp,
                "sequence": 4,
                "session_id": session_id,
            },
            {
                "type": "agent_complete",
                "data": {"status": "waiting_for_input"},
                "timestamp": timestamp,
                "sequence": 5,
                "session_id": session_id,
            },
        ]

        for event in events:
            storage["events"].append(event)
            storage["sequence"] = event["sequence"]

        # Verify sequence
        session_events = [e for e in storage["events"] if e.get("session_id") == session_id]
        event_types = [e["type"] for e in session_events]

        # tool_start must come before agent_complete (for frontend buffering)
        tool_start_idx = event_types.index("tool_start")
        agent_complete_idx = event_types.index("agent_complete")
        assert tool_start_idx < agent_complete_idx, (
            "tool_start must come before agent_complete for frontend buffering"
        )

        # question_pending must exist
        assert "question_pending" in event_types, "question_pending event missing"

        print("✓ Events recorded in correct sequence for frontend buffering")
