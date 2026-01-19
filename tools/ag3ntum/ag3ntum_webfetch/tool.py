"""
Ag3ntumWebFetch - Network access with domain validation.

Provides controlled network access with:
- Domain allowlist/blocklist
- Response size limits
- Timeout handling
- SSRF protection (private IPs, localhost, metadata endpoints)
- Protocol validation (http/https only)
- Redirect validation
- Streaming response with size limits
- User-Agent spoofing
- Multiple output modes (headers-only, HTML, Markdown)

Security: Validates domains against configured blocklist to prevent
access to internal services (metadata endpoints, localhost, etc.).
"""
import ipaddress
import logging
import re
import socket
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

logger = logging.getLogger(__name__)

# Tool name constant
AG3NTUM_WEBFETCH_TOOL: str = "mcp__ag3ntum__WebFetch"

# Default configuration
DEFAULT_TIMEOUT: int = 30
DEFAULT_MAX_SIZE: int = 10 * 1024 * 1024  # 10MB
DEFAULT_MAX_REDIRECTS: int = 5
DEFAULT_BLOCKED_DOMAINS: list[str] = [
    "localhost",
    "127.0.0.1",
    "::1",
    "metadata.google.internal",
    "169.254.169.254",  # AWS metadata
    "metadata.azure.com",
    "169.254.169.123",  # GCP metadata
    "fd00:ec2::254",  # AWS IMDSv2 IPv6
]

# Private IP ranges (RFC 1918, RFC 4193, etc.)
PRIVATE_IP_RANGES: list[str] = [
    "10.0.0.0/8",  # Private network
    "172.16.0.0/12",  # Private network
    "192.168.0.0/16",  # Private network
    "127.0.0.0/8",  # Loopback
    "169.254.0.0/16",  # Link-local
    "::1/128",  # IPv6 loopback
    "fe80::/10",  # IPv6 link-local
    "fc00::/7",  # IPv6 private
    "ff00::/8",  # IPv6 multicast
]

# Dangerous ports (per Chrome's port blocking list)
BLOCKED_PORTS: set[int] = {
    1,     # tcpmux
    7,     # echo
    9,     # discard
    11,    # systat
    13,    # daytime
    15,    # netstat
    17,    # qotd
    19,    # chargen
    20,    # ftp-data
    21,    # ftp
    22,    # ssh
    23,    # telnet
    25,    # smtp
    37,    # time
    42,    # name
    43,    # nicname
    53,    # domain
    69,    # tftp
    77,    # priv-rjs
    79,    # finger
    87,    # ttylink
    95,    # supdup
    101,   # hostname
    102,   # iso-tsap
    103,   # gppitnp
    104,   # acr-nema
    109,   # pop2
    110,   # pop3
    111,   # sunrpc
    113,   # auth
    115,   # sftp
    117,   # uucp-path
    119,   # nntp
    123,   # ntp
    135,   # msrpc
    137,   # netbios-ns
    139,   # netbios-ssn
    143,   # imap
    161,   # snmp
    179,   # bgp
    389,   # ldap
    427,   # svrloc
    465,   # smtp+ssl
    512,   # print / exec
    513,   # login
    514,   # shell
    515,   # printer
    526,   # tempo
    530,   # courier
    531,   # chat
    532,   # netnews
    540,   # uucp
    548,   # afp
    554,   # rtsp
    556,   # remotefs
    563,   # nntp+ssl
    587,   # smtp (submission)
    601,   # syslog
    636,   # ldap+ssl
    989,   # ftps-data
    990,   # ftps
    993,   # imap+ssl
    995,   # pop3+ssl
    1719,  # h323gatestat
    1720,  # h323hostcall
    1723,  # pptp
    2049,  # nfs
    3659,  # apple-sasl
    4045,  # lockd
    5060,  # sip
    5061,  # sips
    6000,  # x11
    6566,  # sane-port
    6665,  # irc (alternate)
    6666,  # irc (alternate)
    6667,  # irc (default)
    6668,  # irc (alternate)
    6669,  # irc (alternate)
    6697,  # irc+tls
}

# Chrome User-Agent for macOS (current as of 2026)
CHROME_USER_AGENT: str = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Default headers to mimic Chrome
CHROME_HEADERS: dict[str, str] = {
    "User-Agent": CHROME_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


def _html_to_markdown(html: str) -> str:
    """
    Convert HTML to Markdown format.
    
    Preserves:
    - Links and images
    - Headings, lists, tables
    - Code blocks
    - Semantic structure
    
    Removes:
    - Scripts, styles
    - Non-printable characters
    - Extra whitespace
    """
    # Remove script and style tags
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Convert common HTML tags to Markdown
    
    # Headings
    html = re.sub(r'<h1[^>]*>(.*?)</h1>', r'\n# \1\n', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<h2[^>]*>(.*?)</h2>', r'\n## \1\n', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<h3[^>]*>(.*?)</h3>', r'\n### \1\n', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<h4[^>]*>(.*?)</h4>', r'\n#### \1\n', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<h5[^>]*>(.*?)</h5>', r'\n##### \1\n', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<h6[^>]*>(.*?)</h6>', r'\n###### \1\n', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Bold and italic
    html = re.sub(r'<(?:strong|b)[^>]*>(.*?)</(?:strong|b)>', r'**\1**', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<(?:em|i)[^>]*>(.*?)</(?:em|i)>', r'*\1*', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Code
    html = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<pre[^>]*>(.*?)</pre>', r'\n```\n\1\n```\n', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Links - extract href and text
    def replace_link(match):
        full_tag = match.group(0)
        href_match = re.search(r'href=["\'](.*?)["\']', full_tag, re.IGNORECASE)
        text_match = re.search(r'>(.*?)<', full_tag, re.DOTALL)
        href = href_match.group(1) if href_match else '#'
        text = text_match.group(1) if text_match else href
        return f'[{text.strip()}]({href})'
    
    html = re.sub(r'<a[^>]*>.*?</a>', replace_link, html, flags=re.DOTALL | re.IGNORECASE)
    
    # Images - extract src and alt
    def replace_img(match):
        full_tag = match.group(0)
        src_match = re.search(r'src=["\'](.*?)["\']', full_tag, re.IGNORECASE)
        alt_match = re.search(r'alt=["\'](.*?)["\']', full_tag, re.IGNORECASE)
        src = src_match.group(1) if src_match else ''
        alt = alt_match.group(1) if alt_match else 'image'
        return f'![{alt}]({src})'
    
    html = re.sub(r'<img[^>]*/?>', replace_img, html, flags=re.IGNORECASE)
    
    # Lists
    html = re.sub(r'<li[^>]*>(.*?)</li>', r'\n- \1', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'</?(?:ul|ol)[^>]*>', '', html, flags=re.IGNORECASE)
    
    # Paragraphs and line breaks
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</?p[^>]*>', '\n\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</?div[^>]*>', '\n', html, flags=re.IGNORECASE)
    
    # Horizontal rules
    html = re.sub(r'<hr[^>]*/?>', '\n---\n', html, flags=re.IGNORECASE)
    
    # Blockquotes
    html = re.sub(r'<blockquote[^>]*>(.*?)</blockquote>', r'\n> \1\n', html, flags=re.DOTALL | re.IGNORECASE)
    
    # Remove all other HTML tags
    html = re.sub(r'<[^>]+>', '', html)
    
    # Decode HTML entities
    html = html.replace('&nbsp;', ' ')
    html = html.replace('&lt;', '<')
    html = html.replace('&gt;', '>')
    html = html.replace('&amp;', '&')
    html = html.replace('&quot;', '"')
    html = html.replace('&#39;', "'")
    html = html.replace('&apos;', "'")
    
    # Remove non-printable characters (except newlines and tabs)
    html = re.sub(r'[^\x20-\x7E\n\t\u0080-\uFFFF]', '', html)
    
    # Clean up whitespace
    # Remove trailing whitespace from lines
    html = re.sub(r'[ \t]+$', '', html, flags=re.MULTILINE)
    # Remove leading whitespace from lines (but preserve indentation for code)
    html = re.sub(r'^[ \t]+', '', html, flags=re.MULTILINE)
    # Collapse multiple blank lines into max 2
    html = re.sub(r'\n{3,}', '\n\n', html)
    # Remove spaces around newlines
    html = re.sub(r' *\n *', '\n', html)
    
    return html.strip()


def _format_headers(headers: httpx.Headers) -> str:
    """Format HTTP headers for display."""
    lines = []
    for key, value in headers.items():
        # Don't show security-sensitive headers in full
        if key.lower() in ('authorization', 'cookie', 'set-cookie'):
            value = '[REDACTED]'
        lines.append(f"{key}: {value}")
    return '\n'.join(lines)


def _is_private_ip(ip_str: str) -> bool:
    """Check if IP address is in private/internal range."""
    try:
        ip = ipaddress.ip_address(ip_str)
        # Check against all private ranges
        for range_str in PRIVATE_IP_RANGES:
            if ip in ipaddress.ip_network(range_str):
                return True
        return False
    except ValueError:
        return False


def _validate_url_security(
    url: str,
    blocked_domains: list[str],
    allowed_domains: list[str] | None,
) -> tuple[bool, str]:
    """
    Validate URL for security issues.

    Returns:
        (is_valid, error_message) - is_valid=True if safe, False with error message if blocked.
    """
    # Parse URL
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"Failed to parse URL: {e}"

    # Validate scheme (only http/https)
    if parsed.scheme not in ("http", "https"):
        return False, f"Invalid protocol: {parsed.scheme} (only http/https allowed)"

    # Validate netloc exists
    if not parsed.netloc:
        return False, f"Invalid URL: missing hostname"

    # Extract hostname and port
    hostname = parsed.hostname
    if not hostname:
        return False, f"Invalid URL: could not extract hostname"

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    # Check blocked ports
    if port in BLOCKED_PORTS:
        return False, f"Port {port} is blocked by security policy"

    # Check if hostname is an IP address
    try:
        ip = ipaddress.ip_address(hostname)
        if _is_private_ip(str(ip)):
            return False, f"Access to private IP address blocked: {hostname}"
    except ValueError:
        # Not an IP address, it's a hostname - validate domain
        pass

    # Resolve DNS to check for DNS rebinding attacks
    try:
        addr_info = socket.getaddrinfo(hostname, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, socktype, proto, canonname, sockaddr in addr_info:
            resolved_ip = sockaddr[0]
            if _is_private_ip(resolved_ip):
                return False, f"Domain {hostname} resolves to private IP: {resolved_ip}"
    except socket.gaierror:
        # DNS resolution failed - will fail later in httpx anyway
        pass
    except Exception as e:
        logger.warning(f"DNS validation error for {hostname}: {e}")

    # Check blocked domains
    for blocked in blocked_domains:
        if hostname == blocked or hostname.endswith(f".{blocked}"):
            return False, f"Domain blocked by security policy: {hostname}"

    # Check allowed domains (whitelist mode)
    if allowed_domains:
        allowed = False
        for allow in allowed_domains:
            if hostname == allow or hostname.endswith(f".{allow}"):
                allowed = True
                break
        if not allowed:
            return False, f"Domain not in allowlist: {hostname}"

    return True, ""


def create_webfetch_tool(
    blocked_domains: list[str] | None = None,
    allowed_domains: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_response_size: int = DEFAULT_MAX_SIZE,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
):
    """
    Create Ag3ntumWebFetch tool with network configuration.

    Args:
        blocked_domains: Domains to block (default: internal/metadata endpoints)
        allowed_domains: If set, only these domains are allowed (whitelist mode)
        timeout: Request timeout in seconds
        max_response_size: Maximum response size in bytes
        max_redirects: Maximum number of redirects to follow

    Returns:
        Tool function decorated with @tool.
    """
    bound_blocked = blocked_domains if blocked_domains is not None else DEFAULT_BLOCKED_DOMAINS
    bound_allowed = allowed_domains
    bound_timeout = timeout
    bound_max_size = max_response_size
    bound_max_redirects = max_redirects

    @tool(
        "WebFetch",
        """Fetch content from a URL with comprehensive security validation.

Args:
    url: The URL to fetch (http/https only)
    method: HTTP method (default: GET)
    headers: Optional headers dict (Chrome headers added automatically)
    body: Optional request body (for POST/PUT)
    output_mode: What to fetch (default: content_html)
        - "http_headers": Fetch only headers (includes full redirect chain)
        - "content_html": Fetch HTML content (default)
        - "content_markdown": Convert HTML to Markdown format

Returns:
    Response content and metadata, or error.

Output Modes:
    - http_headers: Returns headers for all redirects in the chain
    - content_html: Returns raw HTML content
    - content_markdown: Returns cleaned Markdown with links/images preserved

Security:
    - Protocol validation (http/https only)
    - Private IP blocking (prevents SSRF)
    - DNS rebinding protection
    - Redirect validation
    - Response size limits (prevents zip bombs)
    - Port restrictions
    - User-Agent spoofing (appears as Chrome on macOS)

Examples:
    WebFetch(url="https://api.example.com/data")
    WebFetch(url="https://example.com", output_mode="http_headers")
    WebFetch(url="https://en.wikipedia.org/wiki/Python", output_mode="content_markdown")
    WebFetch(url="https://httpbin.org/post", method="POST", body='{"key": "value"}')
""",
        {"url": str, "method": str, "headers": dict, "body": str, "output_mode": str},
    )
    async def webfetch(args: dict[str, Any]) -> dict[str, Any]:
        """Fetch content from a URL with comprehensive security validation."""
        import json
        
        url = args.get("url", "")
        method = args.get("method", "GET").upper()
        
        # Handle headers: SDK might pass as string or dict
        headers_arg = args.get("headers", {})
        if isinstance(headers_arg, str):
            # Try to parse as JSON if it's a string
            if headers_arg.strip():
                try:
                    user_headers = json.loads(headers_arg)
                except json.JSONDecodeError:
                    return _error(f"Invalid headers JSON: {headers_arg}")
            else:
                user_headers = {}
        else:
            user_headers = headers_arg
        
        body = args.get("body")
        output_mode = args.get("output_mode", "content_html")

        # Validate output_mode
        valid_modes: set[str] = {"http_headers", "content_html", "content_markdown"}
        if output_mode not in valid_modes:
            return _error(
                f"Invalid output_mode: {output_mode}. "
                f"Valid modes: {', '.join(sorted(valid_modes))}"
            )

        if not url:
            return _error("url is required")

        # Validate initial URL
        is_valid, error_msg = _validate_url_security(url, bound_blocked, bound_allowed)
        if not is_valid:
            logger.warning(f"Ag3ntumWebFetch: Blocked URL - {url}: {error_msg}")
            return _error(error_msg)

        # Merge Chrome headers with user headers (user headers take precedence)
        headers = {**CHROME_HEADERS, **user_headers}

        # Track redirect chain for validation and headers
        redirect_count = 0
        redirect_chain: list[dict[str, Any]] = []

        async def check_redirect(request: httpx.Request) -> None:
            """Validate each redirect destination."""
            nonlocal redirect_count
            redirect_count += 1

            if redirect_count > bound_max_redirects:
                raise httpx.TooManyRedirects(
                    f"Exceeded maximum redirects: {bound_max_redirects}"
                )

            redirect_url = str(request.url)
            is_valid, error_msg = _validate_url_security(
                redirect_url, bound_blocked, bound_allowed
            )
            if not is_valid:
                logger.warning(
                    f"Ag3ntumWebFetch: Blocked redirect to {redirect_url}: {error_msg}"
                )
                raise httpx.RequestError(
                    f"Redirect blocked: {error_msg}", request=request
                )

        async def track_response(response: httpx.Response) -> None:
            """Track response headers in redirect chain."""
            if output_mode == "http_headers":
                redirect_chain.append({
                    "url": str(response.url),
                    "status": response.status_code,
                    "headers": dict(response.headers),
                })

        # Make request with streaming to prevent memory exhaustion
        try:
            # For headers-only mode, use HEAD method to avoid downloading body
            request_method = "HEAD" if output_mode == "http_headers" and method == "GET" else method
            
            async with httpx.AsyncClient(
                timeout=bound_timeout,
                follow_redirects=True,
                max_redirects=bound_max_redirects,
                event_hooks={
                    "request": [check_redirect],
                    "response": [track_response],
                },
            ) as client:
                async with client.stream(
                    method=request_method,
                    url=url,
                    headers=headers,
                    content=body if method in ("POST", "PUT", "PATCH") else None,
                ) as response:
                    # Handle http_headers mode
                    if output_mode == "http_headers":
                        # Add final response to chain
                        redirect_chain.append({
                            "url": str(response.url),
                            "status": response.status_code,
                            "headers": dict(response.headers),
                        })
                        
                        # Build headers result
                        result = f"**Request:** `{method} {url}`\n"
                        result += f"**Redirects:** {redirect_count}\n\n"
                        
                        for idx, entry in enumerate(redirect_chain):
                            if idx == 0:
                                result += "### Initial Request\n\n"
                            else:
                                result += f"### Redirect {idx}\n\n"
                            
                            result += f"**URL:** `{entry['url']}`\n"
                            result += f"**Status:** {entry['status']}\n\n"
                            result += "**Headers:**\n```\n"
                            result += _format_headers(httpx.Headers(entry['headers']))
                            result += "\n```\n\n"
                        
                        logger.info(
                            f"Ag3ntumWebFetch: {method} {url} -> {response.status_code} "
                            f"(headers only, {redirect_count} redirects)"
                        )
                        
                        return _result(result)
                    
                    # For content modes, download the body
                    
                    # Check response size from Content-Length header
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            size = int(content_length)
                            if size > bound_max_size:
                                return _error(
                                    f"Response too large: {size} bytes (max: {bound_max_size})"
                                )
                        except ValueError:
                            pass

                    # Read response in chunks to prevent memory exhaustion (zip bomb protection)
                    chunks: list[bytes] = []
                    total_size = 0
                    truncated = False

                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        total_size += len(chunk)
                        if total_size > bound_max_size:
                            truncated = True
                            break
                        chunks.append(chunk)

                    # Decode content
                    try:
                        content = b"".join(chunks).decode("utf-8", errors="replace")
                    except Exception:
                        content = b"".join(chunks).decode("latin-1", errors="replace")

                    # Convert to markdown if requested
                    if output_mode == "content_markdown":
                        content = _html_to_markdown(content)
                        content_type = "text/markdown"
                    else:
                        content_type = response.headers.get('content-type', 'unknown')

                    # Log successful request
                    final_url = str(response.url)
                    logger.info(
                        f"Ag3ntumWebFetch: {method} {url} -> {response.status_code} "
                        f"({total_size} bytes, {redirect_count} redirects, mode={output_mode})"
                    )

                    # Build result with timestamp
                    fetch_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    result = f"**URL:** `{url}`\n"
                    if final_url != url:
                        result += f"**Final URL:** `{final_url}`\n"
                    result += (
                        f"**Status:** {response.status_code}\n"
                        f"**Content-Type:** {content_type}\n"
                        f"**Size:** {total_size} bytes\n"
                        f"**Fetched:** {fetch_time}"
                    )
                    if truncated:
                        result += f" (truncated at {bound_max_size} bytes)"
                    if redirect_count > 0:
                        result += f"\n**Redirects:** {redirect_count}"

                    # Display content (limit to 10000 chars for readability)
                    display_content = content[:10000]
                    
                    if output_mode == "content_markdown":
                        result += f"\n\n**Content (Markdown):**\n\n{display_content}"
                    else:
                        result += f"\n\n**Content:**\n```html\n{display_content}\n```"
                    
                    if len(content) > 10000:
                        result += "\n\n[Content truncated for display]"

                    return _result(result)

        except httpx.TooManyRedirects as e:
            return _error(f"Too many redirects: {e}")
        except httpx.TimeoutException:
            return _error(f"Request timed out after {bound_timeout} seconds")
        except httpx.ConnectError as e:
            return _error(f"Connection failed: {e}")
        except httpx.RequestError as e:
            return _error(f"Request blocked: {e}")
        except Exception as e:
            logger.exception(f"Ag3ntumWebFetch: Unexpected error for {url}")
            return _error(f"Request failed: {e}")

    return webfetch


def _result(text: str) -> dict[str, Any]:
    """Create a successful result response."""
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict[str, Any]:
    """Create an error response."""
    return {"content": [{"type": "text", "text": f"**Error:** {message}"}], "isError": True}


def create_ag3ntum_webfetch_mcp_server(
    blocked_domains: list[str] | None = None,
    allowed_domains: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    max_response_size: int = DEFAULT_MAX_SIZE,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    server_name: str = "ag3ntum",
    version: str = "1.0.0",
):
    """
    Create an in-process MCP server for the Ag3ntumWebFetch tool.

    Args:
        blocked_domains: Domains to block
        allowed_domains: If set, only these domains allowed
        timeout: Request timeout in seconds
        max_response_size: Maximum response size in bytes
        max_redirects: Maximum number of redirects to follow
        server_name: MCP server name
        version: Server version

    Returns:
        McpSdkServerConfig for use in ClaudeAgentOptions.mcp_servers.
    """
    webfetch_tool = create_webfetch_tool(
        blocked_domains=blocked_domains,
        allowed_domains=allowed_domains,
        timeout=timeout,
        max_response_size=max_response_size,
        max_redirects=max_redirects,
    )

    logger.info(
        f"Created Ag3ntumWebFetch MCP server "
        f"(timeout={timeout}s, max_size={max_response_size}, max_redirects={max_redirects})"
    )

    return create_sdk_mcp_server(
        name=server_name,
        version=version,
        tools=[webfetch_tool],
    )
