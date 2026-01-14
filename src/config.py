"""
Global configuration for Ag3ntum.

This module defines directory paths and the AgentConfigLoader class
for loading configuration from agent.yaml and secrets.yaml.

Usage:
    from config import AGENT_DIR, LOGS_DIR, SESSIONS_DIR, CONFIG_DIR
    from config import AgentConfigLoader, ConfigNotFoundError

    # Load configuration (fails if files missing)
    loader = AgentConfigLoader()
    config = loader.get_config()

    # Or with custom config path
    loader = AgentConfigLoader(config_path=Path("./custom-agent.yaml"))
"""
import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


class ConfigNotFoundError(Exception):
    """Raised when a required configuration file is not found."""
    pass


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""
    pass


# AGENT_DIR is the root of the AGENT project.
# Allow override via AG3NTUM_ROOT so Docker can mount directories at /config, /src, etc.
_agent_root_override = os.environ.get("AG3NTUM_ROOT")
if _agent_root_override:
    AGENT_DIR: Path = Path(_agent_root_override).resolve()
else:
    # config.py is at AGENT/src/config.py, so parent.parent = AGENT/
    AGENT_DIR = Path(__file__).parent.parent.resolve()

# Standard directories
LOGS_DIR: Path = AGENT_DIR / "logs"
SESSIONS_DIR: Path = AGENT_DIR / "sessions"
CONFIG_DIR: Path = AGENT_DIR / "config"
SKILLS_DIR: Path = AGENT_DIR / "skills"
PROMPTS_DIR: Path = AGENT_DIR / "prompts"
DATA_DIR: Path = AGENT_DIR / "data"
USERS_DIR: Path = AGENT_DIR.parent / "users"

# Configuration files
AGENT_CONFIG_FILE: Path = CONFIG_DIR / "agent.yaml"
SECRETS_FILE: Path = CONFIG_DIR / "secrets.yaml"
PERMISSIONS_FILE: Path = CONFIG_DIR / "permissions.json"

# Source directories
SRC_DIR: Path = AGENT_DIR / "src"
CORE_DIR: Path = SRC_DIR / "core"


class AgentConfigLoader:
    """
    Loads and manages agent configuration from YAML files.

    Similar to PermissionManager, this class provides:
    - Fail-fast loading (no default values in code)
    - CLI override support
    - Validation of required fields
    - Secrets management (API keys)

    Usage:
        loader = AgentConfigLoader()
        config = loader.get_config()

        # With CLI overrides
        loader = AgentConfigLoader()
        loader.apply_cli_overrides(model="claude-sonnet-4-5-20250929", max_turns=50)
        config = loader.get_config()

        # With custom config path
        loader = AgentConfigLoader(config_path=Path("./custom-agent.yaml"))
    """

    # Required fields in agent.yaml
    # Note: allowed_tools and auto_checkpoint_tools come from permission profiles
    # Note: permission_mode removed - managed via permissions.yaml (defaultMode)
    REQUIRED_FIELDS = [
        "model",
        "max_turns",
        "timeout_seconds",
        "enable_skills",
        "enable_file_checkpointing",
        "role",
    ]

    def __init__(
        self,
        config_path: Optional[Path] = None,
        secrets_path: Optional[Path] = None
    ) -> None:
        """
        Initialize the configuration loader.

        Args:
            config_path: Path to agent.yaml. Defaults to CONFIG_DIR/agent.yaml.
            secrets_path: Path to secrets.yaml. Defaults to CONFIG_DIR/secrets.yaml.
        """
        self._config_path = config_path or AGENT_CONFIG_FILE
        self._secrets_path = secrets_path or SECRETS_FILE
        self._config: dict[str, Any] | None = None
        self._secrets: dict[str, Any] | None = None
        self._cli_overrides: dict[str, Any] = {}
        self._loaded = False

    def load(self) -> None:
        """
        Load configuration from agent.yaml and secrets.yaml.

        Raises:
            ConfigNotFoundError: If config files are missing.
            ConfigValidationError: If required fields are missing.
        """
        self._load_agent_config()
        self._load_secrets()
        self._validate_config()
        self._set_environment_variables()
        self._loaded = True
        logger.info(f"Configuration loaded from {self._config_path}")

    def _load_agent_config(self) -> None:
        """Load agent configuration from YAML file."""
        if not self._config_path.exists():
            raise ConfigNotFoundError(
                f"Agent configuration not found: {self._config_path}\n"
                f"Create the configuration file or specify a custom path with --config.\n"
                f"See config/agent.yaml.template for reference."
            )

        try:
            with self._config_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if data is None:
                    raise ConfigValidationError(
                        f"Agent configuration file is empty: {self._config_path}"
                    )
                self._config = data.get("agent", {})
                if not self._config:
                    raise ConfigValidationError(
                        f"No 'agent' section found in {self._config_path}"
                    )
        except yaml.YAMLError as e:
            raise ConfigValidationError(
                f"Failed to parse agent configuration {self._config_path}: {e}"
            ) from e

    def _load_secrets(self) -> None:
        """Load secrets from YAML file."""
        if not self._secrets_path.exists():
            raise ConfigNotFoundError(
                f"Secrets file not found: {self._secrets_path}\n"
                f"Create the secrets file from the template:\n"
                f"  cp {self._secrets_path}.template {self._secrets_path}\n"
                f"Then add your Anthropic API key."
            )

        try:
            with self._secrets_path.open("r", encoding="utf-8") as f:
                self._secrets = yaml.safe_load(f)
                if self._secrets is None:
                    raise ConfigValidationError(
                        f"Secrets file is empty: {self._secrets_path}"
                    )
        except yaml.YAMLError as e:
            raise ConfigValidationError(
                f"Failed to parse secrets file {self._secrets_path}: {e}"
            ) from e

        # Validate API key
        api_key = self._secrets.get("anthropic_api_key", "")
        if not api_key or api_key == "sk-ant-REPLACE_WITH_YOUR_API_KEY":
            raise ConfigValidationError(
                f"Invalid API key in {self._secrets_path}\n"
                f"Please add your actual Anthropic API key.\n"
                f"Get one at: https://console.anthropic.com/settings/keys"
            )

        if not api_key.startswith("sk-ant-"):
            raise ConfigValidationError(
                f"API key appears invalid (expected format: sk-ant-...)\n"
                f"Please check your API key in {self._secrets_path}"
            )

        if len(api_key) < 50:
            raise ConfigValidationError(
                f"API key appears truncated (only {len(api_key)} characters)\n"
                f"Anthropic API keys are typically 100+ characters."
            )

    def _validate_config(self) -> None:
        """Validate that all required fields are present."""
        if self._config is None:
            raise ConfigValidationError("Configuration not loaded")

        missing = []
        for field in self.REQUIRED_FIELDS:
            if field not in self._config:
                missing.append(field)

        if missing:
            raise ConfigValidationError(
                f"Missing required fields in {self._config_path}:\n"
                f"  {', '.join(missing)}\n"
                f"All fields must be explicitly defined - no default values."
            )

    def _set_environment_variables(self) -> None:
        """Set environment variables required by the SDK."""
        if self._secrets:
            api_key = self._secrets.get("anthropic_api_key")
            if api_key:
                os.environ["ANTHROPIC_API_KEY"] = api_key
                logger.debug("ANTHROPIC_API_KEY set from secrets.yaml")

    def apply_cli_overrides(self, **kwargs: Any) -> None:
        """
        Apply CLI argument overrides to the configuration.

        Args:
            **kwargs: Configuration values to override (e.g., model="...", max_turns=50)
        """
        for key, value in kwargs.items():
            if value is not None:
                self._cli_overrides[key] = value
                logger.debug(f"CLI override: {key}={value}")

    def get_config(self) -> dict[str, Any]:
        """
        Get the merged configuration (YAML + CLI overrides).

        Returns:
            Configuration dictionary with all settings.

        Raises:
            ConfigNotFoundError: If configuration has not been loaded.
        """
        if not self._loaded:
            self.load()

        if self._config is None:
            raise ConfigNotFoundError("Configuration not loaded")

        # Merge config with CLI overrides
        merged = dict(self._config)
        merged.update(self._cli_overrides)
        return merged

    def get(self, key: str) -> Any:
        """
        Get a specific configuration value.

        Args:
            key: Configuration key to retrieve.

        Returns:
            Configuration value.

        Raises:
            KeyError: If the key is not found.
        """
        config = self.get_config()
        if key not in config:
            raise KeyError(f"Configuration key not found: {key}")
        return config[key]

    def get_api_key(self) -> str:
        """
        Get the Anthropic API key.

        Returns:
            The API key string.

        Raises:
            ConfigNotFoundError: If secrets not loaded.
        """
        if not self._loaded:
            self.load()

        if self._secrets is None:
            raise ConfigNotFoundError("Secrets not loaded")

        return self._secrets.get("anthropic_api_key", "")

    @property
    def config_path(self) -> Path:
        """Return the path to the agent config file."""
        return self._config_path

    @property
    def secrets_path(self) -> Path:
        """Return the path to the secrets file."""
        return self._secrets_path


# Global loader instance (lazy initialization)
_global_loader: AgentConfigLoader | None = None


def get_config_loader(
    config_path: Optional[Path] = None,
    secrets_path: Optional[Path] = None,
    force_new: bool = False
) -> AgentConfigLoader:
    """
    Get the global AgentConfigLoader instance.

    Args:
        config_path: Optional custom config path.
        secrets_path: Optional custom secrets path.
        force_new: If True, create a new loader even if one exists.

    Returns:
        AgentConfigLoader instance.
    """
    global _global_loader

    if force_new or _global_loader is None or config_path or secrets_path:
        _global_loader = AgentConfigLoader(
            config_path=config_path,
            secrets_path=secrets_path
        )

    return _global_loader


def ensure_dirs() -> None:
    """Ensure all required directories exist."""
    for dir_path in [LOGS_DIR, CONFIG_DIR, SKILLS_DIR]:
        dir_path.mkdir(parents=True, exist_ok=True)
