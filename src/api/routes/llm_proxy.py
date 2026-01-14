"""Claude-compatible LLM proxy endpoint."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from ..llm_proxy.config import load_llm_proxy_config, ProxyConfigError
from ..llm_proxy.translator import (
    claude_to_openai_messages,
    map_claude_tools,
    openai_to_claude_response,
    stream_openai_to_claude,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm-proxy/v1", tags=["llm-proxy"])


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
    api_key = os.environ.get(api_key_env)
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
) -> JSONResponse | StreamingResponse:
    messages = claude_to_openai_messages(payload)
    tools = payload.get("tools") or []
    body: dict[str, Any] = {
        "model": target_model,
        "messages": messages,
        "stream": bool(payload.get("stream")),
    }
    if tools:
        body["tools"] = map_claude_tools(tools)

    for field in ("temperature", "max_tokens", "top_p"):
        if field in payload:
            body[field] = payload[field]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        if body.get("stream"):
            response = await client.stream(
                "POST",
                f"{provider_config.base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            return StreamingResponse(
                stream_openai_to_claude(response, target_model),
                media_type="text/event-stream",
                status_code=response.status_code,
            )

        response = await client.post(
            f"{provider_config.base_url}/chat/completions",
            headers=headers,
            json=body,
        )

    response.raise_for_status()
    translated = openai_to_claude_response(response.json(), target_model)
    return JSONResponse(status_code=response.status_code, content=translated)


@router.post("/messages", response_model=None)
async def proxy_messages(request: Request) -> JSONResponse | StreamingResponse:
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

    try:
        provider_name, target_model, providers = _resolve_target(model_name)
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
        return await _proxy_openai(payload, provider_config, api_key, target_model)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unsupported provider type '{provider_config.type}'",
    )
