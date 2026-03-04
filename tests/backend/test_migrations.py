"""
Unit tests for the database migration runner (src/db/migrations.py).

Tests three bootstrap scenarios without running real Alembic or SQLite:
1. Fresh DB (no tables)        -> create_all() + stamp head
2. Existing DB, no alembic     -> stamp "001" + upgrade head
3. Existing DB with alembic    -> upgrade head only

Alembic is only present inside Docker; this file stubs out the module so tests
can run on the host without that dependency.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Inject stub 'alembic' packages before src.db.migrations is imported
# ---------------------------------------------------------------------------

def _install_alembic_stubs():
    """Create minimal alembic stubs in sys.modules if alembic is absent."""
    if "alembic" in sys.modules:
        return  # real alembic is available — use it

    alembic_mod = types.ModuleType("alembic")
    alembic_command_mod = types.ModuleType("alembic.command")
    alembic_command_mod.stamp = MagicMock()
    alembic_command_mod.upgrade = MagicMock()

    alembic_config_mod = types.ModuleType("alembic.config")

    class _FakeConfig:
        def __init__(self, ini_file=None):
            self._ini = ini_file
            self._opts = {}

        def set_main_option(self, key, value):
            self._opts[key] = value

    alembic_config_mod.Config = _FakeConfig
    alembic_mod.command = alembic_command_mod
    alembic_mod.config = alembic_config_mod

    sys.modules["alembic"] = alembic_mod
    sys.modules["alembic.command"] = alembic_command_mod
    sys.modules["alembic.config"] = alembic_config_mod


_install_alembic_stubs()

# Now safe to import the module under test
import importlib
import src.db.migrations as _mig_module  # noqa: E402


def _reload_migrations():
    """Re-import src.db.migrations so module-level constants can be patched."""
    if "src.db.migrations" in sys.modules:
        del sys.modules["src.db.migrations"]
    import src.db.migrations as m
    return m


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_table_exists(has_alembic: bool, has_users: bool):
    """
    Return a replacement for _table_exists that answers based on table name.
    """
    def _table_exists(connection, table_name: str) -> bool:
        if table_name == "alembic_version":
            return has_alembic
        if table_name == "users":
            return has_users
        return False
    return _table_exists


def _make_mock_engine():
    """Build a MagicMock engine whose context manager returns a connection."""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__ = lambda s: mock_conn
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    return mock_engine, mock_conn


# ---------------------------------------------------------------------------
# TestRunMigrationsSync
# ---------------------------------------------------------------------------

class TestRunMigrationsSync:
    """Tests for run_migrations_sync() — the main migration entrypoint."""

    @pytest.mark.unit
    def test_fresh_db_creates_all_and_stamps_head(self, tmp_path):
        """
        Fresh database (no users table, no alembic_version):
        should call _create_all_sync() then stamp("head").
        """
        import src.db.migrations as mig

        mock_engine, _ = _make_mock_engine()
        fake_cfg = MagicMock()

        with patch.object(mig, "_DATA_DIR", str(tmp_path)), \
             patch.object(mig, "_DB_PATH", str(tmp_path / "ag3ntum.db")), \
             patch.object(mig, "_get_alembic_config", return_value=fake_cfg), \
             patch.object(mig, "_table_exists",
                          side_effect=_make_table_exists(
                              has_alembic=False, has_users=False)), \
             patch.object(mig, "_create_all_sync") as mock_create_all, \
             patch("alembic.command.stamp") as mock_stamp, \
             patch("alembic.command.upgrade") as mock_upgrade, \
             patch("sqlalchemy.create_engine", return_value=mock_engine):

            mig.run_migrations_sync()

        mock_create_all.assert_called_once_with(mock_engine)
        mock_stamp.assert_called_once_with(fake_cfg, "head")
        mock_upgrade.assert_not_called()

    @pytest.mark.unit
    def test_existing_db_without_alembic_stamps_001_then_upgrades(self, tmp_path):
        """
        Existing DB (users table present, no alembic_version):
        should stamp("001") then upgrade("head").
        """
        import src.db.migrations as mig

        mock_engine, _ = _make_mock_engine()
        fake_cfg = MagicMock()

        with patch.object(mig, "_DATA_DIR", str(tmp_path)), \
             patch.object(mig, "_DB_PATH", str(tmp_path / "ag3ntum.db")), \
             patch.object(mig, "_get_alembic_config", return_value=fake_cfg), \
             patch.object(mig, "_table_exists",
                          side_effect=_make_table_exists(
                              has_alembic=False, has_users=True)), \
             patch.object(mig, "_create_all_sync") as mock_create_all, \
             patch("alembic.command.stamp") as mock_stamp, \
             patch("alembic.command.upgrade") as mock_upgrade, \
             patch("sqlalchemy.create_engine", return_value=mock_engine):

            mig.run_migrations_sync()

        mock_create_all.assert_not_called()
        mock_stamp.assert_called_once_with(fake_cfg, "001")
        mock_upgrade.assert_called_once_with(fake_cfg, "head")

    @pytest.mark.unit
    def test_existing_db_with_alembic_upgrades_head_only(self, tmp_path):
        """
        Alembic-managed DB (both tables present):
        should only call upgrade("head"), no stamp, no create_all.
        """
        import src.db.migrations as mig

        mock_engine, _ = _make_mock_engine()
        fake_cfg = MagicMock()

        with patch.object(mig, "_DATA_DIR", str(tmp_path)), \
             patch.object(mig, "_DB_PATH", str(tmp_path / "ag3ntum.db")), \
             patch.object(mig, "_get_alembic_config", return_value=fake_cfg), \
             patch.object(mig, "_table_exists",
                          side_effect=_make_table_exists(
                              has_alembic=True, has_users=True)), \
             patch.object(mig, "_create_all_sync") as mock_create_all, \
             patch("alembic.command.stamp") as mock_stamp, \
             patch("alembic.command.upgrade") as mock_upgrade, \
             patch("sqlalchemy.create_engine", return_value=mock_engine):

            mig.run_migrations_sync()

        mock_create_all.assert_not_called()
        mock_stamp.assert_not_called()
        mock_upgrade.assert_called_once_with(fake_cfg, "head")

    @pytest.mark.unit
    def test_data_dir_is_created(self, tmp_path):
        """
        run_migrations_sync() must create the data directory if it does not
        already exist before connecting to SQLite.
        """
        import src.db.migrations as mig

        data_dir = tmp_path / "new_data_dir"
        assert not data_dir.exists()

        mock_engine, _ = _make_mock_engine()

        with patch.object(mig, "_DATA_DIR", str(data_dir)), \
             patch.object(mig, "_DB_PATH", str(data_dir / "ag3ntum.db")), \
             patch.object(mig, "_get_alembic_config", return_value=MagicMock()), \
             patch.object(mig, "_table_exists",
                          side_effect=_make_table_exists(
                              has_alembic=True, has_users=True)), \
             patch.object(mig, "_create_all_sync"), \
             patch("alembic.command.stamp"), \
             patch("alembic.command.upgrade"), \
             patch("sqlalchemy.create_engine", return_value=mock_engine):

            mig.run_migrations_sync()

        assert data_dir.exists()

    @pytest.mark.unit
    def test_engine_is_disposed_after_run(self, tmp_path):
        """
        Engine.dispose() must be called at the end regardless of which branch
        was taken.
        """
        import src.db.migrations as mig

        mock_engine, _ = _make_mock_engine()

        with patch.object(mig, "_DATA_DIR", str(tmp_path)), \
             patch.object(mig, "_DB_PATH", str(tmp_path / "ag3ntum.db")), \
             patch.object(mig, "_get_alembic_config", return_value=MagicMock()), \
             patch.object(mig, "_table_exists",
                          side_effect=_make_table_exists(
                              has_alembic=True, has_users=True)), \
             patch.object(mig, "_create_all_sync"), \
             patch("alembic.command.stamp"), \
             patch("alembic.command.upgrade"), \
             patch("sqlalchemy.create_engine", return_value=mock_engine):

            mig.run_migrations_sync()

        mock_engine.dispose.assert_called_once()

    @pytest.mark.unit
    def test_stamp_001_before_upgrade_in_legacy_case(self, tmp_path):
        """
        In the legacy-DB case, stamp("001") must be called before
        upgrade("head") — order matters for safe schema recovery.
        """
        import src.db.migrations as mig

        call_order = []
        fake_cfg = MagicMock()

        def record_stamp(cfg, rev):
            call_order.append(("stamp", rev))

        def record_upgrade(cfg, rev):
            call_order.append(("upgrade", rev))

        mock_engine, _ = _make_mock_engine()

        with patch.object(mig, "_DATA_DIR", str(tmp_path)), \
             patch.object(mig, "_DB_PATH", str(tmp_path / "ag3ntum.db")), \
             patch.object(mig, "_get_alembic_config", return_value=fake_cfg), \
             patch.object(mig, "_table_exists",
                          side_effect=_make_table_exists(
                              has_alembic=False, has_users=True)), \
             patch.object(mig, "_create_all_sync"), \
             patch("alembic.command.stamp", side_effect=record_stamp), \
             patch("alembic.command.upgrade", side_effect=record_upgrade), \
             patch("sqlalchemy.create_engine", return_value=mock_engine):

            mig.run_migrations_sync()

        assert call_order == [("stamp", "001"), ("upgrade", "head")]


# ---------------------------------------------------------------------------
# TestTableExists (pure-logic unit tests — no SQLite required)
# ---------------------------------------------------------------------------

class TestTableExists:
    """Unit tests for _table_exists() using a mock connection."""

    @pytest.mark.unit
    def test_returns_true_when_table_found(self):
        """_table_exists returns True when sqlite_master count is 1."""
        from src.db.migrations import _table_exists

        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        mock_conn.execute.return_value = mock_result

        assert _table_exists(mock_conn, "users") is True

    @pytest.mark.unit
    def test_returns_false_when_table_not_found(self):
        """_table_exists returns False when sqlite_master count is 0."""
        from src.db.migrations import _table_exists

        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_conn.execute.return_value = mock_result

        assert _table_exists(mock_conn, "no_such_table") is False
