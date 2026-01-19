"""
Security Tests for Sandboxed Environment Variables.

Tests cover:
- User isolation: User A's sandboxed_envs must not leak to User B
- Session isolation: Fresh SandboxEnvConfig created per session (no shared state)
- Sandbox requirement: Skills MUST fail without SandboxExecutor (no fallback)
- Environment variable injection: bwrap command includes --setenv for custom_env
- Configuration loading: Global + user-specific secrets.yaml merging
- Environment name validation: Invalid env names are rejected

Run with:
    pytest tests/security/test_sandboxed_envs.py -v

Or via deploy.sh:
    ./deploy.sh test
"""
import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest
import yaml

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.sandbox import (
    SandboxConfig,
    SandboxMount,
    SandboxExecutor,
    SandboxEnvConfig,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def basic_sandbox_config(workspace: Path) -> SandboxConfig:
    """Create a basic sandbox config with real paths."""
    return SandboxConfig(
        enabled=True,
        file_sandboxing=True,
        session_mounts={
            "workspace": SandboxMount(
                source=str(workspace),
                target="/workspace",
                mode="rw",
            ),
        },
    )


@pytest.fixture
def sandbox_executor(basic_sandbox_config: SandboxConfig) -> SandboxExecutor:
    """Create a SandboxExecutor with basic config."""
    return SandboxExecutor(basic_sandbox_config)


# =============================================================================
# Test: SandboxEnvConfig.custom_env Field
# =============================================================================

class TestSandboxEnvConfigCustomEnv:
    """Test the custom_env field in SandboxEnvConfig."""

    def test_custom_env_default_empty(self) -> None:
        """custom_env defaults to empty dict."""
        config = SandboxEnvConfig()
        assert config.custom_env == {}

    def test_custom_env_can_be_set(self) -> None:
        """custom_env can be set with environment variables."""
        config = SandboxEnvConfig(
            custom_env={
                "OPENAI_API_KEY": "sk-test-key",
                "GEMINI_API_KEY": "AIzaSy-test",
            }
        )
        assert config.custom_env["OPENAI_API_KEY"] == "sk-test-key"
        assert config.custom_env["GEMINI_API_KEY"] == "AIzaSy-test"

    def test_custom_env_preserves_values(self) -> None:
        """custom_env preserves all key-value pairs."""
        envs = {
            "VAR1": "value1",
            "VAR2": "value2",
            "VAR3": "value3",
        }
        config = SandboxEnvConfig(custom_env=envs)
        assert len(config.custom_env) == 3
        for key, value in envs.items():
            assert config.custom_env[key] == value


# =============================================================================
# Test: Session Isolation via SandboxConfig.resolve()
# =============================================================================

class TestSessionIsolation:
    """Test that SandboxConfig.resolve() creates fresh environment per session.

    SECURITY CRITICAL: This prevents User A's API keys from leaking to User B.
    """

    def test_resolve_creates_fresh_environment(self) -> None:
        """resolve() creates a new SandboxEnvConfig instance."""
        base_config = SandboxConfig()

        resolved1 = base_config.resolve({})
        resolved2 = base_config.resolve({})

        # Must be different objects
        assert resolved1.environment is not resolved2.environment
        assert id(resolved1.environment) != id(resolved2.environment)

    def test_resolve_starts_with_empty_custom_env(self) -> None:
        """resolve() returns config with empty custom_env."""
        base_config = SandboxConfig()
        # Even if base has custom_env set
        base_config.environment.custom_env = {"LEAKED_KEY": "should_not_appear"}

        resolved = base_config.resolve({})

        # Resolved config must have empty custom_env
        assert resolved.environment.custom_env == {}
        assert "LEAKED_KEY" not in resolved.environment.custom_env

    def test_modifying_resolved_does_not_affect_base(self) -> None:
        """Modifying resolved config doesn't affect base config."""
        base_config = SandboxConfig()

        resolved = base_config.resolve({})
        resolved.environment.custom_env["USER_A_KEY"] = "user_a_secret"

        # Base config must be unaffected
        assert "USER_A_KEY" not in base_config.environment.custom_env

    def test_multiple_sessions_isolated(self) -> None:
        """Multiple resolved configs are completely isolated from each other."""
        base_config = SandboxConfig()

        # Simulate User A session
        session_a = base_config.resolve({})
        session_a.environment.custom_env["USER_A_KEY"] = "user_a_secret"
        session_a.environment.custom_env["SHARED_KEY"] = "user_a_value"

        # Simulate User B session
        session_b = base_config.resolve({})
        session_b.environment.custom_env["USER_B_KEY"] = "user_b_secret"
        session_b.environment.custom_env["SHARED_KEY"] = "user_b_value"

        # User A's secrets must not appear in User B's session
        assert "USER_A_KEY" not in session_b.environment.custom_env
        assert session_b.environment.custom_env.get("SHARED_KEY") == "user_b_value"

        # User B's secrets must not appear in User A's session
        assert "USER_B_KEY" not in session_a.environment.custom_env
        assert session_a.environment.custom_env.get("SHARED_KEY") == "user_a_value"

    def test_resolve_preserves_other_env_settings(self) -> None:
        """resolve() preserves home, path, and clear_env settings."""
        base_config = SandboxConfig(
            environment=SandboxEnvConfig(
                home="/custom/home",
                path="/custom/bin:/usr/bin",
                clear_env=True,
            )
        )

        resolved = base_config.resolve({})

        assert resolved.environment.home == "/custom/home"
        assert resolved.environment.path == "/custom/bin:/usr/bin"
        assert resolved.environment.clear_env is True


# =============================================================================
# Test: Environment Variable Injection in bwrap Command
# =============================================================================

class TestBwrapEnvInjection:
    """Test that build_bwrap_command() correctly injects custom_env via --setenv."""

    def test_custom_env_added_as_setenv(self, workspace: Path) -> None:
        """custom_env variables are added as --setenv arguments."""
        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        config.environment.custom_env = {
            "API_KEY": "test-api-key-123",
            "DATABASE_URL": "postgres://localhost/db",
        }

        executor = SandboxExecutor(config)
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)
        cmd_str = " ".join(cmd)

        # Must contain --setenv for custom env vars
        assert "--setenv API_KEY test-api-key-123" in cmd_str
        assert "--setenv DATABASE_URL postgres://localhost/db" in cmd_str

    def test_empty_custom_env_no_setenv(self, workspace: Path) -> None:
        """Empty custom_env adds no extra --setenv arguments."""
        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        config.environment.custom_env = {}

        executor = SandboxExecutor(config)
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)

        # Count --setenv occurrences (should only be HOME and PATH)
        setenv_count = cmd.count("--setenv")
        assert setenv_count == 2  # Only HOME and PATH

    def test_invalid_env_name_skipped(self, workspace: Path) -> None:
        """Environment variable names that aren't valid identifiers are skipped."""
        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        config.environment.custom_env = {
            "VALID_KEY": "valid_value",
            "": "empty_name",  # Invalid: empty
            "123_INVALID": "starts_with_number",  # Invalid: starts with number
            "VALID-KEY": "invalid_chars",  # Invalid: contains dash
        }

        executor = SandboxExecutor(config)
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)
        cmd_str = " ".join(cmd)

        # Valid key should be present
        assert "--setenv VALID_KEY valid_value" in cmd_str
        # Invalid keys should be skipped
        assert "empty_name" not in cmd_str
        assert "starts_with_number" not in cmd_str

    def test_none_value_skipped(self, workspace: Path) -> None:
        """Environment variables with None values are skipped."""
        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        config.environment.custom_env = {
            "VALID_KEY": "valid_value",
            "NONE_KEY": None,  # type: ignore
        }

        executor = SandboxExecutor(config)
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)
        cmd_str = " ".join(cmd)

        assert "--setenv VALID_KEY valid_value" in cmd_str
        assert "NONE_KEY" not in cmd_str


# =============================================================================
# Test: load_sandboxed_envs() Configuration Loading
# =============================================================================

class TestLoadSandboxedEnvs:
    """Test load_sandboxed_envs() function for global + user merge."""

    def test_global_envs_loaded(self, tmp_path: Path) -> None:
        """Global sandboxed_envs are loaded from config loader."""
        from src.config import load_sandboxed_envs, AgentConfigLoader

        # Mock the config loader
        mock_loader = MagicMock(spec=AgentConfigLoader)
        mock_loader.get_sandboxed_envs.return_value = {
            "GLOBAL_KEY": "global_value",
            "SHARED_KEY": "global_shared",
        }

        result = load_sandboxed_envs(username=None, config_loader=mock_loader)

        assert result["GLOBAL_KEY"] == "global_value"
        assert result["SHARED_KEY"] == "global_shared"

    def test_user_envs_override_global(self, tmp_path: Path) -> None:
        """User-specific sandboxed_envs override global values."""
        from src.config import load_sandboxed_envs, AgentConfigLoader, USERS_DIR

        # Mock the config loader
        mock_loader = MagicMock(spec=AgentConfigLoader)
        mock_loader.get_sandboxed_envs.return_value = {
            "GLOBAL_KEY": "global_value",
            "SHARED_KEY": "global_shared",
        }

        # Create user secrets file
        user_secrets = {
            "sandboxed_envs": {
                "USER_KEY": "user_value",
                "SHARED_KEY": "user_override",  # Overrides global
            }
        }

        # Mock USERS_DIR and file operations
        with patch.object(Path, 'exists', return_value=True):
            with patch('builtins.open', mock_open(read_data=yaml.dump(user_secrets))):
                with patch('src.config.USERS_DIR', tmp_path):
                    # Create the user directory structure
                    user_dir = tmp_path / "testuser" / "ag3ntum"
                    user_dir.mkdir(parents=True)
                    secrets_file = user_dir / "secrets.yaml"
                    secrets_file.write_text(yaml.dump(user_secrets))

                    result = load_sandboxed_envs(username="testuser", config_loader=mock_loader)

        # Global key preserved
        assert result["GLOBAL_KEY"] == "global_value"
        # User key added
        assert result["USER_KEY"] == "user_value"
        # User overrides global for shared key
        assert result["SHARED_KEY"] == "user_override"

    def test_nonexistent_user_returns_global_only(self, tmp_path: Path) -> None:
        """Non-existent user secrets file returns only global envs."""
        from src.config import load_sandboxed_envs, AgentConfigLoader

        mock_loader = MagicMock(spec=AgentConfigLoader)
        mock_loader.get_sandboxed_envs.return_value = {
            "GLOBAL_KEY": "global_value",
        }

        with patch('src.config.USERS_DIR', tmp_path):
            result = load_sandboxed_envs(username="nonexistent_user", config_loader=mock_loader)

        assert result == {"GLOBAL_KEY": "global_value"}

    def test_empty_username_returns_global_only(self) -> None:
        """Empty or None username returns only global envs."""
        from src.config import load_sandboxed_envs, AgentConfigLoader

        mock_loader = MagicMock(spec=AgentConfigLoader)
        mock_loader.get_sandboxed_envs.return_value = {
            "GLOBAL_KEY": "global_value",
        }

        result = load_sandboxed_envs(username=None, config_loader=mock_loader)
        assert result == {"GLOBAL_KEY": "global_value"}

        result = load_sandboxed_envs(username="", config_loader=mock_loader)
        assert result == {"GLOBAL_KEY": "global_value"}

    def test_malformed_user_yaml_returns_global(self, tmp_path: Path) -> None:
        """Malformed user YAML returns global envs without crashing."""
        from src.config import load_sandboxed_envs, AgentConfigLoader

        mock_loader = MagicMock(spec=AgentConfigLoader)
        mock_loader.get_sandboxed_envs.return_value = {
            "GLOBAL_KEY": "global_value",
        }

        # Create malformed user secrets file
        with patch('src.config.USERS_DIR', tmp_path):
            user_dir = tmp_path / "testuser" / "ag3ntum"
            user_dir.mkdir(parents=True)
            secrets_file = user_dir / "secrets.yaml"
            secrets_file.write_text("not: valid: yaml: syntax: [")

            result = load_sandboxed_envs(username="testuser", config_loader=mock_loader)

        # Should return global envs, not crash
        assert result == {"GLOBAL_KEY": "global_value"}


# =============================================================================
# Test: SkillToolsManager Sandbox Requirement (No Fallback)
# =============================================================================

class TestSkillToolsSandboxRequirement:
    """Test that SkillToolsManager REQUIRES SandboxExecutor - no fallback.

    SECURITY CRITICAL: Skills must fail with explicit error if sandbox not available.
    """

    def test_skill_execution_fails_without_sandbox(self, tmp_path: Path) -> None:
        """Skill execution fails with explicit error when sandbox not configured."""
        from src.core.skill_tools import execute_skill_sync, SkillExecutionResult
        from src.core.skills import Skill

        # Create a real skill file for testing
        script_file = tmp_path / "test_script.py"
        script_file.write_text("print('hello')")

        # Create a mock skill with a real script file
        mock_skill = MagicMock(spec=Skill)
        mock_skill.name = "test-skill"
        mock_skill.script_file = script_file

        # Execute WITHOUT sandbox_executor
        result = execute_skill_sync(
            skill=mock_skill,
            args=None,
            input_data=None,
            timeout=10,
            sandbox_executor=None,  # No sandbox!
        )

        # Must fail with security error
        assert result.success is False
        assert result.exit_code == 1
        assert "SECURITY ERROR" in result.error
        assert "SandboxExecutor is not configured" in result.error
        assert "MUST run inside the Bubblewrap sandbox" in result.error

    def test_skill_handler_has_no_sandbox_reference(self) -> None:
        """Skill handler created without sandbox has None sandbox_executor."""
        from src.core.skill_tools import SkillToolsManager, SkillToolDefinition
        from src.core.skills import Skill

        # Create manager without sandbox
        manager = SkillToolsManager(
            skills_dir=None,
            workspace_dir=None,
            sandbox_executor=None,  # No sandbox!
        )

        # Verify manager has no sandbox
        assert manager._sandbox_executor is None

        # Create a mock skill
        mock_skill = MagicMock(spec=Skill)
        mock_skill.name = "test-skill"
        mock_skill.description = "Test skill"

        # Create the tool definition manually
        definition = SkillToolDefinition(
            name="test-skill",
            description="Test skill",
            skill=mock_skill,
            script_path=Path("/fake/script.py"),
        )

        # The handler is created but sandbox_executor is None
        # When executed, it should fail with security error
        # (actual execution tested in execute_skill_sync test above)
        handler = manager._create_tool_handler(definition)

        # Handler is created (it's a decorator wrapped function)
        # The actual security check happens at runtime
        assert handler is not None

    def test_skill_manager_creation_without_sandbox_succeeds(self) -> None:
        """SkillToolsManager can be created without sandbox (fails at execution time)."""
        from src.core.skill_tools import SkillToolsManager

        # Creating manager without sandbox should not raise
        manager = SkillToolsManager(
            skills_dir=None,
            workspace_dir=None,
            sandbox_executor=None,
        )

        # Manager exists but has no sandbox
        assert manager._sandbox_executor is None

    def test_skill_missing_script_returns_error(self, tmp_path: Path) -> None:
        """Skill with missing script file returns error (after sandbox check)."""
        from src.core.skill_tools import execute_skill_sync
        from src.core.skills import Skill

        # Create a mock skill without script file
        mock_skill = MagicMock(spec=Skill)
        mock_skill.name = "test-skill"
        mock_skill.script_file = None

        # Create a mock sandbox executor
        mock_executor = MagicMock()

        # Execute with sandbox but no script
        result = execute_skill_sync(
            skill=mock_skill,
            args=None,
            input_data=None,
            timeout=10,
            sandbox_executor=mock_executor,
        )

        # Should fail because no script file
        assert result.success is False
        assert "no script file" in result.error.lower()


# =============================================================================
# Test: User Isolation Security
# =============================================================================

class TestUserIsolationSecurity:
    """Test that user A's secrets cannot leak to user B.

    SECURITY CRITICAL: API keys must be isolated per user.
    """

    def test_different_users_get_different_envs(self, tmp_path: Path) -> None:
        """Different users get different environment variables."""
        from src.config import load_sandboxed_envs, AgentConfigLoader

        mock_loader = MagicMock(spec=AgentConfigLoader)
        mock_loader.get_sandboxed_envs.return_value = {
            "SHARED_API_KEY": "global_key",
        }

        with patch('src.config.USERS_DIR', tmp_path):
            # Create user A's secrets
            user_a_dir = tmp_path / "user_a" / "ag3ntum"
            user_a_dir.mkdir(parents=True)
            (user_a_dir / "secrets.yaml").write_text(yaml.dump({
                "sandboxed_envs": {
                    "SHARED_API_KEY": "user_a_secret_key",
                    "USER_A_ONLY": "user_a_private",
                }
            }))

            # Create user B's secrets
            user_b_dir = tmp_path / "user_b" / "ag3ntum"
            user_b_dir.mkdir(parents=True)
            (user_b_dir / "secrets.yaml").write_text(yaml.dump({
                "sandboxed_envs": {
                    "SHARED_API_KEY": "user_b_secret_key",
                    "USER_B_ONLY": "user_b_private",
                }
            }))

            # Load user A's envs
            user_a_envs = load_sandboxed_envs(username="user_a", config_loader=mock_loader)

            # Load user B's envs
            user_b_envs = load_sandboxed_envs(username="user_b", config_loader=mock_loader)

        # User A should have their own values
        assert user_a_envs["SHARED_API_KEY"] == "user_a_secret_key"
        assert user_a_envs["USER_A_ONLY"] == "user_a_private"
        assert "USER_B_ONLY" not in user_a_envs

        # User B should have their own values
        assert user_b_envs["SHARED_API_KEY"] == "user_b_secret_key"
        assert user_b_envs["USER_B_ONLY"] == "user_b_private"
        assert "USER_A_ONLY" not in user_b_envs

    def test_sandbox_config_not_shared_between_users(self) -> None:
        """SandboxConfig instances are not shared between user sessions."""
        base_config = SandboxConfig()

        # Simulate resolving for user A
        user_a_config = base_config.resolve({})
        user_a_config.environment.custom_env["API_KEY"] = "user_a_secret"

        # Simulate resolving for user B
        user_b_config = base_config.resolve({})
        user_b_config.environment.custom_env["API_KEY"] = "user_b_secret"

        # Configs must be independent
        assert user_a_config.environment.custom_env["API_KEY"] == "user_a_secret"
        assert user_b_config.environment.custom_env["API_KEY"] == "user_b_secret"

        # Modifying one doesn't affect the other
        user_a_config.environment.custom_env["NEW_KEY"] = "user_a_new"
        assert "NEW_KEY" not in user_b_config.environment.custom_env

    def test_sequential_sessions_isolated(self) -> None:
        """Sequential sessions for different users are isolated."""
        base_config = SandboxConfig()

        # Session 1: User A
        session1 = base_config.resolve({})
        session1.environment.custom_env = {
            "SECRET_KEY": "user_a_secret_12345",
            "OPENAI_API_KEY": "sk-user-a-key",
        }

        # Session 2: User B (after User A's session)
        session2 = base_config.resolve({})

        # User B's session must NOT have User A's keys
        assert session2.environment.custom_env == {}
        assert "SECRET_KEY" not in session2.environment.custom_env
        assert "OPENAI_API_KEY" not in session2.environment.custom_env


# =============================================================================
# Test: clearenv Security
# =============================================================================

class TestClearEnvSecurity:
    """Test that --clearenv is used to prevent host environment leakage."""

    def test_clearenv_included_when_enabled(self, workspace: Path) -> None:
        """--clearenv is included in bwrap command when configured."""
        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        config.environment.clear_env = True

        executor = SandboxExecutor(config)
        cmd = executor.build_bwrap_command(["echo", "hello"], allow_network=False)

        assert "--clearenv" in cmd

    def test_clearenv_enabled_by_default(self, workspace: Path) -> None:
        """--clearenv is enabled by default in SandboxEnvConfig."""
        config = SandboxEnvConfig()
        assert config.clear_env is True

    def test_only_explicit_envs_after_clearenv(self, workspace: Path) -> None:
        """After --clearenv, only explicitly set env vars are present."""
        config = SandboxConfig(
            session_mounts={
                "workspace": SandboxMount(
                    source=str(workspace),
                    target="/workspace",
                    mode="rw",
                ),
            },
        )
        config.environment.clear_env = True
        config.environment.custom_env = {
            "ONLY_THIS_KEY": "should_be_present",
        }

        executor = SandboxExecutor(config)
        cmd = executor.build_bwrap_command(["env"], allow_network=False)

        # Find --clearenv position
        clearenv_idx = cmd.index("--clearenv")

        # After --clearenv, we should see --setenv commands
        post_clearenv = cmd[clearenv_idx:]

        # Should have HOME, PATH, and ONLY_THIS_KEY set
        setenv_vars = []
        for i, arg in enumerate(post_clearenv):
            if arg == "--setenv" and i + 2 < len(post_clearenv):
                setenv_vars.append(post_clearenv[i + 1])

        assert "HOME" in setenv_vars
        assert "PATH" in setenv_vars
        assert "ONLY_THIS_KEY" in setenv_vars


# =============================================================================
# Test: AgentConfigLoader.get_sandboxed_envs()
# =============================================================================

class TestAgentConfigLoaderSandboxedEnvs:
    """Test AgentConfigLoader.get_sandboxed_envs() method."""

    def test_get_sandboxed_envs_from_secrets(self, tmp_path: Path) -> None:
        """get_sandboxed_envs() returns envs from secrets.yaml."""
        from src.config import AgentConfigLoader

        # Create mock config file
        config_file = tmp_path / "agent.yaml"
        config_file.write_text(yaml.dump({
            "agent": {
                "model": "claude-sonnet-4-5-20250929",
                "max_turns": 50,
                "timeout_seconds": 300,
                "enable_skills": True,
                "enable_file_checkpointing": False,
                "role": "assistant",
            }
        }))

        # Create mock secrets file with sandboxed_envs
        secrets_file = tmp_path / "secrets.yaml"
        secrets_file.write_text(yaml.dump({
            "anthropic_api_key": "sk-ant-test-key-12345678901234567890123456789012345678901234567890",
            "sandboxed_envs": {
                "GEMINI_API_KEY": "AIzaSy-test-key",
                "OPENAI_API_KEY": "sk-test-key",
            }
        }))

        loader = AgentConfigLoader(
            config_path=config_file,
            secrets_path=secrets_file,
        )

        envs = loader.get_sandboxed_envs()

        assert envs["GEMINI_API_KEY"] == "AIzaSy-test-key"
        assert envs["OPENAI_API_KEY"] == "sk-test-key"

    def test_get_sandboxed_envs_empty_if_missing(self, tmp_path: Path) -> None:
        """get_sandboxed_envs() returns empty dict if section missing."""
        from src.config import AgentConfigLoader

        config_file = tmp_path / "agent.yaml"
        config_file.write_text(yaml.dump({
            "agent": {
                "model": "claude-sonnet-4-5-20250929",
                "max_turns": 50,
                "timeout_seconds": 300,
                "enable_skills": True,
                "enable_file_checkpointing": False,
                "role": "assistant",
            }
        }))

        # Secrets file without sandboxed_envs section
        secrets_file = tmp_path / "secrets.yaml"
        secrets_file.write_text(yaml.dump({
            "anthropic_api_key": "sk-ant-test-key-12345678901234567890123456789012345678901234567890",
        }))

        loader = AgentConfigLoader(
            config_path=config_file,
            secrets_path=secrets_file,
        )

        envs = loader.get_sandboxed_envs()

        assert envs == {}

    def test_get_sandboxed_envs_converts_to_string(self, tmp_path: Path) -> None:
        """get_sandboxed_envs() converts all values to strings."""
        from src.config import AgentConfigLoader

        config_file = tmp_path / "agent.yaml"
        config_file.write_text(yaml.dump({
            "agent": {
                "model": "claude-sonnet-4-5-20250929",
                "max_turns": 50,
                "timeout_seconds": 300,
                "enable_skills": True,
                "enable_file_checkpointing": False,
                "role": "assistant",
            }
        }))

        secrets_file = tmp_path / "secrets.yaml"
        secrets_file.write_text(yaml.dump({
            "anthropic_api_key": "sk-ant-test-key-12345678901234567890123456789012345678901234567890",
            "sandboxed_envs": {
                "STRING_VAR": "string_value",
                "INT_VAR": 12345,
                "BOOL_VAR": True,
            }
        }))

        loader = AgentConfigLoader(
            config_path=config_file,
            secrets_path=secrets_file,
        )

        envs = loader.get_sandboxed_envs()

        # All values must be strings
        assert isinstance(envs["STRING_VAR"], str)
        assert isinstance(envs["INT_VAR"], str)
        assert isinstance(envs["BOOL_VAR"], str)
        assert envs["INT_VAR"] == "12345"
        assert envs["BOOL_VAR"] == "True"
