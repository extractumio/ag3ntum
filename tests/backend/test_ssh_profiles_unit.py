"""
Unit tests for SSH profile models and pure functions.

Tests pure functions without any DB, HTTP, or external dependencies:
- mask_ssh_key() masking behaviour
- CreateSSHProfileRequest Pydantic validation
- load_ssh_profiles() YAML / DB merge logic
"""
import pytest
from pathlib import Path

from pydantic import ValidationError

from src.api.ssh_profile_models import CreateSSHProfileRequest, mask_ssh_key
from src.core.ssh.ssh_config import SSHProfile, load_ssh_profiles


# ---------------------------------------------------------------------------
# mask_ssh_key
# ---------------------------------------------------------------------------

class TestMaskSSHKey:

    @pytest.mark.unit
    def test_mask_normal_key(self):
        """Key longer than 60 chars: first 40 + 20 asterisks + last 20."""
        key = (
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAB"
            "BAAAAC3NzaC1lZDI1NTE5AAAAIFMKMKCATwncj35k3QHa06VTNQ"
            "wCnZzlE+fwCHodPR2KAAAAEm9wZW5jbGF3LWhvc3RpbmdlcgEC\n"
            "-----END OPENSSH PRIVATE KEY-----"
        )
        masked = mask_ssh_key(key)
        assert masked.startswith(key[:40])
        assert "********************" in masked
        assert masked.endswith(key[-20:])
        assert len(masked) < len(key)

    @pytest.mark.unit
    def test_mask_short_key(self):
        """Key with 60 or fewer chars is returned unchanged."""
        short = "-----BEGIN RSA-----\nabc\n-----END RSA-----"
        assert mask_ssh_key(short) == short

    @pytest.mark.unit
    def test_mask_empty_string(self):
        """Empty string returns empty string."""
        assert mask_ssh_key("") == ""

    @pytest.mark.unit
    def test_mask_exactly_60_chars(self):
        """Boundary: exactly 60 chars is returned as-is (not masked)."""
        key = "x" * 60
        assert mask_ssh_key(key) == key

    @pytest.mark.unit
    def test_mask_61_chars(self):
        """Boundary: 61 chars triggers masking."""
        key = "x" * 61
        masked = mask_ssh_key(key)
        assert "********************" in masked
        assert masked != key

    @pytest.mark.unit
    def test_mask_preserves_first_40(self):
        """The first 40 characters of the key are always preserved."""
        key = "A" * 40 + "B" * 30
        masked = mask_ssh_key(key)
        assert masked[:40] == "A" * 40

    @pytest.mark.unit
    def test_mask_preserves_last_20(self):
        """The last 20 characters of the key are always preserved."""
        key = "A" * 40 + "B" * 30
        masked = mask_ssh_key(key)
        assert masked[-20:] == "B" * 20

    @pytest.mark.unit
    def test_asterisks_count_is_20(self):
        """The masked middle section is exactly 20 asterisks."""
        key = "A" * 40 + "MIDDLE_SECRET_DATA_HERE" + "Z" * 20
        masked = mask_ssh_key(key)
        assert "********************" in masked
        # Confirm the asterisk block is exactly 20
        star_index = masked.index("*")
        star_block = masked[star_index:star_index + 20]
        assert star_block == "*" * 20


# ---------------------------------------------------------------------------
# CreateSSHProfileRequest validation
# ---------------------------------------------------------------------------

_VALID_PEM = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMw\n"
    "-----END OPENSSH PRIVATE KEY-----"
)


def _valid_payload(**overrides) -> dict:
    """Return a minimal valid payload dict, with optional field overrides."""
    base = {
        "name": "my-server",
        "host": "192.168.1.100",
        "username": "root",
        "private_key": _VALID_PEM,
    }
    base.update(overrides)
    return base


class TestCreateSSHProfileRequestValidation:

    @pytest.mark.unit
    def test_valid_request_defaults(self):
        """A minimal valid request populates defaults correctly."""
        req = CreateSSHProfileRequest(**_valid_payload())
        assert req.port == 22
        assert req.mode == "readonly"
        assert req.privilege_level == 0
        assert req.passphrase is None
        assert req.description is None

    @pytest.mark.unit
    def test_valid_request_with_all_optional_fields(self):
        """All optional fields can be set without error."""
        req = CreateSSHProfileRequest(**_valid_payload(
            port=2222,
            passphrase="secret",
            mode="operations",
            privilege_level=2,
            allowed_operations=["df", "ps"],
            description="prod server",
        ))
        assert req.port == 2222
        assert req.mode == "operations"
        assert req.privilege_level == 2

    @pytest.mark.unit
    def test_name_rejects_uppercase(self):
        """Profile name containing uppercase letters fails validation."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(name="MyServer"))

    @pytest.mark.unit
    def test_name_rejects_starting_with_digit(self):
        """Profile name starting with a digit fails validation."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(name="1server"))

    @pytest.mark.unit
    def test_name_rejects_starting_with_hyphen(self):
        """Profile name starting with a hyphen fails validation."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(name="-server"))

    @pytest.mark.unit
    def test_name_accepts_dots_hyphens_underscores(self):
        """Valid name with dots, hyphens, and underscores is accepted."""
        req = CreateSSHProfileRequest(**_valid_payload(name="my.server-1_test"))
        assert req.name == "my.server-1_test"

    @pytest.mark.unit
    def test_host_rejects_semicolon(self):
        """Host containing semicolon (injection char) is rejected."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(host="1.2.3.4; rm -rf /"))

    @pytest.mark.unit
    def test_host_rejects_pipe(self):
        """Host containing pipe char is rejected."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(host="host|evil"))

    @pytest.mark.unit
    def test_host_rejects_ampersand(self):
        """Host containing ampersand is rejected."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(host="host&cmd"))

    @pytest.mark.unit
    def test_host_rejects_backtick(self):
        """Host containing backtick is rejected."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(host="host`cmd`"))

    @pytest.mark.unit
    def test_host_rejects_dollar_sign(self):
        """Host containing dollar sign is rejected."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(host="host$VAR"))

    @pytest.mark.unit
    def test_key_must_start_with_begin_marker(self):
        """Private key not starting with '-----BEGIN' is rejected."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(private_key="not-a-pem-key"))

    @pytest.mark.unit
    def test_key_accepts_begin_marker(self):
        """Key starting with '-----BEGIN' passes PEM format check."""
        req = CreateSSHProfileRequest(**_valid_payload(private_key=_VALID_PEM))
        assert req.private_key.startswith("-----BEGIN")

    @pytest.mark.unit
    def test_port_min_boundary(self):
        """Port value 0 is below minimum (ge=1) and is rejected."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(port=0))

    @pytest.mark.unit
    def test_port_max_boundary(self):
        """Port value 65536 is above maximum (le=65535) and is rejected."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(port=65536))

    @pytest.mark.unit
    def test_port_valid_bounds(self):
        """Port values 1 and 65535 are accepted as boundary values."""
        req_min = CreateSSHProfileRequest(**_valid_payload(port=1))
        req_max = CreateSSHProfileRequest(**_valid_payload(port=65535))
        assert req_min.port == 1
        assert req_max.port == 65535

    @pytest.mark.unit
    def test_privilege_level_max_4(self):
        """Privilege level above 4 is rejected."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(privilege_level=5))

    @pytest.mark.unit
    def test_privilege_level_min_0(self):
        """Privilege level below 0 is rejected."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(privilege_level=-1))

    @pytest.mark.unit
    def test_privilege_level_valid_range(self):
        """Privilege levels 0 through 4 are all accepted."""
        for level in range(5):
            req = CreateSSHProfileRequest(**_valid_payload(privilege_level=level))
            assert req.privilege_level == level

    @pytest.mark.unit
    def test_invalid_mode_rejected(self):
        """Mode not in the allowed set is rejected."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(mode="admin"))

    @pytest.mark.unit
    def test_valid_modes_accepted(self):
        """All three allowed mode values are accepted."""
        for mode in ("readonly", "operations", "filtered_shell"):
            req = CreateSSHProfileRequest(**_valid_payload(mode=mode))
            assert req.mode == mode

    @pytest.mark.unit
    def test_key_stripped_of_whitespace(self):
        """Leading/trailing whitespace on the key is stripped by the validator."""
        padded = "  " + _VALID_PEM + "  "
        req = CreateSSHProfileRequest(**_valid_payload(private_key=padded))
        assert not req.private_key.startswith(" ")
        assert not req.private_key.endswith(" ")


# ---------------------------------------------------------------------------
# load_ssh_profiles — YAML / DB merge
# ---------------------------------------------------------------------------

class TestLoadSSHProfilesMerge:

    @pytest.mark.unit
    def test_empty_dir_no_db_returns_empty(self, tmp_path: Path):
        """No YAML file, no DB profiles → empty dict."""
        result = load_ssh_profiles(tmp_path)
        assert result == {}

    @pytest.mark.unit
    def test_db_profiles_loaded_when_no_yaml(self, tmp_path: Path):
        """DB profiles are loaded when no YAML file exists."""
        db_profiles = [
            SSHProfile(name="db-server", host="10.0.0.1", username="deploy"),
        ]
        result = load_ssh_profiles(tmp_path, db_profiles=db_profiles)
        assert "db-server" in result
        assert result["db-server"].host == "10.0.0.1"
        assert result["db-server"].username == "deploy"

    @pytest.mark.unit
    def test_yaml_overrides_db_on_name_collision(self, tmp_path: Path):
        """YAML profile wins over DB profile with the same name."""
        yaml_content = (
            "profiles:\n"
            "  shared-server:\n"
            "    host: yaml-host.example.com\n"
            "    username: yaml-user\n"
        )
        (tmp_path / "ssh-profiles.yaml").write_text(yaml_content)
        db_profiles = [
            SSHProfile(
                name="shared-server",
                host="db-host.example.com",
                username="db-user",
            ),
        ]
        result = load_ssh_profiles(tmp_path, db_profiles=db_profiles)
        assert result["shared-server"].host == "yaml-host.example.com"
        assert result["shared-server"].username == "yaml-user"

    @pytest.mark.unit
    def test_no_db_profiles_is_backward_compatible(self, tmp_path: Path):
        """Calling without db_profiles works identically to the original API."""
        result = load_ssh_profiles(tmp_path)
        assert result == {}

    @pytest.mark.unit
    def test_mixed_db_and_yaml_profiles(self, tmp_path: Path):
        """DB-only and YAML-only profiles both appear in the merged result."""
        yaml_content = (
            "profiles:\n"
            "  yaml-server:\n"
            "    host: yaml.example.com\n"
            "    username: yaml-user\n"
        )
        (tmp_path / "ssh-profiles.yaml").write_text(yaml_content)
        db_profiles = [
            SSHProfile(name="db-server", host="db.example.com", username="db-user"),
        ]
        result = load_ssh_profiles(tmp_path, db_profiles=db_profiles)
        assert "yaml-server" in result
        assert "db-server" in result
        assert len(result) == 2

    @pytest.mark.unit
    def test_multiple_db_profiles(self, tmp_path: Path):
        """Multiple DB profiles are all loaded."""
        db_profiles = [
            SSHProfile(name="server-a", host="a.example.com", username="ua"),
            SSHProfile(name="server-b", host="b.example.com", username="ub"),
            SSHProfile(name="server-c", host="c.example.com", username="uc"),
        ]
        result = load_ssh_profiles(tmp_path, db_profiles=db_profiles)
        assert len(result) == 3
        assert result["server-a"].host == "a.example.com"
        assert result["server-c"].username == "uc"

    @pytest.mark.unit
    def test_yaml_profile_without_host_is_skipped(self, tmp_path: Path):
        """A YAML profile entry missing the required 'host' field is skipped."""
        yaml_content = (
            "profiles:\n"
            "  bad-entry:\n"
            "    username: whoever\n"
            "  good-entry:\n"
            "    host: good.example.com\n"
            "    username: good-user\n"
        )
        (tmp_path / "ssh-profiles.yaml").write_text(yaml_content)
        result = load_ssh_profiles(tmp_path)
        assert "bad-entry" not in result
        assert "good-entry" in result

    @pytest.mark.unit
    def test_yaml_parse_error_returns_db_profiles(self, tmp_path: Path):
        """If YAML is malformed, DB profiles are still returned (no total failure)."""
        (tmp_path / "ssh-profiles.yaml").write_text(":\ninvalid: [yaml\n")
        db_profiles = [
            SSHProfile(name="fallback", host="10.0.0.1", username="u"),
        ]
        result = load_ssh_profiles(tmp_path, db_profiles=db_profiles)
        assert "fallback" in result

    @pytest.mark.unit
    def test_db_profile_defaults(self, tmp_path: Path):
        """DB profiles use sensible defaults for optional fields."""
        db_profiles = [SSHProfile(name="minimal", host="1.2.3.4")]
        result = load_ssh_profiles(tmp_path, db_profiles=db_profiles)
        profile = result["minimal"]
        assert profile.port == 22
        assert profile.mode == "readonly"
        assert profile.privilege_level == 0
