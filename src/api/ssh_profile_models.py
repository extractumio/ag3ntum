"""Pydantic models for SSH profile management API."""
import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Authoritative set of valid SSH modes — used by validators and service layer.
# Frontend equivalent: SSHMode type in src/web_terminal_client/src/types/ssh.ts
VALID_SSH_MODES = frozenset({"readonly", "operations", "filtered_shell"})


def mask_ssh_key(pem: str) -> str:
    """Mask SSH private key for safe display.

    Shows first 40 and last 20 characters with asterisks in between.
    This reveals the key type header/footer for identification
    without exposing the secret material.

    Mirrored in frontend: src/web_terminal_client/src/utils/sshUtils.ts
    """
    if len(pem) <= 60:
        return pem
    return pem[:40] + "********************" + pem[-20:]


# ---------------------------------------------------------------------------
# Shared field validators (called from Create/Update/Test models)
# ---------------------------------------------------------------------------

def _validate_profile_name(v: str) -> str:
    if not re.match(r'^[a-z][a-z0-9._-]*$', v):
        raise ValueError(
            "Profile name must start with a letter and contain only "
            "lowercase letters, numbers, dots, hyphens, underscores"
        )
    return v


def _validate_ssh_mode(v: str) -> str:
    if v not in VALID_SSH_MODES:
        raise ValueError(f"Mode must be one of: {', '.join(sorted(VALID_SSH_MODES))}")
    return v


def _validate_pem_key(v: str) -> str:
    if not v.strip().startswith("-----BEGIN"):
        raise ValueError("Private key must be in PEM format (-----BEGIN ...)")
    return v.strip()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateSSHProfileRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64,
                      description="Profile name (unique per user)")
    host: str = Field(..., min_length=1, max_length=255,
                      description="Hostname or IP address")
    port: int = Field(default=22, ge=1, le=65535,
                      description="SSH port")
    username: str = Field(..., min_length=1, max_length=64,
                          description="SSH username")
    private_key: str = Field(..., min_length=10,
                             description="PEM-encoded SSH private key")
    passphrase: Optional[str] = Field(default=None,
                                      description="Key passphrase if encrypted")
    mode: str = Field(default="readonly",
                      description="readonly | operations | filtered_shell")
    privilege_level: int = Field(default=0, ge=0, le=3,
                                 description="Privilege level 0-3")
    allowed_operations: Optional[list[str]] = Field(
        default=None, description="For operations mode")
    description: Optional[str] = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_profile_name(v)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        return _validate_ssh_mode(v)

    @field_validator("private_key")
    @classmethod
    def validate_key_format(cls, v: str) -> str:
        return _validate_pem_key(v)

    @field_validator("host")
    @classmethod
    def validate_host(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Host cannot be empty")
        # Block obvious bad inputs
        if any(c in v for c in [' ', ';', '|', '&', '$', '`']):
            raise ValueError("Host contains invalid characters")
        return v


class UpdateSSHProfileRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    host: Optional[str] = Field(default=None, min_length=1, max_length=255)
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    username: Optional[str] = Field(default=None, min_length=1, max_length=64)
    private_key: Optional[str] = Field(default=None, min_length=10,
                                       description="New key to replace existing")
    passphrase: Optional[str] = None
    mode: Optional[str] = None
    privilege_level: Optional[int] = Field(default=None, ge=0, le=3)
    allowed_operations: Optional[list[str]] = None
    description: Optional[str] = Field(default=None, max_length=500)
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        return _validate_profile_name(v) if v is not None else v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: Optional[str]) -> Optional[str]:
        return _validate_ssh_mode(v) if v is not None else v

    @field_validator("private_key")
    @classmethod
    def validate_key_format(cls, v: Optional[str]) -> Optional[str]:
        return _validate_pem_key(v) if v is not None else v


class TestSSHConnectionRequest(BaseModel):
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(..., min_length=1, max_length=64)
    private_key: str = Field(..., min_length=10)
    passphrase: Optional[str] = None

    @field_validator("private_key")
    @classmethod
    def validate_key_format(cls, v: str) -> str:
        return _validate_pem_key(v)


class TestSSHConnectionResponse(BaseModel):
    status: Literal["success", "failed"]
    error_code: Optional[str] = None
    message: str
    host_key_fingerprint: Optional[str] = None
    host_key_type: Optional[str] = None
    server_banner: Optional[str] = None
    latency_ms: Optional[int] = None


class SSHProfileResponse(BaseModel):
    id: str
    name: str
    host: str
    port: int
    username: str
    mode: str
    privilege_level: int
    host_key_pinned: bool
    host_key_fingerprint: Optional[str] = None
    key_preview: str
    key_fingerprint: Optional[str] = None
    key_type: Optional[str] = None
    is_active: bool
    last_connected_at: Optional[datetime] = None
    last_connection_error: Optional[str] = None
    description: Optional[str] = None
    created_by: str
    created_at: datetime


class SSHProfileListResponse(BaseModel):
    profiles: list[SSHProfileResponse]
    count: int
