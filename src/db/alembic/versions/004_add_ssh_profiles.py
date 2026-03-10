"""Add ssh_profiles table for UI-managed SSH connection profiles.

User-managed SSH profiles stored in DB alongside vault-encrypted keys.
Supports self-service profile CRUD without server restarts.

Revision ID: 004
Revises: 003
Create Date: 2026-03-09 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text


def _table_exists(conn, table_name: str) -> bool:
    """Return True if the named table exists in the database.

    Inlined here because Alembic loads migration files as standalone modules,
    not as part of a Python package — relative imports fail at runtime.
    """
    result = conn.execute(
        text(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name=:name"
        ),
        {"name": table_name},
    )
    return result.scalar() > 0

# ---------------------------------------------------------------------------
# Revision identifiers
# ---------------------------------------------------------------------------
revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "ssh_profiles"):
        op.create_table(
            "ssh_profiles",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "user_id", sa.String(36),
                sa.ForeignKey("users.id"), nullable=False,
            ),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("host", sa.String(255), nullable=False),
            sa.Column("port", sa.Integer(), nullable=False, server_default="22"),
            sa.Column("username", sa.String(64), nullable=False),
            sa.Column(
                "auth_method", sa.String(20),
                nullable=False, server_default="key",
            ),
            sa.Column(
                "key_vault_secret_id", sa.Integer(),
                sa.ForeignKey("vault_secrets.id"), nullable=True,
            ),
            sa.Column(
                "mode", sa.String(20),
                nullable=False, server_default="readonly",
            ),
            sa.Column(
                "privilege_level", sa.Integer(),
                nullable=False, server_default="0",
            ),
            sa.Column("allowed_operations", sa.Text(), nullable=True),
            sa.Column(
                "passphrase_vault_secret_id", sa.Integer(),
                sa.ForeignKey("vault_secrets.id"), nullable=True,
            ),
            sa.Column(
                "host_key_pinned", sa.Boolean(),
                nullable=False, server_default="0",
            ),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "is_active", sa.Boolean(),
                nullable=False, server_default="1",
            ),
            sa.Column("last_connected_at", sa.DateTime(), nullable=True),
            sa.Column(
                "last_connection_error", sa.String(500), nullable=True,
            ),
            sa.Column(
                "created_by", sa.String(20),
                nullable=False, server_default="self",
            ),
            sa.Column(
                "created_at", sa.DateTime(),
                nullable=False, server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at", sa.DateTime(),
                nullable=False, server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("user_id", "name", name="uq_ssh_profile_user_name"),
        )
        op.create_index(
            "ix_ssh_profile_user", "ssh_profiles", ["user_id"],
        )
        op.create_index(
            "ix_ssh_profile_user_active",
            "ssh_profiles", ["user_id", "is_active"],
        )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    op.drop_index("ix_ssh_profile_user_active", table_name="ssh_profiles")
    op.drop_index("ix_ssh_profile_user", table_name="ssh_profiles")
    op.drop_table("ssh_profiles")
