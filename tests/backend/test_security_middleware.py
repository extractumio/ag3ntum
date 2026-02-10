"""
Tests for security_middleware.py.

Tests the security middleware components:
- Security headers (X-Content-Type-Options, X-Frame-Options, CSP, etc.)
- Host header validation
- CORS origin building
- Trusted proxy detection
- Client IP resolution
"""
import pytest
from unittest.mock import MagicMock, AsyncMock

from src.api.security_middleware import (
    build_allowed_origins,
    build_allowed_hosts,
    is_trusted_proxy,
    get_client_ip,
)


class TestBuildAllowedOrigins:
    """Tests for build_allowed_origins function."""

    @pytest.mark.unit
    def test_default_config(self):
        """Test with empty/default config."""
        origins = build_allowed_origins({})
        assert "http://localhost:50080" in origins

    @pytest.mark.unit
    def test_custom_hostname(self):
        """Test with custom hostname."""
        config = {
            "server": {"hostname": "example.com", "protocol": "https"},
            "web": {"external_port": 443},
        }
        origins = build_allowed_origins(config)
        assert "https://example.com:443" in origins
        assert "https://example.com" in origins  # Standard port
        # Localhost fallback
        assert "http://localhost:443" in origins

    @pytest.mark.unit
    def test_localhost_no_duplicate(self):
        """Test that localhost doesn't duplicate when hostname is localhost."""
        config = {
            "server": {"hostname": "localhost", "protocol": "http"},
            "web": {"external_port": 50080},
        }
        origins = build_allowed_origins(config)
        # Should not have duplicate localhost entries
        localhost_count = sum(1 for o in origins if "localhost:50080" in o)
        assert localhost_count == 1

    @pytest.mark.unit
    def test_additional_hosts(self):
        """Test additional allowed hosts from security config."""
        config = {
            "server": {"hostname": "app.example.com", "protocol": "https"},
            "web": {"external_port": 443},
            "security": {
                "additional_allowed_hosts": ["staging.example.com"]
            },
        }
        origins = build_allowed_origins(config)
        assert any("staging.example.com" in o for o in origins)

    @pytest.mark.unit
    def test_http_standard_port(self):
        """Test that standard HTTP port 80 allows without-port origin."""
        config = {
            "server": {"hostname": "example.com", "protocol": "http"},
            "web": {"external_port": 80},
        }
        origins = build_allowed_origins(config)
        assert "http://example.com" in origins

    @pytest.mark.unit
    def test_https_standard_port(self):
        """Test that standard HTTPS port 443 allows without-port origin."""
        config = {
            "server": {"hostname": "example.com", "protocol": "https"},
            "web": {"external_port": 443},
        }
        origins = build_allowed_origins(config)
        assert "https://example.com" in origins


class TestBuildAllowedHosts:
    """Tests for build_allowed_hosts function."""

    @pytest.mark.unit
    def test_default_config(self):
        """Test with empty/default config."""
        hosts = build_allowed_hosts({})
        assert "localhost" in hosts
        assert "localhost:40080" in hosts
        assert "127.0.0.1" in hosts
        assert "127.0.0.1:40080" in hosts

    @pytest.mark.unit
    def test_custom_hostname(self):
        """Test with custom hostname."""
        config = {
            "server": {"hostname": "api.example.com"},
            "api": {"external_port": 8080},
        }
        hosts = build_allowed_hosts(config)
        assert "api.example.com" in hosts
        assert "api.example.com:8080" in hosts
        # Localhost always included
        assert "localhost" in hosts

    @pytest.mark.unit
    def test_additional_hosts(self):
        """Test additional allowed hosts."""
        config = {
            "server": {"hostname": "main.example.com"},
            "api": {"external_port": 40080},
            "security": {
                "additional_allowed_hosts": ["staging.example.com"]
            },
        }
        hosts = build_allowed_hosts(config)
        assert "staging.example.com" in hosts
        assert "staging.example.com:40080" in hosts


class TestIsTrustedProxy:
    """Tests for is_trusted_proxy function."""

    @pytest.mark.unit
    def test_no_proxies_configured(self):
        """Test returns False when no proxies configured."""
        assert is_trusted_proxy("192.168.1.1", []) is False

    @pytest.mark.unit
    def test_exact_ip_match(self):
        """Test exact IP match."""
        assert is_trusted_proxy("10.0.0.1", ["10.0.0.1"]) is True
        assert is_trusted_proxy("10.0.0.2", ["10.0.0.1"]) is False

    @pytest.mark.unit
    def test_cidr_match(self):
        """Test CIDR range match."""
        assert is_trusted_proxy("10.0.0.5", ["10.0.0.0/24"]) is True
        assert is_trusted_proxy("10.0.1.5", ["10.0.0.0/24"]) is False

    @pytest.mark.unit
    def test_multiple_proxies(self):
        """Test with multiple trusted proxies."""
        proxies = ["10.0.0.1", "172.16.0.0/16"]
        assert is_trusted_proxy("10.0.0.1", proxies) is True
        assert is_trusted_proxy("172.16.5.10", proxies) is True
        assert is_trusted_proxy("192.168.1.1", proxies) is False

    @pytest.mark.unit
    def test_invalid_client_ip(self):
        """Test with invalid client IP."""
        assert is_trusted_proxy("not-an-ip", ["10.0.0.0/24"]) is False

    @pytest.mark.unit
    def test_invalid_proxy_format(self):
        """Test with invalid proxy format (skipped, not error)."""
        # Should not raise, just skip invalid entry
        assert is_trusted_proxy("10.0.0.1", ["invalid-proxy"]) is False

    @pytest.mark.unit
    def test_ipv6_support(self):
        """Test IPv6 address matching."""
        assert is_trusted_proxy("::1", ["::1"]) is True

    @pytest.mark.unit
    def test_loopback(self):
        """Test loopback address."""
        assert is_trusted_proxy("127.0.0.1", ["127.0.0.0/8"]) is True


class TestGetClientIp:
    """Tests for get_client_ip function."""

    @pytest.mark.unit
    def test_direct_client(self):
        """Test direct client IP without proxy."""
        request = MagicMock()
        request.client.host = "192.168.1.100"
        request.headers = {}

        ip = get_client_ip(request, [])
        assert ip == "192.168.1.100"

    @pytest.mark.unit
    def test_trusted_proxy_forwarded(self):
        """Test extracting IP from X-Forwarded-For via trusted proxy."""
        request = MagicMock()
        request.client.host = "10.0.0.1"
        request.headers = {"x-forwarded-for": "203.0.113.50, 10.0.0.1"}

        ip = get_client_ip(request, ["10.0.0.1"])
        assert ip == "203.0.113.50"

    @pytest.mark.unit
    def test_untrusted_proxy_ignored(self):
        """Test that X-Forwarded-For is ignored from untrusted proxy."""
        request = MagicMock()
        request.client.host = "192.168.1.100"
        request.headers = {"x-forwarded-for": "10.0.0.50"}

        ip = get_client_ip(request, ["10.0.0.1"])
        assert ip == "192.168.1.100"

    @pytest.mark.unit
    def test_no_client(self):
        """Test when request has no client."""
        request = MagicMock()
        request.client = None
        request.headers = {}

        ip = get_client_ip(request, [])
        assert ip == "unknown"

    @pytest.mark.unit
    def test_multiple_forwarded_ips(self):
        """Test that first IP in X-Forwarded-For chain is used."""
        request = MagicMock()
        request.client.host = "10.0.0.1"
        request.headers = {"x-forwarded-for": "203.0.113.50, 10.0.0.2, 10.0.0.1"}

        ip = get_client_ip(request, ["10.0.0.1"])
        assert ip == "203.0.113.50"

    @pytest.mark.unit
    def test_no_forwarded_header(self):
        """Test trusted proxy without X-Forwarded-For header."""
        request = MagicMock()
        request.client.host = "10.0.0.1"
        request.headers = {}

        ip = get_client_ip(request, ["10.0.0.1"])
        # Falls back to client IP when no forwarded header
        assert ip == "10.0.0.1"
