"""
SSH-specific test fixtures.

Provides in-memory database, config, service, and fixture objects
for SSH unit tests without requiring containers or real SSH servers.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.db.database import Base
from src.db.models import User, VaultSecret  # noqa: F401 — available for tests
from src.core.ssh.ssh_config import (
    SSHConnectionLimits,
    SSHProfile,
    SSHSecurityConfig,
)
from src.core.ssh.ssh_command_filter import SSHCommandFilter
from src.core.ssh.ssh_connection_pool import SSHConnectionPool
from src.services.vault_encryption import VaultEncryption
from src.services.vault_service import VaultService
from src.core.ssh.ssh_host_key_resolver import SSHHostKeyResolver
from src.services.ssh_audit_service import SSHAuditService


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def ssh_test_engine():
    """In-memory SQLite engine for SSH tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def ssh_session_factory(ssh_test_engine):
    """Async session factory backed by in-memory engine."""
    return async_sessionmaker(
        ssh_test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest_asyncio.fixture
async def ssh_db(ssh_session_factory):
    """Open async DB session for SSH tests."""
    async with ssh_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def test_user_with_jwt(ssh_db):
    """Create a test user with a known jwt_secret for key derivation."""
    user = User(
        id=str(uuid.uuid4()),
        username="ssh_tester",
        email="ssh@test.com",
        password_hash="fakehash",
        role="user",
        jwt_secret="test-jwt-secret-12345",
        linux_uid=59990,
    )
    ssh_db.add(user)
    await ssh_db.commit()
    await ssh_db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ssh_security_config():
    """Enabled SSH security config with tight limits for testing."""
    return SSHSecurityConfig(
        enabled=True,
        default_mode="readonly",
        limits=SSHConnectionLimits(
            max_connections_per_user=3,
            max_concurrent_commands=5,
            command_timeout_seconds=30,
            max_output_bytes=1024,
            max_file_read_bytes=2048,
        ),
    )


@pytest.fixture
def ssh_security_config_disabled():
    """SSH security config — kept for backward compatibility.

    Note: SSH is now always enabled at platform level. Per-user disablement
    is via feature flags, not config. This fixture is used by tests that
    need a config object but the enabled field is no longer the gate.
    """
    return SSHSecurityConfig(enabled=True)


@pytest.fixture
def test_ssh_profile():
    """L0 read-only SSH profile for a generic test server."""
    return SSHProfile(
        name="test-server",
        host="192.168.1.100",
        port=22,
        username="deploy",
        auth_method="key",
        key_ref="test-key",
        mode="readonly",
        privilege_level=0,
    )


@pytest.fixture
def test_ssh_profile_l3():
    """L3 administration profile for blocklist-mode testing."""
    return SSHProfile(
        name="admin-server",
        host="10.0.1.5",
        port=22,
        username="admin",
        auth_method="key",
        key_ref="admin-key",
        mode="filtered_shell",
        privilege_level=3,
    )


# ---------------------------------------------------------------------------
# Service fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def command_filter():
    """SSHCommandFilter loaded from the example privilege-levels config."""
    from pathlib import Path
    example_path = (
        Path(__file__).parent.parent.parent.parent
        / "config" / "security" / "ssh-privilege-levels.yaml.example"
    )
    return SSHCommandFilter(config_path=example_path)


@pytest.fixture
def vault_encryption():
    """VaultEncryption with a fixed test master key."""
    return VaultEncryption(master_key=b"test-master-key-for-hkdf-testing")


@pytest.fixture
def vault_service(vault_encryption):
    """VaultService wired to the test VaultEncryption."""
    return VaultService(vault_encryption=vault_encryption)


@pytest.fixture
def connection_pool():
    """SSHConnectionPool with a short idle timeout for tests."""
    return SSHConnectionPool(
        idle_timeout_seconds=60,
        max_connections_per_session=3,
    )


@pytest.fixture
def audit_service():
    """SSHAuditService instance for unit tests."""
    return SSHAuditService()


@pytest.fixture
def host_key_resolver(vault_service):
    """SSHHostKeyResolver wired to the test VaultService."""
    return SSHHostKeyResolver(vault_service)


@pytest.fixture
def test_ssh_profile_l2():
    """L2 configuration profile for write-mode testing."""
    return SSHProfile(
        name="config-server",
        host="10.0.1.10",
        port=22,
        username="deployer",
        auth_method="key",
        key_ref="deploy-key",
        mode="filtered_shell",
        privilege_level=2,
    )
