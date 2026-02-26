"""
Tests for scan_host_key() admin utility.

Mocks asyncssh.get_server_host_key — no real SSH connections needed.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.ssh.ssh_host_key_scanner import scan_host_key


class TestScanHostKey:

    @pytest.mark.unit
    async def test_scan_returns_public_key_string(self):
        """scan_host_key returns the public key in OpenSSH format."""
        mock_key = MagicMock()
        mock_key.export_public_key.return_value = (
            b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest\n"
        )
        mock_key.get_algorithm.return_value = "ssh-ed25519"

        mock_fn = AsyncMock(return_value=mock_key)
        with patch("asyncssh.get_server_host_key", mock_fn):
            result = await scan_host_key("example.com", port=22)

        assert result == "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITest"
        assert isinstance(result, str)

    @pytest.mark.unit
    async def test_scan_timeout_raises_connection_error(self):
        """Timeout during scan raises ConnectionError."""
        async def slow_scan(host, port):
            await asyncio.sleep(999)

        with patch("asyncssh.get_server_host_key", side_effect=slow_scan):
            with pytest.raises(ConnectionError, match="Timeout"):
                await scan_host_key("example.com", timeout=0.01)

    @pytest.mark.unit
    async def test_scan_connection_refused_raises(self):
        """Network error during scan raises ConnectionError."""
        with patch(
            "asyncssh.get_server_host_key",
            side_effect=OSError("Connection refused"),
        ):
            with pytest.raises(ConnectionError, match="Connection refused"):
                await scan_host_key("example.com")

    @pytest.mark.unit
    async def test_scan_none_key_raises_value_error(self):
        """If the server returns no key, ValueError is raised."""
        mock_fn = AsyncMock(return_value=None)
        with patch("asyncssh.get_server_host_key", mock_fn):
            with pytest.raises(ValueError, match="No host key"):
                await scan_host_key("example.com")
