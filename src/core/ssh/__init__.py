"""
SSH tool infrastructure for Ag3ntum.

Provides secure SSH connectivity with:
- SSH-specific command filtering (privilege tiers L0-L4)
- Persistent connection pool with keepalive and transparent reconnection
- Credential vault integration (agent never sees raw keys)
- Configuration management (profiles, security settings)
"""
from .ssh_config import (
    ALWAYS_BLOCKED_HOSTS,
    SSHProfile,
    SSHSecurityConfig,
    SSHConnectionLimits,
    SSHHostKeyVerificationConfig,
    get_default_ssh_security_config,
)
from .ssh_command_filter import SSHCommandFilter, SSHFilterResult
from .ssh_connection_pool import (
    SSHConnectionPool,
    SSHConnectionEntry,
    SSHCommandResult,
    SSHConnectionLimitError,
)
from .ssh_credential_vault import SSHCredentialVault
from .ssh_host_key_resolver import SSHHostKeyResolver, HOST_KEY_SECRET_TYPE
from .ssh_host_key_scanner import scan_host_key

__all__ = [
    "SSHProfile",
    "SSHSecurityConfig",
    "SSHConnectionLimits",
    "SSHHostKeyVerificationConfig",
    "ALWAYS_BLOCKED_HOSTS",
    "get_default_ssh_security_config",
    "SSHCommandFilter",
    "SSHFilterResult",
    "SSHConnectionPool",
    "SSHConnectionEntry",
    "SSHCommandResult",
    "SSHConnectionLimitError",
    "SSHCredentialVault",
    "SSHHostKeyResolver",
    "HOST_KEY_SECRET_TYPE",
    "scan_host_key",
]
