"""Tests for SSHServiceManager — SSH agent integration."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.ssh.ssh_config import SSHProfile, SSHSecurityConfig
from src.services.ssh_service_manager import SSHServiceManager


@pytest.fixture
def manager():
    """Fresh SSHServiceManager for each test."""
    return SSHServiceManager()


@pytest.fixture
def sample_profile():
    """Sample SSHProfile for testing."""
    return SSHProfile(
        name="test-server",
        host="192.168.1.100",
        port=22,
        username="deploy",
        mode="readonly",
        privilege_level=0,
        description="Test server",
    )


@pytest.fixture
def sample_profiles(sample_profile):
    """Dict of profiles keyed by name."""
    return {sample_profile.name: sample_profile}


class TestInitialize:
    """Tests for SSHServiceManager.initialize()."""

    @pytest.mark.asyncio
    async def test_always_initializes_without_yaml(self, manager):
        """SSH infrastructure always initializes — no YAML needed."""
        await manager.initialize()
        assert manager.enabled is True
        assert manager._pool is not None
        assert manager._command_filter is not None
        assert manager.security_config is not None
        assert manager.security_config.enabled is True

    @pytest.mark.asyncio
    async def test_logs_deprecation_when_yaml_exists(self, manager, tmp_path):
        """Logs warning when legacy ssh-security.yaml is found."""
        yaml_path = tmp_path / "ssh-security.yaml"
        yaml_path.write_text("ssh:\n  enabled: true\n")

        with patch(
            "src.services.ssh_service_manager._LEGACY_SSH_SECURITY_CONFIG_PATH",
            yaml_path,
        ), patch(
            "src.services.ssh_service_manager.logger"
        ) as mock_logger:
            await manager.initialize()

        mock_logger.warning.assert_called_once()
        assert "no longer used" in mock_logger.warning.call_args[0][0]
        assert manager.enabled is True


class TestIsUserSshEnabled:
    """Tests for SSHServiceManager.is_user_ssh_enabled()."""

    @pytest.mark.asyncio
    async def test_returns_false_when_flag_disabled(self, manager):
        """Returns False when user's ssh_enabled feature flag is False."""
        await manager.initialize()

        with patch.object(
            SSHServiceManager,
            "_resolve_user_ssh_flag",
            new_callable=AsyncMock,
            return_value=False,
        ):
            result = await manager.is_user_ssh_enabled("user-1")

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_flag_enabled(self, manager):
        """Returns True when user's ssh_enabled feature flag is True."""
        await manager.initialize()

        with patch.object(
            SSHServiceManager,
            "_resolve_user_ssh_flag",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await manager.is_user_ssh_enabled("user-1")

        assert result is True

    @pytest.mark.asyncio
    async def test_cache_hit_skips_db(self, manager):
        """Redis cache hit returns immediately without calling _resolve_user_ssh_flag."""
        await manager.initialize()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value="1")

        with (
            patch(
                "src.services.ssh_service_manager._redis"
            ) as mock_redis,
            patch.object(
                SSHServiceManager,
                "_resolve_user_ssh_flag",
                new_callable=AsyncMock,
            ) as mock_resolve,
        ):
            mock_redis.get.return_value = mock_client
            result = await manager.is_user_ssh_enabled("user-1")

        assert result is True
        mock_resolve.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_queries_db_and_sets_cache(self, manager):
        """Redis cache miss falls through to DB then caches with 30s TTL."""
        await manager.initialize()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=None)
        mock_client.set = AsyncMock()

        with (
            patch(
                "src.services.ssh_service_manager._redis"
            ) as mock_redis,
            patch.object(
                SSHServiceManager,
                "_resolve_user_ssh_flag",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            mock_redis.get.return_value = mock_client
            result = await manager.is_user_ssh_enabled("user-1")

        assert result is True
        mock_client.set.assert_called_once_with(
            "feature:ssh_enabled:user-1", "1", ex=30,
        )

    @pytest.mark.asyncio
    async def test_redis_unavailable_falls_through(self, manager):
        """When Redis raises, method still returns correct value from DB."""
        await manager.initialize()

        with (
            patch(
                "src.services.ssh_service_manager._redis"
            ) as mock_redis,
            patch.object(
                SSHServiceManager,
                "_resolve_user_ssh_flag",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            mock_redis.get.side_effect = RuntimeError("Redis down")
            result = await manager.is_user_ssh_enabled("user-1")

        assert result is False


class TestBuildSessionContext:
    """Tests for SSHServiceManager.build_session_context()."""

    @pytest.mark.asyncio
    async def test_returns_none_when_not_initialized(self, manager, sample_profiles):
        """Returns None when manager hasn't been initialized."""
        result = await manager.build_session_context(
            session_id="sess-1",
            user_id="user-1",
            profiles=sample_profiles,
            db_session_factory=AsyncMock(),
            vault_service=MagicMock(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_profiles(self, manager):
        """Returns None when profiles dict is empty."""
        await manager.initialize()

        result = await manager.build_session_context(
            session_id="sess-1",
            user_id="user-1",
            profiles={},
            db_session_factory=AsyncMock(),
            vault_service=MagicMock(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_builds_context_with_profiles(self, manager, sample_profiles):
        """Builds SSHToolContext when initialized and profiles exist."""
        await manager.initialize()

        vault_svc = MagicMock()
        db_factory = AsyncMock()

        ctx = await manager.build_session_context(
            session_id="sess-1",
            user_id="user-1",
            profiles=sample_profiles,
            db_session_factory=db_factory,
            vault_service=vault_svc,
        )
        assert ctx is not None
        assert ctx.session_id == "sess-1"
        assert ctx.user_id == "user-1"
        assert ctx.profiles == sample_profiles
        assert ctx.connection_pool is manager._pool
        assert ctx.command_filter is manager._command_filter
        assert ctx.command_semaphore is not None

    @pytest.mark.asyncio
    async def test_multiple_sessions_share_pool(self, manager, sample_profiles):
        """Multiple sessions share the same connection pool."""
        await manager.initialize()

        vault_svc = MagicMock()
        ctx1 = await manager.build_session_context(
            session_id="sess-1",
            user_id="user-1",
            profiles=sample_profiles,
            db_session_factory=AsyncMock(),
            vault_service=vault_svc,
        )
        ctx2 = await manager.build_session_context(
            session_id="sess-2",
            user_id="user-1",
            profiles=sample_profiles,
            db_session_factory=AsyncMock(),
            vault_service=vault_svc,
        )
        assert ctx1.connection_pool is ctx2.connection_pool


class TestCleanupSession:
    """Tests for SSHServiceManager.cleanup_session()."""

    @pytest.mark.asyncio
    async def test_cleanup_calls_pool(self, manager):
        """Cleanup calls close_session_connections on the pool."""
        await manager.initialize()

        manager._pool.close_session_connections = AsyncMock(return_value=2)
        await manager.cleanup_session("sess-1")
        manager._pool.close_session_connections.assert_awaited_once_with("sess-1")

    @pytest.mark.asyncio
    async def test_cleanup_noop_when_not_initialized(self, manager):
        """Cleanup is a no-op when not initialized (no pool)."""
        manager._pool = None
        await manager.cleanup_session("sess-1")  # Should not raise


class TestShutdown:
    """Tests for SSHServiceManager.shutdown()."""

    @pytest.mark.asyncio
    async def test_shutdown_closes_pool(self, manager):
        """Shutdown calls pool.shutdown()."""
        await manager.initialize()

        manager._pool.shutdown = AsyncMock()
        await manager.shutdown()
        manager._pool.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_noop_when_not_initialized(self, manager):
        """Shutdown is a no-op when never initialized."""
        manager._pool = None
        await manager.shutdown()  # Should not raise


class TestPromptContext:
    """Tests for SSH profile injection into prompt context."""

    def test_ssh_enabled_flag_set(self, sample_profiles):
        """SSH_ENABLED flag is True when profiles provided."""
        from src.core.prompt_context import build_prompt_context
        ctx = build_prompt_context(ssh_profiles=sample_profiles)
        assert ctx.flags["SSH_ENABLED"] is True

    def test_ssh_disabled_flag_when_no_profiles(self):
        """SSH_ENABLED flag is False when no profiles."""
        from src.core.prompt_context import build_prompt_context
        ctx = build_prompt_context(ssh_profiles=None)
        assert ctx.flags["SSH_ENABLED"] is False

    def test_ssh_disabled_flag_when_empty_profiles(self):
        """SSH_ENABLED flag is False when profiles dict is empty."""
        from src.core.prompt_context import build_prompt_context
        ctx = build_prompt_context(ssh_profiles={})
        assert ctx.flags["SSH_ENABLED"] is False

    def test_profiles_block_generated(self, sample_profile):
        """SSH_PROFILES_BLOCK contains profile info."""
        from src.core.prompt_context import build_prompt_context
        profiles = {sample_profile.name: sample_profile}
        ctx = build_prompt_context(ssh_profiles=profiles)
        block = ctx.strings["SSH_PROFILES_BLOCK"]
        assert "test-server" in block
        assert "deploy@192.168.1.100:22" in block
        assert "L0 readonly" in block
        assert "Test server" in block

    def test_tool_names_registered(self, sample_profiles):
        """SSH tool name variables are registered in context."""
        from src.core.prompt_context import build_prompt_context
        ctx = build_prompt_context(ssh_profiles=sample_profiles)
        assert ctx.tool_names["AG3NTUM_SSH_EXEC_TOOL"] == "mcp__ag3ntum__SSHExec"
        assert ctx.tool_names["AG3NTUM_SSH_READ_TOOL"] == "mcp__ag3ntum__SSHRead"
        assert ctx.tool_names["AG3NTUM_SSH_CONNECT_TOOL"] == "mcp__ag3ntum__SSHConnect"

    def test_multiple_profiles_in_block(self):
        """Multiple profiles are listed in SSH_PROFILES_BLOCK."""
        from src.core.prompt_context import build_prompt_context
        profiles = {
            "prod-web": SSHProfile(
                name="prod-web", host="10.0.0.1", port=22,
                username="root", mode="operations", privilege_level=1,
            ),
            "staging-db": SSHProfile(
                name="staging-db", host="10.0.0.2", port=2222,
                username="admin", mode="readonly", privilege_level=0,
                description="Staging database",
            ),
        }
        ctx = build_prompt_context(ssh_profiles=profiles)
        block = ctx.strings["SSH_PROFILES_BLOCK"]
        assert "prod-web" in block
        assert "staging-db" in block
        assert "root@10.0.0.1:22" in block
        assert "admin@10.0.0.2:2222" in block
        assert "L1 operations" in block
        assert "L0 readonly" in block


class TestSSHPromptRendering:
    """Tests for SSH system prompt template rendering."""

    def test_prompt_rendered_when_ssh_enabled(self, sample_profiles):
        """07b-ssh.md renders content when SSH_ENABLED is True."""
        from pathlib import Path
        from src.core.prompt_engine import PromptTemplateEngine
        from src.core.prompt_context import build_prompt_context

        ctx = build_prompt_context(ssh_profiles=sample_profiles)
        engine = PromptTemplateEngine(
            base_dir=Path("prompts"),
        )
        prompt_file = Path("prompts/system-prompts/07b-ssh.md")
        rendered = engine.load_and_render(prompt_file, ctx)
        assert "SSH Remote Server Access" in rendered
        assert "test-server" in rendered
        assert "mcp__ag3ntum__SSHExec" in rendered

    def test_prompt_empty_when_ssh_disabled(self):
        """07b-ssh.md renders empty when SSH_ENABLED is False."""
        from pathlib import Path
        from src.core.prompt_engine import PromptTemplateEngine
        from src.core.prompt_context import build_prompt_context

        ctx = build_prompt_context(ssh_profiles=None)
        engine = PromptTemplateEngine(
            base_dir=Path("prompts"),
        )
        prompt_file = Path("prompts/system-prompts/07b-ssh.md")
        rendered = engine.load_and_render(prompt_file, ctx)
        assert "SSH Remote Server Access" not in rendered
        assert rendered.strip() == ""


class TestSSHSecurityDefaults:
    """Tests for hardcoded SSH security defaults."""

    def test_default_config_has_sane_limits(self):
        """get_default_ssh_security_config returns config with sensible limits."""
        from src.core.ssh.ssh_config import get_default_ssh_security_config
        config = get_default_ssh_security_config()
        assert config.enabled is True
        assert config.limits.max_connections_per_user == 3
        assert config.limits.command_timeout_seconds == 300
        assert config.limits.max_output_bytes == 1_048_576
        assert config.credentials.password_auth_allowed is False
        assert config.host_key_verification.mode == "tofu"

    def test_always_blocked_hosts_constant(self):
        """ALWAYS_BLOCKED_HOSTS includes localhost and metadata IPs."""
        from src.core.ssh.ssh_config import ALWAYS_BLOCKED_HOSTS
        assert "127.0.0.1" in ALWAYS_BLOCKED_HOSTS
        assert "localhost" in ALWAYS_BLOCKED_HOSTS
        assert "::1" in ALWAYS_BLOCKED_HOSTS
        assert "169.254.0.0/16" in ALWAYS_BLOCKED_HOSTS

    def test_default_config_blocks_dangerous_hosts(self):
        """Default config always_blocked list includes all ALWAYS_BLOCKED_HOSTS."""
        from src.core.ssh.ssh_config import (
            ALWAYS_BLOCKED_HOSTS,
            get_default_ssh_security_config,
        )
        config = get_default_ssh_security_config()
        for host in ALWAYS_BLOCKED_HOSTS:
            assert host in config.hosts.always_blocked
