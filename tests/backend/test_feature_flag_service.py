"""Unit tests for FeatureFlagService — platform defaults, merge logic, DB ops."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.feature_flag_service import (
    DEFAULT_FEATURES,
    DEFAULT_QUOTAS,
    DEFAULT_SPENDING,
    FeatureFlagService,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def svc():
    return FeatureFlagService()


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


def _make_platform_config_row(key: str, value: dict):
    row = MagicMock()
    row.key = key
    row.value = json.dumps(value)
    return row


# ---------------------------------------------------------------------------
# ensure_loaded
# ---------------------------------------------------------------------------

class TestEnsureLoaded:
    @pytest.mark.asyncio
    async def test_loads_when_not_loaded(self, svc, mock_db):
        assert svc._loaded is False
        # Mock load_platform_defaults
        with patch.object(svc, "load_platform_defaults", new_callable=AsyncMock) as mock_load:
            await svc.ensure_loaded(mock_db)
            mock_load.assert_called_once_with(mock_db)

    @pytest.mark.asyncio
    async def test_noop_when_already_loaded(self, svc, mock_db):
        svc._loaded = True
        with patch.object(svc, "load_platform_defaults", new_callable=AsyncMock) as mock_load:
            await svc.ensure_loaded(mock_db)
            mock_load.assert_not_called()


# ---------------------------------------------------------------------------
# _get_effective / get_platform_*
# ---------------------------------------------------------------------------

class TestGetEffective:
    def test_returns_defaults_when_no_overrides(self, svc):
        result = svc.get_platform_features()
        assert result == DEFAULT_FEATURES

    def test_returns_defaults_for_quotas(self, svc):
        result = svc.get_platform_quotas()
        assert result == DEFAULT_QUOTAS

    def test_returns_defaults_for_spending(self, svc):
        result = svc.get_platform_spending()
        assert result == DEFAULT_SPENDING

    def test_returns_empty_for_retention_by_default(self, svc):
        result = svc.get_platform_retention()
        assert result == {}

    def test_db_override_applies(self, svc):
        svc._db_features = {"ssh_enabled": False}
        result = svc.get_platform_features()
        assert result["ssh_enabled"] is False
        # Other defaults unchanged
        assert result["file_upload_enabled"] is True

    def test_null_override_is_ignored(self, svc):
        svc._db_features = {"ssh_enabled": None}
        result = svc.get_platform_features()
        # Null override does NOT apply
        assert result["ssh_enabled"] is True

    def test_unknown_key_in_override_is_ignored(self, svc):
        svc._db_features = {"nonexistent_feature": True}
        result = svc.get_platform_features()
        assert "nonexistent_feature" not in result

    def test_quotas_override(self, svc):
        svc._db_quotas = {"per_user_daily_limit": 100}
        result = svc.get_platform_quotas()
        assert result["per_user_daily_limit"] == 100
        assert result["global_max_concurrent"] == 4  # unchanged

    def test_spending_override(self, svc):
        svc._db_spending = {"user_monthly_usd": 50.0}
        result = svc.get_platform_spending()
        assert result["user_monthly_usd"] == 50.0

    def test_retention_override(self, svc):
        svc._db_retention = {"events": 7}
        result = svc.get_platform_retention()
        # retention has no hardcoded defaults, so only DB overrides
        assert result["events"] == 7

    def test_get_effective_does_not_mutate_defaults(self, svc):
        svc._db_features = {"ssh_enabled": False}
        svc.get_platform_features()
        # DEFAULT_FEATURES should not be modified
        assert DEFAULT_FEATURES["ssh_enabled"] is True


# ---------------------------------------------------------------------------
# load_platform_defaults
# ---------------------------------------------------------------------------

class TestLoadPlatformDefaults:
    @pytest.mark.asyncio
    async def test_loads_all_sections(self, svc, mock_db):
        rows = [
            _make_platform_config_row("features", {"ssh_enabled": False}),
            _make_platform_config_row("quotas", {"per_user_daily_limit": 100}),
            _make_platform_config_row("spending", {"user_monthly_usd": 25}),
            _make_platform_config_row("retention", {"events": 7}),
        ]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = rows
        mock_db.execute = AsyncMock(return_value=mock_result)

        await svc.load_platform_defaults(mock_db)

        assert svc._loaded is True
        assert svc._db_features == {"ssh_enabled": False}
        assert svc._db_quotas == {"per_user_daily_limit": 100}
        assert svc._db_spending == {"user_monthly_usd": 25}
        assert svc._db_retention == {"events": 7}

    @pytest.mark.asyncio
    async def test_handles_db_error(self, svc, mock_db):
        mock_db.execute = AsyncMock(side_effect=RuntimeError("DB down"))

        await svc.load_platform_defaults(mock_db)

        # Should still mark as loaded so we don't retry forever
        assert svc._loaded is True
        assert svc._db_features == {}

    @pytest.mark.asyncio
    async def test_skips_invalid_json(self, svc, mock_db):
        bad_row = MagicMock()
        bad_row.key = "features"
        bad_row.value = "not-json"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [bad_row]
        mock_db.execute = AsyncMock(return_value=mock_result)

        await svc.load_platform_defaults(mock_db)

        assert svc._loaded is True
        assert svc._db_features == {}

    @pytest.mark.asyncio
    async def test_ignores_unknown_section(self, svc, mock_db):
        row = _make_platform_config_row("unknown_section", {"foo": "bar"})
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [row]
        mock_db.execute = AsyncMock(return_value=mock_result)

        await svc.load_platform_defaults(mock_db)

        assert svc._loaded is True


# ---------------------------------------------------------------------------
# update_platform_defaults
# ---------------------------------------------------------------------------

class TestUpdatePlatformDefaults:
    @pytest.mark.asyncio
    async def test_update_features_upserts_and_caches(self, svc, mock_db):
        # No existing row
        mock_select_result = MagicMock()
        mock_select_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_select_result)

        result = await svc.update_platform_defaults(
            mock_db, "features", {"ssh_enabled": False},
        )

        assert svc._db_features == {"ssh_enabled": False}
        assert result["ssh_enabled"] is False
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_null_value_removes_override(self, svc, mock_db):
        svc._db_features = {"ssh_enabled": False, "vault_enabled": False}

        mock_row = MagicMock()
        mock_select_result = MagicMock()
        mock_select_result.scalar_one_or_none.return_value = mock_row
        mock_db.execute = AsyncMock(return_value=mock_select_result)

        await svc.update_platform_defaults(
            mock_db, "features", {"ssh_enabled": None},
        )

        # ssh_enabled removed, vault_enabled preserved
        assert "ssh_enabled" not in svc._db_features
        assert svc._db_features["vault_enabled"] is False

    @pytest.mark.asyncio
    async def test_update_invalid_section_raises(self, svc, mock_db):
        with pytest.raises(ValueError, match="Invalid section"):
            await svc.update_platform_defaults(
                mock_db, "nonexistent", {"foo": "bar"},
            )

    @pytest.mark.asyncio
    async def test_update_updates_existing_row(self, svc, mock_db):
        mock_row = MagicMock()
        mock_select_result = MagicMock()
        mock_select_result.scalar_one_or_none.return_value = mock_row
        mock_db.execute = AsyncMock(return_value=mock_select_result)

        await svc.update_platform_defaults(
            mock_db, "quotas", {"per_user_daily_limit": 200},
        )

        assert mock_row.value == json.dumps({"per_user_daily_limit": 200})
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_retention_section(self, svc, mock_db):
        mock_select_result = MagicMock()
        mock_select_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_select_result)

        result = await svc.update_platform_defaults(
            mock_db, "retention", {"events": 7},
        )

        assert svc._db_retention == {"events": 7}
        assert result["events"] == 7
