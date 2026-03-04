"""
Tests for CIDR IP allowlisting and audit logging in API key auth.

Covers:
- CIDR notation support in check_ip_allowed()
- Audit log entries written during API key authentication
- Audit log entries for auth failures (invalid key, IP denied, rate limited)
"""
import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from src.db.database import Base
from src.db.models import APIKey, APIKeyAuditLog
from src.services.api_key_service import APIKeyService


# =============================================================================
# Fixtures
# =============================================================================

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_engine():
    """In-memory test database engine."""
    engine = create_async_engine(
        TEST_DB_URL, echo=False, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db(db_engine) -> AsyncSession:
    """Database session."""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


def _make_key(ip_allowlist=None) -> APIKey:
    """Create a mock APIKey with optional IP allowlist."""
    return APIKey(
        id=str(uuid.uuid4()),
        reseller_id=None,
        user_id=str(uuid.uuid4()),
        name="test-key",
        key_prefix="ag3_res_test",
        key_hash="fakehash",
        scopes='["users:read"]',
        ip_allowlist=json.dumps(ip_allowlist) if ip_allowlist is not None else None,
        rate_limit_per_minute=60,
        is_active=True,
    )


# =============================================================================
# CIDR IP Allowlisting Tests
# =============================================================================

class TestCIDRAllowlisting:
    """Tests for CIDR support in check_ip_allowed()."""

    def setup_method(self):
        self.service = APIKeyService()

    def test_null_allowlist_allows_all(self):
        """Null allowlist → allow any IP."""
        key = _make_key(ip_allowlist=None)
        assert self.service.check_ip_allowed(key, "1.2.3.4") is True

    def test_empty_allowlist_allows_all(self):
        """Empty list allowlist → allow any IP."""
        key = _make_key(ip_allowlist=[])
        assert self.service.check_ip_allowed(key, "1.2.3.4") is True

    def test_exact_ip_match(self):
        """Exact IP in allowlist → allowed."""
        key = _make_key(ip_allowlist=["10.0.0.1", "192.168.1.1"])
        assert self.service.check_ip_allowed(key, "10.0.0.1") is True

    def test_exact_ip_no_match(self):
        """IP not in exact allowlist → denied."""
        key = _make_key(ip_allowlist=["10.0.0.1", "192.168.1.1"])
        assert self.service.check_ip_allowed(key, "10.0.0.2") is False

    def test_cidr_match(self):
        """IP within CIDR range → allowed."""
        key = _make_key(ip_allowlist=["10.0.0.0/8"])
        assert self.service.check_ip_allowed(key, "10.1.2.3") is True

    def test_cidr_no_match(self):
        """IP outside CIDR range → denied."""
        key = _make_key(ip_allowlist=["10.0.0.0/8"])
        assert self.service.check_ip_allowed(key, "192.168.1.1") is False

    def test_cidr_24_subnet(self):
        """Standard /24 subnet matching."""
        key = _make_key(ip_allowlist=["192.168.1.0/24"])
        assert self.service.check_ip_allowed(key, "192.168.1.100") is True
        assert self.service.check_ip_allowed(key, "192.168.2.1") is False

    def test_cidr_32_single_host(self):
        """/32 CIDR = exact single host."""
        key = _make_key(ip_allowlist=["10.0.0.5/32"])
        assert self.service.check_ip_allowed(key, "10.0.0.5") is True
        assert self.service.check_ip_allowed(key, "10.0.0.6") is False

    def test_mixed_exact_and_cidr(self):
        """Both exact IPs and CIDRs in the same allowlist."""
        key = _make_key(ip_allowlist=["1.2.3.4", "10.0.0.0/16"])
        assert self.service.check_ip_allowed(key, "1.2.3.4") is True
        assert self.service.check_ip_allowed(key, "10.0.5.5") is True
        assert self.service.check_ip_allowed(key, "172.16.0.1") is False

    def test_ipv6_cidr(self):
        """IPv6 CIDR support."""
        key = _make_key(ip_allowlist=["::1", "fe80::/10"])
        assert self.service.check_ip_allowed(key, "::1") is True
        assert self.service.check_ip_allowed(key, "fe80::1") is True
        assert self.service.check_ip_allowed(key, "2001:db8::1") is False

    def test_invalid_client_ip(self):
        """Invalid client IP → denied."""
        key = _make_key(ip_allowlist=["10.0.0.0/8"])
        assert self.service.check_ip_allowed(key, "not-an-ip") is False

    def test_invalid_allowlist_entry_skipped(self):
        """Invalid entries in allowlist are skipped, valid ones still checked."""
        key = _make_key(ip_allowlist=["invalid-entry", "10.0.0.1"])
        assert self.service.check_ip_allowed(key, "10.0.0.1") is True

    def test_malformed_json_allowlist(self):
        """Malformed JSON in ip_allowlist → denied (fail-closed)."""
        key = _make_key()
        key.ip_allowlist = "not-valid-json"
        assert self.service.check_ip_allowed(key, "10.0.0.1") is False

    def test_ipv6_loopback_matches_ipv4_loopback_cidr(self):
        """::1 should match 127.0.0.0/8 (loopback equivalence)."""
        key = _make_key(ip_allowlist=["127.0.0.0/8"])
        assert self.service.check_ip_allowed(key, "::1") is True

    def test_ipv6_loopback_matches_exact_ipv4_loopback(self):
        """::1 should match exact 127.0.0.1 entry."""
        key = _make_key(ip_allowlist=["127.0.0.1"])
        assert self.service.check_ip_allowed(key, "::1") is True

    def test_ipv4_mapped_ipv6_matches_ipv4_cidr(self):
        """::ffff:10.0.0.1 should match 10.0.0.0/8 CIDR."""
        key = _make_key(ip_allowlist=["10.0.0.0/8"])
        assert self.service.check_ip_allowed(key, "::ffff:10.0.0.1") is True

    def test_ipv4_mapped_ipv6_matches_exact_ipv4(self):
        """::ffff:192.168.1.1 should match exact 192.168.1.1."""
        key = _make_key(ip_allowlist=["192.168.1.1"])
        assert self.service.check_ip_allowed(key, "::ffff:192.168.1.1") is True

    def test_ipv6_nonloopback_does_not_match_ipv4(self):
        """Non-loopback IPv6 should not match IPv4 allowlist."""
        key = _make_key(ip_allowlist=["10.0.0.0/8"])
        assert self.service.check_ip_allowed(key, "2001:db8::1") is False


# =============================================================================
# Audit Logging Tests
# =============================================================================

class TestAuditLogging:
    """Tests for API key audit log entries."""

    @pytest.mark.asyncio
    async def test_log_usage_success(self, db):
        """log_usage writes an audit log entry to the database."""
        service = APIKeyService()
        await service.log_usage(
            db, "key-123", "reseller-456", "authenticate",
            "user-789", "10.0.0.1", 200,
        )

        result = await db.execute(select(APIKeyAuditLog))
        entries = result.scalars().all()
        assert len(entries) == 1

        entry = entries[0]
        assert entry.api_key_id == "key-123"
        assert entry.reseller_id == "reseller-456"
        assert entry.action == "authenticate"
        assert entry.target_user_id == "user-789"
        assert entry.ip_address == "10.0.0.1"
        assert entry.status_code == 200
        assert entry.error is None

    @pytest.mark.asyncio
    async def test_log_usage_failure_entry(self, db):
        """log_usage records error details for failed auth."""
        service = APIKeyService()
        await service.log_usage(
            db, None, None, "auth_failed",
            None, "1.2.3.4", 401, error="Invalid or expired API key",
        )

        result = await db.execute(select(APIKeyAuditLog))
        entry = result.scalars().first()
        assert entry is not None
        assert entry.action == "auth_failed"
        assert entry.status_code == 401
        assert entry.error == "Invalid or expired API key"

    @pytest.mark.asyncio
    async def test_log_usage_ip_denied(self, db):
        """log_usage records IP denial events."""
        service = APIKeyService()
        await service.log_usage(
            db, "key-1", "res-1", "ip_denied",
            "user-1", "10.0.0.99", 403, error="IP not in allowlist",
        )

        result = await db.execute(select(APIKeyAuditLog))
        entry = result.scalars().first()
        assert entry.action == "ip_denied"
        assert entry.status_code == 403

    @pytest.mark.asyncio
    async def test_log_usage_rate_limited(self, db):
        """log_usage records rate limit events."""
        service = APIKeyService()
        await service.log_usage(
            db, "key-1", "res-1", "rate_limited",
            "user-1", "10.0.0.1", 429, error="Rate limit exceeded",
        )

        result = await db.execute(select(APIKeyAuditLog))
        entry = result.scalars().first()
        assert entry.action == "rate_limited"
        assert entry.status_code == 429


# =============================================================================
# HTTP Integration Tests (Audit Logging in Auth Flow)
# =============================================================================

class TestAuditLoggingHTTP:
    """Tests that audit log entries are created during API key auth flow."""

    @pytest.mark.integration
    def test_invalid_api_key_creates_audit_log(self, client, admin_auth_headers):
        """Invalid API key auth attempt creates audit log entry."""
        resp = client.get(
            "/api/v1/reseller/profile",
            headers={"X-API-Key": "ag3_res_invalid_key_12345"},
        )
        assert resp.status_code == 401

    @pytest.mark.integration
    def test_admin_can_view_audit_log(self, client, admin_auth_headers):
        """GET /admin/audit returns audit log entries."""
        resp = client.get(
            "/api/v1/admin/audit",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert "pagination" in data
