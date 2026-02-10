"""Configuration loader for the LLM API proxy."""
from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "llm-api-proxy.yaml"


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    type: str
    base_url: str
    api_key_env: str


@dataclass(frozen=True)
class ModelMapping:
    provider: str
    target_model: str


@dataclass(frozen=True)
class ProxyConfig:
    host: str
    port: int
    log_level: str
    enable_streaming: bool
    debug: bool  # Save request/response to data/llm_proxy_debug/


@dataclass(frozen=True)
class LlmProxyConfig:
    proxy: ProxyConfig
    providers: dict[str, ProviderConfig]
    models: dict[str, ModelMapping]
    routing: dict[str, Any]


class ProxyConfigError(RuntimeError):
    """Raised when proxy configuration is invalid."""


def validate_base_url(url: str, provider_name: str) -> None:
    """Validate that a provider base_url does not point to a private/internal address.

    Prevents SSRF by blocking URLs that resolve to:
    - Loopback addresses (127.0.0.0/8, ::1)
    - Private networks (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
    - Link-local addresses (169.254.0.0/16, fe80::/10)
    - Unspecified addresses (0.0.0.0, ::)

    Raises:
        ProxyConfigError: If the URL resolves to a private/internal address.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname

    if not hostname:
        raise ProxyConfigError(
            f"Provider '{provider_name}' base_url has no hostname: {url}"
        )

    # Check if hostname is a literal IP address
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified:
            raise ProxyConfigError(
                f"Provider '{provider_name}' base_url resolves to a private/internal address: "
                f"{hostname} ({url}). This is blocked to prevent SSRF."
            )
        return
    except ValueError:
        pass  # Not an IP literal - it's a hostname, resolve it below

    # Resolve hostname to IP and check
    try:
        addrinfos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in addrinfos:
            ip_str = sockaddr[0]
            addr = ipaddress.ip_address(ip_str)
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified:
                raise ProxyConfigError(
                    f"Provider '{provider_name}' base_url hostname '{hostname}' resolves to "
                    f"private/internal address {ip_str} ({url}). This is blocked to prevent SSRF."
                )
    except socket.gaierror:
        # DNS resolution failed - log warning but allow (may resolve at runtime)
        logger.warning(
            "SSRF check: Could not resolve hostname '%s' for provider '%s'. "
            "URL will be allowed but may fail at runtime.",
            hostname, provider_name,
        )


def _require(mapping: dict[str, Any], key: str, *, context: str) -> Any:
    if key not in mapping:
        raise ProxyConfigError(f"Missing '{key}' in {context} section")
    return mapping[key]


def load_llm_proxy_config() -> LlmProxyConfig:
    if not CONFIG_PATH.exists():
        raise ProxyConfigError(
            f"Proxy config not found at {CONFIG_PATH}. "
            "Create config/llm-api-proxy.yaml to enable the proxy."
        )

    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ProxyConfigError(f"Failed to parse {CONFIG_PATH}: {exc}") from exc

    proxy_raw = _require(raw, "proxy", context="root")
    providers_raw = _require(raw, "providers", context="root")
    models_raw = _require(raw, "models", context="root")
    routing_raw = raw.get("routing", {})

    proxy = ProxyConfig(
        host=str(_require(proxy_raw, "host", context="proxy")),
        port=int(_require(proxy_raw, "port", context="proxy")),
        log_level=str(_require(proxy_raw, "log_level", context="proxy")),
        enable_streaming=bool(proxy_raw.get("enable_streaming", True)),
        debug=bool(proxy_raw.get("debug", False)),
    )

    providers: dict[str, ProviderConfig] = {}
    for name, provider in providers_raw.items():
        base_url = str(_require(provider, "base_url", context=f"providers.{name}"))
        validate_base_url(base_url, name)
        providers[name] = ProviderConfig(
            name=name,
            type=str(_require(provider, "type", context=f"providers.{name}")),
            base_url=base_url,
            api_key_env=str(
                _require(provider, "api_key_env", context=f"providers.{name}")
            ),
        )

    models: dict[str, ModelMapping] = {}
    for model_name, mapping in models_raw.items():
        models[model_name] = ModelMapping(
            provider=str(_require(mapping, "provider", context=f"models.{model_name}")),
            target_model=str(
                _require(mapping, "target_model", context=f"models.{model_name}")
            ),
        )

    return LlmProxyConfig(
        proxy=proxy,
        providers=providers,
        models=models,
        routing=routing_raw,
    )
