"""
Tests for system_reminders.py.

Tests the system reminder module:
- ReminderType enum completeness
- ReminderContext data mapping
- get_reminder() rendering and wrapping
- System reminder tag format
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.system_reminders import (
    ReminderType,
    ReminderContext,
    get_reminder,
)
from src.core.prompt_manager import PromptManager


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset PromptManager singleton between tests."""
    PromptManager.reset_instance()
    yield
    PromptManager.reset_instance()


@pytest.fixture
def reminders_dir(tmp_path, monkeypatch):
    """Create a minimal reminders directory."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()

    # Create roles (needed by PromptManager)
    roles_dir = prompts_dir / "roles"
    roles_dir.mkdir()
    (roles_dir / "default.md").write_text("default role")

    # Create system-reminders
    reminders = prompts_dir / "system-reminders"
    reminders.mkdir()

    (reminders / "file-modified-by-user-or-linter.md").write_text(
        "<!--\nname: 'File Modified'\ndescription: File modified\nvariables:\n  - FILE_PATH\noverride_allowed: false\n-->\n\n"
        "File `${FILE_PATH}` was modified."
    )
    (reminders / "file-truncated.md").write_text(
        "<!--\nname: 'File Truncated'\ndescription: Truncated\nvariables:\n  - TRUNCATED_LINES\noverride_allowed: false\n-->\n\n"
        "${TRUNCATED_LINES} lines omitted."
    )
    (reminders / "token-usage.md").write_text(
        "<!--\nname: 'Token Usage'\ndescription: Token usage\nvariables:\n  - TOKENS_USED\n  - TOKENS_TOTAL\n  - TOKENS_REMAINING\noverride_allowed: false\n-->\n\n"
        "Usage: ${TOKENS_USED}/${TOKENS_TOTAL} (${TOKENS_REMAINING} left)"
    )
    (reminders / "hook-success.md").write_text(
        "<!--\nname: 'Hook Success'\ndescription: Hook ok\nvariables:\n  - HOOK_NAME\n  - HOOK_OUTPUT\noverride_allowed: false\n-->\n\n"
        "Hook `${HOOK_NAME}` OK. Output: ${HOOK_OUTPUT}"
    )
    (reminders / "team-coordination.md").write_text(
        "<!--\nname: 'Team'\ndescription: Team\nvariables:\n  - TEAM_CONFIG_PATH\n  - TASK_LIST_PATH\noverride_allowed: false\n-->\n\n"
        "Team: ${TEAM_CONFIG_PATH}, Tasks: ${TASK_LIST_PATH}"
    )

    monkeypatch.setattr("src.core.prompt_manager.PROMPTS_DIR", prompts_dir)
    monkeypatch.setattr("src.core.prompt_manager.CONFIG_DIR", prompts_dir / "config")

    return prompts_dir


# ---------------------------------------------------------------------------
# Enum Completeness
# ---------------------------------------------------------------------------
class TestReminderTypeEnum:
    """Tests for ReminderType enum."""

    @pytest.mark.unit
    def test_has_42_values(self):
        assert len(ReminderType) == 42

    @pytest.mark.unit
    def test_all_values_are_kebab_case(self):
        """All enum values should be kebab-case strings."""
        for rt in ReminderType:
            assert "-" in rt.value or rt.value.isalpha(), f"{rt.name}: {rt.value}"
            assert rt.value == rt.value.lower(), f"{rt.name} should be lowercase"

    @pytest.mark.unit
    def test_file_operations_group(self):
        file_ops = [
            ReminderType.FILE_MODIFIED_BY_USER_OR_LINTER,
            ReminderType.FILE_EXISTS_BUT_EMPTY,
            ReminderType.FILE_TRUNCATED,
            ReminderType.FILE_SHORTER_THAN_OFFSET,
            ReminderType.FILE_OPENED_IN_IDE,
            ReminderType.LINES_SELECTED_IN_IDE,
        ]
        assert len(file_ops) == 6

    @pytest.mark.unit
    def test_plan_mode_group(self):
        plan_modes = [
            ReminderType.PLAN_MODE_IS_ACTIVE_5_PHASE,
            ReminderType.PLAN_MODE_IS_ACTIVE_ITERATIVE,
            ReminderType.PLAN_MODE_IS_ACTIVE_SUBAGENT,
            ReminderType.PLAN_MODE_RE_ENTRY,
            ReminderType.EXITED_PLAN_MODE,
            ReminderType.PLAN_FILE_REFERENCE,
            ReminderType.VERIFY_PLAN_REMINDER,
        ]
        assert len(plan_modes) == 7


# ---------------------------------------------------------------------------
# get_reminder()
# ---------------------------------------------------------------------------
class TestGetReminder:
    """Tests for get_reminder() function."""

    @pytest.mark.unit
    def test_file_modified_reminder(self, reminders_dir):
        ctx = ReminderContext(file_path="/workspace/test.py")
        result = get_reminder(ReminderType.FILE_MODIFIED_BY_USER_OR_LINTER, ctx)
        assert result is not None
        assert "<system-reminder>" in result
        assert "</system-reminder>" in result
        assert "/workspace/test.py" in result

    @pytest.mark.unit
    def test_file_truncated_reminder(self, reminders_dir):
        ctx = ReminderContext(truncated_lines=500)
        result = get_reminder(ReminderType.FILE_TRUNCATED, ctx)
        assert result is not None
        assert "500" in result

    @pytest.mark.unit
    def test_token_usage_reminder(self, reminders_dir):
        ctx = ReminderContext(
            tokens_used=5000,
            tokens_total=10000,
            tokens_remaining=5000,
        )
        result = get_reminder(ReminderType.TOKEN_USAGE, ctx)
        assert result is not None
        assert "5000" in result
        assert "10000" in result

    @pytest.mark.unit
    def test_hook_success_reminder(self, reminders_dir):
        ctx = ReminderContext(
            hook_name="pre-commit",
            hook_output="All checks passed",
        )
        result = get_reminder(ReminderType.HOOK_SUCCESS, ctx)
        assert result is not None
        assert "pre-commit" in result
        assert "All checks passed" in result

    @pytest.mark.unit
    def test_team_coordination_reminder(self, reminders_dir):
        ctx = ReminderContext(
            team_config_path="/teams/my-team/config.json",
            task_list_path="/tasks/my-team/",
        )
        result = get_reminder(ReminderType.TEAM_COORDINATION, ctx)
        assert result is not None
        assert "/teams/my-team/config.json" in result

    @pytest.mark.unit
    def test_missing_reminder_returns_none(self, reminders_dir):
        """Reminder type with no file should return None."""
        ctx = ReminderContext()
        result = get_reminder(ReminderType.SESSION_CONTINUATION, ctx)
        assert result is None

    @pytest.mark.unit
    def test_no_context(self, reminders_dir):
        """Reminder without context should still render (variables unreplaced)."""
        result = get_reminder(ReminderType.FILE_MODIFIED_BY_USER_OR_LINTER)
        assert result is not None
        assert "<system-reminder>" in result

    @pytest.mark.unit
    def test_reminder_wrapping_format(self, reminders_dir):
        """Reminders should be wrapped in <system-reminder> tags."""
        ctx = ReminderContext(file_path="test.py")
        result = get_reminder(ReminderType.FILE_MODIFIED_BY_USER_OR_LINTER, ctx)
        assert result.startswith("<system-reminder>\n")
        assert result.endswith("\n</system-reminder>")


# ---------------------------------------------------------------------------
# ReminderContext
# ---------------------------------------------------------------------------
class TestReminderContext:
    """Tests for ReminderContext dataclass."""

    @pytest.mark.unit
    def test_all_fields_optional(self):
        """All fields should default to None."""
        ctx = ReminderContext()
        assert ctx.file_path is None
        assert ctx.tokens_used is None
        assert ctx.hook_name is None

    @pytest.mark.unit
    def test_field_assignment(self):
        ctx = ReminderContext(
            file_path="/test.py",
            tokens_used=100,
            hook_name="test-hook",
        )
        assert ctx.file_path == "/test.py"
        assert ctx.tokens_used == 100
        assert ctx.hook_name == "test-hook"
