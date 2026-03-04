"""
Tests for data retention service and admin endpoints.

Covers:
- DataRetentionService unit tests (defaults, purge logic)
- GET /admin/retention — read config
- PUT /admin/retention — update config
- POST /admin/retention/run — manual purge trigger
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.data_retention_service import (
    DEFAULT_RETENTION,
    DataRetentionService,
)


# =============================================================================
# Unit tests — DataRetentionService
# =============================================================================

class TestDataRetentionDefaults:
    def test_default_retention_periods(self):
        svc = DataRetentionService()
        defaults = svc.get_defaults()
        assert defaults["usage_records"] == 395
        assert defaults["events"] == 30
        assert defaults["webhook_delivery_log"] == 90
        assert defaults["api_key_audit_log"] == 365

    def test_default_keys_match_table_map(self):
        from src.services.data_retention_service import _TABLE_MAP
        assert set(DEFAULT_RETENTION.keys()) == set(_TABLE_MAP.keys())


class TestPurgeTable:
    @pytest.mark.asyncio
    async def test_purge_unknown_table_returns_zero(self):
        svc = DataRetentionService()
        db = AsyncMock()
        count = await svc.purge_table(db, "nonexistent_table", 30)
        assert count == 0

    @pytest.mark.asyncio
    async def test_purge_executes_delete(self):
        svc = DataRetentionService()
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 5
        db.execute = AsyncMock(return_value=mock_result)
        db.commit = AsyncMock()

        count = await svc.purge_table(db, "events", 30)
        assert count == 5
        db.execute.assert_called_once()
        db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_purge_zero_rows(self):
        svc = DataRetentionService()
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        db.execute = AsyncMock(return_value=mock_result)
        db.commit = AsyncMock()

        count = await svc.purge_table(db, "usage_records", 395)
        assert count == 0


class TestGetRetentionConfig:
    @pytest.mark.asyncio
    async def test_returns_defaults_when_no_overrides(self):
        svc = DataRetentionService()
        mock_ffs = MagicMock()
        mock_ffs.ensure_loaded = AsyncMock()
        mock_ffs.get_platform_retention = MagicMock(return_value={})

        with patch(
            "src.services.feature_flag_service.feature_flag_service",
            mock_ffs,
        ):
            db = AsyncMock()
            result = await svc.get_retention_config(db)

        assert result == DEFAULT_RETENTION
        mock_ffs.ensure_loaded.assert_called_once_with(db)

    @pytest.mark.asyncio
    async def test_merges_db_overrides(self):
        svc = DataRetentionService()
        mock_ffs = MagicMock()
        mock_ffs.ensure_loaded = AsyncMock()
        mock_ffs.get_platform_retention = MagicMock(
            return_value={"events": 7, "usage_records": 180},
        )

        with patch(
            "src.services.feature_flag_service.feature_flag_service",
            mock_ffs,
        ):
            db = AsyncMock()
            result = await svc.get_retention_config(db)

        assert result["events"] == 7
        assert result["usage_records"] == 180
        # Non-overridden keys keep defaults
        assert result["webhook_delivery_log"] == 90
        assert result["api_key_audit_log"] == 365


class TestUpdateRetentionConfig:
    @pytest.mark.asyncio
    async def test_filters_invalid_keys(self):
        svc = DataRetentionService()
        mock_ffs = MagicMock()
        mock_ffs.ensure_loaded = AsyncMock()
        mock_ffs.update_platform_defaults = AsyncMock()
        mock_ffs.get_platform_retention = MagicMock(return_value={})

        with patch(
            "src.services.feature_flag_service.feature_flag_service",
            mock_ffs,
        ):
            db = AsyncMock()
            await svc.update_retention_config(
                db, {"events": 7, "nonexistent": 99}, updated_by="admin-1",
            )

        # Only valid key should be passed through
        mock_ffs.update_platform_defaults.assert_called_once_with(
            db, "retention", {"events": 7}, "admin-1",
        )

    @pytest.mark.asyncio
    async def test_empty_valid_keys_returns_current(self):
        svc = DataRetentionService()
        mock_ffs = MagicMock()
        mock_ffs.ensure_loaded = AsyncMock()
        mock_ffs.get_platform_retention = MagicMock(return_value={})

        with patch(
            "src.services.feature_flag_service.feature_flag_service",
            mock_ffs,
        ):
            db = AsyncMock()
            result = await svc.update_retention_config(
                db, {"totally_invalid": 42},
            )

        assert result == DEFAULT_RETENTION


class TestRunAll:
    @pytest.mark.asyncio
    async def test_run_all_aggregates_counts(self):
        svc = DataRetentionService()
        with patch.object(svc, "get_retention_config", new_callable=AsyncMock) as mock_config:
            mock_config.return_value = {
                "events": 30,
                "usage_records": 395,
            }
            with patch.object(svc, "purge_table", new_callable=AsyncMock) as mock_purge:
                mock_purge.return_value = 10
                db = AsyncMock()
                results = await svc.run_all(db)
                assert results["total_purged"] == 20
                assert results["events"]["purged"] == 10
                assert results["usage_records"]["purged"] == 10


# =============================================================================
# HTTP integration tests — Admin retention endpoints
# =============================================================================

class TestGetRetention:
    @pytest.mark.integration
    def test_get_retention_returns_defaults(self, client, admin_auth_headers):
        resp = client.get(
            "/api/v1/admin/retention",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "usage_records" in data
        assert "events" in data
        assert "webhook_delivery_log" in data
        assert "api_key_audit_log" in data
        assert data["usage_records"] >= 1
        assert data["events"] >= 1

    @pytest.mark.integration
    def test_get_retention_requires_admin(self, client, reseller_auth_headers):
        resp = client.get(
            "/api/v1/admin/retention",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 403


class TestUpdateRetention:
    @pytest.mark.integration
    def test_update_retention_changes_value(self, client, admin_auth_headers):
        resp = client.put(
            "/api/v1/admin/retention",
            headers=admin_auth_headers,
            json={"events": 60},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["events"] == 60

    @pytest.mark.integration
    def test_update_retention_empty_body_returns_current(
        self, client, admin_auth_headers,
    ):
        resp = client.put(
            "/api/v1/admin/retention",
            headers=admin_auth_headers,
            json={},
        )
        assert resp.status_code == 200
        assert "usage_records" in resp.json()

    @pytest.mark.integration
    def test_update_retention_rejects_zero(self, client, admin_auth_headers):
        resp = client.put(
            "/api/v1/admin/retention",
            headers=admin_auth_headers,
            json={"events": 0},
        )
        assert resp.status_code == 422  # Validation: ge=1


class TestRunRetention:
    @pytest.mark.integration
    def test_run_retention_returns_results(self, client, admin_auth_headers):
        resp = client.post(
            "/api/v1/admin/retention/run",
            headers=admin_auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "total_purged" in data
        assert "tables" in data
        assert isinstance(data["total_purged"], int)

    @pytest.mark.integration
    def test_run_retention_requires_admin(self, client, reseller_auth_headers):
        resp = client.post(
            "/api/v1/admin/retention/run",
            headers=reseller_auth_headers,
        )
        assert resp.status_code == 403
