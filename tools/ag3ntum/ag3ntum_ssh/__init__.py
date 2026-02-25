"""
Ag3ntum SSH tools package.

Provides MCP tools for secure remote server management via SSH:
- SSHConnect: Manage SSH connections (connect/disconnect/list/status)
- SSHExec:    Execute commands on remote servers (privilege-filtered)
- SSHRead:    Read files from remote servers via SFTP
"""
from .tool import (
    AG3NTUM_SSH_EXEC_TOOL,
    AG3NTUM_SSH_READ_TOOL,
    AG3NTUM_SSH_CONNECT_TOOL,
    SSHToolContext,
    create_ssh_tools,
    create_ssh_exec_tool,
    create_ssh_read_tool,
    create_ssh_connect_tool,
    _ssh_exec_impl,
    _ssh_read_impl,
    _ssh_connect_impl,
)

__all__ = [
    "AG3NTUM_SSH_EXEC_TOOL",
    "AG3NTUM_SSH_READ_TOOL",
    "AG3NTUM_SSH_CONNECT_TOOL",
    "SSHToolContext",
    "create_ssh_tools",
    "create_ssh_exec_tool",
    "create_ssh_read_tool",
    "create_ssh_connect_tool",
    # impl functions exported for direct testing
    "_ssh_exec_impl",
    "_ssh_read_impl",
    "_ssh_connect_impl",
]
