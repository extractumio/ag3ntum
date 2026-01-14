"""
Security test to verify permission_mode bypass is prevented.
"""
import pytest
from pathlib import Path
from src.core.agent_core import ClaudeAgent
from src.core.schemas import AgentConfig
from src.core.permission_profiles import PermissionManager
from src.core.exceptions import AgentError


def test_permission_mode_is_rejected():
    """
    Test that setting permission_mode raises an AgentError.
    This prevents the security bypass where permission_mode causes
    the SDK to use --permission-prompt-tool stdio which bypasses
    all permission checks.
    """
    # Create config with permission_mode set
    config = AgentConfig(
        model="claude-haiku-4-5-20251001",
        max_turns=10,
        permission_mode="default",  # This should be rejected
    )

    # Create permission manager
    permission_manager = PermissionManager(
        profile_path=Path("config/permissions.yaml")
    )

    # Attempting to create ClaudeAgent with permission_mode should raise error
    with pytest.raises(AgentError) as exc_info:
        ClaudeAgent(
            config=config,
            permission_manager=permission_manager,
            tracer=False,
        )

    assert "permission_mode must be null" in str(exc_info.value).lower()


def test_permission_mode_none_is_accepted():
    """
    Test that NOT setting permission_mode (None) is accepted.
    This is the correct configuration for security.
    """
    # Create config without permission_mode
    config = AgentConfig(
        model="claude-haiku-4-5-20251001",
        max_turns=10,
        permission_mode=None,  # This is correct
    )

    # Create permission manager
    permission_manager = PermissionManager(
        profile_path=Path("config/permissions.yaml")
    )

    # This should succeed
    agent = ClaudeAgent(
        config=config,
        permission_manager=permission_manager,
        tracer=False,
    )

    assert agent is not None
    assert agent._config.permission_mode is None


def test_permission_mode_empty_string_is_accepted():
    """
    Test that empty string for permission_mode is accepted.
    """
    # Create config with empty permission_mode
    config = AgentConfig(
        model="claude-haiku-4-5-20251001",
        max_turns=10,
        permission_mode="",  # Empty string should be treated as None
    )

    # Create permission manager
    permission_manager = PermissionManager(
        profile_path=Path("config/permissions.yaml")
    )

    # This should succeed (empty string is acceptable)
    agent = ClaudeAgent(
        config=config,
        permission_manager=permission_manager,
        tracer=False,
    )

    assert agent is not None
