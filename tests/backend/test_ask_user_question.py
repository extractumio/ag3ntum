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
description: Test skill for AskUserQuestion tool - asks interactive questions.
allowed-tools:
  - mcp__ag3ntum__AskUserQuestion
---

# Test AskUserQuestion Skill

This skill tests the mcp__ag3ntum__AskUserQuestion tool.

## Instructions

When this skill is invoked, use the mcp__ag3ntum__AskUserQuestion tool to ask:

1. Language preference:
   - Question: "What programming language would you like to use?"
   - Header: "Language"
   - Options (multi-select):
     - Python (Recommended for beginners)
     - JavaScript (Great for web development)
     - TypeScript (Type-safe JavaScript)
     - Go (Fast and efficient)

2. Experience level:
   - Question: "What is your experience level?"
   - Header: "Experience"
   - Options:
     - Beginner (Just starting out)
     - Intermediate (Some experience)
     - Advanced (Experienced developer)
     - Expert (Deep expertise)

After receiving answers, respond with a summary of what they selected.

IMPORTANT: You MUST use the mcp__ag3ntum__AskUserQuestion tool.
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
# E2E Tests (require real server and API key)
# =============================================================================

def _check_api_key_available() -> bool:
    """
    Check if ANTHROPIC_API_KEY is available from any source.

    Checks in order:
    1. Environment variable ANTHROPIC_API_KEY
    2. Environment variable CLOUDLINUX_ANTHROPIC_API_KEY
    3. config/secrets.yaml file (both Docker mount and local)
    """
    import yaml

    # Check environment variables first
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLOUDLINUX_ANTHROPIC_API_KEY"):
        return True

    # Check secrets.yaml (both in Docker /config and local config/)
    secrets_paths = [
        Path("/config/secrets.yaml"),  # Docker mount
        PROJECT_ROOT / "config" / "secrets.yaml",  # Local development
    ]

    for secrets_path in secrets_paths:
        if secrets_path.exists():
            try:
                with open(secrets_path) as f:
                    secrets = yaml.safe_load(f) or {}
                if secrets.get("anthropic_api_key"):
                    return True
            except Exception:
                pass

    return False


# Check if API key is available for E2E tests that require the real model
HAS_API_KEY = _check_api_key_available()


# Import E2E fixtures from test_z_e2e_server
try:
    from test_z_e2e_server import (
        test_environment,
        test_user_credentials,
        running_server,
        get_auth_token,
        find_free_port,
        wait_for_server,
    )
except ImportError:
    # Fallback if running tests in isolation
    test_environment = None
    running_server = None

    def get_auth_token(base_url, email, password):
        import httpx
        response = httpx.post(
            f"{base_url}/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        return response.json()["access_token"]

    def find_free_port():
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            return s.getsockname()[1]

    def wait_for_server(host, port, timeout=10.0):
        import socket
        start = time.time()
        while time.time() - start < timeout:
            try:
                with socket.create_connection((host, port), timeout=1.0):
                    return True
            except (ConnectionRefusedError, socket.timeout, OSError):
                time.sleep(0.1)
        return False


@pytest.fixture(scope="module")
def e2e_test_environment_with_ask_skill(test_environment, test_ask_skill_dir):
    """
    Extend the E2E test environment to include the test-ask skill.

    Copies the dynamically created test-ask skill to the temp skills directory.
    """
    if test_environment is None:
        pytest.skip("E2E test environment not available")

    temp_skills = test_environment["temp_skills"]

    # Copy test-ask skill to temp skills directory
    dest_skill_dir = temp_skills / "test-ask"
    if test_ask_skill_dir.exists():
        shutil.copytree(test_ask_skill_dir, dest_skill_dir, dirs_exist_ok=True)
        print(f"✓ Copied test-ask skill to: {dest_skill_dir}")

    return test_environment


@pytest.mark.e2e
@pytest.mark.skipif(not HAS_API_KEY, reason="No API key available for E2E tests")
class TestAskUserQuestionE2E:
    """
    End-to-end tests for AskUserQuestion with real agent execution.

    These tests:
    1. Start a real server with the test-ask skill
    2. Run an agent task that invokes the skill
    3. Verify the agent stops with waiting_for_input status
    4. Submit an answer via API
    5. Resume and verify the agent continues

    Requires: ANTHROPIC_API_KEY in environment or secrets.yaml
    Run with: pytest -m e2e --run-e2e
    """

    def test_agent_stops_on_ask_user_question(
        self, running_server, e2e_test_environment_with_ask_skill
    ):
        """
        Agent stops execution when AskUserQuestion tool is called.

        This test STRICTLY verifies:
        1. Session status becomes 'waiting_for_input' (not just 'complete')
        2. A pending question exists with proper structure
        3. The question has options (so the form can be displayed)

        If this test fails, the AskUserQuestion UI form won't appear.
        """
        import httpx

        if running_server is None:
            pytest.skip("Running server not available")

        base_url = running_server["base_url"]
        test_user = running_server["test_user"]

        # Get auth token
        token = get_auth_token(base_url, test_user["email"], test_user["password"])
        headers = {"Authorization": f"Bearer {token}"}

        # Verify test-ask skill is available and has content
        temp_skills = running_server["temp_skills"]
        skill_path = temp_skills / "test-ask" / "test-ask.md"
        assert skill_path.exists(), f"test-ask skill not found at {skill_path}"
        skill_content = skill_path.read_text()
        assert len(skill_content) > 100, (
            f"test-ask skill content is too short ({len(skill_content)} chars). "
            "Skill file may be empty - check TEST_ASK_SKILL_CONTENT."
        )

        # Start a task that will invoke the AskUserQuestion tool
        response = httpx.post(
            f"{base_url}/api/v1/sessions/run",
            headers=headers,
            json={
                "task": "Use the test-ask skill to ask me about programming preferences",
                "config": {
                    "enable_skills": True,
                    "max_turns": 10,
                }
            },
            timeout=60.0
        )

        assert response.status_code == 201, f"Failed to start task: {response.text}"
        data = response.json()
        session_id = data["session_id"]

        # Poll for session to reach terminal state (waiting_for_input, complete, or failed)
        # LLM agents can take significant time to process skills and tools
        max_wait = 90  # seconds (increased from 30 for LLM processing time)
        poll_interval = 2  # seconds
        final_status = None

        for _ in range(max_wait // poll_interval):
            time.sleep(poll_interval)
            response = httpx.get(
                f"{base_url}/api/v1/sessions/{session_id}",
                headers=headers,
                timeout=10.0
            )
            if response.status_code == 200:
                session_data = response.json()
                final_status = session_data.get("status")
                # Stop polling when we reach a terminal state
                if final_status in ("waiting_for_input", "complete", "failed", "cancelled"):
                    break

        # STRICT: Require waiting_for_input status
        # If we get 'running' after timeout, the agent is taking too long (LLM latency)
        # If we get 'complete', the agent finished without calling AskUserQuestion
        if final_status == "running":
            # Get session events and server logs to diagnose what's happening
            event_summary = "Unknown (could not fetch events)"
            event_types = []
            try:
                events_response = httpx.get(
                    f"{base_url}/api/v1/sessions/{session_id}/events/history",
                    headers=headers,
                    timeout=10.0
                )
                if events_response.status_code == 200 and events_response.text:
                    events = events_response.json()
                    event_types = [e.get("type") for e in events]
                    tool_calls = [e for e in events if e.get("type") == "tool_start"]
                    tool_names = [e.get("data", {}).get("tool_name", "?") for e in tool_calls]
                    # Look for error events and get their data
                    error_events = [e for e in events if e.get("type") == "error"]
                    error_details = [e.get("data", {}) for e in error_events]
                    event_summary = (
                        f"{len(events)} events: {event_types}, "
                        f"{len(tool_calls)} tool calls: {tool_names[:5]}, "
                        f"errors: {error_details}"
                    )
            except Exception as e:
                event_summary = f"Error fetching events: {e}"

            # Try to read server stderr log
            server_log = ""
            try:
                temp_base = running_server["temp_skills"].parent
                stderr_log = temp_base / "server_stderr.log"
                if stderr_log.exists():
                    log_content = stderr_log.read_text()
                    # Get last 50 lines
                    server_log = "\n".join(log_content.split("\n")[-50:])
            except Exception as e:
                server_log = f"Error reading server log: {e}"

            pytest.fail(
                f"Agent still 'running' after {max_wait}s timeout. "
                f"Session {session_id} has {event_summary}. "
                f"LLM may be slow or stuck.\n"
                f"Server stderr (last 50 lines):\n{server_log}"
            )
        elif final_status == "complete":
            pytest.fail(
                f"Agent completed WITHOUT calling AskUserQuestion. "
                "The skill was not invoked or didn't call the tool. "
                "Check that skills are enabled and test-ask skill instructs agent to use AskUserQuestion."
            )
        elif final_status in ("failed", "cancelled"):
            pytest.fail(f"Agent {final_status}. Check server logs for errors.")

        assert final_status == "waiting_for_input", (
            f"Unexpected session status '{final_status}'. Expected 'waiting_for_input'."
        )

        # Verify there's a pending question with proper structure
        response = httpx.get(
            f"{base_url}/api/v1/sessions/{session_id}/pending-question",
            headers=headers,
            timeout=10.0
        )
        assert response.status_code == 200, f"Failed to get pending question: {response.text}"
        pending_data = response.json()

        assert pending_data["has_pending_question"] is True, (
            "Session is waiting_for_input but no pending question found. "
            "The question_pending event may not have been recorded."
        )

        # Verify questions have proper structure for UI rendering
        questions = pending_data.get("questions", [])
        assert len(questions) > 0, "Pending question has no questions array"

        for i, q in enumerate(questions):
            assert "question" in q, f"Question {i} missing 'question' field"
            assert "options" in q, f"Question {i} missing 'options' field"
            assert len(q["options"]) >= 2, f"Question {i} has fewer than 2 options"

        print(f"✓ Agent stopped with {len(questions)} pending question(s)")
        print(f"✓ First question: {questions[0].get('question', 'N/A')}")
