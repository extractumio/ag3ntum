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


@pytest.fixture
def enabled_ssh_config():
    """SSHSecurityConfig with SSH enabled."""
    return SSHSecurityConfig(enabled=True)


@pytest.fixture
def disabled_ssh_config():
    """SSHSecurityConfig with SSH disabled."""
    return SSHSecurityConfig(enabled=False)


class TestInitialize:
    """Tests for SSHServiceManager.initialize()."""

    @pytest.mark.asyncio
    async def test_disabled_when_config_missing(self, manager):
        """SSH disabled when config file doesn't exist."""
        with patch(
            "src.services.ssh_service_manager.load_ssh_security_config",
            return_value=SSHSecurityConfig(enabled=False),
        ):
            await manager.initialize()
        assert manager.enabled is False
        assert manager._pool is None

    @pytest.mark.asyncio
    async def test_enabled_when_config_present(self, manager, enabled_ssh_config):
        """SSH enabled when config has enabled=True."""
        with patch(
            "src.services.ssh_service_manager.load_ssh_security_config",
            return_value=enabled_ssh_config,
        ):
            await manager.initialize()
        assert manager.enabled is True
        assert manager._pool is not None
        assert manager._command_filter is not None
        assert manager.security_config is not None
        assert manager.security_config.enabled is True


class TestBuildSessionContext:
    """Tests for SSHServiceManager.build_session_context()."""

    @pytest.mark.asyncio
    async def test_returns_none_when_disabled(self, manager, sample_profiles):
        """Returns None when SSH is disabled."""
        result = await manager.build_session_context(
            session_id="sess-1",
            user_id="user-1",
            profiles=sample_profiles,
            db_session_factory=AsyncMock(),
            vault_service=MagicMock(),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_profiles(self, manager, enabled_ssh_config):
        """Returns None when profiles dict is empty."""
        with patch(
            "src.services.ssh_service_manager.load_ssh_security_config",
            return_value=enabled_ssh_config,
        ):
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
    async def test_builds_context_with_profiles(
        self, manager, enabled_ssh_config, sample_profiles,
    ):
        """Builds SSHToolContext when SSH enabled and profiles exist."""
        with patch(
            "src.services.ssh_service_manager.load_ssh_security_config",
            return_value=enabled_ssh_config,
        ):
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
    async def test_multiple_sessions_share_pool(
        self, manager, enabled_ssh_config, sample_profiles,
    ):
        """Multiple sessions share the same connection pool."""
        with patch(
            "src.services.ssh_service_manager.load_ssh_security_config",
            return_value=enabled_ssh_config,
        ):
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
    async def test_cleanup_calls_pool(self, manager, enabled_ssh_config):
        """Cleanup calls close_session_connections on the pool."""
        with patch(
            "src.services.ssh_service_manager.load_ssh_security_config",
            return_value=enabled_ssh_config,
        ):
            await manager.initialize()

        manager._pool.close_session_connections = AsyncMock(return_value=2)
        await manager.cleanup_session("sess-1")
        manager._pool.close_session_connections.assert_awaited_once_with("sess-1")

    @pytest.mark.asyncio
    async def test_cleanup_noop_when_disabled(self, manager):
        """Cleanup is a no-op when SSH is disabled (no pool)."""
        await manager.cleanup_session("sess-1")  # Should not raise


class TestShutdown:
    """Tests for SSHServiceManager.shutdown()."""

    @pytest.mark.asyncio
    async def test_shutdown_closes_pool(self, manager, enabled_ssh_config):
        """Shutdown calls pool.shutdown()."""
        with patch(
            "src.services.ssh_service_manager.load_ssh_security_config",
            return_value=enabled_ssh_config,
        ):
            await manager.initialize()

        manager._pool.shutdown = AsyncMock()
        await manager.shutdown()
        manager._pool.shutdown.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_noop_when_disabled(self, manager):
        """Shutdown is a no-op when SSH was never initialized."""
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
