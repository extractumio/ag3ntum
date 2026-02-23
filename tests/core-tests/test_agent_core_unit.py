"""
Unit tests for agent_core.py.

Tests core agent logic WITHOUT requiring an API key or running containers.
Uses mocks extensively to isolate each unit under test.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers to build minimal valid AgentConfig objects
# ---------------------------------------------------------------------------

def _make_agent_config(**overrides):
    """
    Build an AgentConfig with all required fields.

    AgentConfig is a Pydantic BaseModel; all ``...`` fields must be supplied.
    """
    from src.core.schemas import AgentConfig

    defaults = {
        "model": "claude-sonnet-4-20250514",
        "max_turns": 10,
        "timeout_seconds": 600,
        "enable_skills": False,
        "enable_file_checkpointing": False,
        "permission_mode": None,
        "role": "default",
        "allowed_tools": [],
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


# ---------------------------------------------------------------------------
# Common mock setup for ClaudeAgent construction
# ---------------------------------------------------------------------------

def _build_agent(config=None, tracer=False, permission_manager=None,
                 sessions_dir=None, logs_dir=None, **kwargs):
    """
    Construct a ClaudeAgent with all heavy dependencies mocked.

    Returns (agent, mocks_dict) so callers can inspect mocks.
    """
    from src.core.agent_core import ClaudeAgent

    if config is None:
        config = _make_agent_config()

    import tempfile

    if sessions_dir is None:
        sessions_dir = Path(tempfile.mkdtemp(prefix="ag3ntum-test-sessions-"))
    if logs_dir is None:
        logs_dir = Path(tempfile.mkdtemp(prefix="ag3ntum-test-logs-"))

    with patch("src.core.agent_core.SessionManager") as mock_sm, \
         patch("src.core.agent_core.SkillManager") as mock_skm:
        # SkillManager and SessionManager are constructed in __init__
        mock_sm_instance = MagicMock()
        mock_sm.return_value = mock_sm_instance
        mock_skm_instance = MagicMock()
        mock_skm.return_value = mock_skm_instance

        agent = ClaudeAgent(
            config=config,
            sessions_dir=sessions_dir,
            logs_dir=logs_dir,
            tracer=tracer,
            permission_manager=permission_manager,
            **kwargs,
        )

    mocks = {
        "session_manager_cls": mock_sm,
        "session_manager": mock_sm_instance,
        "skill_manager_cls": mock_skm,
        "skill_manager": mock_skm_instance,
    }
    return agent, mocks


# ===================================================================
# TestPermissionModeValidation (P0 — SECURITY)
# ===================================================================
class TestPermissionModeValidation:
    """
    permission_mode MUST be None or empty.

    Any non-null value causes the SDK to use --permission-prompt-tool stdio,
    which bypasses can_use_tool and all permission checks.
    """

    def test_none_permission_mode_succeeds(self):
        """permission_mode=None (default) should construct without error."""
        agent, _ = _build_agent(config=_make_agent_config(permission_mode=None))
        assert agent is not None

    def test_empty_string_permission_mode_succeeds(self):
        """permission_mode='' is treated as null and should succeed."""
        agent, _ = _build_agent(config=_make_agent_config(permission_mode=""))
        assert agent is not None

    def test_plan_permission_mode_raises(self):
        """permission_mode='plan' MUST raise AgentError."""
        from src.core.exceptions import AgentError

        with pytest.raises(AgentError, match="permission_mode must be null"):
            _build_agent(config=_make_agent_config(permission_mode="plan"))

    def test_full_permission_mode_raises(self):
        """permission_mode='full' MUST raise AgentError."""
        from src.core.exceptions import AgentError

        with pytest.raises(AgentError, match="permission_mode must be null"):
            _build_agent(config=_make_agent_config(permission_mode="full"))

    def test_default_permission_mode_raises(self):
        """permission_mode='default' MUST raise AgentError."""
        from src.core.exceptions import AgentError

        with pytest.raises(AgentError, match="permission_mode must be null"):
            _build_agent(config=_make_agent_config(permission_mode="default"))

    def test_accept_edits_permission_mode_raises(self):
        """permission_mode='acceptEdits' MUST raise AgentError."""
        from src.core.exceptions import AgentError

        with pytest.raises(AgentError, match="permission_mode must be null"):
            _build_agent(config=_make_agent_config(permission_mode="acceptEdits"))

    def test_bypass_permissions_mode_raises(self):
        """permission_mode='bypassPermissions' MUST raise AgentError."""
        from src.core.exceptions import AgentError

        with pytest.raises(AgentError, match="permission_mode must be null"):
            _build_agent(
                config=_make_agent_config(permission_mode="bypassPermissions")
            )

    def test_null_string_permission_mode_succeeds(self):
        """permission_mode='null' (string literal) is allowed by the guard."""
        agent, _ = _build_agent(config=_make_agent_config(permission_mode="null"))
        assert agent is not None


# ===================================================================
# TestProxyBaseURL
# ===================================================================
class TestProxyBaseURL:
    """Tests for _get_proxy_base_url_for_model()."""

    def test_model_in_proxy_config_returns_url(self):
        """Model found in proxy config should return proxy URL."""
        from src.core.agent_core import _get_proxy_base_url_for_model

        mock_config = MagicMock()
        mock_mapping = MagicMock()
        mock_mapping.provider = "openrouter"
        mock_config.models = {"openrouter:openai/gpt-5.2": mock_mapping}

        mock_provider = MagicMock()
        mock_provider.type = "openai"
        mock_config.providers = {"openrouter": mock_provider}

        with patch(
            "src.core.agent_core.load_llm_proxy_config",
            return_value=mock_config,
        ):
            result = _get_proxy_base_url_for_model("openrouter:openai/gpt-5.2")

        assert result is not None
        assert "127.0.0.1" in result
        assert "/api/llm-proxy" in result

    def test_model_in_proxy_config_with_session_id(self):
        """When session_id is provided, it should be embedded in the URL."""
        from src.core.agent_core import _get_proxy_base_url_for_model

        mock_config = MagicMock()
        mock_mapping = MagicMock()
        mock_mapping.provider = "openrouter"
        mock_config.models = {"test-model": mock_mapping}

        mock_provider = MagicMock()
        mock_provider.type = "openai"
        mock_config.providers = {"openrouter": mock_provider}

        with patch(
            "src.core.agent_core.load_llm_proxy_config",
            return_value=mock_config,
        ):
            result = _get_proxy_base_url_for_model(
                "test-model", session_id="sess-123"
            )

        assert result is not None
        assert "/s/sess-123" in result

    def test_model_not_in_proxy_config_returns_none(self):
        """Model not in proxy config should return None (use direct API)."""
        from src.core.agent_core import _get_proxy_base_url_for_model

        mock_config = MagicMock()
        mock_config.models = {}  # Empty models dict

        with patch(
            "src.core.agent_core.load_llm_proxy_config",
            return_value=mock_config,
        ):
            result = _get_proxy_base_url_for_model("claude-sonnet-4-20250514")

        assert result is None

    def test_missing_proxy_config_returns_none(self):
        """ProxyConfigError (config file missing) should return None."""
        from src.core.agent_core import _get_proxy_base_url_for_model
        from src.api.llm_proxy.config import ProxyConfigError

        with patch(
            "src.core.agent_core.load_llm_proxy_config",
            side_effect=ProxyConfigError("No config"),
        ):
            result = _get_proxy_base_url_for_model("any-model")

        assert result is None

    def test_undefined_provider_returns_none(self):
        """Model referencing a non-existent provider should return None."""
        from src.core.agent_core import _get_proxy_base_url_for_model

        mock_config = MagicMock()
        mock_mapping = MagicMock()
        mock_mapping.provider = "nonexistent"
        mock_config.models = {"test-model": mock_mapping}
        mock_config.providers = {}  # Provider not defined

        with patch(
            "src.core.agent_core.load_llm_proxy_config",
            return_value=mock_config,
        ):
            result = _get_proxy_base_url_for_model("test-model")

        assert result is None

    def test_custom_api_port(self):
        """Custom api_port should be reflected in the URL."""
        from src.core.agent_core import _get_proxy_base_url_for_model

        mock_config = MagicMock()
        mock_mapping = MagicMock()
        mock_mapping.provider = "provider1"
        mock_config.models = {"m1": mock_mapping}

        mock_provider = MagicMock()
        mock_provider.type = "openai"
        mock_config.providers = {"provider1": mock_provider}

        with patch(
            "src.core.agent_core.load_llm_proxy_config",
            return_value=mock_config,
        ):
            result = _get_proxy_base_url_for_model("m1", api_port=9999)

        assert result is not None
        assert ":9999/" in result


# ===================================================================
# TestBuildUserPrompt
# ===================================================================
class TestBuildUserPrompt:
    """Tests for ClaudeAgent._build_user_prompt()."""

    def _make_session_context(self, session_id="test-session"):
        from src.core.schemas import SessionContext
        return SessionContext(session_id=session_id)

    def test_basic_template_rendering(self, tmp_path):
        """Task and context should be rendered into the user prompt."""
        agent, mocks = _build_agent()
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        mocks["session_manager"].get_workspace_dir.return_value = workspace

        # Create a minimal user.md template
        template = "Task: ${TASK}\n{% if HAS_CONTEXT %}Context: ${CONTEXT}{% endif %}\n"
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        user_md = prompts_dir / "user.md"
        user_md.write_text(template)

        ctx = self._make_session_context()
        with patch("src.core.agent_core.PROMPTS_DIR", prompts_dir):
            result = agent._build_user_prompt(
                task="Write hello world",
                session_context=ctx,
                parameters={"context": "Python script"},
            )

        assert "Write hello world" in result
        assert "Python script" in result

    def test_without_context(self, tmp_path):
        """When no context parameter is given, HAS_CONTEXT should be False."""
        agent, mocks = _build_agent()
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        mocks["session_manager"].get_workspace_dir.return_value = workspace

        template = "Task: ${TASK}\n{% if HAS_CONTEXT %}Context: ${CONTEXT}{% endif %}\n"
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "user.md").write_text(template)

        ctx = self._make_session_context()
        with patch("src.core.agent_core.PROMPTS_DIR", prompts_dir):
            result = agent._build_user_prompt(
                task="Do something",
                session_context=ctx,
            )

        assert "Do something" in result
        assert "Context:" not in result

    def test_empty_task_raises(self, tmp_path):
        """Empty task should raise AgentError."""
        from src.core.exceptions import AgentError

        agent, mocks = _build_agent()
        ctx = self._make_session_context()

        with pytest.raises(AgentError, match="Task is required"):
            agent._build_user_prompt(task="", session_context=ctx)

    def test_whitespace_task_raises(self, tmp_path):
        """Whitespace-only task should raise AgentError."""
        from src.core.exceptions import AgentError

        agent, mocks = _build_agent()
        ctx = self._make_session_context()

        with pytest.raises(AgentError, match="Task is required"):
            agent._build_user_prompt(task="   \n  ", session_context=ctx)

    def test_missing_template_raises(self, tmp_path):
        """Missing user.md template should raise AgentError."""
        from src.core.exceptions import AgentError

        agent, mocks = _build_agent()
        ctx = self._make_session_context()

        # Point to an empty directory (no user.md)
        prompts_dir = tmp_path / "empty_prompts"
        prompts_dir.mkdir()

        with patch("src.core.agent_core.PROMPTS_DIR", prompts_dir):
            with pytest.raises(AgentError, match="User prompt template not found"):
                agent._build_user_prompt(
                    task="Test task", session_context=ctx
                )


# ===================================================================
# TestValidateResponse
# ===================================================================
class TestValidateResponse:
    """Tests for ClaudeAgent._validate_response()."""

    def test_none_response_raises_session_incomplete(self):
        """None response should raise SessionIncompleteError."""
        from src.core.exceptions import SessionIncompleteError

        agent, _ = _build_agent()

        with pytest.raises(SessionIncompleteError, match="Session did not complete"):
            agent._validate_response(None)

    def test_error_response_raises_server_error(self):
        """Response with is_error=True should raise ServerError."""
        from src.core.exceptions import ServerError

        agent, _ = _build_agent()

        mock_response = MagicMock()
        mock_response.is_error = True
        mock_response.result = "Connection refused"
        mock_response.subtype = "api_error"

        with pytest.raises(ServerError, match="Connection refused"):
            agent._validate_response(mock_response)

    def test_error_response_falls_back_to_subtype(self):
        """When result is None, error message should use subtype."""
        from src.core.exceptions import ServerError

        agent, _ = _build_agent()

        mock_response = MagicMock()
        mock_response.is_error = True
        mock_response.result = None
        mock_response.subtype = "rate_limit_error"

        with pytest.raises(ServerError, match="rate_limit_error"):
            agent._validate_response(mock_response)

    def test_error_response_unknown_fallback(self):
        """When both result and subtype are None, should say Unknown error."""
        from src.core.exceptions import ServerError

        agent, _ = _build_agent()

        mock_response = MagicMock()
        mock_response.is_error = True
        mock_response.result = None
        mock_response.subtype = None

        with pytest.raises(ServerError, match="Unknown error"):
            agent._validate_response(mock_response)

    def test_max_turns_exceeded_raises(self):
        """Response with subtype='error_max_turns' raises MaxTurnsExceededError."""
        from src.core.exceptions import MaxTurnsExceededError

        agent, _ = _build_agent()

        mock_response = MagicMock()
        mock_response.is_error = False
        mock_response.subtype = "error_max_turns"

        with pytest.raises(MaxTurnsExceededError, match="Exceeded 10 turns"):
            agent._validate_response(mock_response)

    def test_valid_response_passes(self):
        """A normal successful response should not raise."""
        agent, _ = _build_agent()

        mock_response = MagicMock()
        mock_response.is_error = False
        mock_response.subtype = "success"
        mock_response.result = "Done!"

        # Should not raise
        agent._validate_response(mock_response)


# ===================================================================
# TestPersistConversationForResume
# ===================================================================
class TestPersistConversationForResume:
    """Tests for _persist_conversation_for_resume()."""

    def test_jsonl_format_new_file(self, tmp_path):
        """First write should create properly formatted JSONL entries."""
        from src.core.agent_core import _persist_conversation_for_resume

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        claude_session_id = "claude-sess-abc"

        _persist_conversation_for_resume(
            session_dir=session_dir,
            workspace_dir=workspace_dir,
            claude_session_id=claude_session_id,
            user_prompt="Hello agent",
            result_text="Hi, how can I help?",
            model="claude-sonnet-4-20250514",
        )

        # Compute expected slug
        slug = str(workspace_dir).replace("/", "-").replace("_", "-")
        project_file = session_dir / "projects" / slug / f"{claude_session_id}.jsonl"

        assert project_file.exists(), f"JSONL file not created at {project_file}"

        lines = project_file.read_text().strip().split("\n")
        assert len(lines) == 2, f"Expected 2 JSONL entries, got {len(lines)}"

        user_entry = json.loads(lines[0])
        assistant_entry = json.loads(lines[1])

        # User entry checks
        assert user_entry["type"] == "user"
        assert user_entry["sessionId"] == claude_session_id
        assert user_entry["message"]["role"] == "user"
        assert user_entry["message"]["content"] == "Hello agent"
        assert user_entry["parentUuid"] is None  # First message has no parent
        assert "uuid" in user_entry
        assert "timestamp" in user_entry

        # Assistant entry checks
        assert assistant_entry["type"] == "assistant"
        assert assistant_entry["sessionId"] == claude_session_id
        assert assistant_entry["parentUuid"] == user_entry["uuid"]
        assert assistant_entry["message"]["role"] == "assistant"
        assert assistant_entry["message"]["model"] == "claude-sonnet-4-20250514"
        assert assistant_entry["message"]["stop_reason"] == "end_turn"

        content_blocks = assistant_entry["message"]["content"]
        assert len(content_blocks) == 1
        assert content_blocks[0]["type"] == "text"
        assert content_blocks[0]["text"] == "Hi, how can I help?"

    def test_project_slug_replaces_slash_and_underscore(self, tmp_path):
        """
        Project slug must replace BOTH / and _ with -.

        Critical for session resume: the Claude Code binary uses this same
        algorithm. If the slug doesn't match, --resume fails with
        'No conversation found'.
        """
        from src.core.agent_core import _persist_conversation_for_resume

        session_dir = tmp_path / "session"
        session_dir.mkdir()

        # Path with both slashes and underscores
        workspace_dir = tmp_path / "users" / "greg" / "session_20260212_abc"
        workspace_dir.mkdir(parents=True)

        _persist_conversation_for_resume(
            session_dir=session_dir,
            workspace_dir=workspace_dir,
            claude_session_id="s1",
            user_prompt="test",
            result_text="ok",
            model="test-model",
        )

        slug = str(workspace_dir).replace("/", "-").replace("_", "-")

        # Verify the directory was created with the correct slug
        project_dir = session_dir / "projects" / slug
        assert project_dir.exists(), (
            f"Project dir not found. Expected slug: {slug}"
        )

        # Slug must not contain / or _
        assert "/" not in slug
        assert "_" not in slug
        # Slug must start with - (leading / becomes -)
        assert slug.startswith("-")

    def test_parent_uuid_chaining(self, tmp_path):
        """
        Second call should chain parentUuid to previous assistant message.

        The binary searches for the last assistant uuid as parent for the
        next user message, enabling linear conversation threading.
        """
        from src.core.agent_core import _persist_conversation_for_resume

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        workspace_dir = tmp_path / "ws"
        workspace_dir.mkdir()
        claude_session_id = "chain-test"

        # First turn
        _persist_conversation_for_resume(
            session_dir=session_dir,
            workspace_dir=workspace_dir,
            claude_session_id=claude_session_id,
            user_prompt="Turn 1",
            result_text="Response 1",
            model="m",
        )

        # Second turn
        _persist_conversation_for_resume(
            session_dir=session_dir,
            workspace_dir=workspace_dir,
            claude_session_id=claude_session_id,
            user_prompt="Turn 2",
            result_text="Response 2",
            model="m",
        )

        slug = str(workspace_dir).replace("/", "-").replace("_", "-")
        project_file = session_dir / "projects" / slug / f"{claude_session_id}.jsonl"
        lines = project_file.read_text().strip().split("\n")
        assert len(lines) == 4, f"Expected 4 entries (2 turns), got {len(lines)}"

        entries = [json.loads(line) for line in lines]
        # entries: [user1, assistant1, user2, assistant2]
        user1, assistant1, user2, assistant2 = entries

        # First user has no parent
        assert user1["parentUuid"] is None
        # First assistant's parent is first user
        assert assistant1["parentUuid"] == user1["uuid"]
        # Second user's parent is first assistant (chaining!)
        assert user2["parentUuid"] == assistant1["uuid"]
        # Second assistant's parent is second user
        assert assistant2["parentUuid"] == user2["uuid"]

    def test_none_result_text_produces_empty_content(self, tmp_path):
        """When result_text is None (error case), content blocks should be empty."""
        from src.core.agent_core import _persist_conversation_for_resume

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        workspace_dir = tmp_path / "ws"
        workspace_dir.mkdir()

        _persist_conversation_for_resume(
            session_dir=session_dir,
            workspace_dir=workspace_dir,
            claude_session_id="s-err",
            user_prompt="fail task",
            result_text=None,
            model="m",
        )

        slug = str(workspace_dir).replace("/", "-").replace("_", "-")
        project_file = session_dir / "projects" / slug / "s-err.jsonl"
        lines = project_file.read_text().strip().split("\n")
        assistant_entry = json.loads(lines[1])

        assert assistant_entry["message"]["content"] == []

    def test_non_fatal_on_write_error(self, tmp_path):
        """Write failure should log a warning, not raise an exception."""
        from src.core.agent_core import _persist_conversation_for_resume

        # Use a non-existent session_dir that can't be created
        # The function uses mkdir(parents=True, exist_ok=True) which
        # would normally succeed, so we need to make it fail
        # by patching Path.mkdir to raise
        with patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
            # Should NOT raise
            _persist_conversation_for_resume(
                session_dir=tmp_path / "no-access",
                workspace_dir=tmp_path / "ws",
                claude_session_id="s1",
                user_prompt="test",
                result_text="ok",
                model="m",
            )


# ===================================================================
# TestAgentInit
# ===================================================================
class TestAgentInit:
    """Tests for ClaudeAgent.__init__ beyond permission_mode validation."""

    def test_default_tracer_is_execution_tracer(self):
        """tracer=True (default) should create an ExecutionTracer."""
        from src.core.tracer import ExecutionTracer

        agent, _ = _build_agent(tracer=True)
        assert isinstance(agent.tracer, ExecutionTracer)

    def test_false_tracer_is_null_tracer(self):
        """tracer=False should create a NullTracer."""
        from src.core.tracer import NullTracer

        agent, _ = _build_agent(tracer=False)
        assert isinstance(agent.tracer, NullTracer)

    def test_none_tracer_is_null_tracer(self):
        """tracer=None should create a NullTracer."""
        from src.core.tracer import NullTracer

        agent, _ = _build_agent(tracer=None)
        assert isinstance(agent.tracer, NullTracer)

    def test_custom_tracer_is_used(self):
        """Passing a TracerBase instance should use it directly."""
        from src.core.tracer import NullTracer

        custom_tracer = NullTracer()
        agent, _ = _build_agent(tracer=custom_tracer)
        assert agent.tracer is custom_tracer

    def test_permission_manager_wired_to_tracer(self):
        """If permission_manager is provided, set_tracer should be called."""
        mock_pm = MagicMock()
        agent, _ = _build_agent(permission_manager=mock_pm, tracer=False)
        mock_pm.set_tracer.assert_called_once_with(agent.tracer)

    def test_linux_uid_gid_stored(self):
        """linux_uid and linux_gid should be stored on the agent."""
        agent, _ = _build_agent(linux_uid=59990, linux_gid=59990)
        assert agent._linux_uid == 59990
        assert agent._linux_gid == 59990

    def test_config_property(self):
        """config property should return the AgentConfig passed to __init__."""
        cfg = _make_agent_config(model="claude-opus-4-20250514")
        agent, _ = _build_agent(config=cfg)
        assert agent.config is cfg
        assert agent.config.model == "claude-opus-4-20250514"

    def test_sessions_dir_default(self):
        """sessions_dir should be stored and passed to SessionManager."""
        custom_dir = Path("/tmp/custom-sessions")
        agent, mocks = _build_agent(sessions_dir=custom_dir)
        mocks["session_manager_cls"].assert_called_once_with(custom_dir)


# ===================================================================
# TestDetermineSessionStatus (smoke tests — detailed coverage in
# test_structured_output.py)
# ===================================================================
class TestDetermineSessionStatus:
    """Smoke tests for determine_session_status()."""

    def test_no_result_text_returns_complete(self):
        """None result_text should default to COMPLETE."""
        from src.core.agent_core import determine_session_status

        assert determine_session_status(None, had_tool_errors=False) == "COMPLETE"

    def test_empty_result_text_returns_complete(self):
        """Empty result_text should default to COMPLETE."""
        from src.core.agent_core import determine_session_status

        assert determine_session_status("", had_tool_errors=False) == "COMPLETE"

    def test_tool_errors_without_header_returns_complete(self):
        """Tool errors alone do not change status without a structured header."""
        from src.core.agent_core import determine_session_status

        status = determine_session_status(
            "Some output without headers",
            had_tool_errors=True,
            tool_error_count=3,
        )
        assert status == "COMPLETE"


# ===================================================================
# TestBuildOptions (selected aspects — full method is ~600 lines)
# ===================================================================
class TestBuildOptions:
    """
    Tests for ClaudeAgent._build_options().

    Only tests aspects that can be isolated without too many mocks.
    The full method requires many real subsystems; here we test
    validation and early-exit paths.
    """

    def _make_session_context(self, session_id="test-session"):
        from src.core.schemas import SessionContext
        return SessionContext(session_id=session_id)

    def test_empty_system_prompt_raises(self):
        """Empty system_prompt should raise AgentError."""
        from src.core.exceptions import AgentError

        agent, _ = _build_agent()
        ctx = self._make_session_context()

        with pytest.raises(AgentError, match="system_prompt is required"):
            agent._build_options(
                session_context=ctx,
                system_prompt="",
            )

    def test_whitespace_system_prompt_raises(self):
        """Whitespace-only system_prompt should raise AgentError."""
        from src.core.exceptions import AgentError

        agent, _ = _build_agent()
        ctx = self._make_session_context()

        with pytest.raises(AgentError, match="system_prompt is required"):
            agent._build_options(
                session_context=ctx,
                system_prompt="   \n   ",
            )

    def test_missing_permission_manager_raises(self):
        """No permission_manager should raise AgentError."""
        from src.core.exceptions import AgentError

        # Build agent WITHOUT a permission manager
        agent, _ = _build_agent(permission_manager=None)
        ctx = self._make_session_context()

        with pytest.raises(AgentError, match="PermissionManager is required"):
            agent._build_options(
                session_context=ctx,
                system_prompt="You are a helpful agent.",
            )


# ===================================================================
# TestSandboxSystemMessage
# ===================================================================
class TestSandboxSystemMessage:
    """Tests for _format_sandbox_system_message and _sandbox_system_message_builder."""

    def test_format_with_sandbox_config(self):
        """Should produce a message describing sandbox policy."""
        agent, _ = _build_agent()

        mock_sandbox = MagicMock()
        mock_sandbox.enabled = True
        mock_sandbox.file_sandboxing = True
        mock_sandbox.network_sandboxing = False
        mock_sandbox.writable_paths = ["/workspace"]
        mock_sandbox.readonly_paths = ["/skills"]

        workspace = Path("/workspace")
        result = agent._format_sandbox_system_message(mock_sandbox, workspace)

        assert result is not None
        assert "file sandboxing enabled" in result
        assert "network sandboxing disabled" in result
        assert "/workspace" in result
        assert "/skills" in result

    def test_format_with_none_config(self):
        """None sandbox config should return None."""
        agent, _ = _build_agent()
        result = agent._format_sandbox_system_message(None, Path("/workspace"))
        assert result is None

    def test_message_builder_returns_for_file_tools(self):
        """Builder should return message for file/bash tool names."""
        agent, _ = _build_agent()
        agent._sandbox_system_message = "Sandbox policy: test"

        for tool_name in ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "LS"]:
            result = agent._sandbox_system_message_builder(tool_name, {})
            assert result == "Sandbox policy: test", (
                f"Expected message for tool {tool_name}"
            )

    def test_message_builder_returns_none_for_other_tools(self):
        """Builder should return None for non-file tools."""
        agent, _ = _build_agent()
        agent._sandbox_system_message = "Sandbox policy: test"

        for tool_name in ["TodoWrite", "TodoRead", "Skill", "AskUserQuestion"]:
            result = agent._sandbox_system_message_builder(tool_name, {})
            assert result is None, (
                f"Expected None for tool {tool_name}"
            )

    def test_message_builder_returns_none_when_no_message(self):
        """Builder should return None when _sandbox_system_message is not set."""
        agent, _ = _build_agent()
        agent._sandbox_system_message = None

        result = agent._sandbox_system_message_builder("Bash", {})
        assert result is None


# ===================================================================
# TestAgentConfigProperties
# ===================================================================
class TestAgentConfigProperties:
    """Tests for AgentConfig derived properties used by agent_core."""

    def test_base_model_without_thinking(self):
        """base_model should return model as-is without thinking suffix."""
        cfg = _make_agent_config(model="claude-sonnet-4-20250514")
        assert cfg.base_model == "claude-sonnet-4-20250514"

    def test_base_model_with_thinking(self):
        """base_model should strip :mode=thinking suffix."""
        cfg = _make_agent_config(model="claude-sonnet-4-5-20250929:mode=thinking")
        assert cfg.base_model == "claude-sonnet-4-5-20250929"

    def test_thinking_enabled_true(self):
        """thinking_enabled should be True when model has thinking suffix."""
        cfg = _make_agent_config(model="claude-sonnet-4-5-20250929:mode=thinking")
        assert cfg.thinking_enabled is True

    def test_thinking_enabled_false(self):
        """thinking_enabled should be False for normal models."""
        cfg = _make_agent_config(model="claude-sonnet-4-20250514")
        assert cfg.thinking_enabled is False

    def test_effective_thinking_tokens_when_enabled(self):
        """Should return thinking_tokens when thinking is enabled."""
        cfg = _make_agent_config(
            model="claude-sonnet-4-5-20250929:mode=thinking",
            thinking_tokens=16000,
        )
        assert cfg.effective_thinking_tokens == 16000

    def test_effective_thinking_tokens_when_disabled(self):
        """Should return None when thinking is not enabled."""
        cfg = _make_agent_config(
            model="claude-sonnet-4-20250514",
            thinking_tokens=16000,
        )
        assert cfg.effective_thinking_tokens is None
