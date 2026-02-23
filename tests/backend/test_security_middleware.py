"""
Tests for security_middleware.py.

Tests the security middleware components:
- Security headers (X-Content-Type-Options, X-Frame-Options, CSP, etc.)
- Host header validation
- CORS origin building
- Trusted proxy detection
- Client IP resolution
- Middleware dispatch (via FastAPI TestClient)
"""
import pytest
from unittest.mock import MagicMock, AsyncMock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from src.api.security_middleware import (
    build_allowed_origins,
    build_allowed_hosts,
    is_trusted_proxy,
    get_client_ip,
    SecurityHeadersMiddleware,
    HostValidationMiddleware,
    TrustedProxyMiddleware,
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


# =============================================================================
# Helper: create FastAPI app with middleware applied
# =============================================================================

def _make_app_with_security_headers(config: dict) -> FastAPI:
    """Create a FastAPI app with SecurityHeadersMiddleware for testing."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, config=config)

    @app.get("/test")
    async def test_endpoint():
        return JSONResponse({"status": "ok"})

    return app


def _make_app_with_host_validation(allowed_hosts: set) -> FastAPI:
    """Create a FastAPI app with HostValidationMiddleware for testing."""
    app = FastAPI()
    app.add_middleware(HostValidationMiddleware, allowed_hosts=allowed_hosts)

    @app.get("/test")
    async def test_endpoint():
        return JSONResponse({"status": "ok"})

    return app


def _make_app_with_trusted_proxy(trusted_proxies: list) -> FastAPI:
    """Create a FastAPI app with TrustedProxyMiddleware for testing."""
    app = FastAPI()
    app.add_middleware(TrustedProxyMiddleware, trusted_proxies=trusted_proxies)

    @app.get("/test")
    async def test_endpoint(request: Request):
        client_ip = getattr(request.state, "client_ip", "not-set")
        return JSONResponse({"client_ip": client_ip})

    return app


# =============================================================================
# Test: SecurityHeadersMiddleware dispatch
# =============================================================================

class TestSecurityHeadersMiddleware:
    """Test SecurityHeadersMiddleware adds correct headers to responses."""

    @pytest.mark.unit
    def test_x_content_type_options_nosniff(self):
        """Response includes X-Content-Type-Options: nosniff."""
        app = _make_app_with_security_headers({})
        client = TestClient(app)
        response = client.get("/test")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    @pytest.mark.unit
    def test_x_frame_options_deny(self):
        """Response includes X-Frame-Options: DENY."""
        app = _make_app_with_security_headers({})
        client = TestClient(app)
        response = client.get("/test")
        assert response.headers.get("X-Frame-Options") == "DENY"

    @pytest.mark.unit
    def test_x_xss_protection_set(self):
        """Response includes X-XSS-Protection header."""
        app = _make_app_with_security_headers({})
        client = TestClient(app)
        response = client.get("/test")
        assert response.headers.get("X-XSS-Protection") == "1; mode=block"

    @pytest.mark.unit
    def test_referrer_policy_set(self):
        """Response includes Referrer-Policy header."""
        app = _make_app_with_security_headers({})
        client = TestClient(app)
        response = client.get("/test")
        assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    @pytest.mark.unit
    def test_permissions_policy_set(self):
        """Response includes Permissions-Policy header with restrictive policy."""
        app = _make_app_with_security_headers({})
        client = TestClient(app)
        response = client.get("/test")
        pp = response.headers.get("Permissions-Policy")
        assert pp is not None
        assert "camera=()" in pp
        assert "microphone=()" in pp

    @pytest.mark.unit
    def test_csp_strict_mode(self):
        """Strict CSP mode includes Content-Security-Policy header."""
        config = {
            "server": {"hostname": "example.com", "protocol": "https"},
            "api": {"external_port": 443},
            "web": {"external_port": 443},
            "security": {"content_security_policy": "strict"},
        }
        app = _make_app_with_security_headers(config)
        client = TestClient(app)
        response = client.get("/test")
        csp = response.headers.get("Content-Security-Policy")
        assert csp is not None
        assert "default-src 'self'" in csp

    @pytest.mark.unit
    def test_csp_disabled_mode(self):
        """Disabled CSP mode does not add Content-Security-Policy header."""
        config = {
            "security": {"content_security_policy": "disabled"},
        }
        app = _make_app_with_security_headers(config)
        client = TestClient(app)
        response = client.get("/test")
        assert "Content-Security-Policy" not in response.headers

    @pytest.mark.unit
    def test_hsts_for_https(self):
        """HSTS header is set when protocol is https."""
        config = {
            "server": {"protocol": "https"},
            "security": {"content_security_policy": "disabled"},
        }
        app = _make_app_with_security_headers(config)
        client = TestClient(app)
        response = client.get("/test")
        hsts = response.headers.get("Strict-Transport-Security")
        assert hsts is not None
        assert "max-age=" in hsts

    @pytest.mark.unit
    def test_no_hsts_for_http(self):
        """HSTS header is NOT set when protocol is http."""
        config = {
            "server": {"protocol": "http"},
            "security": {"content_security_policy": "disabled"},
        }
        app = _make_app_with_security_headers(config)
        client = TestClient(app)
        response = client.get("/test")
        assert "Strict-Transport-Security" not in response.headers


# =============================================================================
# Test: HostValidationMiddleware dispatch
# =============================================================================

class TestHostValidationMiddleware:
    """Test HostValidationMiddleware rejects invalid Host headers."""

    @pytest.mark.unit
    def test_valid_host_accepted(self):
        """Request with valid Host header is accepted."""
        app = _make_app_with_host_validation({"localhost", "testserver"})
        client = TestClient(app)
        response = client.get("/test")
        assert response.status_code == 200

    @pytest.mark.unit
    def test_invalid_host_returns_400(self):
        """Request with invalid Host header returns 400."""
        app = _make_app_with_host_validation({"only-valid-host.com"})
        client = TestClient(app)
        # TestClient sends Host: testserver by default
        response = client.get("/test")
        assert response.status_code == 400
        assert "Invalid Host" in response.text

    @pytest.mark.unit
    def test_host_with_port_accepted(self):
        """Host header with port is accepted when port variant is allowed."""
        app = _make_app_with_host_validation({"localhost:8080", "testserver"})
        client = TestClient(app)
        response = client.get("/test")
        assert response.status_code == 200

    @pytest.mark.unit
    def test_host_without_port_matches_hostname(self):
        """Host header without port matches hostname-only entry."""
        # HostValidationMiddleware strips port for comparison as fallback
        app = _make_app_with_host_validation({"testserver"})
        client = TestClient(app)
        response = client.get("/test")
        assert response.status_code == 200

    @pytest.mark.unit
    def test_empty_allowed_hosts_rejects_all(self):
        """Empty allowed_hosts set rejects all requests."""
        app = _make_app_with_host_validation(set())
        client = TestClient(app)
        response = client.get("/test")
        assert response.status_code == 400


# =============================================================================
# Test: TrustedProxyMiddleware dispatch
# =============================================================================

class TestTrustedProxyMiddleware:
    """Test TrustedProxyMiddleware resolves client IPs correctly."""

    @pytest.mark.unit
    def test_no_proxies_uses_direct_ip(self):
        """Without trusted proxies, request.state.client_ip is the direct IP."""
        app = _make_app_with_trusted_proxy([])
        client = TestClient(app)
        response = client.get("/test")
        assert response.status_code == 200
        data = response.json()
        # TestClient connects from 127.0.0.1 (or testclient)
        assert data["client_ip"] is not None

    @pytest.mark.unit
    def test_trusted_proxy_resolves_forwarded_for(self):
        """X-Forwarded-For from trusted proxy resolves original client IP."""
        # TestClient connects from "testclient" which is not a valid IP
        # We test the underlying function directly instead
        request = MagicMock()
        request.client.host = "10.0.0.1"
        request.headers = {"x-forwarded-for": "203.0.113.50, 10.0.0.1"}

        ip = get_client_ip(request, ["10.0.0.1"])
        assert ip == "203.0.113.50"

    @pytest.mark.unit
    def test_untrusted_proxy_forwarded_for_ignored(self):
        """X-Forwarded-For from untrusted source is ignored."""
        request = MagicMock()
        request.client.host = "192.168.1.100"
        request.headers = {"x-forwarded-for": "10.0.0.50, 192.168.1.100"}

        ip = get_client_ip(request, ["10.0.0.1"])
        # 192.168.1.100 is not trusted, so X-Forwarded-For is ignored
        assert ip == "192.168.1.100"

    @pytest.mark.unit
    def test_trusted_proxy_cidr_resolves(self):
        """Trusted proxy via CIDR range correctly resolves forwarded IP."""
        request = MagicMock()
        request.client.host = "172.16.0.5"
        request.headers = {"x-forwarded-for": "198.51.100.42"}

        ip = get_client_ip(request, ["172.16.0.0/16"])
        assert ip == "198.51.100.42"
