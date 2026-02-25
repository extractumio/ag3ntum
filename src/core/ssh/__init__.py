"""
SSH tool infrastructure for Ag3ntum.

Provides secure SSH connectivity with:
- SSH-specific command filtering (privilege tiers L0-L4)
- Persistent connection pool with keepalive and transparent reconnection
- Credential vault integration (agent never sees raw keys)
- Configuration management (profiles, security settings)
"""
from .ssh_config import (
    SSHProfile,
    SSHSecurityConfig,
    SSHConnectionLimits,
    load_ssh_security_config,
    load_ssh_profiles,
)
from .ssh_command_filter import SSHCommandFilter, SSHFilterResult
from .ssh_connection_pool import (
    SSHConnectionPool,
    SSHConnectionEntry,
    SSHCommandResult,
    SSHConnectionLimitError,
)
from .ssh_credential_vault import SSHCredentialVault

__all__ = [
    "SSHProfile",
    "SSHSecurityConfig",
    "SSHConnectionLimits",
    "load_ssh_security_config",
    "load_ssh_profiles",
    "SSHCommandFilter",
    "SSHFilterResult",
    "SSHConnectionPool",
    "SSHConnectionEntry",
    "SSHCommandResult",
    "SSHConnectionLimitError",
    "SSHCredentialVault",
]
