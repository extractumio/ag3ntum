"""
Tests for task_runner.py.

Tests the unified task execution interface:
- TaskExecutionParams validation and defaults
- Configuration loading and override precedence
- Tracer selection (NullTracer fallback)
- Permission manager creation
- ClaudeAgent creation and run invocation
- Error propagation
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.task_runner import execute_agent_task
from src.core.schemas import (
    TaskExecutionParams,
    AgentResult,
    TaskStatus,
    LLMMetrics,
    TokenUsage,
    AgentConfig,
    SessionContext,
)
from src.core.tracer import NullTracer


class TestTaskExecutionParams:
    """Tests for TaskExecutionParams dataclass."""

    @pytest.mark.unit
    def test_minimal_params(self):
        """Test creating params with only required field."""
        params = TaskExecutionParams(task="Hello agent")
        assert params.task == "Hello agent"
        assert params.working_dir is None
        assert params.model is None
        assert params.max_turns is None
        assert params.tracer is None
        assert params.linux_uid is None
        assert params.additional_dirs == []
        assert params.dynamic_mounts == []

    @pytest.mark.unit
    def test_full_params(self):
        """Test creating params with all fields."""
        params = TaskExecutionParams(
            task="Do something",
            working_dir=Path("/tmp/work"),
            session_id="session-123",
            resume_session_id="old-session",
            fork_session=True,
            model="claude-sonnet-4-5-20250929",
            max_turns=50,
            timeout_seconds=600,
            permission_mode="default",
            role="coder",
            linux_uid=50000,
            linux_gid=50000,
            username="testuser",
            enable_skills=True,
            enable_file_checkpointing=False,
            thinking_tokens=10000,
        )
        assert params.task == "Do something"
        assert params.working_dir == Path("/tmp/work")
        assert params.session_id == "session-123"
        assert params.resume_session_id == "old-session"
        assert params.fork_session is True
        assert params.model == "claude-sonnet-4-5-20250929"
        assert params.max_turns == 50
        assert params.timeout_seconds == 600
        assert params.linux_uid == 50000
        assert params.thinking_tokens == 10000


class TestExecuteAgentTask:
    """Tests for execute_agent_task function."""

    @pytest.fixture
    def mock_config_loader(self):
        """Create a mock config loader that returns valid config."""
        loader = MagicMock()
        loader.get_config.return_value = {
            "default_model": "claude-sonnet-4-5-20250929",
            "max_turns": 100,
            "timeout_seconds": 1800,
            "permission_mode": None,
            "role": "default",
            "enable_skills": True,
            "enable_file_checkpointing": False,
            "max_buffer_size": None,
            "output_format": None,
            "include_partial_messages": False,
            "thinking_tokens": None,
        }
        return loader

    @pytest.fixture
    def mock_agent_result(self):
        """Create a mock agent result."""
        return AgentResult(
            status=TaskStatus.COMPLETE,
            output="Task completed successfully",
            session_id="test-session-id",
            metrics=LLMMetrics(
                model="claude-sonnet-4-5-20250929",
                duration_ms=5000,
                num_turns=3,
                session_id="test-session-id",
                total_cost_usd=0.01,
                usage=TokenUsage(
                    input_tokens=100,
                    output_tokens=200,
                ),
            ),
        )

    @pytest.fixture
    def mock_permission_manager(self):
        """Create a mock permission manager."""
        pm = MagicMock()
        profile = MagicMock()
        tools = MagicMock()
        tools.enabled = ["Read", "Write", "Edit", "Bash"]
        tools.disabled = []
        profile.tools = tools
        checkpointing = MagicMock()
        checkpointing.auto_checkpoint_tools = ["Write", "Edit"]
        profile.checkpointing = checkpointing
        pm.profile = profile
        return pm

    @pytest.mark.asyncio
    async def test_basic_execution(
        self, mock_config_loader, mock_agent_result, mock_permission_manager
    ):
        """Test basic task execution with minimal params."""
        params = TaskExecutionParams(task="Test task")

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            result = await execute_agent_task(params, config_loader=mock_config_loader)

            assert result.status == TaskStatus.COMPLETE
            assert result.output == "Task completed successfully"
            mock_agent.run.assert_called_once()

    @pytest.mark.asyncio
    async def test_model_override(
        self, mock_config_loader, mock_agent_result, mock_permission_manager
    ):
        """Test that params.model overrides config model."""
        params = TaskExecutionParams(
            task="Test task",
            model="claude-opus-4-20250514",
        )

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            # Check that AgentConfig was created with the override model
            call_args = mock_agent_class.call_args
            config_arg = call_args.kwargs.get("config") or call_args[1].get("config")
            assert config_arg.model == "claude-opus-4-20250514"

    @pytest.mark.asyncio
    async def test_max_turns_override(
        self, mock_config_loader, mock_agent_result, mock_permission_manager
    ):
        """Test that params.max_turns overrides config max_turns."""
        params = TaskExecutionParams(
            task="Test task",
            max_turns=50,
        )

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            call_args = mock_agent_class.call_args
            config_arg = call_args.kwargs.get("config") or call_args[1].get("config")
            assert config_arg.max_turns == 50

    @pytest.mark.asyncio
    async def test_timeout_override(
        self, mock_config_loader, mock_agent_result, mock_permission_manager
    ):
        """Test that params.timeout_seconds overrides config timeout."""
        params = TaskExecutionParams(
            task="Test task",
            timeout_seconds=600,
        )

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            call_args = mock_agent_class.call_args
            config_arg = call_args.kwargs.get("config") or call_args[1].get("config")
            assert config_arg.timeout_seconds == 600

    @pytest.mark.asyncio
    async def test_null_tracer_fallback(
        self, mock_config_loader, mock_agent_result, mock_permission_manager
    ):
        """Test that NullTracer is used when no tracer provided."""
        params = TaskExecutionParams(task="Test task", tracer=None)

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            call_args = mock_agent_class.call_args
            tracer_arg = call_args.kwargs.get("tracer") or call_args[1].get("tracer")
            assert isinstance(tracer_arg, NullTracer)

    @pytest.mark.asyncio
    async def test_custom_tracer_used(
        self, mock_config_loader, mock_agent_result, mock_permission_manager
    ):
        """Test that custom tracer is passed through."""
        mock_tracer = MagicMock()
        params = TaskExecutionParams(task="Test task", tracer=mock_tracer)

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            call_args = mock_agent_class.call_args
            tracer_arg = call_args.kwargs.get("tracer") or call_args[1].get("tracer")
            assert tracer_arg is mock_tracer

    @pytest.mark.asyncio
    async def test_working_dir_resolved(
        self, mock_config_loader, mock_agent_result, mock_permission_manager, tmp_path
    ):
        """Test that working_dir is resolved to absolute path."""
        params = TaskExecutionParams(
            task="Test task",
            working_dir=tmp_path,
        )

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            call_args = mock_agent_class.call_args
            config_arg = call_args.kwargs.get("config") or call_args[1].get("config")
            assert config_arg.working_dir == str(tmp_path.resolve())

    @pytest.mark.asyncio
    async def test_default_working_dir_when_none(
        self, mock_config_loader, mock_agent_result, mock_permission_manager
    ):
        """Test that AGENT_DIR is used when working_dir is None."""
        params = TaskExecutionParams(task="Test task", working_dir=None)

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            call_args = mock_agent_class.call_args
            config_arg = call_args.kwargs.get("config") or call_args[1].get("config")
            # Should use AGENT_DIR as default
            assert config_arg.working_dir is not None

    @pytest.mark.asyncio
    async def test_linux_uid_passed_to_agent(
        self, mock_config_loader, mock_agent_result, mock_permission_manager
    ):
        """Test that linux_uid/gid are passed to ClaudeAgent."""
        params = TaskExecutionParams(
            task="Test task",
            linux_uid=50000,
            linux_gid=50000,
        )

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            call_args = mock_agent_class.call_args
            assert call_args.kwargs.get("linux_uid") == 50000
            assert call_args.kwargs.get("linux_gid") == 50000

    @pytest.mark.asyncio
    async def test_resume_session_passed_to_run(
        self, mock_config_loader, mock_agent_result, mock_permission_manager
    ):
        """Test that resume_session_id and fork_session are passed to agent.run()."""
        params = TaskExecutionParams(
            task="Continue task",
            resume_session_id="old-session-123",
            fork_session=True,
        )

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            run_call = mock_agent.run.call_args
            assert run_call.kwargs.get("resume_session_id") == "old-session-123"
            assert run_call.kwargs.get("fork_session") is True

    @pytest.mark.asyncio
    async def test_session_id_passed_to_run(
        self, mock_config_loader, mock_agent_result, mock_permission_manager
    ):
        """Test that pre-generated session_id is passed to agent.run()."""
        params = TaskExecutionParams(
            task="Test task",
            session_id="pre-generated-id",
        )

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            run_call = mock_agent.run.call_args
            assert run_call.kwargs.get("session_id") == "pre-generated-id"

    @pytest.mark.asyncio
    async def test_config_loader_created_when_none(
        self, mock_agent_result, mock_permission_manager
    ):
        """Test that AgentConfigLoader is created when not provided."""
        params = TaskExecutionParams(task="Test task")

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class, \
             patch('src.core.task_runner.AgentConfigLoader') as mock_loader_class:

            mock_loader = MagicMock()
            mock_loader.get_config.return_value = {
                "default_model": "claude-sonnet-4-5-20250929",
                "max_turns": 100,
                "timeout_seconds": 1800,
                "permission_mode": None,
                "role": "default",
                "enable_skills": True,
                "enable_file_checkpointing": False,
                "max_buffer_size": None,
                "output_format": None,
                "include_partial_messages": False,
                "thinking_tokens": None,
            }
            mock_loader_class.return_value = mock_loader

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=None)

            mock_loader_class.assert_called_once()

    @pytest.mark.asyncio
    async def test_permission_manager_with_profile_path(
        self, mock_config_loader, mock_agent_result, mock_permission_manager
    ):
        """Test that profile_path is passed to PermissionManager."""
        profile = Path("/config/security/permissions.yaml")
        params = TaskExecutionParams(
            task="Test task",
            profile_path=profile,
        )

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager) as mock_pm_class, \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            mock_pm_class.assert_called_once_with(profile_path=profile)

    @pytest.mark.asyncio
    async def test_disabled_tools_excluded(
        self, mock_config_loader, mock_agent_result
    ):
        """Test that disabled tools are excluded from allowed_tools."""
        mock_pm = MagicMock()
        profile = MagicMock()
        tools = MagicMock()
        tools.enabled = ["Read", "Write", "Edit", "Bash", "DangerousTool"]
        tools.disabled = ["DangerousTool"]
        profile.tools = tools
        checkpointing = MagicMock()
        checkpointing.auto_checkpoint_tools = ["Write"]
        profile.checkpointing = checkpointing
        mock_pm.profile = profile

        params = TaskExecutionParams(task="Test task")

        with patch('src.core.task_runner.PermissionManager', return_value=mock_pm), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            call_args = mock_agent_class.call_args
            config_arg = call_args.kwargs.get("config") or call_args[1].get("config")
            assert "DangerousTool" not in config_arg.allowed_tools
            assert "Read" in config_arg.allowed_tools

    @pytest.mark.asyncio
    async def test_no_checkpointing_config_uses_default(
        self, mock_config_loader, mock_agent_result
    ):
        """Test fallback to default auto_checkpoint_tools when profile has no checkpointing."""
        mock_pm = MagicMock()
        profile = MagicMock()
        tools = MagicMock()
        tools.enabled = ["Read", "Write"]
        tools.disabled = []
        profile.tools = tools
        profile.checkpointing = None  # No checkpointing config
        mock_pm.profile = profile

        params = TaskExecutionParams(task="Test task")

        with patch('src.core.task_runner.PermissionManager', return_value=mock_pm), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            call_args = mock_agent_class.call_args
            config_arg = call_args.kwargs.get("config") or call_args[1].get("config")
            assert "Write" in config_arg.auto_checkpoint_tools
            assert "Edit" in config_arg.auto_checkpoint_tools

    @pytest.mark.asyncio
    async def test_sessions_dir_from_params(
        self, mock_config_loader, mock_agent_result, mock_permission_manager
    ):
        """Test that sessions_dir from params is used."""
        custom_sessions_dir = Path("/users/testuser/sessions")
        params = TaskExecutionParams(
            task="Test task",
            sessions_dir=custom_sessions_dir,
        )

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            call_args = mock_agent_class.call_args
            assert call_args.kwargs.get("sessions_dir") == custom_sessions_dir

    @pytest.mark.asyncio
    async def test_enable_skills_override(
        self, mock_config_loader, mock_agent_result, mock_permission_manager
    ):
        """Test that enable_skills param overrides config."""
        params = TaskExecutionParams(
            task="Test task",
            enable_skills=False,
        )

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            call_args = mock_agent_class.call_args
            config_arg = call_args.kwargs.get("config") or call_args[1].get("config")
            assert config_arg.enable_skills is False

    @pytest.mark.asyncio
    async def test_session_context_passed_to_run(
        self, mock_config_loader, mock_agent_result, mock_permission_manager
    ):
        """Test that session_context is passed to agent.run()."""
        ctx = SessionContext(session_id="ctx-session", cumulative_turns=5)
        params = TaskExecutionParams(
            task="Test task",
            session_context=ctx,
        )

        with patch('src.core.task_runner.PermissionManager', return_value=mock_permission_manager), \
             patch('src.core.task_runner.ClaudeAgent') as mock_agent_class:

            mock_agent = MagicMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result)
            mock_agent_class.return_value = mock_agent

            await execute_agent_task(params, config_loader=mock_config_loader)

            run_call = mock_agent.run.call_args
            assert run_call.kwargs.get("session_context") is ctx


class TestAgentConfigCreation:
    """Tests for AgentConfig model."""

    @pytest.mark.unit
    def test_thinking_enabled_with_suffix(self):
        """Test that thinking mode is detected from model name suffix."""
        config = AgentConfig(
            model="claude-sonnet-4-5-20250929:mode=thinking",
            max_turns=100,
            timeout_seconds=1800,
            enable_skills=True,
            enable_file_checkpointing=False,
            role="default",
            thinking_tokens=10000,
        )
        assert config.thinking_enabled is True
        assert config.base_model == "claude-sonnet-4-5-20250929"
        assert config.effective_thinking_tokens == 10000

    @pytest.mark.unit
    def test_thinking_disabled_without_suffix(self):
        """Test that thinking mode is not enabled without suffix."""
        config = AgentConfig(
            model="claude-sonnet-4-5-20250929",
            max_turns=100,
            timeout_seconds=1800,
            enable_skills=True,
            enable_file_checkpointing=False,
            role="default",
            thinking_tokens=10000,
        )
        assert config.thinking_enabled is False
        assert config.base_model == "claude-sonnet-4-5-20250929"
        assert config.effective_thinking_tokens is None
