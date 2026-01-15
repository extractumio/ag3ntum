#!/usr/bin/env python3
"""
Core integration tests for the Ag3ntum agent.

Tests that the agent can execute a simple task successfully
and produces valid output.yaml and agent.jsonl files.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest
import yaml


# Path configuration
TESTS_DIR: Path = Path(__file__).parent
INPUT_DIR: Path = TESTS_DIR / "input"
AGENT_DIR: Path = TESTS_DIR.parent.parent  # Root directory where src/ is located
SESSIONS_DIR: Path = AGENT_DIR / "sessions"


def _check_api_key_available() -> bool:
    """
    Check if ANTHROPIC_API_KEY is available from any source.
    
    Checks in order:
    1. Environment variable ANTHROPIC_API_KEY
    2. Environment variable CLOUDLINUX_ANTHROPIC_API_KEY
    3. config/secrets.yaml file
    """
    # Check environment variables first
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLOUDLINUX_ANTHROPIC_API_KEY"):
        return True
    
    # Check secrets.yaml (both in Docker /config and local config/)
    secrets_paths = [
        Path("/config/secrets.yaml"),  # Docker mount
        AGENT_DIR / "config" / "secrets.yaml",  # Local development
    ]
    
    for secrets_path in secrets_paths:
        if secrets_path.exists():
            try:
                with open(secrets_path) as f:
                    secrets = yaml.safe_load(f) or {}
                if secrets.get("anthropic_api_key"):
                    return True
            except (yaml.YAMLError, OSError):
                pass
    
    return False


# Check if API key is available for E2E tests
HAS_API_KEY = _check_api_key_available()


@pytest.fixture
def task_file() -> Path:
    """Path to the test task file."""
    return INPUT_DIR / "task.md"


@pytest.fixture
def user_profile() -> Path:
    """Path to the permissive user profile for testing."""
    return INPUT_DIR / "permissions.user.permissive.yaml"


def find_latest_session_dir() -> Optional[Path]:
    """
    Find the most recently created session directory.
    
    Returns:
        Path to the latest session directory, or None if not found.
    """
    if not SESSIONS_DIR.exists():
        return None
    
    session_dirs = [
        d for d in SESSIONS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    ]
    
    if not session_dirs:
        return None
    
    # Sort by directory name (contains timestamp)
    session_dirs.sort(key=lambda d: d.name, reverse=True)
    return session_dirs[0]


class TestAgentCore:
    """Core agent integration tests."""

    @pytest.mark.integration
    @pytest.mark.skipif(not HAS_API_KEY, reason="ANTHROPIC_API_KEY not set - skipping E2E test")
    def test_agent_executes_task_successfully(
        self,
        task_file: Path,
        user_profile: Path,
    ) -> None:
        """
        Test that the agent can execute a task and produce valid output.
        
        This test:
        1. Runs the agent with the test task and permissive profile
        2. Verifies output.yaml contains status=COMPLETE and non-empty output
        3. Verifies agent.jsonl last record has valid subtype, is_error=false, non-empty result
        """
        # Verify test files exist
        assert task_file.exists(), f"Task file not found: {task_file}"
        assert user_profile.exists(), f"User profile not found: {user_profile}"

        # Run the agent as a module (python -m src.core)
        cmd = [
            sys.executable,
            "-m", "src.core",
            "--task-file", str(task_file),
            "--profile", str(user_profile),
            "--timeout", "120",
            "--max-turns", "20",
        ]

        env = os.environ.copy()
        # Ensure we have the API key (check common variable names)
        if "ANTHROPIC_API_KEY" not in env:
            # Check for alternative key names
            alt_key = env.get("CLOUDLINUX_ANTHROPIC_API_KEY")
            if alt_key:
                env["ANTHROPIC_API_KEY"] = alt_key

        result = subprocess.run(
            cmd,
            cwd=str(AGENT_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,  # 3 minute timeout for the test
        )

        # Log output for debugging
        print(f"STDOUT:\n{result.stdout}")
        print(f"STDERR:\n{result.stderr}")

        # Agent should exit with code 0 for COMPLETE status
        assert result.returncode == 0, (
            f"Agent exited with code {result.returncode}\n"
            f"STDOUT: {result.stdout}\n"
            f"STDERR: {result.stderr}"
        )

        # Find the session directory
        session_dir = find_latest_session_dir()
        assert session_dir is not None, "No session directory found"

        # Verify agent.jsonl exists and has valid completion record
        agent_jsonl_path = session_dir / "agent.jsonl"
        assert agent_jsonl_path.exists(), (
            f"agent.jsonl not found in session: {agent_jsonl_path}"
        )

        # Read all lines and get the last record
        with open(agent_jsonl_path, "r") as f:
            lines = [line.strip() for line in f if line.strip()]

        assert len(lines) > 0, "agent.jsonl is empty"

        # Parse the last record
        last_record = json.loads(lines[-1])

        # Verify last record has valid subtype
        assert "subtype" in last_record, (
            f"Last record missing 'subtype' field: {last_record}"
        )
        subtype = last_record.get("subtype")
        # Valid subtypes for final record include "success", "result", etc.
        assert subtype is not None and subtype != "", (
            f"Expected non-empty subtype, got: {subtype!r}"
        )

        # Verify is_error is false
        is_error = last_record.get("is_error")
        assert is_error is False, (
            f"Expected is_error=False, got: {is_error!r}"
        )

        # Verify result is non-empty
        result_field = last_record.get("result")
        assert result_field is not None and result_field != "", (
            f"Expected non-empty result, got: {result_field!r}"
        )

        print(f"\n✓ Session completed successfully: {session_dir.name}")
        print(f"  Subtype: {subtype}")
        print(f"  Result: {result_field[:100]}...")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

