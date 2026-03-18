"""
Unit tests for batch script template generator.

Tests generate_batch_script, generate_batch_manifest, and _make_delimiter
against the public API in tools/ag3ntum/ag3ntum_ssh/batch_template.py.
No SSH connections or async required — all pure functions.
"""
import json
import re

import pytest

from tools.ag3ntum.ag3ntum_ssh.batch_template import (
    _make_delimiter,
    generate_batch_manifest,
    generate_batch_script,
)


# ---------------------------------------------------------------------------
# TestMakeDelimiter
# ---------------------------------------------------------------------------

class TestMakeDelimiter:
    """Tests for heredoc delimiter generation."""

    @pytest.mark.unit
    def test_delimiter_unique_per_file(self):
        d1 = _make_delimiter("/etc/nginx/nginx.conf", 0)
        d2 = _make_delimiter("/etc/nginx/sites/default", 1)
        assert d1 != d2

    @pytest.mark.unit
    def test_delimiter_starts_with_prefix(self):
        d = _make_delimiter("/etc/test.conf", 0)
        assert d.startswith("FILE_EOF_")

    @pytest.mark.unit
    def test_delimiter_deterministic(self):
        d1 = _make_delimiter("/etc/test.conf", 0)
        d2 = _make_delimiter("/etc/test.conf", 0)
        assert d1 == d2

    @pytest.mark.unit
    def test_delimiter_has_hash_suffix(self):
        d = _make_delimiter("/etc/test.conf", 0)
        suffix = d.replace("FILE_EOF_", "")
        assert len(suffix) == 8  # SHA-256 truncated to 8 hex chars

    @pytest.mark.unit
    def test_delimiter_differs_by_index(self):
        """Same path but different index produces different delimiter."""
        d0 = _make_delimiter("/etc/test.conf", 0)
        d1 = _make_delimiter("/etc/test.conf", 1)
        assert d0 != d1

    @pytest.mark.unit
    def test_delimiter_hex_chars_only_in_suffix(self):
        d = _make_delimiter("/some/path", 0)
        suffix = d.replace("FILE_EOF_", "")
        assert all(c in "0123456789abcdef" for c in suffix)


# ---------------------------------------------------------------------------
# TestGenerateBatchManifest
# ---------------------------------------------------------------------------

class TestGenerateBatchManifest:
    """Tests for manifest generation."""

    @pytest.mark.unit
    def test_manifest_format_correct(self):
        files = [
            {"path": "/etc/nginx/nginx.conf", "content": "server {}"},
            {"path": "/etc/caddy/Caddyfile", "content": "localhost"},
        ]
        manifest = generate_batch_manifest(
            "prod-web", "session-123", files, "ag3ntum-batch-20260318T114500Z"
        )
        assert manifest["snapshot_id"] == "ag3ntum-batch-20260318T114500Z"
        assert manifest["profile"] == "prod-web"
        assert manifest["file_count"] == 2
        assert manifest["total_bytes"] > 0
        assert len(manifest["files"]) == 2

    @pytest.mark.unit
    def test_manifest_per_file_fields(self):
        files = [{"path": "/etc/test.conf", "content": "key=value"}]
        manifest = generate_batch_manifest("srv", "sess", files, "snap-1")
        entry = manifest["files"][0]
        assert entry["path"] == "/etc/test.conf"
        assert entry["backup_name"] == "test.conf.original"
        assert entry["new_checksum"].startswith("sha256:")
        assert entry["new_size"] == len(b"key=value")
        assert entry["status"] == "pending"

    @pytest.mark.unit
    def test_manifest_total_bytes(self):
        files = [
            {"path": "/a", "content": "hello"},
            {"path": "/b", "content": "world!"},
        ]
        manifest = generate_batch_manifest("p", "s", files, "snap")
        assert manifest["total_bytes"] == len(b"hello") + len(b"world!")

    @pytest.mark.unit
    def test_manifest_created_at_is_iso(self):
        manifest = generate_batch_manifest(
            "p", "s", [{"path": "/a", "content": "x"}], "snap"
        )
        assert "T" in manifest["created_at"]

    @pytest.mark.unit
    def test_manifest_session_id_recorded(self):
        manifest = generate_batch_manifest(
            "p", "my-session-id", [{"path": "/a", "content": "x"}], "snap"
        )
        assert manifest["created_by_session"] == "my-session-id"

    @pytest.mark.unit
    def test_manifest_empty_files_list(self):
        manifest = generate_batch_manifest("p", "s", [], "snap")
        assert manifest["file_count"] == 0
        assert manifest["total_bytes"] == 0
        assert manifest["files"] == []

    @pytest.mark.unit
    def test_manifest_new_checksum_is_sha256_of_content(self):
        import hashlib
        content = "worker_processes auto;"
        files = [{"path": "/etc/nginx.conf", "content": content}]
        manifest = generate_batch_manifest("p", "s", files, "snap")
        expected = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
        assert manifest["files"][0]["new_checksum"] == expected

    @pytest.mark.unit
    def test_manifest_is_json_serializable(self):
        files = [{"path": "/etc/a.conf", "content": "data"}]
        manifest = generate_batch_manifest("p", "s", files, "snap")
        # Should not raise
        serialized = json.dumps(manifest)
        assert len(serialized) > 0


# ---------------------------------------------------------------------------
# TestGenerateBatchScript
# ---------------------------------------------------------------------------

class TestGenerateBatchScript:
    """Tests for batch script generation."""

    def _make_script(self, files=None, profile="test-prof"):
        if files is None:
            files = [{"path": "/etc/test.conf", "content": "key=value"}]
        manifest = generate_batch_manifest(
            profile, "session-1", files, "ag3ntum-batch-test"
        )
        return generate_batch_script(profile, "ag3ntum-batch-test", files, manifest)

    @pytest.mark.unit
    def test_script_starts_with_shebang(self):
        script = self._make_script()
        assert script.startswith("#!/bin/bash")

    @pytest.mark.unit
    def test_script_has_set_euo_pipefail(self):
        script = self._make_script()
        assert "set -euo pipefail" in script

    @pytest.mark.unit
    def test_script_has_snapshot_comment(self):
        script = self._make_script()
        assert "Snapshot: ag3ntum-batch-test" in script

    @pytest.mark.unit
    def test_script_creates_backup_dir(self):
        script = self._make_script()
        assert 'mkdir -p "$BACKUP_DIR"' in script

    @pytest.mark.unit
    def test_script_has_rollback_trap(self):
        script = self._make_script()
        assert "trap rollback ERR" in script

    @pytest.mark.unit
    def test_script_snapshot_before_modify(self):
        script = self._make_script()
        snapshot_pos = script.index('"phase":"snapshot"')
        apply_pos = script.index('"phase":"apply"')
        assert snapshot_pos < apply_pos

    @pytest.mark.unit
    def test_script_atomic_write_pattern(self):
        script = self._make_script()
        assert ".ag3ntum-tmp" in script
        assert 'mv "' in script

    @pytest.mark.unit
    def test_script_self_deletes(self):
        script = self._make_script()
        assert 'rm -f "$0"' in script

    @pytest.mark.unit
    def test_script_contains_manifest(self):
        script = self._make_script()
        assert "MANIFEST_EOF" in script

    @pytest.mark.unit
    def test_script_verify_phase_present(self):
        script = self._make_script()
        assert '"phase":"verify"' in script

    @pytest.mark.unit
    def test_script_uses_unique_heredoc_delimiters(self):
        files = [
            {"path": "/etc/a.conf", "content": "aaa"},
            {"path": "/etc/b.conf", "content": "bbb"},
        ]
        script = self._make_script(files=files)
        assert "FILE_EOF_" in script
        delimiters = re.findall(r"FILE_EOF_[a-f0-9]{8}", script)
        unique = set(delimiters)
        assert len(unique) >= 2  # At least 2 unique delimiters for 2 files

    @pytest.mark.unit
    def test_script_disk_space_check(self):
        script = self._make_script()
        assert "df --output=avail" in script

    @pytest.mark.unit
    def test_batch_dry_run_handled_at_python_level(self):
        """Dry run is handled at the Python level, not in the script itself."""
        script = self._make_script()
        assert "dry_run" not in script.lower()

    @pytest.mark.unit
    def test_script_multiple_files(self):
        files = [
            {"path": f"/etc/conf{i}.conf", "content": f"config{i}"}
            for i in range(5)
        ]
        script = self._make_script(files=files)
        for f in files:
            assert f["path"] in script

    @pytest.mark.unit
    def test_script_contains_file_content(self):
        files = [{"path": "/etc/nginx.conf", "content": "worker_processes auto;"}]
        script = self._make_script(files=files)
        assert "worker_processes auto;" in script

    @pytest.mark.unit
    def test_script_includes_backup_dir_in_header(self):
        files = [{"path": "/etc/a.conf", "content": "x"}]
        manifest = generate_batch_manifest(
            "my-profile", "sess", files, "snap-42"
        )
        script = generate_batch_script("my-profile", "snap-42", files, manifest)
        assert "my-profile" in script

    @pytest.mark.unit
    def test_script_preflight_phase_present(self):
        script = self._make_script()
        assert '"phase":"preflight"' in script

    @pytest.mark.unit
    def test_script_rollback_phase_present(self):
        script = self._make_script()
        assert '"phase":"rollback"' in script

    @pytest.mark.unit
    def test_script_apply_phase_present(self):
        script = self._make_script()
        assert '"phase":"apply"' in script

    @pytest.mark.unit
    def test_script_complete_phase_present(self):
        script = self._make_script()
        assert '"phase":"complete"' in script

    @pytest.mark.unit
    def test_empty_file_list_produces_valid_script(self):
        """An empty file list should still produce a syntactically valid script."""
        files = []
        manifest = generate_batch_manifest("prof", "sess", files, "snap-empty")
        script = generate_batch_script("prof", "snap-empty", files, manifest)
        assert script.startswith("#!/bin/bash")
        assert "set -euo pipefail" in script
