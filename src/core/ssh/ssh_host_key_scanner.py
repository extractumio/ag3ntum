"""
SSH host key scanner — admin-only utility for key pinning.

Retrieves the server's public host key for a given host:port.
NOT an MCP tool — only used by administrators to pin host keys
in the vault before the first connection.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def scan_host_key(
    host: str,
    port: int = 22,
    timeout: float = 10.0,
) -> str:
    """Scan a remote host and return its public key in OpenSSH format.

    Uses asyncssh.get_server_host_key() to retrieve the server's
    host key without establishing a full connection.

    Args:
        host: Remote hostname or IP address.
        port: SSH port (default 22).
        timeout: Connection timeout in seconds.

    Returns:
        Public key string in OpenSSH format (e.g., "ssh-ed25519 AAAA...").

    Raises:
        ConnectionError: On timeout or network failure.
        ValueError: If no key is returned by the server.
    """
    import asyncio

    import asyncssh

    try:
        key = await asyncio.wait_for(
            asyncssh.get_server_host_key(host, port),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        raise ConnectionError(
            f"Timeout scanning host key from {host}:{port} "
            f"(timeout={timeout}s)"
        )
    except OSError as exc:
        raise ConnectionError(
            f"Failed to connect to {host}:{port} for host key scan: {exc}"
        ) from exc

    if key is None:
        raise ValueError(
            f"No host key returned by {host}:{port}"
        )

    public_key_str = key.export_public_key().decode("utf-8").strip()
    logger.info(
        "Scanned host key from %s:%d — type=%s",
        host, port, key.get_algorithm(),
    )
    return public_key_str
