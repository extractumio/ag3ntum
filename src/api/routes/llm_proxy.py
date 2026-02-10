"""Claude-compatible LLM proxy endpoint."""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from ..deps import get_proxy_caller_id
from ..llm_proxy.config import load_llm_proxy_config, ProxyConfigError
from ..llm_proxy.translator import (
    claude_to_openai_messages,
    map_claude_tools,
    openai_to_claude_response,
    stream_openai_to_claude,
)
from ...config import load_sandboxed_envs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm-proxy/v1", tags=["llm-proxy"])

# Debug directory for saving request/response pairs
DEBUG_DIR = Path(__file__).resolve().parents[3] / "data" / "llm_proxy_debug"

# Maximum number of debug files to keep (oldest deleted first)
DEBUG_MAX_FILES = 200

# Fields to redact in debug output (values replaced with "***REDACTED***")
_SENSITIVE_KEYS = frozenset({
    "api_key", "api-key", "x-api-key", "authorization",
    "token", "access_token", "secret", "password",
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
})


def _redact_sensitive(data: Any, *, _depth: int = 0) -> Any:
    """Recursively redact sensitive fields from data before writing to debug files."""
    if _depth > 20:
        return data
    if isinstance(data, dict):
        return {
            k: ("***REDACTED***" if k.lower() in _SENSITIVE_KEYS else _redact_sensitive(v, _depth=_depth + 1))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_redact_sensitive(item, _depth=_depth + 1) for item in data]
    return data


def _is_debug_enabled() -> bool:
    """Check if debug mode is enabled in config."""
    try:
        config = load_llm_proxy_config()
        return config.proxy.debug
    except Exception:
        return False


def _cleanup_debug_files() -> None:
    """Remove oldest debug files if directory exceeds DEBUG_MAX_FILES."""
    try:
        if not DEBUG_DIR.exists():
            return
        files = sorted(DEBUG_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
        if len(files) > DEBUG_MAX_FILES:
            for f in files[: len(files) - DEBUG_MAX_FILES]:
                f.unlink(missing_ok=True)
    except Exception as e:
        logger.debug("LLM Proxy debug: cleanup error: %s", e)


def _save_debug_file(filename: str, data: dict[str, Any]) -> None:
    """Save a debug JSON file with sensitive fields redacted.

    Caller is responsible for checking debug mode.
    """
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        redacted = _redact_sensitive(data)
        redacted["timestamp"] = datetime.now(timezone.utc).isoformat()
        filepath = DEBUG_DIR / filename
        filepath.write_text(json.dumps(redacted, indent=2, default=str))
        logger.debug("LLM Proxy debug: saved %s", filepath)
        _cleanup_debug_files()
    except Exception as e:
        logger.warning("LLM Proxy debug: failed to save %s: %s", filename, e)


def _log_debug_warning() -> None:
    """Log a startup warning if debug mode is enabled."""
    if _is_debug_enabled():
        logger.warning(
            "LLM Proxy debug mode is ENABLED. Request/response payloads will be "
            "saved to %s. Sensitive fields are redacted, but disable debug mode "
            "in production (set proxy.debug: false in llm-api-proxy.yaml).",
            DEBUG_DIR,
        )


# Log warning at import time (module load = server startup)
_log_debug_warning()


def _resolve_target(model_name: str) -> tuple[str, str, dict[str, Any]]:
    config = load_llm_proxy_config()
    mapping = config.models.get(model_name)
    if mapping is not None:
        return mapping.provider, mapping.target_model, config.providers

    if not config.routing.get("allow_unmapped_models", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown model mapping for '{model_name}'",
        )

    default_provider = config.routing.get("default_provider")
    if not default_provider:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No default provider configured for unmapped models",
        )
    return default_provider, model_name, config.providers


def _get_api_key(provider: str, providers: dict[str, Any]) -> str:
    provider_config = providers.get(provider)
    if not provider_config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown provider '{provider}'",
        )
    api_key_env = provider_config.api_key_env

    # First check environment variable
    api_key = os.environ.get(api_key_env)

    # Fall back to sandboxed_envs from secrets.yaml
    if not api_key:
        sandboxed_envs = load_sandboxed_envs()
        api_key = sandboxed_envs.get(api_key_env)

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Missing API key for provider '{provider}' (env {api_key_env})",
        )
    return api_key


async def _proxy_anthropic(
    payload: dict[str, Any],
    provider_config: Any,
    api_key: str,
    stream: bool,
) -> JSONResponse | StreamingResponse:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": payload.get("anthropic_version", "2023-06-01"),
    }
    async with httpx.AsyncClient(timeout=60) as client:
        if stream:
            response = await client.stream(
                "POST",
                f"{provider_config.base_url}/v1/messages",
                headers=headers,
                json=payload,
            )
            return StreamingResponse(
                response.aiter_bytes(),
                media_type="text/event-stream",
                status_code=response.status_code,
            )

        response = await client.post(
            f"{provider_config.base_url}/v1/messages",
            headers=headers,
            json=payload,
        )

    return JSONResponse(status_code=response.status_code, content=response.json())


async def _proxy_openai(
    payload: dict[str, Any],
    provider_config: Any,
    api_key: str,
    target_model: str,
    stream: bool = False,
) -> JSONResponse | StreamingResponse:
    messages = claude_to_openai_messages(payload)
    tools = payload.get("tools") or []

    body: dict[str, Any] = {
        "model": target_model,
        "messages": messages,
        "stream": stream,
    }

    # Enable stream_options to get usage in final chunk (OpenAI API)
    if stream:
        body["stream_options"] = {"include_usage": True}

    if tools:
        openai_tools = map_claude_tools(tools)
        body["tools"] = openai_tools
        # Debug: Log tool count and first tool's schema structure
        logger.debug(
            "Proxy request: model=%s, tools=%d, stream=%s",
            target_model,
            len(openai_tools),
            stream,
        )
        if openai_tools:
            first_tool = openai_tools[0]
            tool_name = first_tool.get("function", {}).get("name", "?")
            params = first_tool.get("function", {}).get("parameters", {})
            required = params.get("required", [])
            logger.debug(
                "First tool: name=%s, required_params=%s",
                tool_name,
                required,
            )

    for field in ("temperature", "max_tokens", "top_p"):
        if field in payload:
            body[field] = payload[field]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Check debug once to avoid re-loading config per call
    debug_enabled = _is_debug_enabled()
    request_uid = str(uuid.uuid4())[:8]

    if debug_enabled:
        _save_debug_file(f"in_{request_uid}.json", {
            "request_uid": request_uid,
            "target_model": target_model,
            "payload": body,
        })

    if stream:
        async def stream_response() -> AsyncIterator[str]:
            translated_chunks: list[str] = []

            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST",
                    f"{provider_config.base_url}/chat/completions",
                    headers=headers,
                    json=body,
                ) as response:
                    response.raise_for_status()
                    async for chunk in stream_openai_to_claude(response, target_model):
                        if debug_enabled:
                            translated_chunks.append(chunk)
                        yield chunk

            if debug_enabled:
                _save_debug_file(f"out_{request_uid}.json", {
                    "request_uid": request_uid,
                    "is_stream": True,
                    "translated_chunks": translated_chunks,
                })

        return StreamingResponse(
            stream_response(),
            media_type="text/event-stream",
        )

    # Non-streaming mode
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{provider_config.base_url}/chat/completions",
            headers=headers,
            json=body,
        )
        response.raise_for_status()
        raw_response = response.json()
        translated = openai_to_claude_response(raw_response, target_model)

        if debug_enabled:
            _save_debug_file(f"out_{request_uid}.json", {
                "request_uid": request_uid,
                "is_stream": False,
                "raw_response": raw_response,
                "translated_response": translated,
            })

        return JSONResponse(status_code=response.status_code, content=translated)


@router.post("/messages/count_tokens", response_model=None)
async def count_tokens(
    request: Request,
    caller_id: str = Depends(get_proxy_caller_id),
) -> JSONResponse:
    """No-op handler for the SDK's count_tokens call.

    The Claude Agent SDK tries to call this endpoint during initialization.
    Returning a minimal valid response prevents 404 noise in logs.
    """
    return JSONResponse(content={"input_tokens": 0})


@router.post("/messages", response_model=None)
async def proxy_messages(
    request: Request,
    user_id: str = Depends(get_proxy_caller_id),
) -> JSONResponse | StreamingResponse:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        ) from exc

    model_name = payload.get("model")
    if not model_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing model in request payload",
        )

    logger.info("LLM Proxy: received request for model=%s", model_name)

    try:
        provider_name, target_model, providers = _resolve_target(model_name)
        logger.info(
            "LLM Proxy: resolved model=%s -> provider=%s, target_model=%s",
            model_name, provider_name, target_model,
        )
    except ProxyConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    provider_config = providers.get(provider_name)
    if not provider_config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown provider '{provider_name}'",
        )

    api_key = _get_api_key(provider_name, providers)
    stream = bool(payload.get("stream"))

    if provider_config.type == "anthropic":
        return await _proxy_anthropic(payload, provider_config, api_key, stream)
    if provider_config.type in {"openai", "openai-compatible"}:
        return await _proxy_openai(payload, provider_config, api_key, target_model, stream)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported provider type '{provider_config.type}'",
    )
