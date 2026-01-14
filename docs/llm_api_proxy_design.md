# LLM API Proxy Design (Claude Code → Multi‑Provider)

## Overview
This design introduces an internal HTTP proxy service that accepts **Anthropic/Claude-compatible** requests (e.g., `POST /api/llm-proxy/v1/messages`) from the Claude Code CLI and translates them to other providers (OpenAI, local OpenAI-compatible endpoints, etc.). The proxy runs inside the same Docker container as the agent and is configured via `config/llm-api-proxy.yaml`. The user selects a model with a prefix such as:

- `anthropic:opus-4.5-202501122`
- `openai:gpt-5.2`
- `local-openai:myllmmodel-55`

The proxy resolves the model prefix to the correct provider endpoint and routes the request accordingly, then returns a **Claude-compatible** response (including streaming SSE) back to Claude Code.

This enables Claude Code to continue using its Anthropic client while transparently targeting other LLM backends.

## Goals
- Provide a drop‑in Claude API endpoint for Claude Code.
- Support routing to multiple providers (OpenAI, local OpenAI‑compatible, optional Anthropic passthrough).
- Centralize model/endpoint mapping in `config/llm-api-proxy.yaml`.
- Preserve Claude features: system prompts, tool use, streaming.
- Keep the service internal to the Docker container and reachable via `ANTHROPIC_BASE_URL` or Foundry env vars.

## Non‑Goals
- Replacing the agent’s core orchestration logic.
- Building a UI for model selection or config editing.
- Implementing provider-specific SDKs in the agent (the proxy handles translation).

## Architecture

```
Claude Code CLI
  |  (Anthropic request /api/llm-proxy/v1/messages)
  v
LLM API Proxy (FastAPI)
  |  (internal base path /api/llm-proxy/v1/)
  |  (provider mapping & translation)
  v
Provider endpoint (OpenAI / local OpenAI / Anthropic passthrough)
```

### Key Components
1. **Proxy server (FastAPI)**
   - Receives Claude‑format requests (`/api/llm-proxy/v1/messages`, streaming SSE).
   - Translates request payloads into provider-specific format.
   - Converts provider responses back into Claude‑format, including tool_use handling.
   - Uses the same API server and Python module patterns already present in the repo (e.g., existing FastAPI server, config loading utilities).

2. **Model routing & translation layer**
   - Parses model identifiers (`prefix:model_name`).
   - Maps prefix → provider config (endpoint, auth, adapter).
   - Maps Claude style request → provider request (OpenAI Chat API or OpenAI-compatible protocol).

3. **Configuration file**
   - `config/llm-api-proxy.yaml` defines providers, model mappings, auth and defaults.

## Configuration (`config/llm-api-proxy.yaml`)

**File location**: `config/llm-api-proxy.yaml`

Suggested schema:

```yaml
proxy:
  host: 0.0.0.0
  port: 8082
  log_level: INFO
  enable_streaming: true

providers:
  anthropic:
    type: anthropic
    base_url: https://api.anthropic.com
    api_key_env: ANTHROPIC_API_KEY
  openai:
    type: openai
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
  local-openai:
    type: openai-compatible
    base_url: http://localhost:11434/v1
    api_key_env: LOCAL_OPENAI_API_KEY

models:
  # model name visible to Claude Code
  "anthropic:opus-4.5-202501122":
    provider: anthropic
    target_model: opus-4.5-202501122
  "openai:gpt-5.2":
    provider: openai
    target_model: gpt-5.2
  "local-openai:myllmmodel-55":
    provider: local-openai
    target_model: myllmmodel-55

routing:
  default_provider: anthropic
  allow_unmapped_models: false
```

**Notes**:
- `provider.type` drives the adapter used for translation.
- `api_key_env` allows secrets to remain in environment variables rather than config files.
- `allow_unmapped_models=false` ensures model requests must be mapped (fail fast).

## Request Flow (Execution Process)

1. **Claude Code request**
   - Claude Code issues `POST /api/llm-proxy/v1/messages` to the proxy with `model` set to a prefixed value like `openai:gpt-5.2`.
2. **Proxy parse & route**
   - Proxy reads `config/llm-api-proxy.yaml`.
   - Looks up `model` in `models` mapping.
   - Determines provider and target model.
3. **Translate request**
   - Convert Claude `messages` structure to OpenAI-compatible Chat API format.
   - Handle tool definitions and tool calls according to provider API.
4. **Dispatch to provider**
   - Build HTTP request to provider endpoint.
   - Use provider API key from env var.
5. **Translate response**
   - Convert provider response to Claude format.
   - For streaming, reformat chunks as Claude SSE events.
6. **Return to client**
   - Proxy returns Claude-compatible response to Claude Code.

## Provider Translation Strategy

### Recommended Implementation Path
- Embed or reuse the **1rgs/claude-code-proxy** module as an internal adapter rather than a standalone server.
  - Instantiate its translation utilities inside our FastAPI route handler.
  - Feed it the incoming Claude request body and stream/return the transformed response.
  - Override its model selection with our config-driven mapping.
- The module provides Claude‑compatible request/response translation, including tool use and streaming semantics (via LiteLLM).
- Wrap or extend it with:
  - Config-driven model mapping via `config/llm-api-proxy.yaml`.
  - Multi-provider routing with explicit prefixes.
  - Standardized logging and metrics consistent with the rest of the repo.
- If using LiteLLM directly, keep the Claude-to-OpenAI conversion logic equivalent to the module to preserve tool use semantics.

### Alternative (Custom proxy)
- Implement a minimal translation layer using LiteLLM or direct OpenAI-compatible conversion code.
- Provides full control but requires more testing for tool-call and streaming fidelity.


## Claude Proxy Module Integration Details

**Goal**: reuse the `1rgs/claude-code-proxy` module as a translation layer inside our FastAPI app.

### Suggested Integration Steps
1. **Vendor or depend on the module**
   - Add the proxy package to `requirements.txt` or vendor it under `tools/` or `src/` if licensing requires.
   - Confirm the module exposes translation helpers (e.g., request conversion, response formatting, streaming SSE).
2. **Wrap the module in an adapter**
   - Create a small adapter that accepts:
     - Claude request body + headers
     - Target provider + model from `config/llm-api-proxy.yaml`
   - Adapter responsibilities:
     - Map the Claude `model` field to `provider` + `target_model`.
     - Inject provider API base URL and API key (from env).
     - Call the module’s conversion functions to build provider payloads.
     - Handle streaming responses and convert them back to Claude SSE format.
3. **FastAPI route**
   - Implement `POST /api/llm-proxy/v1/messages` (and `GET` for health).
   - Parse JSON body and pass to adapter.
   - For streaming: return a `StreamingResponse` with Claude-style SSE events.
4. **Model mapping override**
   - Ignore any internal model mapping inside the module.
   - Always use `config/llm-api-proxy.yaml` to resolve target provider/model.
5. **Tool use**
   - Ensure tool/function call translation is routed through the module so that Claude `tool_use` blocks round-trip correctly.

### Key Data Transformations
- Claude `messages` → OpenAI `messages` (system/user/assistant role mapping).
- Claude tool definitions → OpenAI function schemas.
- Provider streaming chunks → Claude SSE events (`message_start`, `content_block_start`, etc.).

### Error Handling
- Normalize provider errors into Claude error format.
- Preserve HTTP status codes where possible.

### Minimal Module Touchpoints (illustrative)
- `translate_request(claude_payload, target_provider, target_model, base_url, api_key)`
- `stream_response(provider_stream) -> Claude SSE`
- `translate_response(provider_response) -> Claude JSON`

(Use the actual function names exposed by the module; wrap them behind our adapter interface.)

## Deployment in Docker

### Container service
- Run a new FastAPI process alongside existing services, e.g. via `docker-compose.yml` or the existing entrypoint.
- Bind `proxy.host:proxy.port` (default `0.0.0.0:8082`).

### Environment variables
Allow Claude Code to use Foundry (or explicit base URL) to point at the proxy:

```bash
export CLAUDE_CODE_USE_FOUNDRY=1
export ANTHROPIC_FOUNDRY_RESOURCE="<resource-name>"
# or direct
export ANTHROPIC_FOUNDRY_BASE_URL="http://localhost:8082/api/llm-proxy"
export ANTHROPIC_FOUNDRY_API_KEY="dummy"
```

The proxy reads provider API keys from environment variables defined in `config/llm-api-proxy.yaml`.

## Security Considerations
- Keep provider API keys in env vars only.
- Ensure the proxy binds to localhost or internal Docker network unless explicitly exposed.
- Add request size limits and timeouts to prevent abuse.
- Enforce allowed model prefixes to avoid untrusted routing.

## Observability
- Log request ID, provider, model mapping, and response status.
- Optionally export metrics: request count, latency, provider error rate.
- For streaming, track chunk counts and total tokens if available.

## Implementation Checklist

1. **Proxy service**
   - Add FastAPI app (new module under `src/` or `tools/`) implementing `/api/llm-proxy/v1/messages`.
   - Integrate translation layer (LiteLLM or claude-code-proxy components).
2. **Config parsing**
   - Load `config/llm-api-proxy.yaml` using existing config loader patterns.
   - Validate schema on startup.
3. **Model mapping & routing**
   - Implement `prefix:model` parsing.
   - Route to provider based on mapping.
4. **Streaming**
   - Implement Claude-compatible SSE responses.
5. **Docker integration**
   - Update container entrypoint or compose to run proxy.
6. **Docs**
   - Document how to configure and run the proxy with Claude Code.

## Risks & Mitigations
- **Tool call incompatibilities** → rely on LiteLLM or claude-code-proxy code paths.
- **Streaming edge cases** → test SSE output with Claude Code CLI.
- **Model name collisions** → require prefixes in config and enforce mapping.

## Open Questions
- Should the proxy be embedded in the existing API process or run as a separate service?
  - Decision: **Separate endpoint** under the existing API server at `/api/llm-proxy/v1/...`.
- Do we need per-provider concurrency limits or rate limiting?
  - Decision: **No rate limits** in the initial implementation.
- Should model mapping support wildcard prefixes?
  - Decision: **No wildcards**; all models must be explicitly mapped in config.
