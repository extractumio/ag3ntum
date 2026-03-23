"""
SSH-specific command security filter.

Implements the 4-tier privilege model (P0-P3) for SSH command validation:
  P0 = Observer       (read-only, allowlist)
  P1 = Site Manager   (website management, blocklist + path scoping)
  P2 = Server Admin   (full server admin, blocklist + broad sudo)
  P3 = Full Access    (minimal blocklist, time-boxed)

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

# Strip harmless stderr redirects before matching — they are noise.
_STDERR_REDIRECT_RE = re.compile(
    r'\s*2>>\s*/dev/null\s*'   # 2>>/dev/null (append)
    r'|\s*2>\s*/dev/null\s*'   # 2>/dev/null
    r'|\s*&>>\s*/dev/null\s*'  # &>>/dev/null (bash combined append)
    r'|\s*&>\s*/dev/null\s*'   # &>/dev/null  (bash combined)
    r'|\s*2>&1\s*'             # 2>&1
)

# Pre-compiled path extraction patterns for file mutation commands.
# Used by _extract_target_path on the hot path — avoids per-call re.compile.
#
# sed: skip the script expression (single/double quoted or unquoted) then capture
# the absolute path that follows.  The naive .*? approach would match inside a
# quoted expression like 's/80/8080/'.
_SED_PATH_RE = re.compile(
    r"\bsed\s+-i[^\s]*\s+(?:'[^']*'|\"[^\"]*\"|\S+)\s+(/[\w/.~-]+)"
)
_TEE_PATH_RE = re.compile(r'\btee\s+(?:-\w+\s+)*(/[\w/.~-]+)')
_REDIRECT_PATH_RE = re.compile(r'>>?\s*(/[\w/.~-]+)')
_DD_PATH_RE = re.compile(r'\bdd\s+.*of=(/[\w/.~-]+)')

# Constant mapping: shell feature name → list of YAML gate pattern keys.
# The bool (allowed/blocked) comes from the per-level _ShellFeatures object;
# only the pattern key mapping is constant.
_SHELL_FEATURE_PATTERN_KEYS: dict[str, list[str]] = {
    "subshell": ["subshell"],
    "backticks": ["backticks"],
    "brace_expansion": ["brace_expansion"],
    "var_reference": ["var_reference"],
    "eval": ["eval"],
    "exec": ["exec"],
    "source": ["source"],
    "inline_shell": ["inline_shell", "inline_python", "inline_perl"],
}


def _split_compound_command(command: str) -> list[str]:
    """Split a command on shell operators (;, |, &&, ||) respecting quotes.

    Uses a quote-aware scanner so | inside "nginx|apache" is preserved.
    """
    parts: list[str] = []
    current: list[str] = []
    i = 0
    n = len(command)
    in_single = False
    in_double = False

    while i < n:
        ch = command[i]

        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
            i += 1
            continue

        if in_single or in_double:
            current.append(ch)
            i += 1
            continue

        # Two-char operators checked before single-char
        if i + 1 < n:
            two = command[i:i + 2]
            if two in ("&&", "||"):
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
                i += 2
                continue

        if ch in (";", "|"):
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    part = "".join(current).strip()
    if part:
        parts.append(part)

    return parts


logger = logging.getLogger(__name__)

_CONFIG_PRIMARY = (
    Path(__file__).parent.parent.parent.parent
    / "config" / "security" / "ssh-privilege-levels.yaml"
)
_CONFIG_FALLBACK = (
    Path(__file__).parent.parent.parent.parent
    / "config" / "security" / "ssh-privilege-levels.yaml.example"
)

# Maps privilege integer (0-3) to YAML level key.
# Indexed directly after clamping — fallback is never needed.
_LEVEL_KEYS = {
    0: "P0_observer",
    1: "P1_site_manager",
    2: "P2_server_admin",
    3: "P3_full_access",
}


@dataclass
class SSHFilterResult:
    """Result of SSH command filter evaluation."""
    allowed: bool
    action: str      # "allow", "block", "requires_approval"
    reason: str      # Human-readable explanation
    rule: str        # Which rule matched (e.g., "P0_observer:allowlist")
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


@dataclass
class _ShellFeatures:
    """Shell feature flags for a privilege level."""
    var_reference: bool = False
    brace_expansion: bool = False
    subshell: bool = False
    backticks: bool = False
    eval: bool = False
    exec: bool = False
    source: bool = False
    inline_shell: bool = False


class SSHCommandFilter:
    """4-tier privilege model for SSH command validation.

    All security decisions are at Python level — zero LLM dependence.
    Fail-closed: config errors block all commands.

    Profiles:
        P0 (0): Observer     — allowlist of safe read-only patterns
        P1 (1): Site Manager — blocklist + path-scoped file mutations
        P2 (2): Server Admin — blocklist + broad sudo
        P3 (3): Full Access  — minimal blocklist, time-boxed
    """

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self._config_valid = False

        # hard_blocked: (category, rule) — blocks at ALL levels including P3
        self._hard_blocked: list[tuple[str, _CompiledRule]] = []

        # Requires approval at all levels (not full block)
        self._requires_approval: list[_CompiledRule] = []

        # P0 allowlist
        self._p0_allowlist: list[_CompiledRule] = []

        # Blocklists for P1-P3 (P2 pre-merged with P1 at load time)
        self._level_blocklists: dict[int, list[_CompiledRule]] = {
            1: [], 2: [], 3: [],
        }

        # Sudo restrictions per level (P2 pre-merged with P1 at load time)
        self._sudo_restricted: dict[int, list[_CompiledRule]] = {
            1: [], 2: [],
        }

        # Approval triggers per level
        self._approval_triggers: dict[int, list[_CompiledRule]] = {
            1: [], 2: [],
        }

        # Path control per level
        self._writable_paths: dict[int, list[str]] = {1: [], 2: []}
        self._blocked_paths: dict[int, list[str]] = {1: [], 2: []}

        # Shell feature flags per level
        self._shell_features: dict[int, _ShellFeatures] = {}

        # Level-gated shell patterns (compiled from level_gated_shell)
        self._shell_gate_patterns: dict[str, _CompiledRule] = {}

        # Level-gated file mutation patterns
        self._file_mutation_patterns: list[tuple[_CompiledRule, int]] = []

        # Level-gated capabilities (min_level gated commands)
        self._level_gated_caps: list[tuple[_CompiledRule, int]] = []

        # Raw operations per level (for get_allowed_operations)
        self._level_operations: dict[int, list[dict]] = {
            0: [], 1: [], 2: [], 3: [],
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

        # --- hard_blocked: persistence + lateral_movement ---
        hard_blocked_cfg = config.get("hard_blocked", {})
        for category in ("persistence", "lateral_movement"):
            for rule in self._parse_rule_list(
                hard_blocked_cfg.get(category, []), "hard_blocked"
            ):
                self._hard_blocked.append((category, rule))

        self._requires_approval = self._parse_rule_list(
            hard_blocked_cfg.get("data_exfiltration_require_approval", []),
            "hard_blocked",
        )

        # --- Level-gated shell patterns ---
        shell_gate_cfg = config.get("level_gated_shell", {})
        for feature_name, rule_data in shell_gate_cfg.items():
            pattern = rule_data.get("pattern", "")
            desc = rule_data.get("description", "")
            if not pattern:
                continue
            compiled = _compile_pattern(pattern, desc, "level_gated_shell")
            if compiled:
                self._shell_gate_patterns[feature_name] = compiled

        # --- Level-gated file mutation patterns ---
        file_mut_cfg = config.get("level_gated_file_mutation", {})
        for _, rule_data in file_mut_cfg.items():
            pattern = rule_data.get("pattern", "")
            desc = rule_data.get("description", "")
            min_level = rule_data.get("min_level", 1)
            if not pattern:
                continue
            compiled = _compile_pattern(pattern, desc, "level_gated_file_mutation")
            if compiled:
                self._file_mutation_patterns.append((compiled, min_level))

        # --- Level-gated capabilities ---
        cap_cfg = config.get("level_gated_capabilities", {})
        for _, rule_data in cap_cfg.items():
            pattern = rule_data.get("pattern", "")
            desc = rule_data.get("description", "")
            min_level = rule_data.get("min_level", 1)
            if not pattern:
                continue
            compiled = _compile_pattern(pattern, desc, "level_gated_capability")
            if compiled:
                self._level_gated_caps.append((compiled, min_level))

        # --- Privilege levels ---
        levels_cfg = config.get("privilege_levels", {})

        # P0: allowlist only
        p0_data = levels_cfg.get("P0_observer", {})
        self._p0_allowlist = self._parse_rule_list(
            p0_data.get("allowed_commands", []), "P0_observer"
        )
        self._level_operations[0] = self._extract_operations(p0_data)
        self._shell_features[0] = self._parse_shell_features(p0_data)

        # P1: blocklist + path scoping + sudo restrictions
        p1_data = levels_cfg.get("P1_site_manager", {})
        p1_blocklist = self._parse_rule_list(
            p1_data.get("blocked_commands", []), "P1_site_manager"
        )
        self._level_blocklists[1] = p1_blocklist
        self._sudo_restricted[1] = self._parse_sudo_restricted(
            p1_data, "P1_site_manager"
        )
        self._approval_triggers[1] = self._parse_rule_list(
            p1_data.get("approval_triggers", []), "P1_site_manager"
        )
        self._writable_paths[1] = p1_data.get("writable_paths", [])
        self._blocked_paths[1] = p1_data.get("blocked_paths", [])
        self._shell_features[1] = self._parse_shell_features(p1_data)

        # P2: blocklist + broader paths + broader sudo
        # Pre-merge P1 blocklist and sudo rules into P2 for O(1) inheritance
        p2_data = levels_cfg.get("P2_server_admin", {})
        p2_own_blocklist = self._parse_rule_list(
            p2_data.get("blocked_commands", []), "P2_server_admin"
        )
        self._level_blocklists[2] = p2_own_blocklist + p1_blocklist
        p2_own_sudo = self._parse_sudo_restricted(p2_data, "P2_server_admin")
        self._sudo_restricted[2] = p2_own_sudo + self._sudo_restricted[1]
        self._writable_paths[2] = p2_data.get("writable_paths", [])
        self._blocked_paths[2] = p2_data.get("blocked_paths", [])
        self._shell_features[2] = self._parse_shell_features(p2_data)

        # P3: minimal blocklist
        p3_data = levels_cfg.get("P3_full_access", {})
        self._level_blocklists[3] = self._parse_rule_list(
            p3_data.get("blocked_commands", []), "P3_full_access"
        )
        self._shell_features[3] = self._parse_shell_features(p3_data)

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
            len(self._hard_blocked)
            + len(self._requires_approval)
            + len(self._p0_allowlist)
            + sum(len(v) for v in self._level_blocklists.values())
            + sum(len(v) for v in self._sudo_restricted.values())
            + len(self._shell_gate_patterns)
            + len(self._file_mutation_patterns)
            + len(self._level_gated_caps)
        )
        logger.info(
            f"SSHCommandFilter: Loaded from {path}. "
            f"{rule_count} compiled rules, "
            f"{len(self._output_redaction)} redaction patterns."
        )
        self._config_valid = True

    # --- Config parsing helpers ---

    @staticmethod
    def _parse_rule_list(
        items: list[dict], level_key: str
    ) -> list[_CompiledRule]:
        """Parse and compile a list of {pattern, description} dicts."""
        rules = []
        for rule_data in items:
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
        """Parse and compile sudo_restricted_to patterns (flat string list)."""
        rules = []
        for pattern in level_data.get("sudo_restricted_to", []):
            if not pattern:
                continue
            compiled = _compile_pattern(pattern, "sudo restricted", level_key)
            if compiled:
                rules.append(compiled)
        return rules

    @staticmethod
    def _parse_shell_features(level_data: dict) -> _ShellFeatures:
        """Parse shell_features from a level config dict."""
        sf = level_data.get("shell_features", {})
        return _ShellFeatures(
            var_reference=sf.get("var_reference", False),
            brace_expansion=sf.get("brace_expansion", False),
            subshell=sf.get("subshell", False),
            backticks=sf.get("backticks", False),
            eval=sf.get("eval", False),
            exec=sf.get("exec", False),
            source=sf.get("source", False),
            inline_shell=sf.get("inline_shell", False),
        )

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

    # --- Path checks ---

    @staticmethod
    def _path_is_under(target: str, config_path: str) -> bool:
        """Check whether target path is under a config directory path."""
        norm_target = target.rstrip("/")
        norm_config = config_path.rstrip("/")
        if norm_target == norm_config:
            return True
        return norm_target.startswith(norm_config + "/")

    def _find_blocked_path(self, target_path: str, level: int) -> Optional[str]:
        """Return the first blocked_path that covers target_path for a level."""
        for path in self._blocked_paths.get(level, []):
            if self._path_is_under(target_path, path):
                return path
        return None

    def _find_writable_path(self, target_path: str, level: int) -> Optional[str]:
        """Return the first writable_path that covers target_path for a level."""
        for path in self._writable_paths.get(level, []):
            if self._path_is_under(target_path, path):
                return path
        return None

    @staticmethod
    def _extract_target_path(command: str) -> Optional[str]:
        """Extract a file path target from a mutation command.

        Handles: sed -i ... /path, tee /path, > /path, >> /path, dd of=/path.
        Returns the first absolute path found.
        """
        for pattern in (_SED_PATH_RE, _TEE_PATH_RE, _REDIRECT_PATH_RE, _DD_PATH_RE):
            m = pattern.search(command)
            if m:
                return m.group(1)
        return None

    # --- Shell feature check ---

    def _check_shell_features(
        self, command: str, level: int, level_key: str
    ) -> Optional[SSHFilterResult]:
        """Check command against shell feature gates for the given level.

        Returns SSHFilterResult (block) if a blocked feature is found,
        or None if all features in the command are permitted.
        """
        features = self._shell_features.get(level, _ShellFeatures())

        for feature_name, pattern_keys in _SHELL_FEATURE_PATTERN_KEYS.items():
            if getattr(features, feature_name, False):
                continue
            for key in pattern_keys:
                gate = self._shell_gate_patterns.get(key)
                if gate and gate.compiled.search(command):
                    logger.warning(
                        f"SSHCommandFilter: BLOCKED shell_feature/{feature_name} "
                        f"pattern='{gate.pattern_str[:60]}' cmd={command[:80]}"
                    )
                    return SSHFilterResult(
                        allowed=False,
                        action="block",
                        reason=f"Shell feature not permitted at this level: {gate.description}",
                        rule=f"{level_key}:shell_feature:{feature_name}",
                        category="shell_feature",
                    )
        return None

    # --- File mutation path check ---

    def _check_file_mutation(
        self, command: str, level: int, level_key: str
    ) -> Optional[SSHFilterResult]:
        """Check file mutation commands against path scoping.

        At P0: all file mutations blocked (min_level >= 1 covers this).
        At P1+: file mutations allowed only within writable_paths.
        Returns SSHFilterResult if blocked, None if allowed or not a mutation.
        """
        for rule, min_level in self._file_mutation_patterns:
            if not rule.compiled.search(command):
                continue
            # Mutation detected — check level gate
            if level < min_level:
                return SSHFilterResult(
                    allowed=False,
                    action="block",
                    reason=f"File mutation not permitted below level {min_level}: {rule.description}",
                    rule=f"{level_key}:file_mutation_level",
                    category="file_mutation",
                )
            # Extract target path and validate against writable_paths
            target = self._extract_target_path(command)
            if target:
                blocked = self._find_blocked_path(target, level)
                if blocked:
                    return SSHFilterResult(
                        allowed=False,
                        action="block",
                        reason=f"File mutation to protected path blocked: {blocked}",
                        rule=f"{level_key}:blocked_path",
                        category="protected_path",
                    )
                writable = self._find_writable_path(target, level)
                if not writable and level <= 2:
                    return SSHFilterResult(
                        allowed=False,
                        action="block",
                        reason=f"File mutation target '{target}' not in writable paths",
                        rule=f"{level_key}:not_in_writable_paths",
                        category="write_blocked",
                    )
        return None

    # --- Level-gated capability check ---

    def _check_level_gated_capabilities(
        self, command: str, level: int, level_key: str
    ) -> Optional[SSHFilterResult]:
        """Check level-gated capabilities (crontab edit, systemctl enable, etc.)."""
        for rule, min_level in self._level_gated_caps:
            if rule.compiled.search(command):
                if level < min_level:
                    return SSHFilterResult(
                        allowed=False,
                        action="block",
                        reason=f"Command requires level {min_level}+: {rule.description}",
                        rule=f"{level_key}:level_gated:{min_level}",
                        category="level_gated",
                    )
                return None
        return None

    # --- Sudo check for P1/P2 ---

    def _check_sudo_restricted(
        self, command: str, level: int, level_key: str
    ) -> Optional[SSHFilterResult]:
        """For P1 and P2: if command starts with sudo, check sudo_restricted_to.

        P2 rules are pre-merged with P1 at load time — no runtime inheritance.
        """
        if not command.strip().startswith("sudo "):
            return None

        if level >= 3:
            return None

        for rule in self._sudo_restricted.get(level, []):
            if rule.compiled.search(command):
                return None

        logger.warning(
            f"SSHCommandFilter: BLOCKED {level_key}:sudo_not_in_restricted "
            f"cmd={command[:80]}"
        )
        return SSHFilterResult(
            allowed=False,
            action="block",
            reason=f"Sudo command not in allowed patterns for level {level}",
            rule=f"{level_key}:sudo_restricted",
            category="sudo_restricted",
        )

    # --- Core check logic ---

    def check_command(
        self, command: str, privilege_level: int
    ) -> SSHFilterResult:
        """Check a command against the 4-tier privilege model.

        Compound commands (joined by ;, |, &&, ||) are split and each
        subcommand is checked individually.

        Args:
            command: Full command string (may include pipes, redirects).
            privilege_level: Integer 0-3 corresponding to P0-P3.

        Returns:
            SSHFilterResult with allow/block/requires_approval decision.
        """
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

        level = max(0, min(3, privilege_level))
        level_key = _LEVEL_KEYS[level]

        # 1. Hard-blocked check on FULL command string first
        for category, rule in self._hard_blocked:
            if rule.compiled.search(command):
                logger.warning(
                    f"SSHCommandFilter: BLOCKED hard_blocked/{category} "
                    f"pattern='{rule.pattern_str[:60]}' cmd={command[:80]}"
                )
                return SSHFilterResult(
                    allowed=False,
                    action="block",
                    reason=f"Command blocked: {rule.description}",
                    rule=f"hard_blocked:{category}",
                    category=category,
                )

        # 2. Requires human approval — full-string check (all levels)
        for rule in self._requires_approval:
            if rule.compiled.search(command):
                return SSHFilterResult(
                    allowed=False,
                    action="requires_approval",
                    reason=f"Command requires human approval: {rule.description}",
                    rule="hard_blocked:data_exfiltration_require_approval",
                    category="data_exfiltration",
                )

        # 3. Shell feature gating on FULL command string
        shell_result = self._check_shell_features(command, level, level_key)
        if shell_result:
            return shell_result

        # 4. File mutation path check on FULL command string
        mutation_result = self._check_file_mutation(command, level, level_key)
        if mutation_result:
            return mutation_result

        # 5. Level-gated capabilities check on FULL command string
        cap_result = self._check_level_gated_capabilities(command, level, level_key)
        if cap_result:
            return cap_result

        # 6. Split compound commands and check each subcommand
        subcommands = [
            s for raw in _split_compound_command(command)
            if (s := _STDERR_REDIRECT_RE.sub('', raw).strip())
        ]
        if not subcommands:
            return SSHFilterResult(
                allowed=False,
                action="block",
                reason="Empty command",
                rule="empty",
                category="empty",
            )

        is_compound = len(subcommands) > 1

        # 7. Profile-specific check per subcommand
        if level == 0:
            for subcmd in subcommands:
                result = self._check_p0_allowlist(subcmd, level_key)
                if not result.allowed:
                    if is_compound:
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
            return result  # type: ignore[possibly-undefined]
        else:
            # P1-P3: Blocklist mode
            # Full-string blocklist check only for compound commands
            # (catches multi-part patterns like fork bombs spanning operators)
            if is_compound:
                full_result = self._check_blocklist(command, level, level_key)
                if not full_result.allowed:
                    return full_result
            for subcmd in subcommands:
                result = self._check_blocklist(subcmd, level, level_key)
                if not result.allowed:
                    if is_compound:
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
                sudo_result = self._check_sudo_restricted(subcmd, level, level_key)
                if sudo_result:
                    return sudo_result
                trigger_result = self._check_approval_triggers(subcmd, level, level_key)
                if trigger_result:
                    return trigger_result
            return result  # type: ignore[possibly-undefined]

    def _check_p0_allowlist(
        self, command: str, level_key: str
    ) -> SSHFilterResult:
        """P0 allowlist: command must match an allowed pattern."""
        for rule in self._p0_allowlist:
            if rule.compiled.search(command):
                return SSHFilterResult(
                    allowed=True,
                    action="allow",
                    reason=f"Matches allowed pattern: {rule.description}",
                    rule=f"{level_key}:allowlist",
                    category="safe_read",
                )
        logger.info(
            f"SSHCommandFilter: BLOCK {level_key}:no_match cmd={command[:80]}"
        )
        return SSHFilterResult(
            allowed=False,
            action="block",
            reason="Command not in allowlist for Observer level",
            rule=f"{level_key}:no_allowlist_match",
            category="unlisted",
        )

    def _check_blocklist(
        self, command: str, level: int, level_key: str
    ) -> SSHFilterResult:
        """Blocklist model for P1-P3: command blocked only if it matches.

        P2 blocklist is pre-merged with P1 at load time — single pass.
        """
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

        return SSHFilterResult(
            allowed=True,
            action="allow",
            reason=f"No blocked patterns matched at privilege level {level}",
            rule=f"{level_key}:no_blocklist_match",
            category="unrestricted",
        )

    def _check_approval_triggers(
        self, command: str, level: int, level_key: str
    ) -> Optional[SSHFilterResult]:
        """Check if a command matches an approval trigger for the given level."""
        for rule in self._approval_triggers.get(level, []):
            if rule.compiled.search(command):
                return SSHFilterResult(
                    allowed=False,
                    action="requires_approval",
                    reason=f"Requires approval: {rule.description}",
                    rule=f"{level_key}:approval_trigger",
                    category="approval_required",
                )
        return None

    # --- Host and path checks ---

    def check_host(
        self, host: str, security_config: SSHSecurityConfig
    ) -> SSHFilterResult:
        """Check whether a host is permitted by the SSH security config."""
        hosts_cfg = security_config.hosts

        host_addr: Optional[ipaddress.IPv4Address | ipaddress.IPv6Address] = None
        try:
            host_addr = ipaddress.ip_address(host)
        except ValueError:
            pass

        for entry in hosts_cfg.always_blocked:
            if self._host_matches_entry(host, host_addr, entry):
                return SSHFilterResult(
                    allowed=False,
                    action="block",
                    reason=f"Host '{host}' is in the always-blocked list",
                    rule="hosts:always_blocked",
                    category="blocked_host",
                )

        is_private = self._is_private_host(host, host_addr)
        if is_private:
            in_exceptions = any(
                self._host_matches_entry(host, host_addr, exc)
                for exc in hosts_cfg.private_network_exceptions
            )
            if not in_exceptions:
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
        try:
            network = ipaddress.ip_network(entry, strict=False)
            if host_addr is not None and host_addr in network:
                return True
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

        P3 (Full Access) has no blocked_paths of its own but still inherits
        P2's blocked_paths — protected system files remain off-limits even at
        the highest privilege level.
        """
        level = max(0, min(3, privilege_level))
        level_key = _LEVEL_KEYS[level]

        if level == 0:
            return SSHFilterResult(
                allowed=False,
                action="block",
                reason="Write access not permitted at Observer level",
                rule=f"{level_key}:no_write_access",
                category="write_blocked",
            )

        # P3 inherits P2's blocked_paths so critical files stay protected.
        check_level = min(level, 2)
        blocked = self._find_blocked_path(path, check_level)
        if blocked:
            return SSHFilterResult(
                allowed=False,
                action="block",
                reason=f"Write to protected path blocked: {blocked}",
                rule=f"{level_key}:blocked_path",
                category="protected_path",
            )

        writable = self._find_writable_path(path, level)
        if writable:
            return SSHFilterResult(
                allowed=True,
                action="allow",
                reason=f"Write to allowed path: {writable}",
                rule=f"{level_key}:writable_path",
                category="config_write",
            )

        if level >= 3:
            return SSHFilterResult(
                allowed=True,
                action="allow",
                reason="Write permitted at Full Access level",
                rule=f"{level_key}:unrestricted_write",
                category="unrestricted",
            )

        return SSHFilterResult(
            allowed=False,
            action="block",
            reason=f"Path '{path}' is not in the writable paths for level {level}",
            rule=f"{level_key}:not_in_writable_paths",
            category="write_blocked",
        )

    def check_path_readable(
        self, path: str, privilege_level: int
    ) -> SSHFilterResult:
        """Check whether a path is readable at the given privilege level.

        P0 has no blocked_paths configured (allowlist mode covers it), so we
        fall back to P1's blocked_paths — P0 is MORE restrictive, not less.
        """
        level = max(0, min(3, privilege_level))
        level_key = _LEVEL_KEYS[level]

        if level <= 2:
            # P0 inherits P1's blocked_paths so sensitive paths are still
            # protected even in observer mode.
            check_level = max(level, 1)
            blocked = self._find_blocked_path(path, check_level)
            if blocked:
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
        """Return list of allowed command patterns for a privilege level."""
        level = max(0, min(3, privilege_level))
        return list(self._level_operations.get(level, []))
