"""
Tests for hooks.py.

Tests the SDK hooks system:
- HooksManager registration and building
- HookResult SDK response conversion
- Permission hook (allow/deny/interrupt)
- Audit hook (logging, callbacks)
- Header reminder hook
- Prompt enhancement hook
- Stop and SubagentStop hooks
- Sandbox bypass detection
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.hooks import (
    HookResult,
    HooksManager,
    ToolUsageRecord,
    create_permission_hook,
    create_audit_hook,
    create_header_reminder_hook,
    create_prompt_enhancement_hook,
    create_stop_hook,
    create_subagent_stop_hook,
)


class TestHookResult:
    """Tests for HookResult dataclass."""

    @pytest.mark.unit
    def test_default_values(self):
        """Test default HookResult values."""
        result = HookResult()
        assert result.permission_decision is None
        assert result.block is False
        assert result.interrupt is False
        assert result.system_message is None

    @pytest.mark.unit
    def test_allow_response(self):
        """Test allow decision SDK response."""
        result = HookResult(permission_decision="allow")
        response = result.to_sdk_response("PreToolUse")
        assert "hookSpecificOutput" in response
        assert response["hookSpecificOutput"]["permissionDecision"] == "allow"

    @pytest.mark.unit
    def test_deny_response(self):
        """Test deny decision SDK response."""
        result = HookResult(
            permission_decision="deny",
            permission_reason="Not allowed",
        )
        response = result.to_sdk_response("PreToolUse")
        hook_output = response["hookSpecificOutput"]
        assert hook_output["permissionDecision"] == "deny"
        assert hook_output["permissionDecisionReason"] == "Not allowed"

    @pytest.mark.unit
    def test_block_response(self):
        """Test block decision."""
        result = HookResult(block=True)
        response = result.to_sdk_response("PreToolUse")
        assert response["decision"] == "block"

    @pytest.mark.unit
    def test_interrupt_response(self):
        """Test interrupt flag in response."""
        result = HookResult(
            permission_decision="deny",
            interrupt=True,
        )
        response = result.to_sdk_response("PreToolUse")
        assert response["hookSpecificOutput"]["interrupt"] is True

    @pytest.mark.unit
    def test_system_message(self):
        """Test system message in response."""
        result = HookResult(system_message="Remember to do X")
        response = result.to_sdk_response("PostToolUse")
        assert response["systemMessage"] == "Remember to do X"

    @pytest.mark.unit
    def test_empty_result(self):
        """Test empty result gives empty response."""
        result = HookResult()
        response = result.to_sdk_response("PreToolUse")
        assert response == {}


class TestToolUsageRecord:
    """Tests for ToolUsageRecord dataclass."""

    @pytest.mark.unit
    def test_basic_record(self):
        """Test creating a basic usage record."""
        record = ToolUsageRecord(
            tool_name="Read",
            tool_id="use_123",
            input_data={"file_path": "/workspace/test.txt"},
        )
        assert record.tool_name == "Read"
        assert record.tool_id == "use_123"
        assert record.is_error is False
        assert record.timestamp is not None


class TestHooksManager:
    """Tests for HooksManager."""

    @pytest.mark.unit
    def test_empty_manager(self):
        """Test empty manager builds empty config."""
        manager = HooksManager()
        config = manager.build_hooks_config()
        assert config == {}

    @pytest.mark.unit
    def test_add_pre_tool_hook(self):
        """Test adding a PreToolUse hook."""
        manager = HooksManager()
        callback = AsyncMock()
        manager.add_pre_tool_hook(callback, matcher="Bash")
        config = manager.build_hooks_config()

        assert "PreToolUse" in config
        assert len(config["PreToolUse"]) == 1

    @pytest.mark.unit
    def test_add_post_tool_hook(self):
        """Test adding a PostToolUse hook."""
        manager = HooksManager()
        callback = AsyncMock()
        manager.add_post_tool_hook(callback)
        config = manager.build_hooks_config()

        assert "PostToolUse" in config

    @pytest.mark.unit
    def test_add_user_prompt_hook(self):
        """Test adding a UserPromptSubmit hook."""
        manager = HooksManager()
        callback = AsyncMock()
        manager.add_user_prompt_hook(callback)
        config = manager.build_hooks_config()

        assert "UserPromptSubmit" in config

    @pytest.mark.unit
    def test_add_stop_hook(self):
        """Test adding a Stop hook."""
        manager = HooksManager()
        callback = AsyncMock()
        manager.add_stop_hook(callback)
        config = manager.build_hooks_config()

        assert "Stop" in config

    @pytest.mark.unit
    def test_add_subagent_stop_hook(self):
        """Test adding a SubagentStop hook."""
        manager = HooksManager()
        callback = AsyncMock()
        manager.add_subagent_stop_hook(callback)
        config = manager.build_hooks_config()

        assert "SubagentStop" in config

    @pytest.mark.unit
    def test_multiple_hooks(self):
        """Test adding multiple hooks of same type."""
        manager = HooksManager()
        manager.add_pre_tool_hook(AsyncMock(), matcher="Bash")
        manager.add_pre_tool_hook(AsyncMock(), matcher="Write")
        config = manager.build_hooks_config()

        assert len(config["PreToolUse"]) == 2

    @pytest.mark.unit
    def test_tool_usage_records(self):
        """Test tool usage records tracking."""
        manager = HooksManager()
        assert manager.tool_usage_records == []

        manager._tool_usage_records.append(
            ToolUsageRecord(tool_name="Read", tool_id="1", input_data={})
        )
        assert len(manager.tool_usage_records) == 1

    @pytest.mark.unit
    def test_clear_records(self):
        """Test clearing tool usage records."""
        manager = HooksManager()
        manager._tool_usage_records.append(
            ToolUsageRecord(tool_name="Read", tool_id="1", input_data={})
        )
        manager.clear_records()
        assert len(manager.tool_usage_records) == 0

    @pytest.mark.unit
    def test_set_permission_check_callback(self):
        """Test setting permission check callback."""
        manager = HooksManager()
        callback = MagicMock()
        manager.set_permission_check_callback(callback)
        assert manager._on_permission_check is callback


class TestPermissionHook:
    """Tests for create_permission_hook."""

    @pytest.mark.asyncio
    async def test_allowed_tool(self):
        """Test that allowed tool returns allow decision."""
        pm = MagicMock()
        pm.is_allowed.return_value = True

        hook = create_permission_hook(pm)
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": "/workspace/test.txt"}},
            "use_123",
            None,
        )

        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

    @pytest.mark.asyncio
    async def test_denied_tool(self):
        """Test that denied tool returns deny decision."""
        pm = MagicMock()
        pm.is_allowed.return_value = False
        pm.get_allowed_patterns_for_tool.return_value = []
        pm.get_denied_patterns_for_tool.return_value = []

        hook = create_permission_hook(pm)
        result = await hook(
            {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}},
            "use_456",
            None,
        )

        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    @pytest.mark.asyncio
    async def test_sandbox_bypass_blocked(self):
        """Test that sandbox bypass attempt is immediately interrupted."""
        pm = MagicMock()
        pm.is_allowed.return_value = True  # Even if allowed

        hook = create_permission_hook(pm)
        result = await hook(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "echo hello",
                    "dangerouslyDisableSandbox": True,
                },
            },
            "use_789",
            None,
        )

        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert result["hookSpecificOutput"]["interrupt"] is True

    @pytest.mark.asyncio
    async def test_denial_count_interrupt(self):
        """Test that repeated denials trigger interrupt."""
        pm = MagicMock()
        pm.is_allowed.return_value = False
        pm.get_allowed_patterns_for_tool.return_value = []
        pm.get_denied_patterns_for_tool.return_value = []

        hook = create_permission_hook(pm, max_denials_before_interrupt=3)

        # Deny 3 times for same tool
        for i in range(3):
            result = await hook(
                {"tool_name": "Bash", "tool_input": {"command": "bad"}},
                f"use_{i}",
                None,
            )

        # Third denial should trigger interrupt
        assert result["hookSpecificOutput"].get("interrupt") is True

    @pytest.mark.asyncio
    async def test_permission_check_callback(self):
        """Test that permission check callback is invoked."""
        pm = MagicMock()
        pm.is_allowed.return_value = True
        callback = MagicMock()

        hook = create_permission_hook(pm, on_permission_check=callback)
        await hook(
            {"tool_name": "Read", "tool_input": {}},
            "use_1",
            None,
        )

        callback.assert_called_once_with("Read", "allow")

    @pytest.mark.asyncio
    async def test_denial_tracker_called(self):
        """Test that denial tracker is called on deny."""
        pm = MagicMock()
        pm.is_allowed.return_value = False
        pm.get_allowed_patterns_for_tool.return_value = []
        pm.get_denied_patterns_for_tool.return_value = []
        tracker = MagicMock()

        hook = create_permission_hook(pm, denial_tracker=tracker)
        await hook(
            {"tool_name": "Bash", "tool_input": {"command": "dangerous"}},
            "use_1",
            None,
        )

        tracker.record_denial.assert_called_once()


class TestAuditHook:
    """Tests for create_audit_hook."""

    @pytest.mark.asyncio
    async def test_audit_returns_empty(self):
        """Test that audit hook returns empty dict (no modifications)."""
        hook = create_audit_hook()
        result = await hook(
            {"tool_name": "Read", "tool_result": {"content": "data"}},
            "use_1",
            None,
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_audit_writes_log_file(self, tmp_path):
        """Test that audit hook writes to log file."""
        log_file = tmp_path / "audit.log"
        hook = create_audit_hook(log_file=log_file)

        await hook(
            {"tool_name": "Write", "tool_result": {"is_error": False}},
            "use_1",
            None,
        )

        assert log_file.exists()
        log_entry = json.loads(log_file.read_text().strip())
        assert log_entry["tool_name"] == "Write"
        assert log_entry["is_error"] is False

    @pytest.mark.asyncio
    async def test_audit_calls_completion_callback(self):
        """Test that audit hook calls on_tool_complete callback."""
        callback = MagicMock()
        hook = create_audit_hook(on_tool_complete=callback)

        await hook(
            {"tool_name": "Edit", "tool_result": {"is_error": True, "content": "err"}},
            "use_2",
            None,
        )

        callback.assert_called_once()
        args = callback.call_args
        assert args[0][0] == "Edit"
        assert args[0][3] is True  # is_error


class TestHeaderReminderHook:
    """Tests for create_header_reminder_hook."""

    @pytest.mark.asyncio
    async def test_enabled_returns_system_message(self):
        """Test that enabled hook returns system message."""
        hook = create_header_reminder_hook(enable=True)
        result = await hook(
            {"tool_name": "Read"},
            "use_1",
            None,
        )
        assert "systemMessage" in result
        assert "header" in result["systemMessage"].lower()

    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self):
        """Test that disabled hook returns empty dict."""
        hook = create_header_reminder_hook(enable=False)
        result = await hook(
            {"tool_name": "Read"},
            "use_1",
            None,
        )
        assert result == {}

    @pytest.mark.asyncio
    async def test_skip_tools(self):
        """Test that skipped tools return empty dict."""
        hook = create_header_reminder_hook(
            enable=True,
            skip_tools={"AskUserQuestion"}
        )
        result = await hook(
            {"tool_name": "AskUserQuestion"},
            "use_1",
            None,
        )
        assert result == {}


class TestPromptEnhancementHook:
    """Tests for create_prompt_enhancement_hook."""

    @pytest.mark.asyncio
    async def test_adds_timestamp(self):
        """Test that timestamp is added to prompt."""
        hook = create_prompt_enhancement_hook(add_timestamp=True)
        result = await hook(
            {"prompt": "Hello"},
            None,
            None,
        )
        updated = result["hookSpecificOutput"]["updatedPrompt"]
        assert "Hello" in updated
        assert "[" in updated  # Timestamp format

    @pytest.mark.asyncio
    async def test_adds_context(self):
        """Test that context is added to prompt."""
        hook = create_prompt_enhancement_hook(
            add_timestamp=False,
            add_context="Context: test"
        )
        result = await hook(
            {"prompt": "Do something"},
            None,
            None,
        )
        updated = result["hookSpecificOutput"]["updatedPrompt"]
        assert "Context: test" in updated
        assert "Do something" in updated


class TestStopHook:
    """Tests for create_stop_hook."""

    @pytest.mark.asyncio
    async def test_on_stop_called(self):
        """Test that on_stop callback is called."""
        on_stop = AsyncMock()
        hook = create_stop_hook(on_stop=on_stop)
        await hook({"reason": "complete"}, None, None)
        on_stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_called(self):
        """Test that cleanup function is called."""
        cleanup = AsyncMock()
        hook = create_stop_hook(cleanup_fn=cleanup)
        await hook({}, None, None)
        cleanup.assert_called_once()

    @pytest.mark.asyncio
    async def test_callback_exception_handled(self):
        """Test that exceptions in callbacks are handled gracefully."""
        on_stop = AsyncMock(side_effect=RuntimeError("cleanup failed"))
        hook = create_stop_hook(on_stop=on_stop)
        # Should not raise
        result = await hook({}, None, None)
        assert result == {}


class TestSubagentStopHook:
    """Tests for create_subagent_stop_hook."""

    @pytest.mark.asyncio
    async def test_callback_called(self):
        """Test that subagent complete callback is called."""
        callback = AsyncMock()
        hook = create_subagent_stop_hook(on_subagent_complete=callback)
        await hook(
            {"subagent_type": "research", "result": {"output": "data"}},
            None,
            None,
        )
        callback.assert_called_once_with("research", {"output": "data"})

    @pytest.mark.asyncio
    async def test_no_callback(self):
        """Test hook works without callback."""
        hook = create_subagent_stop_hook()
        result = await hook({"subagent_type": "task"}, None, None)
        assert result == {}

    @pytest.mark.asyncio
    async def test_callback_exception_handled(self):
        """Test that exceptions in callback are handled gracefully."""
        callback = AsyncMock(side_effect=RuntimeError("failed"))
        hook = create_subagent_stop_hook(on_subagent_complete=callback)
        result = await hook({"subagent_type": "task", "result": {}}, None, None)
        assert result == {}
