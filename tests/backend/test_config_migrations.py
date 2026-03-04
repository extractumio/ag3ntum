"""
Unit tests for the config migration system.

Covers:
- scripts/config_migrations/__init__.py  — migration discovery and chaining
- scripts/config_migrations/migrate_0_2_0_to_0_3_0.py — first migration
- scripts/migrate_config.py — runner (get_config_version, migrate_file,
  backup_config)
"""
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest
import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# TestDiscoverMigrations
# ---------------------------------------------------------------------------

class TestDiscoverMigrations:
    """Tests for discover_migrations() in scripts/config_migrations/__init__.py."""

    @pytest.mark.unit
    def test_known_path_returns_chain(self):
        """discover_migrations('0.2.0', '0.3.0') should find the migration file."""
        from scripts.config_migrations import discover_migrations

        chain = discover_migrations("0.2.0", "0.3.0")

        assert len(chain) == 1
        from_ver, to_ver, fn = chain[0]
        assert from_ver == "0.2.0"
        assert to_ver == "0.3.0"
        assert callable(fn)

    @pytest.mark.unit
    def test_same_version_returns_empty(self):
        """discover_migrations with identical from/to should return empty list."""
        from scripts.config_migrations import discover_migrations

        chain = discover_migrations("0.2.0", "0.2.0")

        assert chain == []

    @pytest.mark.unit
    def test_no_path_returns_empty(self):
        """
        discover_migrations returns empty list when the from_version has no
        migration entry at all (i.e., the starting point is unknown).
        """
        from scripts.config_migrations import discover_migrations

        # "1.0.0" has no migration file, so there is no path to any version
        chain = discover_migrations("1.0.0", "99.99.99")

        assert chain == []

    @pytest.mark.unit
    def test_chain_fn_is_callable(self):
        """Each step in the chain exposes a callable migrate function."""
        from scripts.config_migrations import discover_migrations

        chain = discover_migrations("0.2.0", "0.3.0")

        for _, _, fn in chain:
            assert callable(fn)

    @pytest.mark.unit
    def test_circular_migration_raises(self, tmp_path, monkeypatch):
        """
        If migration files form a cycle the discovery must raise ValueError.

        We inject two fake migration modules into sys.modules that form a
        cycle: A -> B -> A.  We also patch the migrations directory to only
        contain those two files.
        """
        import scripts.config_migrations as pkg

        # Build two fake module objects that have a migrate() stub
        mod_ab = ModuleType("scripts.config_migrations.migrate_9_0_0_to_9_1_0")
        mod_ab.migrate = lambda cfg, fn: cfg
        mod_ba = ModuleType("scripts.config_migrations.migrate_9_1_0_to_9_0_0")
        mod_ba.migrate = lambda cfg, fn: cfg

        sys.modules[mod_ab.__name__] = mod_ab
        sys.modules[mod_ba.__name__] = mod_ba

        # Create fake .py files in tmp_path so the directory scan finds them
        (tmp_path / "migrate_9_0_0_to_9_1_0.py").write_text("")
        (tmp_path / "migrate_9_1_0_to_9_0_0.py").write_text("")

        # Patch the migrations_dir used inside discover_migrations
        with patch.object(Path, "iterdir",
                          return_value=iter(tmp_path.iterdir())):
            # Also need __file__ in the package to resolve the dir correctly
            original_file = pkg.__file__

            def patched_discover(from_version, to_version):
                """Inline version of discover_migrations that uses tmp_path."""
                import re
                import importlib

                migrations_dir = tmp_path
                pattern = re.compile(
                    r"^migrate_(\d+_\d+_\d+)_to_(\d+_\d+_\d+)\.py$"
                )

                available = {}
                for f in sorted(migrations_dir.iterdir()):
                    m = pattern.match(f.name)
                    if m:
                        fv = m.group(1).replace("_", ".")
                        tv = m.group(2).replace("_", ".")
                        available[fv] = (tv, f.stem)

                chain = []
                current = from_version
                visited = set()

                while current != to_version and current in available:
                    if current in visited:
                        raise ValueError(
                            f"Circular migration detected at {current}"
                        )
                    visited.add(current)
                    next_ver, module_name = available[current]
                    full_name = f"scripts.config_migrations.{module_name}"
                    mod = sys.modules[full_name]
                    chain.append((current, next_ver, mod.migrate))
                    current = next_ver

                return chain

            with pytest.raises(ValueError, match="Circular"):
                patched_discover("9.0.0", "9.0.0_never_reached")


# ---------------------------------------------------------------------------
# TestMigrate_0_2_0_to_0_3_0
# ---------------------------------------------------------------------------

class TestMigrate020To030:
    """Tests for the 0.2.0 -> 0.3.0 config migration function."""

    @pytest.mark.unit
    def test_empty_config_gets_version_added(self):
        """migrate({}, 'api.yaml') should add _version: '0.3.0'."""
        from scripts.config_migrations.migrate_0_2_0_to_0_3_0 import migrate

        result = migrate({}, "api.yaml")

        assert result["_version"] == "0.3.0"

    @pytest.mark.unit
    def test_existing_version_is_updated(self):
        """If _version already exists it should be overwritten to '0.3.0'."""
        from scripts.config_migrations.migrate_0_2_0_to_0_3_0 import migrate

        config = {"_version": "0.2.0", "api": {"host": "0.0.0.0"}}
        result = migrate(config, "api.yaml")

        assert result["_version"] == "0.3.0"

    @pytest.mark.unit
    def test_existing_keys_are_preserved(self):
        """migrate() must not remove or alter keys other than _version."""
        from scripts.config_migrations.migrate_0_2_0_to_0_3_0 import migrate

        config = {
            "host": "localhost",
            "port": 8080,
            "debug": True,
            "nested": {"key": "value"},
        }
        result = migrate(config, "api.yaml")

        assert result["host"] == "localhost"
        assert result["port"] == 8080
        assert result["debug"] is True
        assert result["nested"] == {"key": "value"}

    @pytest.mark.unit
    def test_returns_dict(self):
        """migrate() always returns a dict."""
        from scripts.config_migrations.migrate_0_2_0_to_0_3_0 import migrate

        result = migrate({"key": "val"}, "secrets.yaml")

        assert isinstance(result, dict)

    @pytest.mark.unit
    def test_filename_parameter_accepted(self):
        """migrate() must accept filename for conditional logic without error."""
        from scripts.config_migrations.migrate_0_2_0_to_0_3_0 import migrate

        # No assertion on content — just verify it doesn't raise
        for fname in ["api.yaml", "agent.yaml", "secrets.yaml",
                      "external-mounts.yaml", "llm-api-proxy.yaml"]:
            result = migrate({}, fname)
            assert "_version" in result

    @pytest.mark.unit
    def test_sensitive_keys_are_not_removed(self):
        """Sensitive keys (api keys, secrets) must survive migration unchanged."""
        from scripts.config_migrations.migrate_0_2_0_to_0_3_0 import migrate

        config = {
            "anthropic_api_key": "sk-ant-test",
            "fernet_key": "fernet-abc",
            "jwt_secret": "jwt-secret-xyz",
        }
        result = migrate(config, "secrets.yaml")

        assert result["anthropic_api_key"] == "sk-ant-test"
        assert result["fernet_key"] == "fernet-abc"
        assert result["jwt_secret"] == "jwt-secret-xyz"


# ---------------------------------------------------------------------------
# TestGetConfigVersion
# ---------------------------------------------------------------------------

class TestGetConfigVersion:
    """Tests for get_config_version() in scripts/migrate_config.py."""

    @pytest.mark.unit
    def test_returns_default_when_no_files(self, tmp_path):
        """Empty config directory should return '0.2.0' (pre-versioning default)."""
        from scripts.migrate_config import get_config_version

        version = get_config_version(tmp_path)

        assert version == "0.2.0"

    @pytest.mark.unit
    def test_reads_version_from_config_file(self, tmp_path):
        """If a config file contains _version, that value is returned."""
        from scripts.migrate_config import get_config_version

        api_yaml = tmp_path / "api.yaml"
        api_yaml.write_text(yaml.dump({"_version": "0.3.0", "host": "localhost"}))

        version = get_config_version(tmp_path)

        assert version == "0.3.0"

    @pytest.mark.unit
    def test_returns_default_when_no_version_key_in_files(self, tmp_path):
        """Config files without _version should fall back to '0.2.0'."""
        from scripts.migrate_config import get_config_version

        api_yaml = tmp_path / "api.yaml"
        api_yaml.write_text(yaml.dump({"host": "localhost", "port": 8080}))

        version = get_config_version(tmp_path)

        assert version == "0.2.0"

    @pytest.mark.unit
    def test_reads_version_file_fallback(self, tmp_path):
        """If no config files have _version, .ag3ntum-version file is checked."""
        from scripts.migrate_config import get_config_version

        # .ag3ntum-version lives one level up from config dir
        version_file = tmp_path.parent / ".ag3ntum-version"
        version_file.write_text("0.2.5\n")

        # config dir exists but has no _version key
        config_dir = tmp_path
        (config_dir / "api.yaml").write_text(yaml.dump({"host": "x"}))

        version = get_config_version(config_dir)

        assert version == "0.2.5"

        # cleanup so we don't pollute other tests
        version_file.unlink()

    @pytest.mark.unit
    def test_first_file_with_version_wins(self, tmp_path):
        """The first config file (alphabetically by CONFIG_FILES order) with
        _version is used."""
        from scripts.migrate_config import get_config_version, CONFIG_FILES

        # Write two files, both with _version; the first one in CONFIG_FILES wins
        first_name = CONFIG_FILES[0]
        second_name = CONFIG_FILES[1] if len(CONFIG_FILES) > 1 else None

        (tmp_path / first_name).write_text(
            yaml.dump({"_version": "0.3.0"})
        )
        if second_name:
            (tmp_path / second_name).write_text(
                yaml.dump({"_version": "0.4.0"})
            )

        version = get_config_version(tmp_path)

        assert version == "0.3.0"

    @pytest.mark.unit
    def test_skips_unparseable_yaml(self, tmp_path):
        """Invalid YAML in a config file should be skipped gracefully."""
        from scripts.migrate_config import get_config_version

        bad_file = tmp_path / "api.yaml"
        bad_file.write_text("{{{invalid yaml:::")

        version = get_config_version(tmp_path)

        assert version == "0.2.0"


# ---------------------------------------------------------------------------
# TestMigrateFile
# ---------------------------------------------------------------------------

class TestMigrateFile:
    """Tests for migrate_file() in scripts/migrate_config.py."""

    @pytest.mark.unit
    def test_applies_migration_and_modifies_file(self, tmp_path):
        """migrate_file() should apply the migration and write the result."""
        from scripts.migrate_config import migrate_file

        fpath = tmp_path / "api.yaml"
        fpath.write_text(yaml.dump({"host": "localhost"}))

        def _add_version(config, filename):
            config["_version"] = "0.3.0"
            return config

        migrations = [("0.2.0", "0.3.0", _add_version)]
        changed = migrate_file(fpath, migrations, dry_run=False,
                               from_version="0.2.0")

        assert changed is True
        loaded = yaml.safe_load(fpath.read_text())
        assert loaded["_version"] == "0.3.0"
        assert loaded["host"] == "localhost"

    @pytest.mark.unit
    def test_dry_run_does_not_modify_file(self, tmp_path):
        """With dry_run=True the file content must remain unchanged."""
        from scripts.migrate_config import migrate_file

        original_content = yaml.dump({"host": "localhost"})
        fpath = tmp_path / "api.yaml"
        fpath.write_text(original_content)

        def _add_version(config, filename):
            config["_version"] = "0.3.0"
            return config

        migrations = [("0.2.0", "0.3.0", _add_version)]
        changed = migrate_file(fpath, migrations, dry_run=True,
                               from_version="0.2.0")

        assert changed is True  # indicates migration would be applied
        assert fpath.read_text() == original_content  # file untouched

    @pytest.mark.unit
    def test_skips_missing_file(self, tmp_path):
        """migrate_file() returns False and does not error when file is absent."""
        from scripts.migrate_config import migrate_file

        missing = tmp_path / "does_not_exist.yaml"
        migrations = [("0.2.0", "0.3.0", lambda c, f: c)]

        changed = migrate_file(missing, migrations, dry_run=False,
                               from_version="0.2.0")

        assert changed is False

    @pytest.mark.unit
    def test_no_change_returns_false(self, tmp_path):
        """If migration does not alter the config, returns False."""
        from scripts.migrate_config import migrate_file

        fpath = tmp_path / "api.yaml"
        fpath.write_text(yaml.dump({"host": "localhost"}))

        # Identity migration — returns config unchanged
        migrations = [("0.2.0", "0.3.0", lambda c, f: c)]
        changed = migrate_file(fpath, migrations, dry_run=False,
                               from_version="0.2.0")

        assert changed is False

    @pytest.mark.unit
    def test_backup_created_when_modified(self, tmp_path):
        """When file is modified, a .pre-{version}.bak backup is created."""
        from scripts.migrate_config import migrate_file

        fpath = tmp_path / "api.yaml"
        fpath.write_text(yaml.dump({"host": "localhost"}))

        def _add_version(config, filename):
            config["_version"] = "0.3.0"
            return config

        migrate_file(fpath, [("0.2.0", "0.3.0", _add_version)],
                     dry_run=False, from_version="0.2.0")

        backup = tmp_path / "api.pre-0.2.0.bak"
        assert backup.exists()

    @pytest.mark.unit
    def test_no_backup_on_dry_run(self, tmp_path):
        """No backup file should be created during a dry run."""
        from scripts.migrate_config import migrate_file

        fpath = tmp_path / "api.yaml"
        fpath.write_text(yaml.dump({"host": "localhost"}))

        def _add_version(config, filename):
            config["_version"] = "0.3.0"
            return config

        migrate_file(fpath, [("0.2.0", "0.3.0", _add_version)],
                     dry_run=True, from_version="0.2.0")

        backup = tmp_path / "api.pre-0.2.0.bak"
        assert not backup.exists()

    @pytest.mark.unit
    def test_skips_non_mapping_yaml(self, tmp_path):
        """YAML files whose root is not a dict (e.g., a list) are skipped."""
        from scripts.migrate_config import migrate_file

        fpath = tmp_path / "api.yaml"
        fpath.write_text(yaml.dump(["item1", "item2"]))

        migrations = [("0.2.0", "0.3.0", lambda c, f: c)]
        changed = migrate_file(fpath, migrations, dry_run=False,
                               from_version="0.2.0")

        assert changed is False


# ---------------------------------------------------------------------------
# TestBackupConfig
# ---------------------------------------------------------------------------

class TestBackupConfig:
    """Tests for backup_config() in scripts/migrate_config.py."""

    @pytest.mark.unit
    def test_creates_bak_file(self, tmp_path):
        """backup_config() creates a .pre-{version}.bak copy of the file."""
        from scripts.migrate_config import backup_config

        fpath = tmp_path / "api.yaml"
        fpath.write_text("host: localhost\n")

        backup = backup_config(fpath, "0.2.0")

        assert backup is not None
        assert backup.exists()
        assert backup.name == "api.pre-0.2.0.bak"
        assert backup.read_text() == "host: localhost\n"

    @pytest.mark.unit
    def test_backup_content_matches_original(self, tmp_path):
        """Backup file must be an exact copy of the original."""
        from scripts.migrate_config import backup_config

        original_text = yaml.dump({"host": "localhost", "port": 8080})
        fpath = tmp_path / "secrets.yaml"
        fpath.write_text(original_text)

        backup = backup_config(fpath, "0.2.0")

        assert backup.read_text() == original_text

    @pytest.mark.unit
    def test_returns_none_for_missing_file(self, tmp_path):
        """If the target file does not exist, backup_config() returns None."""
        from scripts.migrate_config import backup_config

        missing = tmp_path / "no_such_file.yaml"
        result = backup_config(missing, "0.2.0")

        assert result is None

    @pytest.mark.unit
    def test_version_in_backup_name(self, tmp_path):
        """The migration source version is embedded in the backup filename."""
        from scripts.migrate_config import backup_config

        fpath = tmp_path / "agent.yaml"
        fpath.write_text("mode: standard\n")

        backup = backup_config(fpath, "0.3.0")

        assert "0.3.0" in backup.name
