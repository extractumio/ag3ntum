"""
Unit tests for SSH profile models and pure functions.

Tests pure functions without any DB, HTTP, or external dependencies:
- mask_ssh_key() masking behaviour
- CreateSSHProfileRequest Pydantic validation
"""
import pytest

from pydantic import ValidationError

from src.api.ssh_profile_models import CreateSSHProfileRequest, mask_ssh_key


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
    def test_privilege_level_max_3(self):
        """Privilege level above 3 is rejected (max is P3)."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(privilege_level=4))

    @pytest.mark.unit
    def test_privilege_level_min_0(self):
        """Privilege level below 0 is rejected."""
        with pytest.raises(ValidationError):
            CreateSSHProfileRequest(**_valid_payload(privilege_level=-1))

    @pytest.mark.unit
    def test_privilege_level_valid_range(self):
        """Privilege levels 0 through 3 are all accepted (4-tier P0-P3 model)."""
        for level in range(4):
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
