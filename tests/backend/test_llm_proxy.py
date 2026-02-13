"""
Tests for LLM proxy integration.

Tests cover:
1. Proxy URL detection for non-Anthropic models (_get_proxy_base_url_for_model)
2. Error message formatting (_validate_response)
3. API key loading from sandboxed_envs (_get_api_key fallback)
4. Proxy endpoint routing
5. Authentication enforcement on proxy endpoints
6. SSRF protection for provider base URLs
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path
from fastapi.testclient import TestClient


class TestLlmProxyAuthentication:
    """Tests that LLM proxy endpoints enforce correct authentication.

    The proxy supports two auth paths:
    1. Loopback (127.0.0.1) + x-api-key header → "internal-agent" (SDK traffic)
    2. Standard JWT Bearer token → user_id from token

    External requests (non-loopback) MUST use JWT Bearer auth.
    """

    def test_proxy_messages_rejects_unauthenticated(self, client):
        """POST /api/llm-proxy/v1/messages should reject without any auth."""
        response = client.post(
            "/api/llm-proxy/v1/messages",
            json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code in (401, 403)

    def test_proxy_messages_rejects_invalid_token(self, client):
        """POST /api/llm-proxy/v1/messages should reject invalid Bearer token."""
        response = client.post(
            "/api/llm-proxy/v1/messages",
            headers={"Authorization": "Bearer invalid-token-value"},
            json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code == 401

    def test_proxy_messages_accepts_valid_jwt(self, client, auth_headers):
        """POST /api/llm-proxy/v1/messages should accept valid JWT Bearer token.

        The request will fail at the proxy config level (no model mapping),
        but it should get past authentication (not 401/403).
        """
        with patch("src.api.routes.llm_proxy.load_llm_proxy_config") as mock_config:
            mock_config.return_value = MagicMock(
                models={},
                routing={"allow_unmapped_models": False},
            )
            response = client.post(
                "/api/llm-proxy/v1/messages",
                headers=auth_headers,
                json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
            )
        # Should NOT be 401 or 403 — authentication passed
        assert response.status_code not in (401, 403)

    def test_proxy_messages_accepts_loopback_x_api_key(self, test_app):
        """Loopback request with x-api-key should be accepted as internal-agent.

        This simulates the Claude Agent SDK calling the proxy from inside the
        container (127.0.0.1) with x-api-key header instead of JWT.
        """
        from src.api.deps import get_proxy_caller_id

        # Override the dependency to simulate loopback auth succeeding
        async def loopback_proxy_caller():
            return "internal-agent"

        test_app.dependency_overrides[get_proxy_caller_id] = loopback_proxy_caller

        try:
            with TestClient(test_app, base_url="http://localhost") as c:
                with patch("src.api.routes.llm_proxy.load_llm_proxy_config") as mock_config:
                    mock_config.return_value = MagicMock(
                        models={},
                        routing={"allow_unmapped_models": False},
                    )
                    response = c.post(
                        "/api/llm-proxy/v1/messages",
                        headers={"x-api-key": "sk-ant-some-key"},
                        json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
                    )
            # Should get past auth — 400 from model resolution, not 401/403
            assert response.status_code not in (401, 403)
        finally:
            test_app.dependency_overrides.pop(get_proxy_caller_id, None)

    def test_proxy_messages_rejects_non_loopback_x_api_key(self, client):
        """Non-loopback request with only x-api-key should be rejected (401).

        External requests must use JWT Bearer auth. x-api-key alone is only
        valid from 127.0.0.1.
        """
        response = client.post(
            "/api/llm-proxy/v1/messages",
            headers={"x-api-key": "sk-ant-some-key"},
            json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert response.status_code in (401, 403)

    def test_count_tokens_returns_200_with_loopback_auth(self, test_app):
        """POST /api/llm-proxy/v1/messages/count_tokens should return 200 for internal agent.

        The SDK calls count_tokens during initialization. A no-op 200 response
        prevents 404 noise in logs.
        """
        from src.api.deps import get_proxy_caller_id

        async def loopback_proxy_caller():
            return "internal-agent"

        test_app.dependency_overrides[get_proxy_caller_id] = loopback_proxy_caller

        try:
            with TestClient(test_app, base_url="http://localhost") as c:
                response = c.post(
                    "/api/llm-proxy/v1/messages/count_tokens",
                    headers={"x-api-key": "sk-ant-some-key"},
                    json={"model": "test-model", "messages": []},
                )
            assert response.status_code == 200
            assert response.json()["input_tokens"] == 0
        finally:
            test_app.dependency_overrides.pop(get_proxy_caller_id, None)

    def test_count_tokens_rejects_unauthenticated(self, client):
        """POST /api/llm-proxy/v1/messages/count_tokens should reject without auth."""
        response = client.post(
            "/api/llm-proxy/v1/messages/count_tokens",
            json={"model": "test-model", "messages": []},
        )
        assert response.status_code in (401, 403)


class TestGetProxyCallerIdDependency:
    """Unit tests for get_proxy_caller_id dependency function.

    Tests the auth logic directly to ensure loopback detection and JWT
    fallback work correctly at the dependency level.
    """

    @pytest.mark.asyncio
    async def test_loopback_with_x_api_key_returns_internal_agent(self):
        """127.0.0.1 + x-api-key should return 'internal-agent'."""
        from src.api.deps import get_proxy_caller_id

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"
        mock_request.headers = {"x-api-key": "sk-ant-test-key"}

        result = await get_proxy_caller_id(
            request=mock_request, credentials=None, db=MagicMock()
        )
        assert result == "internal-agent"

    @pytest.mark.asyncio
    async def test_non_loopback_with_x_api_key_only_raises_401(self):
        """Non-loopback + x-api-key only (no JWT) should raise 401."""
        from src.api.deps import get_proxy_caller_id
        from fastapi import HTTPException

        mock_request = MagicMock()
        mock_request.client.host = "172.17.0.1"  # Docker gateway
        mock_request.headers = {"x-api-key": "sk-ant-test-key"}

        with pytest.raises(HTTPException) as exc_info:
            await get_proxy_caller_id(
                request=mock_request, credentials=None, db=MagicMock()
            )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_jwt_returns_user_id(self):
        """Valid JWT Bearer token should return user_id."""
        from src.api.deps import get_proxy_caller_id

        mock_request = MagicMock()
        mock_request.client.host = "172.17.0.1"
        mock_request.headers = {}

        mock_credentials = MagicMock()
        mock_credentials.credentials = "valid-jwt-token"

        with patch("src.api.deps.auth_service") as mock_auth:
            mock_auth.validate_token = AsyncMock(return_value="user-123")

            result = await get_proxy_caller_id(
                request=mock_request, credentials=mock_credentials, db=MagicMock()
            )
        assert result == "user-123"

    @pytest.mark.asyncio
    async def test_invalid_jwt_raises_401(self):
        """Invalid JWT token should raise 401."""
        from src.api.deps import get_proxy_caller_id
        from fastapi import HTTPException

        mock_request = MagicMock()
        mock_request.client.host = "172.17.0.1"
        mock_request.headers = {}

        mock_credentials = MagicMock()
        mock_credentials.credentials = "bad-token"

        with patch("src.api.deps.auth_service") as mock_auth:
            mock_auth.validate_token = AsyncMock(return_value=None)

            with pytest.raises(HTTPException) as exc_info:
                await get_proxy_caller_id(
                    request=mock_request, credentials=mock_credentials, db=MagicMock()
                )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_no_auth_at_all_raises_401(self):
        """No x-api-key and no JWT should raise 401."""
        from src.api.deps import get_proxy_caller_id
        from fastapi import HTTPException

        mock_request = MagicMock()
        mock_request.client.host = "172.17.0.1"
        mock_request.headers = {}

        with pytest.raises(HTTPException) as exc_info:
            await get_proxy_caller_id(
                request=mock_request, credentials=None, db=MagicMock()
            )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_loopback_without_x_api_key_falls_through_to_jwt(self):
        """127.0.0.1 without x-api-key should still require JWT."""
        from src.api.deps import get_proxy_caller_id
        from fastapi import HTTPException

        mock_request = MagicMock()
        mock_request.client.host = "127.0.0.1"
        mock_request.headers = {}  # No x-api-key

        with pytest.raises(HTTPException) as exc_info:
            await get_proxy_caller_id(
                request=mock_request, credentials=None, db=MagicMock()
            )
        assert exc_info.value.status_code == 401


class TestGetProxyBaseUrlForModel:
    """Tests for _get_proxy_base_url_for_model() in agent_core.py."""

    def test_returns_proxy_url_for_proxy_defined_model(self):
        """Models defined in llm-api-proxy.yaml should return proxy URL."""
        from src.core.agent_core import _get_proxy_base_url_for_model

        # Mock the config with a proxy-defined model
        mock_config = MagicMock()
        mock_config.models = {
            "openrouter:openai/gpt-5.2": MagicMock(provider="openrouter"),
        }
        mock_config.providers = {
            "openrouter": MagicMock(type="openai-compatible"),
        }

        with patch("src.core.agent_core.load_llm_proxy_config", return_value=mock_config):
            result = _get_proxy_base_url_for_model("openrouter:openai/gpt-5.2")

        assert result is not None
        assert "llm-proxy" in result
        assert "127.0.0.1" in result or "localhost" in result

    def test_returns_none_for_anthropic_model(self):
        """Anthropic models not in proxy config should return None."""
        from src.core.agent_core import _get_proxy_base_url_for_model

        # Mock config without the model
        mock_config = MagicMock()
        mock_config.models = {}

        with patch("src.core.agent_core.load_llm_proxy_config", return_value=mock_config):
            result = _get_proxy_base_url_for_model("claude-haiku-4-5-20251001")

        assert result is None

    def test_returns_none_for_unknown_model(self):
        """Unknown models should return None (direct Anthropic API)."""
        from src.core.agent_core import _get_proxy_base_url_for_model

        mock_config = MagicMock()
        mock_config.models = {}

        with patch("src.core.agent_core.load_llm_proxy_config", return_value=mock_config):
            result = _get_proxy_base_url_for_model("unknown-model-xyz")

        assert result is None

    def test_returns_none_when_config_not_found(self):
        """Should return None gracefully when config file doesn't exist."""
        from src.core.agent_core import _get_proxy_base_url_for_model
        from src.api.llm_proxy.config import ProxyConfigError

        with patch("src.core.agent_core.load_llm_proxy_config", side_effect=ProxyConfigError("not found")):
            result = _get_proxy_base_url_for_model("any-model")

        assert result is None

    def test_returns_none_for_undefined_provider(self):
        """Model with undefined provider should return None."""
        from src.core.agent_core import _get_proxy_base_url_for_model

        mock_config = MagicMock()
        mock_config.models = {
            "bad:model": MagicMock(provider="nonexistent"),
        }
        mock_config.providers = {}  # Provider not defined

        with patch("src.core.agent_core.load_llm_proxy_config", return_value=mock_config):
            result = _get_proxy_base_url_for_model("bad:model")

        assert result is None

    def test_proxy_url_uses_correct_port(self):
        """Proxy URL should use the specified API port."""
        from src.core.agent_core import _get_proxy_base_url_for_model

        mock_config = MagicMock()
        mock_config.models = {
            "test:model": MagicMock(provider="test-provider"),
        }
        mock_config.providers = {
            "test-provider": MagicMock(type="openai-compatible"),
        }

        with patch("src.core.agent_core.load_llm_proxy_config", return_value=mock_config):
            result = _get_proxy_base_url_for_model("test:model", api_port=8080)

        assert ":8080" in result

    def test_proxy_url_format_for_sdk(self):
        """Proxy URL should NOT include /v1 suffix (SDK adds it)."""
        from src.core.agent_core import _get_proxy_base_url_for_model

        mock_config = MagicMock()
        mock_config.models = {
            "test:model": MagicMock(provider="test-provider"),
        }
        mock_config.providers = {
            "test-provider": MagicMock(type="openai-compatible"),
        }

        with patch("src.core.agent_core.load_llm_proxy_config", return_value=mock_config):
            result = _get_proxy_base_url_for_model("test:model")

        # Should end with /llm-proxy, not /llm-proxy/v1
        # because SDK appends /v1/messages
        assert result.endswith("/api/llm-proxy")

    def test_proxy_url_includes_session_id(self):
        """Proxy URL should include session_id in path when provided."""
        from src.core.agent_core import _get_proxy_base_url_for_model

        mock_config = MagicMock()
        mock_config.models = {
            "test:model": MagicMock(provider="test-provider"),
        }
        mock_config.providers = {
            "test-provider": MagicMock(type="openai-compatible"),
        }

        with patch("src.core.agent_core.load_llm_proxy_config", return_value=mock_config):
            result = _get_proxy_base_url_for_model("test:model", session_id="abc-123")

        assert result.endswith("/api/llm-proxy/s/abc-123")

    def test_proxy_url_no_session_id_by_default(self):
        """Proxy URL should not include session path when session_id is None."""
        from src.core.agent_core import _get_proxy_base_url_for_model

        mock_config = MagicMock()
        mock_config.models = {
            "test:model": MagicMock(provider="test-provider"),
        }
        mock_config.providers = {
            "test-provider": MagicMock(type="openai-compatible"),
        }

        with patch("src.core.agent_core.load_llm_proxy_config", return_value=mock_config):
            result = _get_proxy_base_url_for_model("test:model", session_id=None)

        assert result.endswith("/api/llm-proxy")
        assert "/s/" not in result


class TestValidateResponse:
    """Tests for _validate_response() error message improvements."""

    def test_error_message_uses_result_field(self):
        """Error message should prefer response.result over subtype."""
        from src.core.agent_core import ClaudeAgent
        from src.core.exceptions import ServerError
        from unittest.mock import MagicMock

        # Create mock response with is_error=True and meaningful result
        mock_response = MagicMock()
        mock_response.is_error = True
        mock_response.result = "Connection refused: Could not reach API"
        mock_response.subtype = "success"  # This is the confusing value

        # Create agent with minimal config
        mock_config = MagicMock()
        mock_tracer = MagicMock()

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent._config = mock_config
        agent._tracer = mock_tracer

        with pytest.raises(ServerError) as exc_info:
            agent._validate_response(mock_response)

        # Should use result, not subtype
        assert "Connection refused" in str(exc_info.value)
        assert "success" not in str(exc_info.value).lower() or "Connection refused" in str(exc_info.value)

    def test_error_message_falls_back_to_subtype(self):
        """Error message should fall back to subtype if result is empty."""
        from src.core.agent_core import ClaudeAgent
        from src.core.exceptions import ServerError

        mock_response = MagicMock()
        mock_response.is_error = True
        mock_response.result = None
        mock_response.subtype = "api_error"

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent._config = MagicMock()
        agent._tracer = MagicMock()

        with pytest.raises(ServerError) as exc_info:
            agent._validate_response(mock_response)

        assert "api_error" in str(exc_info.value)

    def test_error_message_unknown_fallback(self):
        """Error message should show 'Unknown error' if both fields empty."""
        from src.core.agent_core import ClaudeAgent
        from src.core.exceptions import ServerError

        mock_response = MagicMock()
        mock_response.is_error = True
        mock_response.result = None
        mock_response.subtype = None

        agent = ClaudeAgent.__new__(ClaudeAgent)
        agent._config = MagicMock()
        agent._tracer = MagicMock()

        with pytest.raises(ServerError) as exc_info:
            agent._validate_response(mock_response)

        assert "Unknown error" in str(exc_info.value)


class TestProxyApiKeyLoading:
    """Tests for _get_api_key() with sandboxed_envs fallback."""

    def test_uses_environment_variable_first(self):
        """Should use environment variable if set."""
        from src.api.routes.llm_proxy import _get_api_key

        mock_provider = MagicMock()
        mock_provider.api_key_env = "TEST_API_KEY"

        providers = {"test": mock_provider}

        with patch.dict("os.environ", {"TEST_API_KEY": "env-key-value"}):
            with patch("src.api.routes.llm_proxy.load_sandboxed_envs", return_value={}):
                result = _get_api_key("test", providers)

        assert result == "env-key-value"

    def test_falls_back_to_sandboxed_envs(self):
        """Should fall back to sandboxed_envs if env var not set."""
        from src.api.routes.llm_proxy import _get_api_key

        mock_provider = MagicMock()
        mock_provider.api_key_env = "OPENROUTER_API_KEY"

        providers = {"openrouter": mock_provider}

        with patch.dict("os.environ", {}, clear=True):
            with patch("src.api.routes.llm_proxy.load_sandboxed_envs", return_value={
                "OPENROUTER_API_KEY": "sandboxed-key-value"
            }):
                result = _get_api_key("openrouter", providers)

        assert result == "sandboxed-key-value"

    def test_raises_error_when_key_not_found(self):
        """Should raise HTTPException if key not found anywhere."""
        from src.api.routes.llm_proxy import _get_api_key
        from fastapi import HTTPException

        mock_provider = MagicMock()
        mock_provider.api_key_env = "MISSING_KEY"

        providers = {"test": mock_provider}

        with patch.dict("os.environ", {}, clear=True):
            with patch("src.api.routes.llm_proxy.load_sandboxed_envs", return_value={}):
                with pytest.raises(HTTPException) as exc_info:
                    _get_api_key("test", providers)

        assert exc_info.value.status_code == 500
        assert "Missing API key" in exc_info.value.detail

    def test_raises_error_for_unknown_provider(self):
        """Should raise HTTPException for unknown provider."""
        from src.api.routes.llm_proxy import _get_api_key
        from fastapi import HTTPException

        providers = {}  # Empty providers dict

        with pytest.raises(HTTPException) as exc_info:
            _get_api_key("nonexistent", providers)

        assert exc_info.value.status_code == 400
        assert "Unknown provider" in exc_info.value.detail


class TestProxyEndpointRouting:
    """Tests for proxy endpoint routing logic."""

    def test_resolve_target_finds_mapped_model(self):
        """_resolve_target should find models in config."""
        from src.api.routes.llm_proxy import _resolve_target

        mock_config = MagicMock()
        mock_config.models = {
            "openrouter:openai/gpt-5.2": MagicMock(
                provider="openrouter",
                target_model="openai/gpt-5.2"
            ),
        }
        mock_config.providers = {"openrouter": MagicMock()}
        mock_config.routing = {"allow_unmapped_models": False}

        with patch("src.api.routes.llm_proxy.load_llm_proxy_config", return_value=mock_config):
            provider, target_model, providers = _resolve_target("openrouter:openai/gpt-5.2")

        assert provider == "openrouter"
        assert target_model == "openai/gpt-5.2"

    def test_resolve_target_rejects_unmapped_model(self):
        """_resolve_target should reject unmapped models when not allowed."""
        from src.api.routes.llm_proxy import _resolve_target
        from fastapi import HTTPException

        mock_config = MagicMock()
        mock_config.models = {}
        mock_config.routing = {"allow_unmapped_models": False}

        with patch("src.api.routes.llm_proxy.load_llm_proxy_config", return_value=mock_config):
            with pytest.raises(HTTPException) as exc_info:
                _resolve_target("unknown-model")

        assert exc_info.value.status_code == 400
        assert "Unknown model mapping" in exc_info.value.detail


class TestProxyOpenAIFunction:
    """Tests for _proxy_openai function - non-streaming mode."""

    @pytest.mark.asyncio
    async def test_non_streaming_mode_uses_post(self):
        """_proxy_openai non-streaming mode should use POST and return JSON."""
        from src.api.routes.llm_proxy import _proxy_openai
        from fastapi.responses import JSONResponse

        mock_provider = MagicMock()
        mock_provider.base_url = "https://api.openrouter.ai/api/v1"

        # Create a mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "id": "test-id",
            "choices": [{
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        }

        class MockClient:
            async def post(self, url, **kwargs):
                return mock_response
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=MockClient()):
            payload = {
                "model": "openrouter:openai/gpt-5.2",
                "messages": [{"role": "user", "content": "Hello"}],
            }

            result = await _proxy_openai(
                payload, mock_provider, "test-key", "openai/gpt-5.2", stream=False
            )

        assert isinstance(result, JSONResponse)

    @pytest.mark.asyncio
    async def test_translates_response_to_claude_format(self):
        """_proxy_openai should translate OpenAI response to Claude format."""
        from src.api.routes.llm_proxy import _proxy_openai
        import json

        mock_provider = MagicMock()
        mock_provider.base_url = "https://api.example.com/v1"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "id": "chatcmpl-123",
            "choices": [{
                "message": {"role": "assistant", "content": "Hello there!"},
                "finish_reason": "stop"
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}
        }

        class MockClient:
            async def post(self, url, **kwargs):
                return mock_response
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=MockClient()):
            payload = {
                "model": "test:model",
                "messages": [{"role": "user", "content": "Hello"}],
            }

            result = await _proxy_openai(
                payload, mock_provider, "test-key", "gpt-4", stream=False
            )

        # Parse the JSONResponse body
        body = json.loads(result.body.decode())

        # Verify Claude format
        assert body["type"] == "message"
        assert body["role"] == "assistant"
        assert body["model"] == "gpt-4"
        assert body["content"][0]["type"] == "text"
        assert body["content"][0]["text"] == "Hello there!"
        assert body["stop_reason"] == "end_turn"
        assert body["usage"]["input_tokens"] == 10
        assert body["usage"]["output_tokens"] == 5


class TestLlmProxyConfig:
    """Tests for LLM proxy configuration loading."""

    def test_load_config_parses_providers(self):
        """Config loader should parse provider configs correctly."""
        from src.api.llm_proxy.config import load_llm_proxy_config, LlmProxyConfig

        # Test with actual config if it exists
        config_path = Path(__file__).parent.parent.parent / "config" / "llm-api-proxy.yaml"
        if not config_path.exists():
            pytest.skip("llm-api-proxy.yaml not found")

        config = load_llm_proxy_config()

        assert isinstance(config, LlmProxyConfig)
        assert len(config.providers) > 0
        assert "anthropic" in config.providers or "openrouter" in config.providers

    def test_load_config_parses_models(self):
        """Config loader should parse model mappings correctly."""
        from src.api.llm_proxy.config import load_llm_proxy_config

        config_path = Path(__file__).parent.parent.parent / "config" / "llm-api-proxy.yaml"
        if not config_path.exists():
            pytest.skip("llm-api-proxy.yaml not found")

        config = load_llm_proxy_config()

        # Should have at least one model mapping
        assert len(config.models) > 0

        # Each model should have provider and target_model
        for model_name, mapping in config.models.items():
            assert mapping.provider is not None
            assert mapping.target_model is not None


class TestAgentCoreProxyIntegration:
    """Integration tests for proxy routing in agent core."""

    def test_build_options_sets_anthropic_base_url_for_proxy_model(self):
        """_build_options should set ANTHROPIC_BASE_URL for proxy models."""
        from src.core.agent_core import ClaudeAgent, _get_proxy_base_url_for_model
        from src.core.schemas import AgentConfig, SessionContext

        # Mock the proxy config
        mock_config = MagicMock()
        mock_config.models = {
            "openrouter:openai/gpt-5.2": MagicMock(provider="openrouter"),
        }
        mock_config.providers = {
            "openrouter": MagicMock(type="openai-compatible"),
        }

        with patch("src.core.agent_core.load_llm_proxy_config", return_value=mock_config):
            proxy_url = _get_proxy_base_url_for_model("openrouter:openai/gpt-5.2")

        assert proxy_url is not None
        assert "ANTHROPIC_BASE_URL" not in proxy_url  # Just the URL, not the env var name
        assert "llm-proxy" in proxy_url

    def test_build_options_no_proxy_for_claude_model(self):
        """_build_options should not set proxy URL for Claude models."""
        from src.core.agent_core import _get_proxy_base_url_for_model

        mock_config = MagicMock()
        mock_config.models = {}  # Claude models not in proxy config

        with patch("src.core.agent_core.load_llm_proxy_config", return_value=mock_config):
            proxy_url = _get_proxy_base_url_for_model("claude-sonnet-4-5-20250929")

        assert proxy_url is None


class TestSSEFormat:
    """Tests for SSE streaming format compliance with Anthropic SDK requirements.

    The Claude Agent SDK requires SSE events to have `event: <type>` prefix lines
    before each `data:` line. Without this prefix, the SDK won't parse events correctly.

    Correct format:
        event: message_start
        data: {"type":"message_start","message":{...}}

    Incorrect format (won't work):
        data: {"type":"message_start","message":{...}}
    """

    def test_sse_events_have_event_prefix(self):
        """All SSE events must have 'event: <type>' line before 'data:' line."""
        import json

        # Sample SSE output from stream_openai_to_claude
        sample_events = [
            'event: message_start\ndata: {"type":"message_start","message":{"id":"test"}}\n\n',
            'event: content_block_start\ndata: {"type":"content_block_start","index":0}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"text":"Hi"}}\n\n',
            'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        for event in sample_events:
            lines = event.strip().split('\n')
            # First line must be event: <type>
            assert lines[0].startswith('event: '), f"Missing event prefix in: {event}"
            # Second line must be data:
            assert lines[1].startswith('data: '), f"Missing data prefix in: {event}"
            # Event type should match JSON type field
            event_type = lines[0].replace('event: ', '')
            data = json.loads(lines[1].replace('data: ', ''))
            assert data.get('type') == event_type, f"Event type mismatch: {event_type} vs {data.get('type')}"

    def test_sse_format_matches_anthropic_specification(self):
        """SSE format must match Anthropic's expected format for SDK compatibility."""
        import re

        # Pattern for valid Anthropic SSE event
        # Must be: "event: <type>\ndata: <json>\n\n"
        anthropic_sse_pattern = re.compile(
            r'^event: (message_start|message_delta|message_stop|'
            r'content_block_start|content_block_delta|content_block_stop)\n'
            r'data: \{.*\}\n\n$',
            re.DOTALL
        )

        valid_events = [
            'event: message_start\ndata: {"type":"message_start"}\n\n',
            'event: content_block_delta\ndata: {"type":"content_block_delta"}\n\n',
            'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]

        for event in valid_events:
            assert anthropic_sse_pattern.match(event), f"Invalid SSE format: {event}"

    def test_missing_event_prefix_is_invalid(self):
        """SSE events without 'event:' prefix are invalid and will break SDK parsing."""
        import re

        # Pattern for valid Anthropic SSE event
        anthropic_sse_pattern = re.compile(
            r'^event: \w+\ndata: \{.*\}\n\n$',
            re.DOTALL
        )

        # These formats are INVALID (missing event: prefix)
        invalid_events = [
            'data: {"type":"message_start"}\n\n',  # Missing event: line
            '{"type":"message_start"}\n\n',  # No prefixes at all
        ]

        for event in invalid_events:
            assert not anthropic_sse_pattern.match(event), f"Should be invalid: {event}"


class TestStreamOpenAIToClaude:
    """Tests for stream_openai_to_claude() translator function."""

    @pytest.mark.asyncio
    async def test_yields_correct_event_sequence(self):
        """stream_openai_to_claude should yield events in correct order."""
        from src.api.llm_proxy.translator import stream_openai_to_claude
        import json

        # Mock OpenAI streaming response
        class MockAsyncIterator:
            def __init__(self, lines):
                self.lines = iter(lines)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.lines)
                except StopIteration:
                    raise StopAsyncIteration

        mock_response = MagicMock()
        mock_response.aiter_lines = lambda: MockAsyncIterator([
            'data: {"id":"1","choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"id":"1","choices":[{"delta":{"content":" world"}}]}',
            'data: {"id":"1","choices":[{"delta":{}}],"usage":{"prompt_tokens":10,"completion_tokens":5}}',
            'data: [DONE]',
        ])

        events = []
        async for event in stream_openai_to_claude(mock_response, "test-model"):
            events.append(event)

        # Verify event sequence
        assert len(events) >= 6, f"Expected at least 6 events, got {len(events)}"

        # First event: message_start
        assert 'event: message_start' in events[0]
        assert '"type":"message_start"' in events[0] or '"type": "message_start"' in events[0]

        # Second event: content_block_start
        assert 'event: content_block_start' in events[1]

        # Content deltas should follow
        content_events = [e for e in events if 'content_block_delta' in e]
        assert len(content_events) >= 2, "Should have content delta events"

        # Last events: content_block_stop, message_delta, message_stop
        assert 'event: content_block_stop' in events[-3]
        assert 'event: message_delta' in events[-2]
        assert 'event: message_stop' in events[-1]

    @pytest.mark.asyncio
    async def test_all_events_have_event_prefix(self):
        """Every yielded event must have 'event:' prefix line."""
        from src.api.llm_proxy.translator import stream_openai_to_claude

        class MockAsyncIterator:
            def __init__(self, lines):
                self.lines = iter(lines)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.lines)
                except StopIteration:
                    raise StopAsyncIteration

        mock_response = MagicMock()
        mock_response.aiter_lines = lambda: MockAsyncIterator([
            'data: {"id":"1","choices":[{"delta":{"content":"Hi"}}]}',
            'data: [DONE]',
        ])

        async for event in stream_openai_to_claude(mock_response, "test-model"):
            # Every event must start with 'event: '
            assert event.startswith('event: '), f"Event missing prefix: {event[:50]}"
            # Must have data line after event line
            assert '\ndata: ' in event, f"Event missing data line: {event[:50]}"

    @pytest.mark.asyncio
    async def test_extracts_usage_from_openai_stream(self):
        """stream_openai_to_claude should extract token usage from OpenAI chunks."""
        from src.api.llm_proxy.translator import stream_openai_to_claude
        import json

        class MockAsyncIterator:
            def __init__(self, lines):
                self.lines = iter(lines)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.lines)
                except StopIteration:
                    raise StopAsyncIteration

        mock_response = MagicMock()
        mock_response.aiter_lines = lambda: MockAsyncIterator([
            'data: {"id":"1","choices":[{"delta":{"content":"Test"}}]}',
            'data: {"id":"1","choices":[{"delta":{}}],"usage":{"prompt_tokens":100,"completion_tokens":50}}',
            'data: [DONE]',
        ])

        events = []
        async for event in stream_openai_to_claude(mock_response, "test-model"):
            events.append(event)

        # Find message_delta event (second to last)
        message_delta_events = [e for e in events if 'message_delta' in e]
        assert len(message_delta_events) == 1

        # Extract the data part and verify usage
        data_line = message_delta_events[0].split('\ndata: ')[1].strip()
        delta_data = json.loads(data_line)
        assert delta_data['usage']['output_tokens'] == 50

    @pytest.mark.asyncio
    async def test_handles_tool_calls(self):
        """stream_openai_to_claude should handle tool call chunks."""
        from src.api.llm_proxy.translator import stream_openai_to_claude
        import json

        class MockAsyncIterator:
            def __init__(self, lines):
                self.lines = iter(lines)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.lines)
                except StopIteration:
                    raise StopAsyncIteration

        mock_response = MagicMock()
        mock_response.aiter_lines = lambda: MockAsyncIterator([
            'data: {"id":"1","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_123","function":{"name":"get_weather","arguments":"{\\"loc"}}]}}]}',
            'data: {"id":"1","choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"ation\\":\\"NYC\\"}"}}]}}]}',
            'data: [DONE]',
        ])

        events = []
        async for event in stream_openai_to_claude(mock_response, "test-model"):
            events.append(event)

        # Should have tool_use content block
        tool_events = [e for e in events if '"type":"tool_use"' in e or '"type": "tool_use"' in e]
        assert len(tool_events) >= 1, "Should have tool_use content block"

    @pytest.mark.asyncio
    async def test_tool_input_streamed_via_input_json_delta(self):
        """Tool input must be streamed via input_json_delta, not in content_block_start.

        The Claude SDK expects:
        1. content_block_start with empty input: {}
        2. content_block_delta with type: input_json_delta containing the actual input
        3. content_block_stop

        If input is put directly in content_block_start, the SDK ignores it and the
        tool receives empty {} arguments.
        """
        from src.api.llm_proxy.translator import stream_openai_to_claude
        import json

        class MockAsyncIterator:
            def __init__(self, lines):
                self.lines = iter(lines)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.lines)
                except StopIteration:
                    raise StopAsyncIteration

        mock_response = MagicMock()
        # Simulate a tool call with file_path argument
        mock_response.aiter_lines = lambda: MockAsyncIterator([
            'data: {"id":"1","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_456","function":{"name":"Write","arguments":"{\\"file_path\\":"}}]}}]}',
            'data: {"id":"1","choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"test.txt\\",\\"content\\":\\"hello\\"}"}}]}}]}',
            'data: [DONE]',
        ])

        events = []
        async for event in stream_openai_to_claude(mock_response, "test-model"):
            events.append(event)

        # Find content_block_start for tool_use
        tool_start_events = [e for e in events if 'content_block_start' in e and 'tool_use' in e]
        assert len(tool_start_events) == 1, "Should have exactly one tool_use content_block_start"

        # The content_block_start should have EMPTY input: {}
        tool_start_data = json.loads(tool_start_events[0].split('\ndata: ')[1].strip())
        assert tool_start_data['content_block']['input'] == {}, \
            f"content_block_start should have empty input, got: {tool_start_data['content_block']['input']}"

        # Find input_json_delta event
        input_delta_events = [e for e in events if 'input_json_delta' in e]
        assert len(input_delta_events) >= 1, "Should have input_json_delta event for tool input"

        # The input_json_delta should contain the actual input
        delta_data = json.loads(input_delta_events[0].split('\ndata: ')[1].strip())
        assert delta_data['delta']['type'] == 'input_json_delta'
        partial_json = delta_data['delta']['partial_json']
        tool_input = json.loads(partial_json)
        assert 'file_path' in tool_input, f"Tool input should have file_path, got: {tool_input}"
        assert tool_input['file_path'] == 'test.txt'
        assert tool_input['content'] == 'hello'

    @pytest.mark.asyncio
    async def test_stop_reason_is_tool_use_when_tools_called(self):
        """message_delta should have stop_reason: tool_use when tool calls are present."""
        from src.api.llm_proxy.translator import stream_openai_to_claude
        import json

        class MockAsyncIterator:
            def __init__(self, lines):
                self.lines = iter(lines)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.lines)
                except StopIteration:
                    raise StopAsyncIteration

        mock_response = MagicMock()
        mock_response.aiter_lines = lambda: MockAsyncIterator([
            'data: {"id":"1","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_789","function":{"name":"Read","arguments":"{\\"file_path\\":\\"config.yaml\\"}"}}]}}]}',
            'data: [DONE]',
        ])

        events = []
        async for event in stream_openai_to_claude(mock_response, "test-model"):
            events.append(event)

        # Find message_delta event
        message_delta_events = [e for e in events if 'message_delta' in e]
        assert len(message_delta_events) == 1

        delta_data = json.loads(message_delta_events[0].split('\ndata: ')[1].strip())
        assert delta_data['delta']['stop_reason'] == 'tool_use', \
            f"stop_reason should be 'tool_use' when tools are called, got: {delta_data['delta']['stop_reason']}"

    @pytest.mark.asyncio
    async def test_stop_reason_is_end_turn_without_tools(self):
        """message_delta should have stop_reason: end_turn when no tool calls."""
        from src.api.llm_proxy.translator import stream_openai_to_claude
        import json

        class MockAsyncIterator:
            def __init__(self, lines):
                self.lines = iter(lines)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.lines)
                except StopIteration:
                    raise StopAsyncIteration

        mock_response = MagicMock()
        mock_response.aiter_lines = lambda: MockAsyncIterator([
            'data: {"id":"1","choices":[{"delta":{"content":"Hello!"}}]}',
            'data: [DONE]',
        ])

        events = []
        async for event in stream_openai_to_claude(mock_response, "test-model"):
            events.append(event)

        # Find message_delta event
        message_delta_events = [e for e in events if 'message_delta' in e]
        assert len(message_delta_events) == 1

        delta_data = json.loads(message_delta_events[0].split('\ndata: ')[1].strip())
        assert delta_data['delta']['stop_reason'] == 'end_turn', \
            f"stop_reason should be 'end_turn' when no tools called, got: {delta_data['delta']['stop_reason']}"

    @pytest.mark.asyncio
    async def test_text_block_closes_before_tool_blocks(self):
        """Text block (index 0) must close before tool blocks start.

        Event order must be:
        1. content_block_start (text, index 0)
        2. content_block_delta (text)
        3. content_block_stop (index 0) <-- text block closes
        4. content_block_start (tool_use, index 1)
        5. content_block_delta (input_json_delta)
        6. content_block_stop (index 1)
        7. message_delta
        8. message_stop
        """
        from src.api.llm_proxy.translator import stream_openai_to_claude
        import json

        class MockAsyncIterator:
            def __init__(self, lines):
                self.lines = iter(lines)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.lines)
                except StopIteration:
                    raise StopAsyncIteration

        mock_response = MagicMock()
        mock_response.aiter_lines = lambda: MockAsyncIterator([
            'data: {"id":"1","choices":[{"delta":{"content":"I will help you."}}]}',
            'data: {"id":"1","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_abc","function":{"name":"Read","arguments":"{\\"file_path\\":\\"test.txt\\"}"}}]}}]}',
            'data: [DONE]',
        ])

        events = []
        async for event in stream_openai_to_claude(mock_response, "test-model"):
            events.append(event)

        # Find indices of key events
        text_block_stop_idx = None
        tool_block_start_idx = None

        for i, event in enumerate(events):
            if 'content_block_stop' in event and '"index":0' in event.replace(' ', ''):
                text_block_stop_idx = i
            if 'content_block_start' in event and 'tool_use' in event:
                tool_block_start_idx = i
                break  # We only need the first tool block

        assert text_block_stop_idx is not None, "Text block stop not found"
        assert tool_block_start_idx is not None, "Tool block start not found"
        assert text_block_stop_idx < tool_block_start_idx, \
            f"Text block (index 0) must close before tool blocks start. " \
            f"Text stop at position {text_block_stop_idx}, tool start at {tool_block_start_idx}"


class TestProxyOpenAIStreaming:
    """Tests for _proxy_openai streaming mode."""

    @pytest.mark.asyncio
    async def test_returns_streaming_response_when_stream_true(self):
        """_proxy_openai should return StreamingResponse when stream=True."""
        from src.api.routes.llm_proxy import _proxy_openai
        from fastapi.responses import StreamingResponse
        import httpx

        mock_provider = MagicMock()
        mock_provider.base_url = "https://api.example.com/v1"

        # Mock the httpx client and stream context
        mock_stream_response = MagicMock()
        mock_stream_response.raise_for_status = MagicMock()

        async def mock_aiter_lines():
            yield 'data: {"id":"1","choices":[{"delta":{"content":"Hi"}}]}'
            yield 'data: [DONE]'

        mock_stream_response.aiter_lines = mock_aiter_lines

        class MockStreamContext:
            async def __aenter__(self):
                return mock_stream_response
            async def __aexit__(self, *args):
                pass

        class MockClient:
            def stream(self, *args, **kwargs):
                return MockStreamContext()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=MockClient()):
            payload = {
                "model": "test:model",
                "messages": [{"role": "user", "content": "Hello"}],
            }

            result = await _proxy_openai(
                payload, mock_provider, "test-key", "gpt-4", stream=True
            )

        assert isinstance(result, StreamingResponse)
        assert result.media_type == "text/event-stream"

    @pytest.mark.asyncio
    async def test_streaming_passes_stream_true_to_api(self):
        """_proxy_openai should pass stream=True in request body."""
        from src.api.routes.llm_proxy import _proxy_openai
        import httpx

        mock_provider = MagicMock()
        mock_provider.base_url = "https://api.example.com/v1"

        captured_body = {}

        mock_stream_response = MagicMock()
        mock_stream_response.raise_for_status = MagicMock()

        async def mock_aiter_lines():
            yield 'data: [DONE]'

        mock_stream_response.aiter_lines = mock_aiter_lines

        class MockStreamContext:
            async def __aenter__(self):
                return mock_stream_response
            async def __aexit__(self, *args):
                pass

        class MockClient:
            def stream(self, method, url, **kwargs):
                captured_body.update(kwargs.get('json', {}))
                return MockStreamContext()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=MockClient()):
            payload = {
                "model": "test:model",
                "messages": [{"role": "user", "content": "Hello"}],
            }

            result = await _proxy_openai(
                payload, mock_provider, "test-key", "gpt-4", stream=True
            )

            # Force the generator to run by consuming it
            # (StreamingResponse is lazy)
            async for _ in result.body_iterator:
                pass

        assert captured_body.get('stream') is True, "Should pass stream=True to API"

    @pytest.mark.asyncio
    async def test_non_streaming_returns_json_response(self):
        """_proxy_openai should return JSONResponse when stream=False."""
        from src.api.routes.llm_proxy import _proxy_openai
        from fastapi.responses import JSONResponse
        import httpx

        mock_provider = MagicMock()
        mock_provider.base_url = "https://api.example.com/v1"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "test",
            "choices": [{"message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}
        }
        mock_response.raise_for_status = MagicMock()

        class MockClient:
            async def post(self, *args, **kwargs):
                return mock_response
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=MockClient()):
            payload = {
                "model": "test:model",
                "messages": [{"role": "user", "content": "Hello"}],
            }

            result = await _proxy_openai(
                payload, mock_provider, "test-key", "gpt-4", stream=False
            )

        assert isinstance(result, JSONResponse)


class TestMapOpenAIUsage:
    """Tests for _map_openai_usage() helper function."""

    def test_maps_basic_token_counts(self):
        """Should map prompt_tokens and completion_tokens correctly."""
        from src.api.llm_proxy.translator import _map_openai_usage

        openai_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }

        result = _map_openai_usage(openai_usage)

        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50

    def test_extracts_cached_tokens(self):
        """Should extract cached_tokens from prompt_tokens_details."""
        from src.api.llm_proxy.translator import _map_openai_usage

        openai_usage = {
            "prompt_tokens": 17077,
            "completion_tokens": 78,
            "prompt_tokens_details": {
                "cached_tokens": 7808,
                "audio_tokens": 0,
            },
        }

        result = _map_openai_usage(openai_usage)

        assert result["input_tokens"] == 17077
        assert result["output_tokens"] == 78
        assert result["cache_read_input_tokens"] == 7808

    def test_no_cache_field_when_zero_cached(self):
        """Should not include cache_read_input_tokens when cached_tokens is 0."""
        from src.api.llm_proxy.translator import _map_openai_usage

        openai_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_tokens_details": {
                "cached_tokens": 0,
            },
        }

        result = _map_openai_usage(openai_usage)

        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50
        assert "cache_read_input_tokens" not in result

    def test_handles_missing_prompt_tokens_details(self):
        """Should handle missing prompt_tokens_details gracefully."""
        from src.api.llm_proxy.translator import _map_openai_usage

        openai_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
        }

        result = _map_openai_usage(openai_usage)

        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50
        assert "cache_read_input_tokens" not in result

    def test_handles_none_prompt_tokens_details(self):
        """Should handle null/None prompt_tokens_details."""
        from src.api.llm_proxy.translator import _map_openai_usage

        openai_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_tokens_details": None,
        }

        result = _map_openai_usage(openai_usage)

        assert result["input_tokens"] == 100
        assert result["output_tokens"] == 50
        assert "cache_read_input_tokens" not in result

    def test_handles_empty_usage_dict(self):
        """Should return zeros for empty usage dict."""
        from src.api.llm_proxy.translator import _map_openai_usage

        result = _map_openai_usage({})

        assert result["input_tokens"] == 0
        assert result["output_tokens"] == 0


class TestStreamingUsageExtraction:
    """Tests for usage extraction in streaming mode."""

    @pytest.mark.asyncio
    async def test_extracts_cached_tokens_in_streaming(self):
        """stream_openai_to_claude should extract cached_tokens from final chunk."""
        from src.api.llm_proxy.translator import stream_openai_to_claude
        import json

        class MockAsyncIterator:
            def __init__(self, lines):
                self.lines = iter(lines)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.lines)
                except StopIteration:
                    raise StopAsyncIteration

        mock_response = MagicMock()
        mock_response.aiter_lines = lambda: MockAsyncIterator([
            'data: {"id":"1","choices":[{"delta":{"content":"Test"}}]}',
            # Final chunk with usage including prompt caching
            'data: {"id":"1","choices":[{"delta":{}}],"usage":{"prompt_tokens":17077,"completion_tokens":78,"prompt_tokens_details":{"cached_tokens":7808,"audio_tokens":0}}}',
            'data: [DONE]',
        ])

        events = []
        async for event in stream_openai_to_claude(mock_response, "test-model"):
            events.append(event)

        # Find message_delta event
        message_delta_events = [e for e in events if 'message_delta' in e]
        assert len(message_delta_events) == 1

        data_line = message_delta_events[0].split('\ndata: ')[1].strip()
        delta_data = json.loads(data_line)

        assert delta_data['usage']['input_tokens'] == 17077
        assert delta_data['usage']['output_tokens'] == 78
        assert delta_data['usage']['cache_read_input_tokens'] == 7808

    @pytest.mark.asyncio
    async def test_no_cache_field_when_no_caching(self):
        """Should not include cache_read_input_tokens when no caching."""
        from src.api.llm_proxy.translator import stream_openai_to_claude
        import json

        class MockAsyncIterator:
            def __init__(self, lines):
                self.lines = iter(lines)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.lines)
                except StopIteration:
                    raise StopAsyncIteration

        mock_response = MagicMock()
        mock_response.aiter_lines = lambda: MockAsyncIterator([
            'data: {"id":"1","choices":[{"delta":{"content":"Test"}}]}',
            'data: {"id":"1","choices":[{"delta":{}}],"usage":{"prompt_tokens":100,"completion_tokens":50}}',
            'data: [DONE]',
        ])

        events = []
        async for event in stream_openai_to_claude(mock_response, "test-model"):
            events.append(event)

        message_delta_events = [e for e in events if 'message_delta' in e]
        data_line = message_delta_events[0].split('\ndata: ')[1].strip()
        delta_data = json.loads(data_line)

        assert delta_data['usage']['input_tokens'] == 100
        assert delta_data['usage']['output_tokens'] == 50
        assert 'cache_read_input_tokens' not in delta_data['usage']


class TestStreamOptionsIncludeUsage:
    """Tests for stream_options.include_usage being set."""

    @pytest.mark.asyncio
    async def test_streaming_sets_include_usage(self):
        """_proxy_openai should set stream_options.include_usage for streaming."""
        from src.api.routes.llm_proxy import _proxy_openai

        mock_provider = MagicMock()
        mock_provider.base_url = "https://api.example.com/v1"

        captured_body = {}

        mock_stream_response = MagicMock()
        mock_stream_response.raise_for_status = MagicMock()

        async def mock_aiter_lines():
            yield 'data: [DONE]'

        mock_stream_response.aiter_lines = mock_aiter_lines

        class MockStreamContext:
            async def __aenter__(self):
                return mock_stream_response
            async def __aexit__(self, *args):
                pass

        class MockClient:
            def stream(self, method, url, **kwargs):
                captured_body.update(kwargs.get('json', {}))
                return MockStreamContext()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=MockClient()):
            payload = {
                "model": "test:model",
                "messages": [{"role": "user", "content": "Hello"}],
            }

            result = await _proxy_openai(
                payload, mock_provider, "test-key", "gpt-4", stream=True
            )

            # Consume the generator
            async for _ in result.body_iterator:
                pass

        assert captured_body.get('stream') is True
        assert captured_body.get('stream_options') == {"include_usage": True}

    @pytest.mark.asyncio
    async def test_non_streaming_does_not_set_stream_options(self):
        """_proxy_openai non-streaming should not set stream_options."""
        from src.api.routes.llm_proxy import _proxy_openai

        mock_provider = MagicMock()
        mock_provider.base_url = "https://api.example.com/v1"

        captured_body = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "test",
            "choices": [{"message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}
        }
        mock_response.raise_for_status = MagicMock()

        class MockClient:
            async def post(self, url, **kwargs):
                captured_body.update(kwargs.get('json', {}))
                return mock_response
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=MockClient()):
            payload = {
                "model": "test:model",
                "messages": [{"role": "user", "content": "Hello"}],
            }

            await _proxy_openai(
                payload, mock_provider, "test-key", "gpt-4", stream=False
            )

        assert captured_body.get('stream') is False
        assert 'stream_options' not in captured_body


class TestProxyDebugMode:
    """Tests for LLM proxy debug mode functionality."""

    def test_is_debug_enabled_returns_config_value(self):
        """_is_debug_enabled should return value from config."""
        from src.api.routes.llm_proxy import _is_debug_enabled

        mock_config = MagicMock()
        mock_config.proxy.debug = True

        with patch("src.api.routes.llm_proxy.load_llm_proxy_config", return_value=mock_config):
            result = _is_debug_enabled()

        assert result is True

    def test_is_debug_enabled_returns_false_on_error(self):
        """_is_debug_enabled should return False if config load fails."""
        from src.api.routes.llm_proxy import _is_debug_enabled
        from src.api.llm_proxy.config import ProxyConfigError

        with patch("src.api.routes.llm_proxy.load_llm_proxy_config", side_effect=ProxyConfigError("not found")):
            result = _is_debug_enabled()

        assert result is False

    def test_save_debug_file_creates_directory(self, tmp_path):
        """_save_debug_file should create debug directory if needed."""
        from src.api.routes.llm_proxy import _save_debug_file
        import json

        # Temporarily override DEBUG_DIR
        test_debug_dir = tmp_path / "llm_proxy_debug"

        with patch("src.api.routes.llm_proxy.DEBUG_DIR", test_debug_dir):
            _save_debug_file("test_file.json", {"key": "value"})

        assert test_debug_dir.exists()
        saved_file = test_debug_dir / "test_file.json"
        assert saved_file.exists()

        content = json.loads(saved_file.read_text())
        assert content["key"] == "value"
        assert "timestamp" in content

    def test_save_debug_file_adds_timestamp(self, tmp_path):
        """_save_debug_file should add timestamp to saved data."""
        from src.api.routes.llm_proxy import _save_debug_file
        import json

        test_debug_dir = tmp_path / "llm_proxy_debug"

        with patch("src.api.routes.llm_proxy.DEBUG_DIR", test_debug_dir):
            _save_debug_file("timestamped.json", {"data": "test"})

        content = json.loads((test_debug_dir / "timestamped.json").read_text())
        assert "timestamp" in content
        # Timestamp should be ISO format
        assert "T" in content["timestamp"]

    def test_save_debug_file_handles_errors_gracefully(self, tmp_path):
        """_save_debug_file should not raise on write errors."""
        from src.api.routes.llm_proxy import _save_debug_file

        # Point to an invalid path (file instead of directory)
        invalid_path = tmp_path / "not_a_dir.txt"
        invalid_path.write_text("I'm a file")

        with patch("src.api.routes.llm_proxy.DEBUG_DIR", invalid_path):
            # Should not raise
            _save_debug_file("test.json", {"key": "value"})

    def test_save_debug_file_redacts_sensitive_fields(self, tmp_path):
        """_save_debug_file should redact API keys and tokens."""
        from src.api.routes.llm_proxy import _save_debug_file
        import json

        test_debug_dir = tmp_path / "llm_proxy_debug"

        with patch("src.api.routes.llm_proxy.DEBUG_DIR", test_debug_dir):
            _save_debug_file("redact_test.json", {
                "payload": {
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "hello"}],
                },
                "headers": {
                    "Authorization": "Bearer sk-real-secret-key",
                    "x-api-key": "sk-ant-real-secret",
                    "Content-Type": "application/json",
                },
                "api_key": "sk-should-be-hidden",
            })

        content = json.loads((test_debug_dir / "redact_test.json").read_text())
        # Sensitive values should be redacted
        assert content["headers"]["Authorization"] == "***REDACTED***"
        assert content["headers"]["x-api-key"] == "***REDACTED***"
        assert content["api_key"] == "***REDACTED***"
        # Non-sensitive values should be preserved
        assert content["headers"]["Content-Type"] == "application/json"
        assert content["payload"]["model"] == "test-model"

    def test_debug_file_cleanup_enforces_max_files(self, tmp_path):
        """Cleanup should remove oldest files when exceeding max."""
        from src.api.routes.llm_proxy import _cleanup_debug_files, DEBUG_MAX_FILES
        import time

        test_debug_dir = tmp_path / "llm_proxy_debug"
        test_debug_dir.mkdir()

        # Create more files than the limit
        for i in range(DEBUG_MAX_FILES + 10):
            (test_debug_dir / f"test_{i:04d}.json").write_text("{}")
            time.sleep(0.001)  # Ensure different mtimes

        with patch("src.api.routes.llm_proxy.DEBUG_DIR", test_debug_dir):
            _cleanup_debug_files()

        remaining = list(test_debug_dir.glob("*.json"))
        assert len(remaining) <= DEBUG_MAX_FILES

    @pytest.mark.asyncio
    async def test_debug_mode_saves_request_file(self, tmp_path):
        """Debug mode should save request to in_<uid>.json."""
        from src.api.routes.llm_proxy import _proxy_openai
        import json

        mock_provider = MagicMock()
        mock_provider.base_url = "https://api.example.com/v1"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "test",
            "choices": [{"message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}
        }
        mock_response.raise_for_status = MagicMock()

        class MockClient:
            async def post(self, *args, **kwargs):
                return mock_response
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        test_debug_dir = tmp_path / "llm_proxy_debug"

        with patch("httpx.AsyncClient", return_value=MockClient()):
            with patch("src.api.routes.llm_proxy._is_debug_enabled", return_value=True):
                with patch("src.api.routes.llm_proxy.DEBUG_DIR", test_debug_dir):
                    payload = {
                        "model": "test:model",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }

                    await _proxy_openai(
                        payload, mock_provider, "test-key", "gpt-4", stream=False
                    )

        # Check in_*.json file was created
        in_files = list(test_debug_dir.glob("in_*.json"))
        assert len(in_files) == 1

        content = json.loads(in_files[0].read_text())
        assert "request_uid" in content
        assert content["target_model"] == "gpt-4"
        assert "payload" in content

    @pytest.mark.asyncio
    async def test_debug_mode_saves_response_file(self, tmp_path):
        """Debug mode should save response to out_<uid>.json."""
        from src.api.routes.llm_proxy import _proxy_openai
        import json

        mock_provider = MagicMock()
        mock_provider.base_url = "https://api.example.com/v1"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "test",
            "choices": [{"message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}
        }
        mock_response.raise_for_status = MagicMock()

        class MockClient:
            async def post(self, *args, **kwargs):
                return mock_response
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        test_debug_dir = tmp_path / "llm_proxy_debug"

        with patch("httpx.AsyncClient", return_value=MockClient()):
            with patch("src.api.routes.llm_proxy._is_debug_enabled", return_value=True):
                with patch("src.api.routes.llm_proxy.DEBUG_DIR", test_debug_dir):
                    payload = {
                        "model": "test:model",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }

                    await _proxy_openai(
                        payload, mock_provider, "test-key", "gpt-4", stream=False
                    )

        # Check out_*.json file was created
        out_files = list(test_debug_dir.glob("out_*.json"))
        assert len(out_files) == 1

        content = json.loads(out_files[0].read_text())
        assert "request_uid" in content
        assert content["is_stream"] is False
        assert "raw_response" in content
        assert "translated_response" in content

    @pytest.mark.asyncio
    async def test_debug_mode_disabled_skips_file_creation(self, tmp_path):
        """When debug disabled, should not create debug files."""
        from src.api.routes.llm_proxy import _proxy_openai

        mock_provider = MagicMock()
        mock_provider.base_url = "https://api.example.com/v1"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "test",
            "choices": [{"message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}
        }
        mock_response.raise_for_status = MagicMock()

        class MockClient:
            async def post(self, *args, **kwargs):
                return mock_response
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        test_debug_dir = tmp_path / "llm_proxy_debug"

        with patch("httpx.AsyncClient", return_value=MockClient()):
            with patch("src.api.routes.llm_proxy._is_debug_enabled", return_value=False):
                with patch("src.api.routes.llm_proxy.DEBUG_DIR", test_debug_dir):
                    payload = {
                        "model": "test:model",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }

                    await _proxy_openai(
                        payload, mock_provider, "test-key", "gpt-4", stream=False
                    )

        # Directory should not exist or be empty
        assert not test_debug_dir.exists() or len(list(test_debug_dir.iterdir())) == 0

    def test_save_debug_file_with_session_id(self, tmp_path):
        """_save_debug_file with session_id should save under session subdirectory."""
        from src.api.routes.llm_proxy import _save_debug_file
        import json

        test_debug_dir = tmp_path / "llm_proxy_debug"

        with patch("src.api.routes.llm_proxy.DEBUG_DIR", test_debug_dir):
            _save_debug_file("test_file.json", {"key": "value"}, session_id="sess-abc-123")

        session_dir = test_debug_dir / "sess-abc-123"
        assert session_dir.exists()
        saved_file = session_dir / "test_file.json"
        assert saved_file.exists()

        content = json.loads(saved_file.read_text())
        assert content["key"] == "value"
        assert "timestamp" in content

    def test_save_debug_file_without_session_id_saves_flat(self, tmp_path):
        """_save_debug_file without session_id should save in root debug dir."""
        from src.api.routes.llm_proxy import _save_debug_file
        import json

        test_debug_dir = tmp_path / "llm_proxy_debug"

        with patch("src.api.routes.llm_proxy.DEBUG_DIR", test_debug_dir):
            _save_debug_file("test_file.json", {"key": "value"})

        saved_file = test_debug_dir / "test_file.json"
        assert saved_file.exists()
        # Should NOT be in a subdirectory
        assert not any(p.is_dir() for p in test_debug_dir.iterdir())

    def test_cleanup_scoped_to_session_directory(self, tmp_path):
        """Cleanup with session_id should only affect that session's directory."""
        from src.api.routes.llm_proxy import _cleanup_debug_files, DEBUG_MAX_FILES
        import time

        test_debug_dir = tmp_path / "llm_proxy_debug"
        session_dir = test_debug_dir / "test-session"
        session_dir.mkdir(parents=True)
        other_dir = test_debug_dir / "other-session"
        other_dir.mkdir(parents=True)

        # Create files in both directories
        for i in range(DEBUG_MAX_FILES + 5):
            (session_dir / f"test_{i:04d}.json").write_text("{}")
            time.sleep(0.001)
        (other_dir / "keep_me.json").write_text("{}")

        with patch("src.api.routes.llm_proxy.DEBUG_DIR", test_debug_dir):
            _cleanup_debug_files(session_id="test-session")

        # Session directory should be cleaned up
        remaining = list(session_dir.glob("*.json"))
        assert len(remaining) <= DEBUG_MAX_FILES

        # Other session directory should be untouched
        assert (other_dir / "keep_me.json").exists()

    @pytest.mark.asyncio
    async def test_proxy_openai_passes_session_id_to_debug(self, tmp_path):
        """_proxy_openai should save debug files under session subdirectory."""
        from src.api.routes.llm_proxy import _proxy_openai
        import json

        mock_provider = MagicMock()
        mock_provider.base_url = "https://api.example.com/v1"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "test",
            "choices": [{"message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}
        }
        mock_response.raise_for_status = MagicMock()

        class MockClient:
            async def post(self, *args, **kwargs):
                return mock_response
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        test_debug_dir = tmp_path / "llm_proxy_debug"

        with patch("httpx.AsyncClient", return_value=MockClient()):
            with patch("src.api.routes.llm_proxy._is_debug_enabled", return_value=True):
                with patch("src.api.routes.llm_proxy.DEBUG_DIR", test_debug_dir):
                    payload = {
                        "model": "test:model",
                        "messages": [{"role": "user", "content": "Hello"}],
                    }
                    await _proxy_openai(
                        payload, mock_provider, "test-key", "gpt-4",
                        stream=False, session_id="my-session-42"
                    )

        session_dir = test_debug_dir / "my-session-42"
        assert session_dir.exists()
        in_files = list(session_dir.glob("in_*.json"))
        out_files = list(session_dir.glob("out_*.json"))
        assert len(in_files) == 1
        assert len(out_files) == 1


class TestNonStreamingUsageMapping:
    """Tests for usage mapping in non-streaming mode."""

    @pytest.mark.asyncio
    async def test_non_streaming_extracts_cached_tokens(self):
        """Non-streaming response should include cache_read_input_tokens."""
        from src.api.routes.llm_proxy import _proxy_openai
        import json

        mock_provider = MagicMock()
        mock_provider.base_url = "https://api.example.com/v1"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "test",
            "choices": [{"message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 17077,
                "completion_tokens": 78,
                "prompt_tokens_details": {
                    "cached_tokens": 7808,
                    "audio_tokens": 0,
                },
            }
        }
        mock_response.raise_for_status = MagicMock()

        class MockClient:
            async def post(self, *args, **kwargs):
                return mock_response
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=MockClient()):
            payload = {
                "model": "test:model",
                "messages": [{"role": "user", "content": "Hello"}],
            }

            result = await _proxy_openai(
                payload, mock_provider, "test-key", "gpt-4", stream=False
            )

        body = json.loads(result.body.decode())

        assert body["usage"]["input_tokens"] == 17077
        assert body["usage"]["output_tokens"] == 78
        assert body["usage"]["cache_read_input_tokens"] == 7808


class TestSSRFProtection:
    """Tests for SSRF protection on provider base_url validation."""

    def test_loopback_ip_rejected(self):
        """Provider base_url pointing to 127.0.0.1 should be rejected."""
        from src.api.llm_proxy.config import validate_base_url, ProxyConfigError

        with pytest.raises(ProxyConfigError, match="private/internal"):
            validate_base_url("http://127.0.0.1:8080/v1", "evil-provider")

    def test_loopback_localhost_rejected(self):
        """Provider base_url pointing to localhost should be rejected."""
        from src.api.llm_proxy.config import validate_base_url, ProxyConfigError

        with pytest.raises(ProxyConfigError, match="private/internal"):
            validate_base_url("http://localhost:8080/v1", "evil-provider")

    def test_private_10_range_rejected(self):
        """Provider base_url pointing to 10.x.x.x should be rejected."""
        from src.api.llm_proxy.config import validate_base_url, ProxyConfigError

        with pytest.raises(ProxyConfigError, match="private/internal"):
            validate_base_url("http://10.0.0.1:8080/v1", "evil-provider")

    def test_private_172_range_rejected(self):
        """Provider base_url pointing to 172.16.x.x should be rejected."""
        from src.api.llm_proxy.config import validate_base_url, ProxyConfigError

        with pytest.raises(ProxyConfigError, match="private/internal"):
            validate_base_url("http://172.16.0.1:8080/v1", "evil-provider")

    def test_private_192_168_range_rejected(self):
        """Provider base_url pointing to 192.168.x.x should be rejected."""
        from src.api.llm_proxy.config import validate_base_url, ProxyConfigError

        with pytest.raises(ProxyConfigError, match="private/internal"):
            validate_base_url("http://192.168.1.1:8080/v1", "evil-provider")

    def test_ipv6_loopback_rejected(self):
        """Provider base_url pointing to ::1 should be rejected."""
        from src.api.llm_proxy.config import validate_base_url, ProxyConfigError

        with pytest.raises(ProxyConfigError, match="private/internal"):
            validate_base_url("http://[::1]:8080/v1", "evil-provider")

    def test_unspecified_address_rejected(self):
        """Provider base_url pointing to 0.0.0.0 should be rejected."""
        from src.api.llm_proxy.config import validate_base_url, ProxyConfigError

        with pytest.raises(ProxyConfigError, match="private/internal"):
            validate_base_url("http://0.0.0.0:8080/v1", "evil-provider")

    def test_public_url_accepted(self):
        """Provider base_url pointing to public API should be accepted."""
        from src.api.llm_proxy.config import validate_base_url

        # Should not raise
        validate_base_url("https://api.openai.com/v1", "openai")

    def test_public_ip_accepted(self):
        """Provider base_url with public IP should be accepted."""
        from src.api.llm_proxy.config import validate_base_url

        # 8.8.8.8 is Google's public DNS
        validate_base_url("http://8.8.8.8:8080/v1", "custom-provider")

    def test_missing_hostname_rejected(self):
        """Provider base_url without hostname should be rejected."""
        from src.api.llm_proxy.config import validate_base_url, ProxyConfigError

        with pytest.raises(ProxyConfigError, match="no hostname"):
            validate_base_url("", "empty-provider")

    def test_link_local_rejected(self):
        """Provider base_url pointing to link-local address should be rejected."""
        from src.api.llm_proxy.config import validate_base_url, ProxyConfigError

        with pytest.raises(ProxyConfigError, match="private/internal"):
            validate_base_url("http://169.254.169.254/latest/meta-data", "cloud-metadata")
