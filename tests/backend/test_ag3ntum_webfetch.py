"""
Tests for Ag3ntumWebFetch tool.

Tests the WebFetch tool functionality:
- Private IP detection (SSRF prevention)
- URL security validation (protocol, port, domain blocking)
- HTML to Markdown conversion
- Response size limiting
- Redirect handling and validation
- Error response formatting
- Tool creation and configuration

Note: create_webfetch_tool() returns an SdkMcpTool dataclass, not a raw
callable.  The actual async handler is in .handler — tests that exercise
the tool at runtime call tool.handler(args).
"""
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tools.ag3ntum.ag3ntum_webfetch.tool import (
    AG3NTUM_WEBFETCH_TOOL,
    BLOCKED_PORTS,
    DEFAULT_BLOCKED_DOMAINS,
    DEFAULT_MAX_SIZE,
    DEFAULT_TIMEOUT,
    PRIVATE_IP_RANGES,
    _error,
    _format_headers,
    _html_to_markdown,
    _is_private_ip,
    _result,
    _validate_url_security,
    create_webfetch_tool,
)


def _get_handler(**kwargs):
    """Create a webfetch tool and return only its async handler."""
    sdk_tool = create_webfetch_tool(**kwargs)
    return sdk_tool.handler


# ---------------------------------------------------------------------------
# TestToolConstants
# ---------------------------------------------------------------------------

class TestToolConstants:
    """Tests for tool constants and configuration defaults."""

    @pytest.mark.unit
    def test_tool_name_constant(self):
        """Tool name constant matches the expected MCP naming pattern."""
        assert AG3NTUM_WEBFETCH_TOOL == "mcp__ag3ntum__WebFetch"

    @pytest.mark.unit
    def test_default_timeout(self):
        """Default timeout is 30 seconds."""
        assert DEFAULT_TIMEOUT == 30

    @pytest.mark.unit
    def test_default_max_size(self):
        """Default max response size is 10MB."""
        assert DEFAULT_MAX_SIZE == 10 * 1024 * 1024

    @pytest.mark.unit
    def test_private_ip_ranges_cover_rfc1918(self):
        """Private IP ranges include all three RFC 1918 ranges."""
        range_strs = set(PRIVATE_IP_RANGES)
        assert "10.0.0.0/8" in range_strs
        assert "172.16.0.0/12" in range_strs
        assert "192.168.0.0/16" in range_strs

    @pytest.mark.unit
    def test_private_ip_ranges_cover_loopback(self):
        """Private IP ranges include loopback."""
        assert "127.0.0.0/8" in PRIVATE_IP_RANGES

    @pytest.mark.unit
    def test_private_ip_ranges_cover_link_local(self):
        """Private IP ranges include link-local."""
        assert "169.254.0.0/16" in PRIVATE_IP_RANGES

    @pytest.mark.unit
    def test_private_ip_ranges_cover_ipv6(self):
        """Private IP ranges include IPv6 loopback, link-local, and private."""
        range_strs = set(PRIVATE_IP_RANGES)
        assert "::1/128" in range_strs
        assert "fe80::/10" in range_strs
        assert "fc00::/7" in range_strs

    @pytest.mark.unit
    def test_blocked_domains_include_metadata_endpoints(self):
        """Default blocked domains include cloud metadata endpoints."""
        assert "169.254.169.254" in DEFAULT_BLOCKED_DOMAINS
        assert "metadata.google.internal" in DEFAULT_BLOCKED_DOMAINS

    @pytest.mark.unit
    def test_blocked_ports_include_ssh(self):
        """Blocked ports include SSH (22)."""
        assert 22 in BLOCKED_PORTS

    @pytest.mark.unit
    def test_blocked_ports_include_smtp(self):
        """Blocked ports include SMTP (25)."""
        assert 25 in BLOCKED_PORTS

    @pytest.mark.unit
    def test_blocked_ports_include_ftp(self):
        """Blocked ports include FTP (21)."""
        assert 21 in BLOCKED_PORTS


# ---------------------------------------------------------------------------
# TestPrivateIPDetection
# ---------------------------------------------------------------------------

class TestPrivateIPDetection:
    """Tests for _is_private_ip() — core SSRF prevention."""

    # --- RFC 1918: 10.0.0.0/8 ---

    @pytest.mark.unit
    @pytest.mark.parametrize("ip", [
        "10.0.0.0",
        "10.0.0.1",
        "10.255.255.255",
        "10.128.0.1",
    ])
    def test_rfc1918_10_range_blocked(self, ip):
        """10.0.0.0/8 addresses are detected as private."""
        assert _is_private_ip(ip) is True

    # --- RFC 1918: 172.16.0.0/12 ---

    @pytest.mark.unit
    @pytest.mark.parametrize("ip", [
        "172.16.0.0",
        "172.16.0.1",
        "172.31.255.255",
        "172.20.10.5",
    ])
    def test_rfc1918_172_range_blocked(self, ip):
        """172.16.0.0/12 addresses are detected as private."""
        assert _is_private_ip(ip) is True

    @pytest.mark.unit
    def test_172_15_is_public(self):
        """172.15.x.x is outside the /12 range and should be public."""
        assert _is_private_ip("172.15.255.255") is False

    @pytest.mark.unit
    def test_172_32_is_public(self):
        """172.32.x.x is outside the /12 range and should be public."""
        assert _is_private_ip("172.32.0.1") is False

    # --- RFC 1918: 192.168.0.0/16 ---

    @pytest.mark.unit
    @pytest.mark.parametrize("ip", [
        "192.168.0.0",
        "192.168.0.1",
        "192.168.1.1",
        "192.168.255.255",
    ])
    def test_rfc1918_192_168_range_blocked(self, ip):
        """192.168.0.0/16 addresses are detected as private."""
        assert _is_private_ip(ip) is True

    # --- Loopback ---

    @pytest.mark.unit
    @pytest.mark.parametrize("ip", [
        "127.0.0.1",
        "127.0.0.0",
        "127.255.255.255",
        "127.0.0.2",
    ])
    def test_loopback_range_blocked(self, ip):
        """127.0.0.0/8 (loopback) addresses are detected as private."""
        assert _is_private_ip(ip) is True

    # --- Link-local ---

    @pytest.mark.unit
    @pytest.mark.parametrize("ip", [
        "169.254.0.0",
        "169.254.169.254",  # AWS metadata
        "169.254.255.255",
        "169.254.1.1",
    ])
    def test_link_local_range_blocked(self, ip):
        """169.254.0.0/16 (link-local) addresses are detected as private."""
        assert _is_private_ip(ip) is True

    # --- Cloud metadata endpoint ---

    @pytest.mark.unit
    def test_aws_metadata_ip_blocked(self):
        """AWS metadata endpoint 169.254.169.254 is detected as private."""
        assert _is_private_ip("169.254.169.254") is True

    # --- IPv6 private ---

    @pytest.mark.unit
    def test_ipv6_loopback_blocked(self):
        """IPv6 loopback ::1 is detected as private."""
        assert _is_private_ip("::1") is True

    @pytest.mark.unit
    @pytest.mark.parametrize("ip", [
        "fc00::1",
        "fd00::1",
        "fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff",
    ])
    def test_ipv6_unique_local_blocked(self, ip):
        """IPv6 unique local addresses (fc00::/7) are detected as private."""
        assert _is_private_ip(ip) is True

    @pytest.mark.unit
    @pytest.mark.parametrize("ip", [
        "fe80::1",
        "fe80::ffff:ffff:ffff:ffff",
    ])
    def test_ipv6_link_local_blocked(self, ip):
        """IPv6 link-local addresses (fe80::/10) are detected as private."""
        assert _is_private_ip(ip) is True

    @pytest.mark.unit
    @pytest.mark.parametrize("ip", [
        "ff02::1",
        "ff00::1",
    ])
    def test_ipv6_multicast_blocked(self, ip):
        """IPv6 multicast addresses (ff00::/8) are detected as private."""
        assert _is_private_ip(ip) is True

    # --- Public IPs should pass ---

    @pytest.mark.unit
    @pytest.mark.parametrize("ip", [
        "8.8.8.8",
        "1.1.1.1",
        "208.67.222.222",
        "93.184.216.34",
        "151.101.1.69",
    ])
    def test_public_ipv4_not_private(self, ip):
        """Public IPv4 addresses are not detected as private."""
        assert _is_private_ip(ip) is False

    @pytest.mark.unit
    @pytest.mark.parametrize("ip", [
        "2607:f8b0:4004:800::200e",  # Google
        "2606:4700:4700::1111",  # Cloudflare
    ])
    def test_public_ipv6_not_private(self, ip):
        """Public IPv6 addresses are not detected as private."""
        assert _is_private_ip(ip) is False

    # --- Invalid input ---

    @pytest.mark.unit
    def test_invalid_ip_returns_false(self):
        """Invalid IP string returns False (not private), not an exception."""
        assert _is_private_ip("not-an-ip") is False

    @pytest.mark.unit
    def test_empty_string_returns_false(self):
        """Empty string returns False."""
        assert _is_private_ip("") is False

    @pytest.mark.unit
    def test_hostname_returns_false(self):
        """A hostname (not IP) returns False."""
        assert _is_private_ip("example.com") is False


# ---------------------------------------------------------------------------
# TestURLSecurityValidation
# ---------------------------------------------------------------------------

class TestURLSecurityValidation:
    """Tests for _validate_url_security() — URL-level security checks."""

    # --- Protocol validation ---

    @pytest.mark.unit
    def test_https_allowed(self):
        """HTTPS URLs pass protocol validation."""
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]):
            valid, msg = _validate_url_security("https://example.com", [], None)
        assert valid is True
        assert msg == ""

    @pytest.mark.unit
    def test_http_allowed(self):
        """HTTP URLs pass protocol validation."""
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 80))]):
            valid, msg = _validate_url_security("http://example.com", [], None)
        assert valid is True

    @pytest.mark.unit
    @pytest.mark.parametrize("url,scheme", [
        ("ftp://example.com/file.txt", "ftp"),
        ("file:///etc/passwd", "file"),
        ("gopher://evil.com/", "gopher"),
        ("data:text/html,<h1>hi</h1>", "data"),
        ("javascript:alert(1)", "javascript"),
    ])
    def test_non_http_protocols_blocked(self, url, scheme):
        """Non-HTTP/HTTPS protocols are blocked."""
        valid, msg = _validate_url_security(url, [], None)
        assert valid is False
        assert "protocol" in msg.lower() or "Invalid" in msg

    @pytest.mark.unit
    def test_empty_scheme_blocked(self):
        """URL without a scheme is blocked."""
        valid, msg = _validate_url_security("example.com", [], None)
        assert valid is False

    # --- Missing hostname ---

    @pytest.mark.unit
    def test_missing_hostname_blocked(self):
        """URL without hostname is blocked."""
        valid, msg = _validate_url_security("http://", [], None)
        assert valid is False
        assert "hostname" in msg.lower() or "netloc" in msg.lower() or "missing" in msg.lower()

    # --- Direct private IP in URL ---

    @pytest.mark.unit
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/admin",
        "http://10.0.0.1/internal",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",
    ])
    def test_direct_private_ip_blocked(self, url):
        """URLs with private IP addresses as host are blocked."""
        valid, msg = _validate_url_security(url, [], None)
        assert valid is False
        assert "private" in msg.lower() or "blocked" in msg.lower()

    @pytest.mark.unit
    def test_ipv6_loopback_in_url_blocked(self):
        """IPv6 loopback address in URL is blocked."""
        valid, msg = _validate_url_security("http://[::1]/", [], None)
        assert valid is False
        assert "private" in msg.lower()

    # --- Blocked ports ---

    @pytest.mark.unit
    @pytest.mark.parametrize("port", [22, 25, 21, 23, 6667])
    def test_blocked_ports_rejected(self, port):
        """URLs targeting blocked ports are rejected."""
        url = f"https://example.com:{port}/path"
        valid, msg = _validate_url_security(url, [], None)
        assert valid is False
        assert "port" in msg.lower()
        assert str(port) in msg

    @pytest.mark.unit
    def test_standard_https_port_allowed(self):
        """Standard HTTPS port 443 is allowed."""
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]):
            valid, msg = _validate_url_security("https://example.com:443/", [], None)
        assert valid is True

    @pytest.mark.unit
    def test_port_8080_allowed(self):
        """Non-blocked port 8080 is allowed."""
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 8080))]):
            valid, msg = _validate_url_security("https://example.com:8080/", [], None)
        assert valid is True

    # --- Domain blocking ---

    @pytest.mark.unit
    def test_blocked_domain_rejected(self):
        """Explicitly blocked domain is rejected."""
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]):
            valid, msg = _validate_url_security(
                "https://evil.com/data", ["evil.com"], None
            )
        assert valid is False
        assert "blocked" in msg.lower()

    @pytest.mark.unit
    def test_blocked_subdomain_rejected(self):
        """Subdomain of a blocked domain is also rejected."""
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]):
            valid, msg = _validate_url_security(
                "https://api.evil.com/data", ["evil.com"], None
            )
        assert valid is False
        assert "blocked" in msg.lower()

    @pytest.mark.unit
    def test_default_blocked_localhost(self):
        """Default blocked domains include localhost.

        Patch DNS so we hit the domain blocklist specifically, not
        the DNS-rebinding check (localhost may resolve to ::1).
        """
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]):
            valid, msg = _validate_url_security(
                "https://localhost/admin", DEFAULT_BLOCKED_DOMAINS, None
            )
        assert valid is False
        assert "blocked" in msg.lower()

    @pytest.mark.unit
    def test_default_blocked_metadata(self):
        """Default blocked domains include cloud metadata IPs."""
        # 169.254.169.254 is both a private IP and a blocked domain
        valid, msg = _validate_url_security(
            "http://169.254.169.254/latest/meta-data/",
            DEFAULT_BLOCKED_DOMAINS, None
        )
        assert valid is False

    # --- Allowlist mode ---

    @pytest.mark.unit
    def test_allowlist_permits_listed_domain(self):
        """In allowlist mode, listed domains are permitted."""
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]):
            valid, msg = _validate_url_security(
                "https://allowed.com/page",
                [],
                ["allowed.com"]
            )
        assert valid is True

    @pytest.mark.unit
    def test_allowlist_permits_subdomain(self):
        """In allowlist mode, subdomains of allowed domains are permitted."""
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]):
            valid, msg = _validate_url_security(
                "https://sub.allowed.com/page",
                [],
                ["allowed.com"]
            )
        assert valid is True

    @pytest.mark.unit
    def test_allowlist_blocks_unlisted_domain(self):
        """In allowlist mode, domains not in the list are blocked."""
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]):
            valid, msg = _validate_url_security(
                "https://notallowed.com/page",
                [],
                ["allowed.com"]
            )
        assert valid is False
        assert "allowlist" in msg.lower()

    # --- DNS rebinding protection ---

    @pytest.mark.unit
    def test_dns_resolving_to_private_ip_blocked(self):
        """Domain that resolves to a private IP is blocked (DNS rebinding)."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, '', ("10.0.0.1", 80))
        ]
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=fake_addrinfo):
            valid, msg = _validate_url_security(
                "http://evil-rebind.com/", [], None
            )
        assert valid is False
        assert "private" in msg.lower()
        assert "resolves" in msg.lower() or "10.0.0.1" in msg

    @pytest.mark.unit
    def test_dns_resolving_to_loopback_blocked(self):
        """Domain that resolves to 127.0.0.1 is blocked."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, '', ("127.0.0.1", 80))
        ]
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=fake_addrinfo):
            valid, msg = _validate_url_security(
                "http://evil-localhost.com/", [], None
            )
        assert valid is False
        assert "private" in msg.lower()

    @pytest.mark.unit
    def test_dns_resolving_to_metadata_blocked(self):
        """Domain that resolves to 169.254.169.254 is blocked."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, '', ("169.254.169.254", 80))
        ]
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=fake_addrinfo):
            valid, msg = _validate_url_security(
                "http://metadata-steal.com/", [], None
            )
        assert valid is False

    @pytest.mark.unit
    def test_dns_resolving_to_public_ip_allowed(self):
        """Domain resolving to a public IP is allowed."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))
        ]
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=fake_addrinfo):
            valid, msg = _validate_url_security(
                "https://example.com/", [], None
            )
        assert valid is True

    @pytest.mark.unit
    def test_dns_failure_does_not_block(self):
        """DNS resolution failure does not block the request (httpx will fail later)."""
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    side_effect=socket.gaierror("Name resolution failed")):
            valid, msg = _validate_url_security(
                "https://nonexistent-host-abc123.example/", [], None
            )
        assert valid is True

    @pytest.mark.unit
    def test_multiple_dns_results_all_checked(self):
        """If DNS returns multiple IPs and any is private, request is blocked."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, '', ("10.0.0.1", 80)),
        ]
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=fake_addrinfo):
            valid, msg = _validate_url_security(
                "http://dual-homed.com/", [], None
            )
        assert valid is False
        assert "private" in msg.lower()


# ---------------------------------------------------------------------------
# TestHTMLToMarkdown
# ---------------------------------------------------------------------------

class TestHTMLToMarkdown:
    """Tests for _html_to_markdown() conversion."""

    # --- Basic conversions ---

    @pytest.mark.unit
    def test_headings_converted(self):
        """HTML headings are converted to Markdown headings."""
        html = "<h1>Title</h1><h2>Subtitle</h2><h3>Section</h3>"
        md = _html_to_markdown(html)
        assert "# Title" in md
        assert "## Subtitle" in md
        assert "### Section" in md

    @pytest.mark.unit
    def test_bold_converted(self):
        """<strong> and <b> are converted to **bold**."""
        html = "<strong>bold text</strong> and <b>also bold</b>"
        md = _html_to_markdown(html)
        assert "**bold text**" in md
        assert "**also bold**" in md

    @pytest.mark.unit
    def test_italic_converted(self):
        """<em> and <i> are converted to *italic*."""
        html = "<em>italic text</em> and <i>also italic</i>"
        md = _html_to_markdown(html)
        assert "*italic text*" in md
        assert "*also italic*" in md

    @pytest.mark.unit
    def test_inline_code_converted(self):
        """<code> is converted to `backtick` code."""
        html = "Use <code>print()</code> function"
        md = _html_to_markdown(html)
        assert "`print()`" in md

    @pytest.mark.unit
    def test_code_block_converted(self):
        """<pre> is converted to fenced code block."""
        html = "<pre>def hello():\n    pass</pre>"
        md = _html_to_markdown(html)
        assert "```" in md
        assert "def hello():" in md

    @pytest.mark.unit
    def test_links_converted(self):
        """<a href> is converted to [text](url) format."""
        html = '<a href="https://example.com">Example</a>'
        md = _html_to_markdown(html)
        assert "[Example](https://example.com)" in md

    @pytest.mark.unit
    def test_images_converted(self):
        """<img> is converted to ![alt](src) format."""
        html = '<img src="photo.jpg" alt="A photo">'
        md = _html_to_markdown(html)
        assert "![A photo](photo.jpg)" in md

    @pytest.mark.unit
    def test_list_items_converted(self):
        """<li> elements are converted to Markdown list items."""
        html = "<ul><li>First</li><li>Second</li></ul>"
        md = _html_to_markdown(html)
        assert "- First" in md
        assert "- Second" in md

    @pytest.mark.unit
    def test_horizontal_rule_converted(self):
        """<hr> is converted to --- in Markdown."""
        html = "<p>Before</p><hr><p>After</p>"
        md = _html_to_markdown(html)
        assert "---" in md

    @pytest.mark.unit
    def test_blockquote_converted(self):
        """<blockquote> is converted to > prefix."""
        html = "<blockquote>Quote text</blockquote>"
        md = _html_to_markdown(html)
        assert "> Quote text" in md

    @pytest.mark.unit
    def test_br_converted_to_newline(self):
        """<br> tags are converted to newlines."""
        html = "Line 1<br>Line 2<br/>Line 3"
        md = _html_to_markdown(html)
        assert "Line 1" in md
        assert "Line 2" in md
        assert "Line 3" in md

    # --- Script/style stripping ---

    @pytest.mark.unit
    def test_script_tags_removed(self):
        """<script> tags and their content are completely removed."""
        html = "<p>Safe</p><script>alert('xss')</script><p>Also safe</p>"
        md = _html_to_markdown(html)
        assert "alert" not in md
        assert "script" not in md.lower()
        assert "Safe" in md
        assert "Also safe" in md

    @pytest.mark.unit
    def test_style_tags_removed(self):
        """<style> tags and their content are completely removed."""
        html = "<style>body{color:red}</style><p>Content</p>"
        md = _html_to_markdown(html)
        assert "color:red" not in md
        assert "Content" in md

    # --- XSS payload stripping ---

    @pytest.mark.unit
    def test_xss_script_injection_stripped(self):
        """Script-based XSS payloads are stripped."""
        html = '<img src=x onerror="alert(1)"><script>document.cookie</script>'
        md = _html_to_markdown(html)
        assert "onerror" not in md
        assert "alert" not in md
        assert "document.cookie" not in md

    @pytest.mark.unit
    def test_xss_event_handler_attributes_stripped(self):
        """HTML tags with event handlers are stripped to just content."""
        html = '<div onmouseover="steal()">Content</div>'
        md = _html_to_markdown(html)
        assert "onmouseover" not in md
        assert "steal" not in md
        assert "Content" in md

    @pytest.mark.unit
    def test_xss_nested_script_stripped(self):
        """Nested/obfuscated script tags are stripped."""
        html = "<p>Text</p><script type='text/javascript'>var x=1;</script><p>More</p>"
        md = _html_to_markdown(html)
        assert "var x" not in md
        assert "Text" in md
        assert "More" in md

    # --- HTML entity decoding ---

    @pytest.mark.unit
    def test_html_entities_decoded(self):
        """Common HTML entities are decoded to plain text."""
        html = "5 &lt; 10 &amp; 10 &gt; 5 &quot;hello&quot; &#39;world&#39;"
        md = _html_to_markdown(html)
        assert "5 < 10 & 10 > 5" in md
        assert '"hello"' in md
        assert "'world'" in md

    @pytest.mark.unit
    def test_nbsp_converted_to_space(self):
        """&nbsp; is converted to a regular space."""
        html = "Hello&nbsp;World"
        md = _html_to_markdown(html)
        assert "Hello World" in md

    # --- Edge cases ---

    @pytest.mark.unit
    def test_empty_string_input(self):
        """Empty string input returns empty string."""
        result = _html_to_markdown("")
        assert result == ""

    @pytest.mark.unit
    def test_plain_text_passthrough(self):
        """Plain text with no HTML tags passes through unchanged."""
        text = "Just plain text with no tags"
        result = _html_to_markdown(text)
        assert "Just plain text with no tags" in result

    @pytest.mark.unit
    def test_malformed_html_handled(self):
        """Malformed HTML does not raise an exception."""
        html = "<p>Unclosed paragraph<div>Mixed<b>nesting</p></div>"
        result = _html_to_markdown(html)
        # Should not raise; content should be extractable
        assert "Unclosed paragraph" in result

    @pytest.mark.unit
    def test_multiple_blank_lines_collapsed(self):
        """Multiple consecutive blank lines are collapsed to max 2."""
        html = "<p>A</p>\n\n\n\n\n<p>B</p>"
        md = _html_to_markdown(html)
        # Should not have more than 2 consecutive newlines
        assert "\n\n\n" not in md

    @pytest.mark.unit
    def test_remaining_html_tags_stripped(self):
        """Any unrecognized HTML tags are stripped, leaving their content."""
        html = "<span class='custom'>Some text</span><nav>Navigation</nav>"
        md = _html_to_markdown(html)
        assert "Some text" in md
        assert "Navigation" in md
        assert "<span" not in md
        assert "<nav" not in md

    @pytest.mark.unit
    def test_all_heading_levels(self):
        """All six heading levels (h1-h6) are converted correctly."""
        html = (
            "<h1>H1</h1><h2>H2</h2><h3>H3</h3>"
            "<h4>H4</h4><h5>H5</h5><h6>H6</h6>"
        )
        md = _html_to_markdown(html)
        assert "# H1" in md
        assert "## H2" in md
        assert "### H3" in md
        assert "#### H4" in md
        assert "##### H5" in md
        assert "###### H6" in md


# ---------------------------------------------------------------------------
# TestFormatHeaders
# ---------------------------------------------------------------------------

class TestFormatHeaders:
    """Tests for _format_headers() — header display formatting."""

    @pytest.mark.unit
    def test_basic_headers_formatted(self):
        """Headers are formatted as 'key: value' lines."""
        headers = httpx.Headers({"Content-Type": "text/html", "Server": "nginx"})
        result = _format_headers(headers)
        assert "content-type: text/html" in result
        assert "server: nginx" in result

    @pytest.mark.unit
    def test_authorization_header_redacted(self):
        """Authorization header value is redacted."""
        headers = httpx.Headers({"Authorization": "Bearer secret-token"})
        result = _format_headers(headers)
        assert "[REDACTED]" in result
        assert "secret-token" not in result

    @pytest.mark.unit
    def test_cookie_header_redacted(self):
        """Cookie header value is redacted."""
        headers = httpx.Headers({"Cookie": "session=abc123"})
        result = _format_headers(headers)
        assert "[REDACTED]" in result
        assert "abc123" not in result

    @pytest.mark.unit
    def test_set_cookie_header_redacted(self):
        """Set-Cookie header value is redacted."""
        headers = httpx.Headers({"Set-Cookie": "id=xyz; Path=/"})
        result = _format_headers(headers)
        assert "[REDACTED]" in result
        assert "xyz" not in result

    @pytest.mark.unit
    def test_empty_headers(self):
        """Empty headers produce empty string."""
        headers = httpx.Headers({})
        result = _format_headers(headers)
        assert result == ""


# ---------------------------------------------------------------------------
# TestResultAndErrorFormatting
# ---------------------------------------------------------------------------

class TestResultAndErrorFormatting:
    """Tests for _result() and _error() response formatting."""

    @pytest.mark.unit
    def test_result_structure(self):
        """_result() returns properly structured MCP content response."""
        res = _result("Hello")
        assert "content" in res
        assert len(res["content"]) == 1
        assert res["content"][0]["type"] == "text"
        assert res["content"][0]["text"] == "Hello"
        assert "is_error" not in res

    @pytest.mark.unit
    def test_error_structure(self):
        """_error() returns properly structured MCP error response."""
        res = _error("Something went wrong")
        assert "content" in res
        assert res["is_error"] is True
        assert "**Error:**" in res["content"][0]["text"]
        assert "Something went wrong" in res["content"][0]["text"]

    @pytest.mark.unit
    def test_error_uses_snake_case_is_error(self):
        """Error responses use snake_case 'is_error' (not camelCase 'isError')."""
        res = _error("test")
        assert "is_error" in res
        assert "isError" not in res


# ---------------------------------------------------------------------------
# TestWebFetchToolCreation
# ---------------------------------------------------------------------------

class TestWebFetchToolCreation:
    """Tests for create_webfetch_tool() configuration."""

    @pytest.mark.unit
    def test_create_with_defaults(self):
        """create_webfetch_tool() returns an SdkMcpTool with a callable handler."""
        sdk_tool = create_webfetch_tool()
        assert sdk_tool.name == "WebFetch"
        assert callable(sdk_tool.handler)

    @pytest.mark.unit
    def test_create_with_custom_blocked_domains(self):
        """create_webfetch_tool() accepts custom blocked domains."""
        sdk_tool = create_webfetch_tool(blocked_domains=["evil.com"])
        assert callable(sdk_tool.handler)

    @pytest.mark.unit
    def test_create_with_allowed_domains(self):
        """create_webfetch_tool() accepts an allowlist."""
        sdk_tool = create_webfetch_tool(allowed_domains=["safe.com"])
        assert callable(sdk_tool.handler)

    @pytest.mark.unit
    def test_create_with_custom_timeout(self):
        """create_webfetch_tool() accepts custom timeout."""
        sdk_tool = create_webfetch_tool(timeout=10)
        assert callable(sdk_tool.handler)

    @pytest.mark.unit
    def test_create_with_custom_max_size(self):
        """create_webfetch_tool() accepts custom max response size."""
        sdk_tool = create_webfetch_tool(max_response_size=1024)
        assert callable(sdk_tool.handler)

    @pytest.mark.unit
    def test_create_with_custom_max_redirects(self):
        """create_webfetch_tool() accepts custom max redirects."""
        sdk_tool = create_webfetch_tool(max_redirects=3)
        assert callable(sdk_tool.handler)


# ---------------------------------------------------------------------------
# TestWebFetchToolExecution
# ---------------------------------------------------------------------------

class TestWebFetchToolExecution:
    """Tests for the webfetch tool function execution (with mocked HTTP)."""

    @pytest.fixture
    def tool_fn(self):
        """Create a webfetch tool handler with defaults for testing."""
        return _get_handler()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_missing_url_returns_error(self, tool_fn):
        """Calling with empty url returns an error."""
        result = await tool_fn({"url": ""})
        assert result.get("is_error") is True
        assert "url is required" in result["content"][0]["text"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_invalid_output_mode_returns_error(self, tool_fn):
        """Invalid output_mode returns an error listing valid modes."""
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]):
            result = await tool_fn({"url": "https://example.com", "output_mode": "invalid"})
        assert result.get("is_error") is True
        assert "output_mode" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_blocked_url_returns_error(self, tool_fn):
        """URL targeting private IP returns security error before making request."""
        result = await tool_fn({"url": "http://127.0.0.1/admin"})
        assert result.get("is_error") is True
        assert "private" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_ftp_protocol_blocked(self, tool_fn):
        """FTP protocol is blocked before any request is made."""
        result = await tool_fn({"url": "ftp://evil.com/secret.txt"})
        assert result.get("is_error") is True
        assert "protocol" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_invalid_headers_json_returns_error(self, tool_fn):
        """Invalid JSON in headers string arg returns error."""
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]):
            result = await tool_fn({
                "url": "https://example.com",
                "headers": "not valid json"
            })
        assert result.get("is_error") is True
        assert "headers" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_successful_html_fetch(self, tool_fn):
        """Successful HTML fetch returns content with metadata."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.url = httpx.URL("https://example.com")
        mock_response.headers = httpx.Headers({"content-type": "text/html"})

        html_bytes = b"<html><body><h1>Hello</h1></body></html>"

        async def mock_aiter_bytes(chunk_size=8192):
            yield html_bytes

        mock_response.aiter_bytes = mock_aiter_bytes

        # Create a mock context manager for stream()
        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)

        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]), \
             patch("tools.ag3ntum.ag3ntum_webfetch.tool.httpx.AsyncClient",
                   return_value=mock_client_ctx):
            result = await tool_fn({
                "url": "https://example.com",
                "output_mode": "content_html"
            })

        assert result.get("is_error") is not True
        text = result["content"][0]["text"]
        assert "**URL:**" in text
        assert "**Status:** 200" in text
        assert "Hello" in text

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_successful_markdown_fetch(self, tool_fn):
        """content_markdown mode converts HTML to Markdown in output."""
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.url = httpx.URL("https://example.com")
        mock_response.headers = httpx.Headers({"content-type": "text/html"})

        html_bytes = b"<html><body><h1>Title</h1><p>Paragraph</p></body></html>"

        async def mock_aiter_bytes(chunk_size=8192):
            yield html_bytes

        mock_response.aiter_bytes = mock_aiter_bytes

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)

        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]), \
             patch("tools.ag3ntum.ag3ntum_webfetch.tool.httpx.AsyncClient",
                   return_value=mock_client_ctx):
            result = await tool_fn({
                "url": "https://example.com",
                "output_mode": "content_markdown"
            })

        assert result.get("is_error") is not True
        text = result["content"][0]["text"]
        assert "Content (Markdown)" in text
        assert "# Title" in text

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, tool_fn):
        """Request timeout returns appropriate error."""
        mock_client_ctx = AsyncMock()
        mock_client = AsyncMock()

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(
            side_effect=httpx.TimeoutException("Connection timed out")
        )
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client.stream = MagicMock(return_value=mock_stream_ctx)
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]), \
             patch("tools.ag3ntum.ag3ntum_webfetch.tool.httpx.AsyncClient",
                   return_value=mock_client_ctx):
            result = await tool_fn({"url": "https://slow-site.example.com"})

        assert result.get("is_error") is True
        assert "timed out" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_connection_error_returns_error(self, tool_fn):
        """Connection failure returns appropriate error."""
        mock_client_ctx = AsyncMock()
        mock_client = AsyncMock()

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client.stream = MagicMock(return_value=mock_stream_ctx)
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]), \
             patch("tools.ag3ntum.ag3ntum_webfetch.tool.httpx.AsyncClient",
                   return_value=mock_client_ctx):
            result = await tool_fn({"url": "https://unreachable.example.com"})

        assert result.get("is_error") is True
        assert "connection" in result["content"][0]["text"].lower()


# ---------------------------------------------------------------------------
# TestResponseSizeLimiting
# ---------------------------------------------------------------------------

class TestResponseSizeLimiting:
    """Tests for response size limiting and truncation."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_content_length_exceeding_limit_returns_error(self):
        """Response with Content-Length exceeding limit is rejected early."""
        tool_fn = _get_handler(max_response_size=1024)

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.url = httpx.URL("https://example.com/big")
        mock_response.headers = httpx.Headers({
            "content-type": "text/html",
            "content-length": "999999",
        })

        async def mock_aiter_bytes(chunk_size=8192):
            yield b"x" * 999999

        mock_response.aiter_bytes = mock_aiter_bytes

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)

        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]), \
             patch("tools.ag3ntum.ag3ntum_webfetch.tool.httpx.AsyncClient",
                   return_value=mock_client_ctx):
            result = await tool_fn({
                "url": "https://example.com/big",
                "output_mode": "content_html",
            })

        assert result.get("is_error") is True
        assert "too large" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_streaming_truncation_on_oversized_body(self):
        """Response body exceeding max_response_size during streaming is truncated."""
        max_size = 100
        tool_fn = _get_handler(max_response_size=max_size)

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.url = httpx.URL("https://example.com/stream-big")
        mock_response.headers = httpx.Headers({"content-type": "text/html"})

        # Yield chunks that exceed the limit in total
        async def mock_aiter_bytes(chunk_size=8192):
            yield b"A" * 60
            yield b"B" * 60  # total 120 > 100

        mock_response.aiter_bytes = mock_aiter_bytes

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)

        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]), \
             patch("tools.ag3ntum.ag3ntum_webfetch.tool.httpx.AsyncClient",
                   return_value=mock_client_ctx):
            result = await tool_fn({
                "url": "https://example.com/stream-big",
                "output_mode": "content_html",
            })

        # Should not be an error, but should indicate truncation
        assert result.get("is_error") is not True
        text = result["content"][0]["text"]
        assert "truncated" in text.lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_response_within_limit_not_truncated(self):
        """Response within size limit is returned without truncation notice."""
        tool_fn = _get_handler(max_response_size=10000)

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.url = httpx.URL("https://example.com/small")
        mock_response.headers = httpx.Headers({"content-type": "text/html"})

        async def mock_aiter_bytes(chunk_size=8192):
            yield b"<p>Small content</p>"

        mock_response.aiter_bytes = mock_aiter_bytes

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)

        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]), \
             patch("tools.ag3ntum.ag3ntum_webfetch.tool.httpx.AsyncClient",
                   return_value=mock_client_ctx):
            result = await tool_fn({
                "url": "https://example.com/small",
                "output_mode": "content_html",
            })

        assert result.get("is_error") is not True
        text = result["content"][0]["text"]
        assert "Small content" in text
        # "truncated at X bytes" should NOT appear (display truncation at 10000 chars is separate)
        assert "truncated at" not in text.lower()


# ---------------------------------------------------------------------------
# TestRedirectHandling
# ---------------------------------------------------------------------------

class TestRedirectHandling:
    """Tests for redirect validation (SSRF via redirect)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_redirect_to_private_ip_blocked_via_validation(self):
        """
        A redirect destination that resolves to a private IP should be
        caught by _validate_url_security() called in check_redirect.
        This tests the validation function directly.
        """
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, '', ("192.168.1.1", 80))
        ]
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=fake_addrinfo):
            valid, msg = _validate_url_security(
                "http://redirected-to-internal.com/", [], None
            )
        assert valid is False
        assert "private" in msg.lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_redirect_to_localhost_blocked(self):
        """Redirect destination resolving to 127.0.0.1 is blocked."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, '', ("127.0.0.1", 80))
        ]
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=fake_addrinfo):
            valid, msg = _validate_url_security(
                "http://evil-redirect.com/", [], None
            )
        assert valid is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_redirect_to_metadata_endpoint_blocked(self):
        """Redirect to cloud metadata endpoint IP is blocked."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, '', ("169.254.169.254", 80))
        ]
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=fake_addrinfo):
            valid, msg = _validate_url_security(
                "http://metadata-redirect.com/", [], None
            )
        assert valid is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_redirect_to_public_ip_allowed(self):
        """Redirect destination resolving to a public IP is allowed."""
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, '', ("8.8.8.8", 443))
        ]
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=fake_addrinfo):
            valid, msg = _validate_url_security(
                "https://safe-redirect.com/", [], None
            )
        assert valid is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_too_many_redirects_error(self):
        """httpx.TooManyRedirects is caught and returned as error."""
        tool_fn = _get_handler(max_redirects=2)

        mock_client_ctx = AsyncMock()
        mock_client = AsyncMock()

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(
            side_effect=httpx.TooManyRedirects(
                "Exceeded maximum redirects: 2"
            )
        )
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client.stream = MagicMock(return_value=mock_stream_ctx)
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]), \
             patch("tools.ag3ntum.ag3ntum_webfetch.tool.httpx.AsyncClient",
                   return_value=mock_client_ctx):
            result = await tool_fn({"url": "https://redirect-loop.example.com"})

        assert result.get("is_error") is True
        assert "redirect" in result["content"][0]["text"].lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_redirect_to_blocked_domain(self):
        """Redirect to a blocked domain is caught by validation.

        localhost may resolve to ::1 (private IP) in Docker, triggering
        DNS rebinding before the domain blocklist.  Mock DNS to return
        a public IP so we specifically exercise the domain blocklist path.
        """
        fake_addrinfo = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))
        ]
        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=fake_addrinfo):
            valid, msg = _validate_url_security(
                "https://localhost/internal",
                DEFAULT_BLOCKED_DOMAINS, None
            )
        assert valid is False
        assert "blocked" in msg.lower()


# ---------------------------------------------------------------------------
# TestHeadersOnlyMode
# ---------------------------------------------------------------------------

class TestHeadersOnlyMode:
    """Tests for http_headers output mode."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_headers_mode_returns_header_info(self):
        """http_headers mode returns formatted header information."""
        tool_fn = _get_handler()

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.url = httpx.URL("https://example.com")
        mock_response.headers = httpx.Headers({
            "content-type": "text/html",
            "server": "nginx",
        })

        async def mock_aiter_bytes(chunk_size=8192):
            yield b""

        mock_response.aiter_bytes = mock_aiter_bytes

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)

        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]), \
             patch("tools.ag3ntum.ag3ntum_webfetch.tool.httpx.AsyncClient",
                   return_value=mock_client_ctx):
            result = await tool_fn({
                "url": "https://example.com",
                "output_mode": "http_headers",
            })

        assert result.get("is_error") is not True
        text = result["content"][0]["text"]
        assert "**Request:**" in text
        assert "Headers" in text


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Tests for edge cases and unusual inputs."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_headers_passed_as_empty_string(self):
        """Empty string headers arg is handled gracefully."""
        tool_fn = _get_handler()

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.url = httpx.URL("https://example.com")
        mock_response.headers = httpx.Headers({"content-type": "text/html"})

        async def mock_aiter_bytes(chunk_size=8192):
            yield b"<p>OK</p>"

        mock_response.aiter_bytes = mock_aiter_bytes

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)

        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]), \
             patch("tools.ag3ntum.ag3ntum_webfetch.tool.httpx.AsyncClient",
                   return_value=mock_client_ctx):
            result = await tool_fn({
                "url": "https://example.com",
                "headers": "",
            })

        assert result.get("is_error") is not True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_headers_passed_as_valid_json_string(self):
        """Headers passed as a JSON string are parsed correctly."""
        tool_fn = _get_handler()

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.url = httpx.URL("https://example.com")
        mock_response.headers = httpx.Headers({"content-type": "text/html"})

        async def mock_aiter_bytes(chunk_size=8192):
            yield b"<p>OK</p>"

        mock_response.aiter_bytes = mock_aiter_bytes

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)

        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]), \
             patch("tools.ag3ntum.ag3ntum_webfetch.tool.httpx.AsyncClient",
                   return_value=mock_client_ctx):
            result = await tool_fn({
                "url": "https://example.com",
                "headers": '{"X-Custom": "value"}',
            })

        assert result.get("is_error") is not True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_method_defaults_to_get(self):
        """Default method is GET when not specified."""
        tool_fn = _get_handler()

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.url = httpx.URL("https://example.com")
        mock_response.headers = httpx.Headers({"content-type": "text/html"})

        async def mock_aiter_bytes(chunk_size=8192):
            yield b"<p>Response</p>"

        mock_response.aiter_bytes = mock_aiter_bytes

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_stream_ctx)

        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]), \
             patch("tools.ag3ntum.ag3ntum_webfetch.tool.httpx.AsyncClient",
                   return_value=mock_client_ctx):
            result = await tool_fn({"url": "https://example.com"})

        assert result.get("is_error") is not True
        # Verify stream was called with GET
        call_args = mock_client.stream.call_args
        assert call_args[1]["method"] == "GET" or call_args[0][0] == "GET"

    @pytest.mark.unit
    def test_is_private_ip_with_zero_address(self):
        """0.0.0.0 is not in the defined private ranges (it is unspecified)."""
        # 0.0.0.0 is not in any of the listed PRIVATE_IP_RANGES
        result = _is_private_ip("0.0.0.0")
        # This tests the actual behavior - 0.0.0.0 is NOT in the defined ranges
        assert result is False

    @pytest.mark.unit
    def test_validate_url_security_with_ipv6_private_in_url(self):
        """IPv6 private address in URL brackets is blocked."""
        valid, msg = _validate_url_security("http://[fc00::1]/", [], None)
        assert valid is False
        assert "private" in msg.lower()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_request_error_returns_blocked_message(self):
        """httpx.RequestError (e.g. from redirect block) returns error."""
        tool_fn = _get_handler()

        mock_client_ctx = AsyncMock()
        mock_client = AsyncMock()

        mock_request = MagicMock()
        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(
            side_effect=httpx.RequestError(
                "Redirect blocked: private IP", request=mock_request
            )
        )
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client.stream = MagicMock(return_value=mock_stream_ctx)
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("tools.ag3ntum.ag3ntum_webfetch.tool.socket.getaddrinfo",
                    return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, '', ("93.184.216.34", 443))]), \
             patch("tools.ag3ntum.ag3ntum_webfetch.tool.httpx.AsyncClient",
                   return_value=mock_client_ctx):
            result = await tool_fn({"url": "https://evil-redirect.example.com"})

        assert result.get("is_error") is True
        assert "blocked" in result["content"][0]["text"].lower()
