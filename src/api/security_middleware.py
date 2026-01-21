"""
Security middleware for Ag3ntum API.

Provides:
- Security headers (X-Content-Type-Options, X-Frame-Options, etc.)
- Host header validation (prevents host header injection attacks)
- Content Security Policy
- CORS origin derivation from configuration
"""
import ipaddress
import logging
from typing import Callable, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

logger = logging.getLogger(__name__)


def build_allowed_origins(config: dict) -> list[str]:
    """
    Build CORS allowed origins from server configuration.

    Derives origins from server.hostname and server.protocol.
    Always includes localhost variants for development.

    Args:
        config: Full configuration dict with 'server', 'api', 'web' sections.

    Returns:
        List of allowed origins for CORS.
    """
    server = config.get("server", {})
    api = config.get("api", {})
    web = config.get("web", {})
    security = config.get("security", {})

    hostname = server.get("hostname", "localhost")
    protocol = server.get("protocol", "http")
    api_port = api.get("external_port", 40080)
    web_port = web.get("external_port", 50080)

    origins = set()

    # Primary origin from config
    origins.add(f"{protocol}://{hostname}:{web_port}")

    # If using standard ports, also allow without port
    if (protocol == "http" and web_port == 80) or (protocol == "https" and web_port == 443):
        origins.add(f"{protocol}://{hostname}")

    # Always allow localhost variants for development/debugging
    if hostname != "localhost":
        origins.add(f"http://localhost:{web_port}")
        origins.add(f"http://127.0.0.1:{web_port}")

    # Add additional allowed hosts from security config
    additional_hosts = security.get("additional_allowed_hosts", [])
    for host in additional_hosts:
        origins.add(f"{protocol}://{host}:{web_port}")
        origins.add(f"http://{host}:{web_port}")

    logger.info(f"CORS allowed origins: {sorted(origins)}")
    return list(origins)


def build_allowed_hosts(config: dict) -> set[str]:
    """
    Build set of allowed Host header values.

    Args:
        config: Full configuration dict.

    Returns:
        Set of allowed host values (hostname:port format).
    """
    server = config.get("server", {})
    api = config.get("api", {})
    security = config.get("security", {})

    hostname = server.get("hostname", "localhost")
    api_port = api.get("external_port", 40080)

    allowed = set()

    # Primary hostname with and without port
    allowed.add(hostname)
    allowed.add(f"{hostname}:{api_port}")

    # Always allow localhost for internal health checks, etc.
    allowed.add("localhost")
    allowed.add(f"localhost:{api_port}")
    allowed.add("127.0.0.1")
    allowed.add(f"127.0.0.1:{api_port}")

    # Add additional allowed hosts
    additional = security.get("additional_allowed_hosts", [])
    for host in additional:
        allowed.add(host)
        allowed.add(f"{host}:{api_port}")

    return allowed


def is_trusted_proxy(client_ip: str, trusted_proxies: list[str]) -> bool:
    """
    Check if client IP is from a trusted proxy.

    Args:
        client_ip: Client IP address string.
        trusted_proxies: List of trusted IP/CIDR ranges.

    Returns:
        True if client is a trusted proxy.
    """
    if not trusted_proxies:
        return False

    try:
        client = ipaddress.ip_address(client_ip)
        for proxy in trusted_proxies:
            try:
                if "/" in proxy:
                    # CIDR notation
                    if client in ipaddress.ip_network(proxy, strict=False):
                        return True
                else:
                    # Single IP
                    if client == ipaddress.ip_address(proxy):
                        return True
            except ValueError:
                logger.warning(f"Invalid trusted proxy format: {proxy}")
                continue
    except ValueError:
        logger.warning(f"Invalid client IP: {client_ip}")
        return False

    return False


def get_client_ip(request: Request, trusted_proxies: list[str]) -> str:
    """
    Get real client IP, considering trusted proxies.

    Args:
        request: FastAPI request.
        trusted_proxies: List of trusted proxy IPs/CIDRs.

    Returns:
        Client IP address.
    """
    client_ip = request.client.host if request.client else "unknown"

    if trusted_proxies and is_trusted_proxy(client_ip, trusted_proxies):
        # Trust X-Forwarded-For from trusted proxy
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Take first IP (original client)
            client_ip = forwarded.split(",")[0].strip()

    return client_ip


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware that adds security headers to all responses.

    Headers added:
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - X-XSS-Protection: 1; mode=block
    - Referrer-Policy: strict-origin-when-cross-origin
    - Permissions-Policy: (restrictive policy)
    - Content-Security-Policy: (if enabled)
    - Strict-Transport-Security: (if HTTPS)
    """

    def __init__(self, app, config: dict):
        super().__init__(app)
        self.config = config
        self.server = config.get("server", {})
        self.security = config.get("security", {})
        self.protocol = self.server.get("protocol", "http")
        self.csp_mode = self.security.get("content_security_policy", "strict")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # Core security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions Policy - restrict dangerous APIs
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()"
        )

        # HSTS for HTTPS
        if self.protocol == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        # Content Security Policy
        if self.csp_mode == "strict":
            hostname = self.server.get("hostname", "localhost")
            api_port = self.config.get("api", {}).get("external_port", 40080)
            web_port = self.config.get("web", {}).get("external_port", 50080)

            # Build CSP that allows the app to function
            csp_parts = [
                "default-src 'self'",
                f"connect-src 'self' {self.protocol}://{hostname}:{api_port} ws://{hostname}:{api_port} wss://{hostname}:{api_port}",
                "img-src 'self' data: blob:",
                "style-src 'self' 'unsafe-inline'",  # Needed for React/styled-components
                "script-src 'self'",
                "font-src 'self'",
                "frame-ancestors 'none'",
                "base-uri 'self'",
                "form-action 'self'",
            ]
            response.headers["Content-Security-Policy"] = "; ".join(csp_parts)

        elif self.csp_mode == "relaxed":
            # Development mode - allows inline scripts
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "connect-src 'self' http: https: ws: wss:; "
                "img-src 'self' data: blob:; "
                "style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "font-src 'self'; "
                "frame-ancestors 'none'"
            )
        # else: disabled - no CSP header

        return response


class HostValidationMiddleware(BaseHTTPMiddleware):
    """
    Middleware that validates the Host header.

    Prevents host header injection attacks by rejecting requests
    with unexpected Host values.
    """

    def __init__(self, app, allowed_hosts: set[str]):
        super().__init__(app)
        self.allowed_hosts = allowed_hosts
        logger.info(f"Host validation enabled for: {sorted(allowed_hosts)}")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        host = request.headers.get("host", "")

        # Remove port for comparison if needed
        host_without_port = host.split(":")[0] if ":" in host else host

        # Check if host is allowed
        if host not in self.allowed_hosts and host_without_port not in self.allowed_hosts:
            logger.warning(f"Rejected request with invalid Host header: {host}")
            return PlainTextResponse(
                "Invalid Host header",
                status_code=400
            )

        return await call_next(request)


class TrustedProxyMiddleware(BaseHTTPMiddleware):
    """
    Middleware that handles trusted proxy headers.

    When behind a trusted proxy:
    - Uses X-Forwarded-For for client IP
    - Uses X-Forwarded-Proto for protocol detection
    """

    def __init__(self, app, trusted_proxies: list[str]):
        super().__init__(app)
        self.trusted_proxies = trusted_proxies
        if trusted_proxies:
            logger.info(f"Trusted proxies configured: {trusted_proxies}")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if self.trusted_proxies:
            client_ip = request.client.host if request.client else None

            if client_ip and is_trusted_proxy(client_ip, self.trusted_proxies):
                # Store real client IP in request state
                forwarded = request.headers.get("x-forwarded-for")
                if forwarded:
                    request.state.client_ip = forwarded.split(",")[0].strip()
                else:
                    request.state.client_ip = client_ip
            else:
                request.state.client_ip = client_ip
        else:
            request.state.client_ip = request.client.host if request.client else None

        return await call_next(request)
