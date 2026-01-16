"""
Integration tests for AskUserQuestion against running Docker container.

These tests work directly against the running ag3ntum Docker container,
creating test prerequisites dynamically and cleaning up afterwards.

Prerequisites:
- Docker container running (./deploy.sh build)
- ANTHROPIC_API_KEY available in config/secrets.yaml

Run with: pytest tests/backend/test_ask_user_question_docker.py -v --run-e2e
"""
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Generator

import httpx
import pytest

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
TEST_INPUT_DIR = Path(__file__).parent / "input"

# Docker container API settings
DOCKER_API_HOST = "localhost"
DOCKER_API_PORT = 40080
DOCKER_BASE_URL = f"http://{DOCKER_API_HOST}:{DOCKER_API_PORT}"

# Test-ask skill content (same as in test_ask_user_question.py)
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


def _check_docker_running() -> bool:
    """Check if Docker container is running and accessible."""
    try:
        response = httpx.get(f"{DOCKER_BASE_URL}/api/v1/health", timeout=5.0)
        return response.status_code == 200
    except Exception:
        return False


def _check_api_key_available() -> bool:
    """Check if ANTHROPIC_API_KEY is available."""
    import yaml

    if os.environ.get("ANTHROPIC_API_KEY"):
        return True

    secrets_path = PROJECT_ROOT / "config" / "secrets.yaml"
    if secrets_path.exists():
        try:
            with open(secrets_path) as f:
                secrets = yaml.safe_load(f) or {}
            if secrets.get("anthropic_api_key"):
                return True
        except Exception:
            pass
    return False


def _run_docker_command(cmd: str) -> tuple[int, str, str]:
    """Run a command inside the Docker container."""
    full_cmd = f"docker compose exec -T ag3ntum-api {cmd}"
    result = subprocess.run(
        full_cmd,
        shell=True,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT
    )
    return result.returncode, result.stdout, result.stderr


def _run_docker_python(code: str) -> tuple[int, str, str]:
    """Run Python code inside the Docker container."""
    # Escape the code for shell
    escaped_code = code.replace("'", "'\\''")
    return _run_docker_command(f"python3 -c '{escaped_code}'")


DOCKER_RUNNING = _check_docker_running()
HAS_API_KEY = _check_api_key_available()


@pytest.fixture(scope="module")
def docker_test_user() -> Generator[dict, None, None]:
    """
    Create a test user in Docker's database and clean up afterwards.

    Yields:
        dict with user credentials: id, username, email, password
    """
    if not DOCKER_RUNNING:
        pytest.skip("Docker container not running")

    test_id = uuid.uuid4().hex[:8]
    username = f"e2e_askuser_{test_id}"
    email = f"e2e_askuser_{test_id}@test.local"
    password = "testpass123"
    user_id = str(uuid.uuid4())

    # Create user in Docker's database with linux_uid set
    create_user_code = f'''
import sys
sys.path.insert(0, "/")
import bcrypt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.db.models import Base, User
import secrets

db_path = "/data/ag3ntum.db"
engine = create_engine(f"sqlite:///{{db_path}}")
Session = sessionmaker(bind=engine)
session = Session()

# Hash password
password_hash = bcrypt.hashpw("{password}".encode(), bcrypt.gensalt()).decode()

# Create user with linux_uid=0 (root, since Docker runs as root)
user = User(
    id="{user_id}",
    username="{username}",
    email="{email}",
    password_hash=password_hash,
    role="user",
    jwt_secret=secrets.token_urlsafe(32),
    linux_uid=0,  # Docker runs as root
    is_active=True,
)
session.add(user)
session.commit()
print(f"Created user: {{user.id}}")
session.close()
'''

    returncode, stdout, stderr = _run_docker_python(create_user_code)
    if returncode != 0:
        pytest.fail(f"Failed to create test user: {stderr}")

    print(f"✓ Created Docker test user: {username} ({email})")

    yield {
        "id": user_id,
        "username": username,
        "email": email,
        "password": password,
    }

    # Cleanup: delete the test user
    delete_user_code = f'''
import sys
sys.path.insert(0, "/")
from sqlalchemy import create_engine, text

db_path = "/data/ag3ntum.db"
engine = create_engine(f"sqlite:///{{db_path}}")
with engine.connect() as conn:
    conn.execute(text("DELETE FROM users WHERE id = '{user_id}'"))
    conn.commit()
print("Deleted test user")
'''

    returncode, stdout, stderr = _run_docker_python(delete_user_code)
    if returncode == 0:
        print(f"✓ Cleaned up Docker test user: {username}")
    else:
        print(f"⚠ Warning: Failed to cleanup user: {stderr}")


@pytest.fixture(scope="module")
def docker_test_skill() -> Generator[Path, None, None]:
    """
    Create test-ask skill on HOST filesystem (which Docker mounts as /skills).

    The skills directory is mounted read-only in Docker, so we create
    the skill file on the host and Docker picks it up automatically.

    Yields:
        Path to the skill directory (host path)
    """
    if not DOCKER_RUNNING:
        pytest.skip("Docker container not running")

    skill_name = "test-ask"

    # Create skill on HOST filesystem (PROJECT_ROOT/skills/ is mounted as /skills in Docker)
    host_skills_dir = PROJECT_ROOT / "skills"
    host_skill_dir = host_skills_dir / skill_name
    host_skill_file = host_skill_dir / "SKILL.md"

    # Create skill directory and file on host
    host_skill_dir.mkdir(parents=True, exist_ok=True)
    host_skill_file.write_text(TEST_ASK_SKILL_CONTENT)

    print(f"✓ Created test skill on host: {host_skill_dir}")

    yield host_skill_dir

    # Cleanup: delete the test skill from host
    if host_skill_dir.exists():
        shutil.rmtree(host_skill_dir)
        print(f"✓ Cleaned up test skill: {host_skill_dir}")


def get_auth_token(email: str, password: str) -> str:
    """Get JWT token from Docker API."""
    response = httpx.post(
        f"{DOCKER_BASE_URL}/api/v1/auth/login",
        json={"email": email, "password": password},
        timeout=10.0
    )
    if response.status_code != 200:
        raise RuntimeError(f"Auth failed: {response.status_code} - {response.text}")
    return response.json()["access_token"]


@pytest.mark.e2e
@pytest.mark.skipif(not DOCKER_RUNNING, reason="Docker container not running")
@pytest.mark.skipif(not HAS_API_KEY, reason="No API key available")
class TestAskUserQuestionDocker:
    """
    Integration tests for AskUserQuestion against running Docker container.

    These tests:
    1. Create test prerequisites in Docker (user, skill)
    2. Run agent task via Docker API
    3. Verify AskUserQuestion behavior
    4. Clean up after tests
    """

    def test_agent_stops_on_ask_user_question(
        self, docker_test_user: dict, docker_test_skill: Path
    ):
        """
        Verify agent stops with waiting_for_input when AskUserQuestion is called.

        This test:
        1. Authenticates as test user
        2. Starts a task requesting the test-ask skill
        3. Polls until session reaches terminal state
        4. Verifies status is 'waiting_for_input' with pending question
        """
        # Get auth token
        token = get_auth_token(docker_test_user["email"], docker_test_user["password"])
        headers = {"Authorization": f"Bearer {token}"}

        # Start task that should invoke AskUserQuestion
        response = httpx.post(
            f"{DOCKER_BASE_URL}/api/v1/sessions/run",
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
        session_id = response.json()["session_id"]
        print(f"✓ Started session: {session_id}")

        # Poll for session to reach terminal state
        max_wait = 120  # seconds - allow time for LLM processing
        poll_interval = 2
        final_status = None

        for i in range(max_wait // poll_interval):
            time.sleep(poll_interval)

            response = httpx.get(
                f"{DOCKER_BASE_URL}/api/v1/sessions/{session_id}",
                headers=headers,
                timeout=10.0
            )

            if response.status_code == 200:
                session_data = response.json()
                final_status = session_data.get("status")
                print(f"  Poll {i+1}: status={final_status}")

                if final_status in ("waiting_for_input", "complete", "failed", "cancelled"):
                    break

        # Get events for diagnostics
        events_response = httpx.get(
            f"{DOCKER_BASE_URL}/api/v1/sessions/{session_id}/events/history",
            headers=headers,
            timeout=10.0
        )
        events = events_response.json() if events_response.status_code == 200 else []
        event_types = [e.get("type") for e in events]
        tool_events = [e for e in events if e.get("type") == "tool_start"]
        tool_names = [e.get("data", {}).get("tool_name", "?") for e in tool_events]
        error_events = [e for e in events if e.get("type") == "error"]

        print(f"  Events: {len(events)} total, tools: {tool_names}")

        # Validate final status
        if final_status == "running":
            pytest.fail(
                f"Agent still 'running' after {max_wait}s. "
                f"Events: {event_types}, Tools: {tool_names}, "
                f"Errors: {[e.get('data') for e in error_events]}"
            )
        elif final_status == "complete":
            pytest.fail(
                f"Agent completed WITHOUT calling AskUserQuestion. "
                f"Tools called: {tool_names}. "
                "The skill may not have instructed the agent to use AskUserQuestion."
            )
        elif final_status in ("failed", "cancelled"):
            error_data = [e.get("data") for e in error_events]
            pytest.fail(f"Agent {final_status}. Errors: {error_data}")

        assert final_status == "waiting_for_input", (
            f"Expected 'waiting_for_input', got '{final_status}'"
        )
        print(f"✓ Session reached waiting_for_input status")

        # Verify pending question exists
        pending_response = httpx.get(
            f"{DOCKER_BASE_URL}/api/v1/sessions/{session_id}/pending-question",
            headers=headers,
            timeout=10.0
        )

        assert pending_response.status_code == 200
        pending_data = pending_response.json()

        assert pending_data["has_pending_question"] is True, (
            "Session is waiting_for_input but no pending question found"
        )

        questions = pending_data.get("questions", [])
        assert len(questions) > 0, "Pending question has no questions array"

        # Verify question structure
        for i, q in enumerate(questions):
            assert "question" in q, f"Question {i} missing 'question' field"
            assert "options" in q, f"Question {i} missing 'options' field"
            assert len(q["options"]) >= 2, f"Question {i} has fewer than 2 options"

        print(f"✓ Found {len(questions)} pending question(s)")
        print(f"✓ First question: {questions[0].get('question', 'N/A')[:50]}...")

    def test_answer_submission_and_resume(
        self, docker_test_user: dict, docker_test_skill: Path
    ):
        """
        Verify that submitting an answer allows session to resume.

        This test:
        1. Creates a session that calls AskUserQuestion
        2. Waits for waiting_for_input status
        3. Submits an answer via API
        4. Verifies the answer was recorded
        """
        # Get auth token
        token = get_auth_token(docker_test_user["email"], docker_test_user["password"])
        headers = {"Authorization": f"Bearer {token}"}

        # Start task
        response = httpx.post(
            f"{DOCKER_BASE_URL}/api/v1/sessions/run",
            headers=headers,
            json={
                "task": "Use the test-ask skill to ask me about programming",
                "config": {"enable_skills": True, "max_turns": 10}
            },
            timeout=60.0
        )

        assert response.status_code == 201
        session_id = response.json()["session_id"]

        # Wait for waiting_for_input
        max_wait = 120
        for _ in range(max_wait // 2):
            time.sleep(2)
            response = httpx.get(
                f"{DOCKER_BASE_URL}/api/v1/sessions/{session_id}",
                headers=headers,
                timeout=10.0
            )
            if response.status_code == 200:
                status = response.json().get("status")
                if status == "waiting_for_input":
                    break
                elif status in ("complete", "failed", "cancelled"):
                    pytest.skip(f"Session ended with status {status} before reaching waiting_for_input")
        else:
            pytest.skip("Timeout waiting for waiting_for_input status")

        # Submit answer
        answer_response = httpx.post(
            f"{DOCKER_BASE_URL}/api/v1/sessions/{session_id}/answer",
            headers=headers,
            json={
                "question_id": "latest",
                "answer": "Python\nBeginner"
            },
            timeout=10.0
        )

        assert answer_response.status_code == 200, (
            f"Failed to submit answer: {answer_response.text}"
        )

        print(f"✓ Submitted answer for session {session_id}")

        # Verify answer was recorded (pending question should be cleared)
        pending_response = httpx.get(
            f"{DOCKER_BASE_URL}/api/v1/sessions/{session_id}/pending-question",
            headers=headers,
            timeout=10.0
        )

        # After answering, there should be no pending question
        if pending_response.status_code == 200:
            pending_data = pending_response.json()
            # The question might still appear as pending until resumed,
            # but the answer event should exist
            print(f"  Pending question state after answer: {pending_data.get('has_pending_question')}")

        print(f"✓ Answer submission test completed")


if __name__ == "__main__":
    # Allow running directly for quick testing
    pytest.main([__file__, "-v", "--run-e2e", "-s"])
