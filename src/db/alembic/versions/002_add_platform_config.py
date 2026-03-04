"""Add platform_config table for mutable platform defaults.

Stores key-value configuration that admins can update via PUT /admin/config,
replacing the hardcoded defaults in FeatureFlagService and QuotaManager.

Revision ID: 002
Revises: 001
Create Date: 2026-03-01 00:00:00.000000

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
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "platform_config"):
        op.create_table(
            "platform_config",
            sa.Column("key", sa.String(100), primary_key=True),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("updated_by", sa.String(36), nullable=True),
        )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    op.drop_table("platform_config")
