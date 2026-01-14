"""
Command Security Filter for Ag3ntumBash.

Loads security rules from YAML configuration and validates commands
before execution. Provides defense-in-depth beyond bwrap sandboxing.

Rules are defined in config/security/command_filtering.yaml with:
- pattern: Python regex to match against command
- action: "block" (deny) or "record" (log but allow)
- exploit: Example command for testing

Security Philosophy:
1. Deny by default for dangerous categories
2. Fail-closed on any error
3. Log all matches for audit trail
"""
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import yaml

logger = logging.getLogger(__name__)

# Default path to security rules
DEFAULT_RULES_PATH = Path(__file__).parent.parent.parent / "config" / "security" / "command_filtering.yaml"


@dataclass
class SecurityRule:
    """Single security rule for command filtering."""
    pattern: str
    action: Literal["block", "record"]
    exploit: str
    category: str
    compiled_pattern: re.Pattern = field(init=False, repr=False)
    
    def __post_init__(self) -> None:
        """Compile the regex pattern."""
        try:
            self.compiled_pattern = re.compile(self.pattern, re.IGNORECASE)
        except re.error as e:
            logger.error(f"Invalid regex in rule: {self.pattern} - {e}")
            # Create a pattern that never matches as fallback
            self.compiled_pattern = re.compile(r"^\b$")


@dataclass
class SecurityCheckResult:
    """Result of security check on a command."""
    allowed: bool
    matched_rule: Optional[SecurityRule] = None
    message: str = ""
    
    @property
    def should_block(self) -> bool:
        """Return True if command should be blocked."""
        return not self.allowed
    
    @property
    def action(self) -> str:
        """Return the action that was/will be taken."""
        if self.matched_rule:
            return self.matched_rule.action
        return "allow"


class CommandSecurityFilter:
    """
    Command security filter that validates commands against security rules.
    
    Loads rules from YAML configuration and provides methods to check
    commands before execution.
    
    Usage:
        filter = CommandSecurityFilter()
        result = filter.check_command("kill -9 147")
        if result.should_block:
            logger.warning(f"Blocked: {result.message}")
            return error_response(result.message)
    """
    
    def __init__(
        self,
        rules_path: Optional[Path] = None,
        fail_closed: bool = True,
    ) -> None:
        """
        Initialize the command security filter.
        
        Args:
            rules_path: Path to YAML rules file. Defaults to config/security/command_filtering.yaml
            fail_closed: If True, block commands when rules fail to load (security-first).
                        If False, allow commands when rules fail to load (availability-first).
        """
        self._rules_path = rules_path or DEFAULT_RULES_PATH
        self._fail_closed = fail_closed
        self._rules: list[SecurityRule] = []
        self._rules_loaded = False
        self._load_error: Optional[str] = None
        
        self._load_rules()
    
    def _load_rules(self) -> None:
        """Load security rules from YAML configuration."""
        try:
            if not self._rules_path.exists():
                self._load_error = f"Rules file not found: {self._rules_path}"
                logger.error(f"CommandSecurityFilter: {self._load_error}")
                return
            
            with self._rules_path.open("r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            
            if not config:
                self._load_error = "Empty rules configuration"
                logger.error(f"CommandSecurityFilter: {self._load_error}")
                return
            
            # Parse rules from all categories
            rules: list[SecurityRule] = []
            for category, category_rules in config.items():
                # Skip metadata keys
                if category in ("version",):
                    continue
                
                if not isinstance(category_rules, list):
                    continue
                
                for rule_data in category_rules:
                    if not isinstance(rule_data, dict):
                        continue
                    
                    pattern = rule_data.get("pattern")
                    action = rule_data.get("action", "block")
                    exploit = rule_data.get("exploit", "")
                    
                    if not pattern:
                        continue
                    
                    if action not in ("block", "record"):
                        action = "block"  # Default to secure
                    
                    rule = SecurityRule(
                        pattern=pattern,
                        action=action,
                        exploit=exploit,
                        category=category,
                    )
                    rules.append(rule)
            
            self._rules = rules
            self._rules_loaded = True
            logger.info(
                f"CommandSecurityFilter: Loaded {len(rules)} rules "
                f"from {self._rules_path}"
            )
            
        except yaml.YAMLError as e:
            self._load_error = f"YAML parse error: {e}"
            logger.error(f"CommandSecurityFilter: {self._load_error}")
        except Exception as e:
            self._load_error = f"Failed to load rules: {e}"
            logger.exception(f"CommandSecurityFilter: {self._load_error}")
    
    def reload_rules(self) -> bool:
        """
        Reload rules from configuration file.
        
        Returns:
            True if rules loaded successfully, False otherwise.
        """
        self._rules = []
        self._rules_loaded = False
        self._load_error = None
        self._load_rules()
        return self._rules_loaded
    
    @property
    def rules_loaded(self) -> bool:
        """Return True if rules were loaded successfully."""
        return self._rules_loaded
    
    @property
    def rule_count(self) -> int:
        """Return number of loaded rules."""
        return len(self._rules)
    
    def check_command(self, command: str) -> SecurityCheckResult:
        """
        Check a command against security rules.
        
        Args:
            command: The command string to check.
            
        Returns:
            SecurityCheckResult with allowed status and matched rule if any.
        """
        # Handle load failures
        if not self._rules_loaded:
            if self._fail_closed:
                return SecurityCheckResult(
                    allowed=False,
                    message=f"Security rules not loaded: {self._load_error}. "
                            "Commands blocked for security (fail-closed mode)."
                )
            else:
                logger.warning(
                    f"CommandSecurityFilter: Rules not loaded, allowing command "
                    f"(fail-open mode): {command[:50]}..."
                )
                return SecurityCheckResult(
                    allowed=True,
                    message="Rules not loaded, allowing (fail-open mode)"
                )
        
        # Check command against all rules
        for rule in self._rules:
            try:
                if rule.compiled_pattern.search(command):
                    # Found a match
                    if rule.action == "block":
                        message = (
                            f"Command blocked by security rule [{rule.category}]: "
                            f"pattern='{rule.pattern[:50]}...'"
                        )
                        logger.warning(
                            f"CommandSecurityFilter: BLOCKED - "
                            f"category={rule.category}, command={command[:100]}..."
                        )
                        return SecurityCheckResult(
                            allowed=False,
                            matched_rule=rule,
                            message=message,
                        )
                    else:  # record
                        logger.info(
                            f"CommandSecurityFilter: RECORDED - "
                            f"category={rule.category}, command={command[:100]}..."
                        )
                        return SecurityCheckResult(
                            allowed=True,
                            matched_rule=rule,
                            message=f"Command recorded for audit [{rule.category}]",
                        )
            except Exception as e:
                logger.error(
                    f"CommandSecurityFilter: Error checking rule {rule.pattern}: {e}"
                )
                if self._fail_closed:
                    return SecurityCheckResult(
                        allowed=False,
                        message=f"Security check error: {e}. Blocking for safety."
                    )
        
        # No rules matched - command is allowed
        return SecurityCheckResult(
            allowed=True,
            message="No security rules matched",
        )
    
    def get_rules_by_category(self, category: str) -> list[SecurityRule]:
        """Get all rules in a specific category."""
        return [r for r in self._rules if r.category == category]
    
    def get_categories(self) -> list[str]:
        """Get list of all rule categories."""
        return list(set(r.category for r in self._rules))
    
    def get_exploits_for_testing(self) -> list[tuple[str, SecurityRule]]:
        """
        Get all exploit examples for security testing.
        
        Returns:
            List of (exploit_command, rule) tuples.
        """
        return [(r.exploit, r) for r in self._rules if r.exploit]
    
    def get_block_rules(self) -> list[SecurityRule]:
        """Get all rules that block commands."""
        return [r for r in self._rules if r.action == "block"]
    
    def get_record_rules(self) -> list[SecurityRule]:
        """Get all rules that only record commands."""
        return [r for r in self._rules if r.action == "record"]


# Module-level singleton for easy access
_default_filter: Optional[CommandSecurityFilter] = None


def get_command_security_filter() -> CommandSecurityFilter:
    """
    Get the default command security filter singleton.
    
    Returns:
        CommandSecurityFilter instance.
    """
    global _default_filter
    if _default_filter is None:
        _default_filter = CommandSecurityFilter()
    return _default_filter


def check_command_security(command: str) -> SecurityCheckResult:
    """
    Convenience function to check a command using the default filter.
    
    Args:
        command: Command string to check.
        
    Returns:
        SecurityCheckResult with allowed status.
    """
    return get_command_security_filter().check_command(command)
