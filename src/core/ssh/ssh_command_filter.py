"""
SSH-specific command security filter.

Implements the 5-tier privilege model (L0-L4) for SSH command validation.
All security decisions are made at the Python level — zero dependence on
LLM capability, reasoning, or judgment.

Fail-closed: any configuration error blocks all commands.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from .ssh_config import SSHSecurityConfig

# Regex to split compound commands on shell operators.
# Ported from command_security.py for SSH command filtering.
_SSH_COMPOUND_SPLIT_RE = re.compile(r'\s*(?:&&|\|\||[;|])\s*')

logger = logging.getLogger(__name__)

# Primary and fallback config paths
_CONFIG_PRIMARY = (
    Path(__file__).parent.parent.parent.parent
    / "config" / "security" / "ssh-privilege-levels.yaml"
)
_CONFIG_FALLBACK = (
    Path(__file__).parent.parent.parent.parent
    / "config" / "security" / "ssh-privilege-levels.yaml.example"
)

# Maps privilege integer (0-4) to YAML level key
_LEVEL_KEYS = {
    0: "L0_monitoring",
    1: "L1_service_management",
    2: "L2_configuration",
    3: "L3_administration",
    4: "L4_emergency",
}


@dataclass
class SSHFilterResult:
    """Result of SSH command filter evaluation."""
    allowed: bool
    action: str      # "allow", "block", "requires_approval"
    reason: str      # Human-readable explanation
    rule: str        # Which rule matched (e.g., "L0_monitoring:allowlist")
    category: str    # e.g., "persistence", "lateral_movement", "safe_read"


@dataclass
class _CompiledRule:
    """Compiled regex pattern with metadata."""
    pattern_str: str
    compiled: re.Pattern
    description: str
    level_key: str = field(default="")


def _compile_pattern(
    pattern_str: str,
    description: str,
    level_key: str = "",
) -> Optional[_CompiledRule]:
    """Compile a regex pattern, returning None on error."""
    try:
        compiled = re.compile(pattern_str, re.IGNORECASE)
        return _CompiledRule(
            pattern_str=pattern_str,
            compiled=compiled,
            description=description,
            level_key=level_key,
        )
    except re.error as e:
        logger.error(
            f"SSHCommandFilter: Invalid regex pattern '{pattern_str}': {e}"
        )
        return None


class SSHCommandFilter:
    """5-tier privilege model for SSH command validation.

    All security decisions are at Python level — zero LLM dependence.
    Fail-closed: config errors block all commands.

    Levels:
        L0 (0): Monitoring — allowlist of ~20 safe read-only patterns
        L1 (1): Service management — L0 + targeted sudo patterns
        L2 (2): Configuration — L1 + writes to writable_paths
        L3 (3): Administration — blocklist only (broad access)
        L4 (4): Emergency — minimal blocklist (time-boxed)
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self._config_valid = False

        # always_blocked: (category, rule) — blocks at ALL levels
        self._always_blocked: list[tuple[str, _CompiledRule]] = []

        # Requires approval at all levels (not full block)
        self._requires_approval: list[_CompiledRule] = []

        # Accumulated allowlists for L0-L2 (each level includes prior levels)
        self._level_allowlists: dict[int, list[_CompiledRule]] = {
            0: [], 1: [], 2: [],
        }

        # Blocklists for L3-L4
        self._level_blocklists: dict[int, list[_CompiledRule]] = {
            3: [], 4: [],
        }

        # L2 path control
        self._writable_paths: list[str] = []
        self._blocked_paths: list[str] = []

        # Raw operations per level (for get_allowed_operations)
        self._level_operations: dict[int, list[dict]] = {
            0: [], 1: [], 2: [], 3: [], 4: [],
        }

        # Output redaction patterns: (compiled_pattern, replacement_string)
        self._output_redaction: list[tuple[re.Pattern, str]] = []

        self._load_config(config_path)

    def _load_config(self, config_path: Optional[Path]) -> None:
        """Load and compile all privilege level rules from YAML."""
        path = config_path
        if path is None:
            if _CONFIG_PRIMARY.exists():
                path = _CONFIG_PRIMARY
            elif _CONFIG_FALLBACK.exists():
                path = _CONFIG_FALLBACK
            else:
                logger.error(
                    "SSHCommandFilter: Config not found at primary or fallback "
                    "path. All commands blocked (fail-closed)."
                )
                return

        try:
            with open(path) as f:
                config = yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(
                f"SSHCommandFilter: Failed to load config from {path}: {e}. "
                "All commands blocked (fail-closed)."
            )
            return

        if not config:
            logger.error(
                "SSHCommandFilter: Empty config. All commands blocked."
            )
            return

        # --- always_blocked: persistence + lateral_movement + shell_expansion ---
        always_blocked_cfg = config.get("always_blocked", {})
        for category in ("persistence", "lateral_movement", "shell_expansion", "output_redirect"):
            for rule_data in always_blocked_cfg.get(category, []):
                pattern = rule_data.get("pattern", "")
                desc = rule_data.get("description", "")
                if not pattern:
                    continue
                compiled = _compile_pattern(pattern, desc, "always_blocked")
                if compiled:
                    self._always_blocked.append((category, compiled))

        # --- data_exfiltration_require_approval ---
        for rule_data in always_blocked_cfg.get(
            "data_exfiltration_require_approval", []
        ):
            pattern = rule_data.get("pattern", "")
            desc = rule_data.get("description", "")
            if not pattern:
                continue
            compiled = _compile_pattern(pattern, desc, "always_blocked")
            if compiled:
                self._requires_approval.append(compiled)

        # --- Privilege levels ---
        levels_cfg = config.get("privilege_levels", {})

        # L0: allowlist only
        l0_data = levels_cfg.get("L0_monitoring", {})
        l0_rules = self._parse_allowed_commands(l0_data, "L0_monitoring")
        self._level_allowlists[0] = l0_rules
        self._level_operations[0] = self._extract_operations(l0_data)

        # L1: inherits L0 + targeted sudo patterns + L1 allowed_commands
        l1_data = levels_cfg.get("L1_service_management", {})
        l1_sudo = self._parse_sudo_restricted(l1_data, "L1_service_management")
        l1_allowed = self._parse_allowed_commands(l1_data, "L1_service_management")
        self._level_allowlists[1] = l0_rules + l1_sudo + l1_allowed
        self._level_operations[1] = (
            self._level_operations[0]
            + [{"pattern": r.pattern_str, "description": r.description}
               for r in l1_sudo]
            + self._extract_operations(l1_data)
        )

        # L2: inherits L1 + path-based write allowance
        l2_data = levels_cfg.get("L2_configuration", {})
        self._writable_paths = l2_data.get("writable_paths", [])
        self._blocked_paths = l2_data.get("blocked_paths", [])
        # The path-based extension is checked dynamically in _check_allowlist
        self._level_allowlists[2] = list(self._level_allowlists[1])
        self._level_operations[2] = list(self._level_operations[1])

        # L3: blocklist mode (broad access minus specific dangerous commands)
        l3_data = levels_cfg.get("L3_administration", {})
        self._level_blocklists[3] = self._parse_blocked_commands(
            l3_data, "L3_administration"
        )

        # L4: minimal blocklist (emergency access)
        l4_data = levels_cfg.get("L4_emergency", {})
        self._level_blocklists[4] = self._parse_blocked_commands(
            l4_data, "L4_emergency"
        )

        # --- output_redaction patterns ---
        for rule_data in config.get("output_redaction", []):
            pattern_str = rule_data.get("pattern", "")
            replacement = rule_data.get("replacement", "[REDACTED]")
            if not pattern_str:
                continue
            try:
                compiled_re = re.compile(pattern_str)
                self._output_redaction.append((compiled_re, replacement))
            except re.error as e:
                logger.warning(
                    f"SSHCommandFilter: Invalid output_redaction pattern "
                    f"'{pattern_str}': {e} — skipped."
                )

        rule_count = (
            len(self._always_blocked)
            + len(self._requires_approval)
            + sum(len(v) for v in self._level_allowlists.values())
            + sum(len(v) for v in self._level_blocklists.values())
        )
        logger.info(
            f"SSHCommandFilter: Loaded from {path}. "
            f"{rule_count} compiled rules, "
            f"{len(self._output_redaction)} redaction patterns."
        )
        self._config_valid = True

    # --- Config parsing helpers ---

    def _parse_allowed_commands(
        self, level_data: dict, level_key: str
    ) -> list[_CompiledRule]:
        """Parse and compile allowed_commands from a level config dict."""
        rules = []
        for rule_data in level_data.get("allowed_commands", []):
            pattern = rule_data.get("pattern", "")
            desc = rule_data.get("description", "")
            if not pattern:
                continue
            compiled = _compile_pattern(pattern, desc, level_key)
            if compiled:
                rules.append(compiled)
        return rules

    def _parse_sudo_restricted(
        self, level_data: dict, level_key: str
    ) -> list[_CompiledRule]:
        """Parse and compile sudo_restricted_to patterns."""
        rules = []
        for pattern in level_data.get("sudo_restricted_to", []):
            if not pattern:
                continue
            compiled = _compile_pattern(pattern, "sudo restricted", level_key)
            if compiled:
                rules.append(compiled)
        return rules

    def _parse_blocked_commands(
        self, level_data: dict, level_key: str
    ) -> list[_CompiledRule]:
        """Parse and compile blocked_commands from a level config dict."""
        rules = []
        for rule_data in level_data.get("blocked_commands", []):
            pattern = rule_data.get("pattern", "")
            desc = rule_data.get("description", "")
            if not pattern:
                continue
            compiled = _compile_pattern(pattern, desc, level_key)
            if compiled:
                rules.append(compiled)
        return rules

    def _extract_operations(self, level_data: dict) -> list[dict]:
        """Extract raw operation dicts from allowed_commands."""
        ops = []
        for rule_data in level_data.get("allowed_commands", []):
            pattern = rule_data.get("pattern", "")
            if pattern:
                ops.append({
                    "pattern": pattern,
                    "description": rule_data.get("description", ""),
                })
        return ops

    # --- Path checks for L2 ---

    @staticmethod
    def _path_appears_in_command(config_path: str, command: str) -> bool:
        """Check whether a config path appears as a real path in the command.

        Uses word-boundary-aware matching to prevent substring false positives.
        E.g. config path '/etc/nginx' matches '/etc/nginx/conf.d' but not
        '/notetc/nginx' or '/tmp/etc/nginx'.
        """
        escaped = re.escape(config_path)
        pattern = rf'(?:^|\s|["\'])({escaped})(?:[/\s"\']|$)'
        return re.search(pattern, command) is not None

    @staticmethod
    def _path_is_under(target: str, config_path: str) -> bool:
        """Check whether target path is under a config directory path.

        Normalises paths and checks proper prefix matching with separators.
        E.g. '/etc/nginx/sites/default' is under '/etc/nginx' but
        '/notetc/nginx' is not.
        """
        # Normalise: strip trailing slashes for consistency, then ensure
        # the config path either matches exactly or is a proper prefix
        # followed by a separator.
        norm_target = target.rstrip("/")
        norm_config = config_path.rstrip("/")
        if norm_target == norm_config:
            return True
        return norm_target.startswith(norm_config + "/")

    def _find_blocked_path_in_command(self, command: str) -> Optional[str]:
        """Return the first blocked_path found in a command string."""
        for path in self._blocked_paths:
            if self._path_appears_in_command(path, command):
                return path
        return None

    def _find_writable_path_in_command(self, command: str) -> Optional[str]:
        """Return the first writable_path found in a command string."""
        for path in self._writable_paths:
            if self._path_appears_in_command(path, command):
                return path
        return None

    def _find_blocked_path(self, target_path: str) -> Optional[str]:
        """Return the first blocked_path that covers target_path."""
        for path in self._blocked_paths:
            if self._path_is_under(target_path, path):
                return path
        return None

    def _find_writable_path(self, target_path: str) -> Optional[str]:
        """Return the first writable_path that covers target_path."""
        for path in self._writable_paths:
            if self._path_is_under(target_path, path):
                return path
        return None

    # --- Core check logic ---

    def check_command(
        self, command: str, privilege_level: int
    ) -> SSHFilterResult:
        """Check a command against the 5-tier privilege model.

        Compound commands (joined by ;, |, &&, ||) are split and each
        subcommand is checked individually.  This prevents injection attacks
        such as ``uptime; rm -rf /`` from bypassing allowlist patterns.

        Args:
            command: Full command string (may include pipes, redirects).
            privilege_level: Integer 0-4 corresponding to L0-L4.

        Returns:
            SSHFilterResult with allow/block/requires_approval decision.
        """
        # 1. Fail-closed if config did not load
        if not self._config_valid:
            logger.warning(
                f"SSHCommandFilter: Config invalid, blocking: {command[:80]}"
            )
            return SSHFilterResult(
                allowed=False,
                action="block",
                reason="SSH command filter config invalid (fail-closed)",
                rule="fail_closed",
                category="config_error",
            )

        # 2. Always-blocked check on FULL command string first.
        # This runs before splitting so cross-subcommand patterns such as
        # ``curl ... | bash`` are caught regardless of how the command is
        # structured.
        for category, rule in self._always_blocked:
            if rule.compiled.search(command):
                logger.warning(
                    f"SSHCommandFilter: BLOCKED always_blocked/{category} "
                    f"pattern='{rule.pattern_str[:60]}' cmd={command[:80]}"
                )
                return SSHFilterResult(
                    allowed=False,
                    action="block",
                    reason=f"Command blocked: {rule.description}",
                    rule=f"always_blocked:{category}",
                    category=category,
                )

        # 3. Requires human approval — full-string check (all levels)
        for rule in self._requires_approval:
            if rule.compiled.search(command):
                logger.info(
                    f"SSHCommandFilter: REQUIRES_APPROVAL "
                    f"pattern='{rule.pattern_str[:60]}' cmd={command[:80]}"
                )
                return SSHFilterResult(
                    allowed=False,
                    action="requires_approval",
                    reason=f"Command requires human approval: {rule.description}",
                    rule="always_blocked:data_exfiltration_require_approval",
                    category="data_exfiltration",
                )

        # 4. Split compound commands and check each subcommand individually.
        # Splitting prevents ``uptime; rm -rf /`` from matching ``^uptime$``
        # because we check each part in isolation.
        subcommands = [
            s.strip()
            for s in _SSH_COMPOUND_SPLIT_RE.split(command)
            if s.strip()
        ]
        if not subcommands:
            logger.info(
                f"SSHCommandFilter: BLOCK empty command cmd={command[:80]}"
            )
            return SSHFilterResult(
                allowed=False,
                action="block",
                reason="Empty command",
                rule="empty",
                category="empty",
            )

        # 5. Privilege-level check applied per subcommand
        level = max(0, min(4, privilege_level))
        level_key = _LEVEL_KEYS.get(level, f"L{level}")

        if level <= 2:
            # Allowlist mode: every subcommand must individually match
            for subcmd in subcommands:
                result = self._check_allowlist(subcmd, level, level_key)
                if not result.allowed:
                    if len(subcommands) > 1:
                        result = SSHFilterResult(
                            allowed=False,
                            action=result.action,
                            reason=(
                                result.reason
                                + f" (in compound command, failing part:"
                                f" '{subcmd[:60]}')"
                            ),
                            rule=result.rule,
                            category=result.category,
                        )
                    return result
            # All subcommands passed — return allow from the last check
            return self._check_allowlist(subcommands[-1], level, level_key)
        else:
            # Blocklist mode: check full string first (catches multi-part
            # patterns like fork bombs that span shell operators), then
            # check each subcommand individually.
            full_result = self._check_blocklist(command, level, level_key)
            if not full_result.allowed:
                return full_result
            for subcmd in subcommands:
                result = self._check_blocklist(subcmd, level, level_key)
                if not result.allowed:
                    if len(subcommands) > 1:
                        result = SSHFilterResult(
                            allowed=False,
                            action=result.action,
                            reason=(
                                result.reason
                                + f" (in compound command, failing part:"
                                f" '{subcmd[:60]}')"
                            ),
                            rule=result.rule,
                            category=result.category,
                        )
                    return result
            # No subcommand blocked — allow
            return self._check_blocklist(subcommands[-1], level, level_key)

    def _check_allowlist(
        self, command: str, level: int, level_key: str
    ) -> SSHFilterResult:
        """Allowlist model for L0-L2: command must match an allowed pattern."""
        for rule in self._level_allowlists.get(level, []):
            if rule.compiled.search(command):
                logger.debug(
                    f"SSHCommandFilter: ALLOW {level_key}:allowlist "
                    f"pattern='{rule.pattern_str[:60]}' cmd={command[:80]}"
                )
                return SSHFilterResult(
                    allowed=True,
                    action="allow",
                    reason=f"Matches allowed pattern: {rule.description}",
                    rule=f"{level_key}:allowlist",
                    category="safe_read",
                )

        # L2 extension: allow writes to writable_paths, deny to blocked_paths
        if level == 2:
            blocked_path = self._find_blocked_path_in_command(command)
            if blocked_path:
                logger.warning(
                    f"SSHCommandFilter: BLOCKED L2:blocked_path "
                    f"path='{blocked_path}' cmd={command[:80]}"
                )
                return SSHFilterResult(
                    allowed=False,
                    action="block",
                    reason=f"Write to protected path blocked: {blocked_path}",
                    rule="L2_configuration:blocked_path",
                    category="protected_path",
                )

            writable_path = self._find_writable_path_in_command(command)
            if writable_path:
                logger.debug(
                    f"SSHCommandFilter: ALLOW L2:writable_path "
                    f"path='{writable_path}' cmd={command[:80]}"
                )
                return SSHFilterResult(
                    allowed=True,
                    action="allow",
                    reason=f"Write to allowed config path: {writable_path}",
                    rule="L2_configuration:writable_path",
                    category="config_write",
                )

        # Default: block (allowlist model — no match means denied)
        logger.info(
            f"SSHCommandFilter: BLOCK {level_key}:no_match cmd={command[:80]}"
        )
        return SSHFilterResult(
            allowed=False,
            action="block",
            reason=f"Command not in allowlist for privilege level {level}",
            rule=f"{level_key}:no_allowlist_match",
            category="unlisted",
        )

    def _check_blocklist(
        self, command: str, level: int, level_key: str
    ) -> SSHFilterResult:
        """Blocklist model for L3-L4: command blocked only if it matches."""
        for rule in self._level_blocklists.get(level, []):
            if rule.compiled.search(command):
                logger.warning(
                    f"SSHCommandFilter: BLOCKED {level_key}:blocklist "
                    f"pattern='{rule.pattern_str[:60]}' cmd={command[:80]}"
                )
                return SSHFilterResult(
                    allowed=False,
                    action="block",
                    reason=f"Command blocked: {rule.description}",
                    rule=f"{level_key}:blocklist",
                    category="blocked_command",
                )

        # Default: allow (blocklist model — no match means allowed)
        logger.debug(
            f"SSHCommandFilter: ALLOW {level_key}:no_blocklist_match "
            f"cmd={command[:80]}"
        )
        return SSHFilterResult(
            allowed=True,
            action="allow",
            reason=f"No blocked patterns matched at privilege level {level}",
            rule=f"{level_key}:no_blocklist_match",
            category="unrestricted",
        )

    # --- Host and path checks ---

    def check_host(
        self, host: str, security_config: SSHSecurityConfig
    ) -> SSHFilterResult:
        """Check whether a host is permitted by the SSH security config.

        Checks, in order:
        1. hosts.always_blocked — explicit host strings and CIDR ranges.
        2. Private network ranges (RFC 1918 / loopback / link-local) unless
           the host appears in hosts.private_network_exceptions.
        3. Mode (allowlist vs blocklist) — allowlist requires an explicit
           match in private_network_exceptions; blocklist allows by default.

        Args:
            host: Hostname or IP address string.
            security_config: Loaded SSHSecurityConfig for the instance.

        Returns:
            SSHFilterResult with allow/block decision.
        """
        hosts_cfg = security_config.hosts

        # Resolve host to IP if possible (for CIDR checks)
        host_addr: Optional[ipaddress.IPv4Address | ipaddress.IPv6Address] = None
        try:
            host_addr = ipaddress.ip_address(host)
        except ValueError:
            pass  # hostname string — can only do exact matches for CIDR entries

        # 1. always_blocked: exact hostname or CIDR
        for entry in hosts_cfg.always_blocked:
            if self._host_matches_entry(host, host_addr, entry):
                logger.warning(
                    f"SSHCommandFilter: HOST BLOCKED always_blocked "
                    f"entry='{entry}' host='{host}'"
                )
                return SSHFilterResult(
                    allowed=False,
                    action="block",
                    reason=f"Host '{host}' is in the always-blocked list",
                    rule="hosts:always_blocked",
                    category="blocked_host",
                )

        # 2. Private network check
        is_private = self._is_private_host(host, host_addr)
        if is_private:
            in_exceptions = any(
                self._host_matches_entry(host, host_addr, exc)
                for exc in hosts_cfg.private_network_exceptions
            )
            if not in_exceptions:
                logger.warning(
                    f"SSHCommandFilter: HOST BLOCKED private_network "
                    f"host='{host}'"
                )
                return SSHFilterResult(
                    allowed=False,
                    action="block",
                    reason=(
                        f"Host '{host}' is a private/loopback address and not "
                        "listed in private_network_exceptions"
                    ),
                    rule="hosts:private_network",
                    category="private_network",
                )

        # 3. Mode check
        if hosts_cfg.mode == "allowlist":
            in_exceptions = any(
                self._host_matches_entry(host, host_addr, exc)
                for exc in hosts_cfg.private_network_exceptions
            )
            if not in_exceptions and is_private is False:
                # allowlist mode: only private-exception hosts are explicitly
                # listed — public IPs are allowed unless always_blocked matched
                pass  # fall through to allow

        logger.debug(
            f"SSHCommandFilter: HOST ALLOW host='{host}' mode={hosts_cfg.mode}"
        )
        return SSHFilterResult(
            allowed=True,
            action="allow",
            reason=f"Host '{host}' is permitted",
            rule=f"hosts:{hosts_cfg.mode}",
            category="allowed_host",
        )

    def _host_matches_entry(
        self,
        host: str,
        host_addr: Optional[ipaddress.IPv4Address | ipaddress.IPv6Address],
        entry: str,
    ) -> bool:
        """Return True if host matches an entry (exact string or CIDR)."""
        if host == entry:
            return True
        # Try CIDR match
        try:
            network = ipaddress.ip_network(entry, strict=False)
            if host_addr is not None and host_addr in network:
                return True
            # entry may be a plain IP — try comparing as address
            entry_addr = ipaddress.ip_address(entry)
            return host_addr is not None and host_addr == entry_addr
        except ValueError:
            pass
        return False

    def _is_private_host(
        self,
        host: str,
        host_addr: Optional[ipaddress.IPv4Address | ipaddress.IPv6Address],
    ) -> bool:
        """Return True if host is a private/loopback/link-local address."""
        if host in ("localhost", "localhost.localdomain"):
            return True
        if host_addr is not None:
            return (
                host_addr.is_private
                or host_addr.is_loopback
                or host_addr.is_link_local
            )
        return False

    def check_path_writable(
        self, path: str, privilege_level: int
    ) -> SSHFilterResult:
        """Check whether a path is writable at the given privilege level.

        L0-L1: no write access — all paths blocked.
        L2+: path must appear in writable_paths and not in blocked_paths.
        L3-L4: writable_paths/blocked_paths still enforced for explicit checks.

        Args:
            path: Absolute path string to check.
            privilege_level: Integer 0-4.

        Returns:
            SSHFilterResult with allow/block decision.
        """
        level = max(0, min(4, privilege_level))
        level_key = _LEVEL_KEYS.get(level, f"L{level}")

        if level < 2:
            logger.info(
                f"SSHCommandFilter: PATH WRITE BLOCKED {level_key} "
                f"(no write access below L2) path='{path}'"
            )
            return SSHFilterResult(
                allowed=False,
                action="block",
                reason=f"Write access not permitted at privilege level {level} (L0/L1 are read-only)",
                rule=f"{level_key}:no_write_access",
                category="write_blocked",
            )

        # Check blocked_paths first (higher priority)
        blocked = self._find_blocked_path(path)
        if blocked:
            logger.warning(
                f"SSHCommandFilter: PATH WRITE BLOCKED {level_key}:blocked_path "
                f"match='{blocked}' path='{path}'"
            )
            return SSHFilterResult(
                allowed=False,
                action="block",
                reason=f"Write to protected path blocked: {blocked}",
                rule=f"{level_key}:blocked_path",
                category="protected_path",
            )

        # Check writable_paths
        writable = self._find_writable_path(path)
        if writable:
            logger.debug(
                f"SSHCommandFilter: PATH WRITE ALLOW {level_key}:writable_path "
                f"match='{writable}' path='{path}'"
            )
            return SSHFilterResult(
                allowed=True,
                action="allow",
                reason=f"Write to allowed config path: {writable}",
                rule=f"{level_key}:writable_path",
                category="config_write",
            )

        # L3/L4 blocklist levels allow writes generally (no allowlist constraint)
        if level >= 3:
            logger.debug(
                f"SSHCommandFilter: PATH WRITE ALLOW {level_key} "
                f"(blocklist level, no path restriction) path='{path}'"
            )
            return SSHFilterResult(
                allowed=True,
                action="allow",
                reason=f"Write permitted at privilege level {level} (no path restrictions apply)",
                rule=f"{level_key}:unrestricted_write",
                category="unrestricted",
            )

        # L2: path not in writable_paths and not in blocked_paths — deny
        logger.info(
            f"SSHCommandFilter: PATH WRITE BLOCKED {level_key}:not_in_writable_paths "
            f"path='{path}'"
        )
        return SSHFilterResult(
            allowed=False,
            action="block",
            reason=f"Path '{path}' is not in the allowed writable paths for privilege level {level}",
            rule=f"{level_key}:not_in_writable_paths",
            category="write_blocked",
        )

    def check_path_readable(
        self, path: str, privilege_level: int
    ) -> SSHFilterResult:
        """Check whether a path is readable at the given privilege level.

        L0-L2: paths in blocked_paths are denied (sensitive files).
        L3-L4: no read restrictions (blocklist mode).

        Args:
            path: Absolute path string to check.
            privilege_level: Integer 0-4.

        Returns:
            SSHFilterResult with allow/block decision.
        """
        level = max(0, min(4, privilege_level))
        level_key = _LEVEL_KEYS.get(level, f"L{level}")

        if level <= 2:
            blocked = self._find_blocked_path(path)
            if blocked:
                logger.info(
                    f"SSHCommandFilter: PATH READ BLOCKED {level_key}:blocked_path "
                    f"match='{blocked}' path='{path}'"
                )
                return SSHFilterResult(
                    allowed=False,
                    action="block",
                    reason=f"Read of protected path blocked: {blocked}",
                    rule=f"{level_key}:blocked_path",
                    category="protected_path",
                )

        return SSHFilterResult(
            allowed=True,
            action="allow",
            reason=f"Read permitted at privilege level {level}",
            rule=f"{level_key}:read_allowed",
            category="read_access",
        )

    @property
    def output_redaction_patterns(self) -> list[tuple[re.Pattern, str]]:
        """Compiled output redaction patterns from config."""
        return self._output_redaction

    @property
    def config_valid(self) -> bool:
        """Return True if config loaded successfully."""
        return self._config_valid

    def get_allowed_operations(self, privilege_level: int) -> list[dict]:
        """Return list of allowed command patterns for a privilege level.

        Used by SSHExec in operations mode to show available commands.

        Returns:
            List of dicts with 'pattern' and 'description' keys.
        """
        level = max(0, min(4, privilege_level))
        return list(self._level_operations.get(level, []))
