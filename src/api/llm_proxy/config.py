"""Configuration loader for the LLM API proxy."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

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


@dataclass(frozen=True)
class LlmProxyConfig:
    proxy: ProxyConfig
    providers: dict[str, ProviderConfig]
    models: dict[str, ModelMapping]
    routing: dict[str, Any]


class ProxyConfigError(RuntimeError):
    """Raised when proxy configuration is invalid."""


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
    )

    providers: dict[str, ProviderConfig] = {}
    for name, provider in providers_raw.items():
        providers[name] = ProviderConfig(
            name=name,
            type=str(_require(provider, "type", context=f"providers.{name}")),
            base_url=str(_require(provider, "base_url", context=f"providers.{name}")),
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
