"""Add webhook_endpoints and webhook_delivery_log tables.

Supports reseller webhook notifications with HMAC signing,
retry logic, and delivery tracking.

Revision ID: 003
Revises: 002
Create Date: 2026-03-02 00:00:00.000000

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
revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "webhook_endpoints"):
        op.create_table(
            "webhook_endpoints",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "reseller_id", sa.String(36),
                sa.ForeignKey("resellers.id"), nullable=False, index=True,
            ),
            sa.Column("url", sa.String(2048), nullable=False),
            sa.Column("secret", sa.String(128), nullable=False),
            sa.Column("events", sa.Text(), nullable=False),  # JSON array
            sa.Column("is_active", sa.Boolean(), default=True),
            sa.Column("description", sa.String(255), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(),
                nullable=False, server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at", sa.DateTime(),
                nullable=False, server_default=sa.func.now(),
            ),
        )

    if not _table_exists(bind, "webhook_delivery_log"):
        op.create_table(
            "webhook_delivery_log",
            sa.Column(
                "id", sa.Integer(), primary_key=True, autoincrement=True,
            ),
            sa.Column(
                "endpoint_id", sa.String(36),
                sa.ForeignKey("webhook_endpoints.id"), nullable=False,
                index=True,
            ),
            sa.Column("event_type", sa.String(50), nullable=False),
            sa.Column("payload", sa.Text(), nullable=False),
            sa.Column(
                "status", sa.String(20), nullable=False, server_default="pending",
            ),
            sa.Column("attempts", sa.Integer(), default=0),
            sa.Column("max_attempts", sa.Integer(), default=5),
            sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
            sa.Column("next_retry_at", sa.DateTime(), nullable=True),
            sa.Column("response_status", sa.Integer(), nullable=True),
            sa.Column("response_body", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column(
                "created_at", sa.DateTime(),
                nullable=False, server_default=sa.func.now(),
            ),
        )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    op.drop_table("webhook_delivery_log")
    op.drop_table("webhook_endpoints")
