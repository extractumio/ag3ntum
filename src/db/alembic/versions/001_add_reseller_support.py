"""Add reseller support tables and extend users table.

This is the initial Alembic migration on a database that may already have
tables created via SQLAlchemy create_all(). All create_table and add_column
operations guard against pre-existing objects so the migration is safe to
run on both fresh and existing databases.

Revision ID: 001
Revises:
Create Date: 2026-03-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

# ---------------------------------------------------------------------------
# Revision identifiers
# ---------------------------------------------------------------------------
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _table_exists(conn, table_name: str) -> bool:
    """Return True if the named table exists in the database."""
    result = conn.execute(
        text(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name=:name"
        ),
        {"name": table_name},
    )
    return result.scalar() > 0


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Return True if the named column exists in the given table."""
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    return any(row[1] == column_name for row in result)


def _index_exists(conn, index_name: str) -> bool:
    """Return True if the named index exists in the database."""
    result = conn.execute(
        text(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='index' AND name=:name"
        ),
        {"name": index_name},
    )
    return result.scalar() > 0


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. resellers — must exist before users.reseller_id FK
    # ------------------------------------------------------------------
    if not _table_exists(bind, "resellers"):
        op.create_table(
            "resellers",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(100), nullable=False, unique=True),
            sa.Column("company", sa.String(255), nullable=True),
            sa.Column("contact_email", sa.String(255), nullable=False, unique=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("owner_user_id", sa.String(36), nullable=False, unique=True),
            sa.Column("max_users", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("max_concurrent_tasks", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("max_daily_tasks", sa.Integer(), nullable=False, server_default="500"),
            sa.Column("max_monthly_spending_usd", sa.Float(), nullable=True),
            sa.Column("max_daily_spending_usd", sa.Float(), nullable=True),
            sa.Column("spending_alert_threshold_pct", sa.Integer(), nullable=False, server_default="80"),
            sa.Column("llm_provider", sa.String(100), nullable=True),
            sa.Column("features_json", sa.Text(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("suspended_at", sa.DateTime(), nullable=True),
            sa.Column("suspended_reason", sa.Text(), nullable=True),
            sa.Column("pre_suspend_user_states", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            # FK to users.id deferred — SQLite FK enforcement is opt-in
            sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        )

    # ------------------------------------------------------------------
    # 2. Extend users table with reseller columns
    # ------------------------------------------------------------------
    users_additions = [
        ("reseller_id", sa.String(36), True, None),
        ("metadata_json", sa.Text(), True, None),
        ("features_json", sa.Text(), True, None),
        ("settings_mode", sa.String(20), False, "readonly"),
        ("allowed_overrides", sa.Text(), True, None),
        ("security_overrides_json", sa.Text(), True, None),
        ("spending_limit_monthly_usd", sa.Float(), True, None),
        ("spending_limit_daily_usd", sa.Float(), True, None),
        ("spending_limit_per_session_usd", sa.Float(), True, None),
    ]
    for col_name, col_type, nullable, server_default in users_additions:
        if not _column_exists(bind, "users", col_name):
            with op.batch_alter_table("users", schema=None) as batch_op:
                kwargs = {"nullable": nullable}
                if server_default is not None:
                    kwargs["server_default"] = server_default
                batch_op.add_column(sa.Column(col_name, col_type, **kwargs))

    # Add index on users.reseller_id if the column was just created
    if not _index_exists(bind, "ix_users_reseller_id"):
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.create_index("ix_users_reseller_id", ["reseller_id"])

    # ------------------------------------------------------------------
    # 3. api_keys
    # ------------------------------------------------------------------
    if not _table_exists(bind, "api_keys"):
        op.create_table(
            "api_keys",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("reseller_id", sa.String(36), nullable=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("key_prefix", sa.String(12), nullable=False),
            sa.Column("key_hash", sa.String(128), nullable=False),
            sa.Column("scopes", sa.Text(), nullable=False),
            sa.Column("ip_allowlist", sa.Text(), nullable=True),
            sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("last_used_ip", sa.String(45), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["reseller_id"], ["resellers.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        )
        op.create_index("ix_api_keys_reseller_id", "api_keys", ["reseller_id"])
        op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"])

    # ------------------------------------------------------------------
    # 4. api_key_audit_log
    # ------------------------------------------------------------------
    if not _table_exists(bind, "api_key_audit_log"):
        op.create_table(
            "api_key_audit_log",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("api_key_id", sa.String(36), nullable=True),
            sa.Column("reseller_id", sa.String(36), nullable=True),
            sa.Column("action", sa.String(50), nullable=False),
            sa.Column("target_user_id", sa.String(36), nullable=True),
            sa.Column("ip_address", sa.String(45), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"]),
            sa.ForeignKeyConstraint(["reseller_id"], ["resellers.id"]),
        )
        op.create_index("ix_api_key_audit_log_api_key_id", "api_key_audit_log", ["api_key_id"])
        op.create_index("ix_api_key_audit_log_reseller_id", "api_key_audit_log", ["reseller_id"])
        op.create_index("ix_api_key_audit_log_timestamp", "api_key_audit_log", ["timestamp"])

    # ------------------------------------------------------------------
    # 5. usage_records
    # ------------------------------------------------------------------
    if not _table_exists(bind, "usage_records"):
        op.create_table(
            "usage_records",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("reseller_id", sa.String(36), nullable=True),
            sa.Column("session_id", sa.String(50), nullable=False),
            sa.Column("period_start", sa.DateTime(), nullable=False),
            sa.Column("period_end", sa.DateTime(), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cache_creation_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("model", sa.String(100), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("num_turns", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("ssh_commands_executed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("files_uploaded", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["reseller_id"], ["resellers.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        )
        op.create_index("ix_usage_records_user_id", "usage_records", ["user_id"])
        op.create_index("ix_usage_records_reseller_id", "usage_records", ["reseller_id"])
        op.create_index("ix_usage_reseller_period", "usage_records", ["reseller_id", "period_start"])
        op.create_index("ix_usage_user_period", "usage_records", ["user_id", "period_start"])

    # ------------------------------------------------------------------
    # 6. reseller_quotas
    # ------------------------------------------------------------------
    if not _table_exists(bind, "reseller_quotas"):
        op.create_table(
            "reseller_quotas",
            sa.Column("reseller_id", sa.String(36), primary_key=True),
            sa.Column("current_user_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tasks_today", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_reset", sa.DateTime(), nullable=False),
            sa.Column("monthly_input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("monthly_output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("monthly_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("monthly_reset", sa.DateTime(), nullable=False),
            sa.Column("daily_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("daily_cost_reset", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["reseller_id"], ["resellers.id"]),
        )

    # ------------------------------------------------------------------
    # 7. user_skills
    # ------------------------------------------------------------------
    if not _table_exists(bind, "user_skills"):
        op.create_table(
            "user_skills",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("reseller_id", sa.String(36), nullable=True),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("source", sa.String(20), nullable=False),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["reseller_id"], ["resellers.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.UniqueConstraint("user_id", "name", name="uq_user_skill_name"),
        )
        op.create_index("ix_user_skills_user_id", "user_skills", ["user_id"])
        op.create_index("ix_user_skills_reseller_id", "user_skills", ["reseller_id"])

    # ------------------------------------------------------------------
    # 8. reseller_skill_library
    # ------------------------------------------------------------------
    if not _table_exists(bind, "reseller_skill_library"):
        op.create_table(
            "reseller_skill_library",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("reseller_id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["reseller_id"], ["resellers.id"]),
            sa.UniqueConstraint("reseller_id", "name", name="uq_reseller_skill_name"),
        )
        op.create_index(
            "ix_reseller_skill_library_reseller_id",
            "reseller_skill_library",
            ["reseller_id"],
        )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    bind = op.get_bind()

    # Drop tables in reverse dependency order
    for table in [
        "reseller_skill_library",
        "user_skills",
        "reseller_quotas",
        "usage_records",
        "api_key_audit_log",
        "api_keys",
    ]:
        if _table_exists(bind, table):
            op.drop_table(table)

    # Remove index on users.reseller_id before batch-altering the column
    if _index_exists(bind, "ix_users_reseller_id"):
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_index("ix_users_reseller_id")

    # Remove reseller-related columns from users via batch mode
    users_reseller_cols = [
        "reseller_id",
        "metadata_json",
        "features_json",
        "settings_mode",
        "allowed_overrides",
        "security_overrides_json",
        "spending_limit_monthly_usd",
        "spending_limit_daily_usd",
        "spending_limit_per_session_usd",
    ]
    existing_cols = {
        row[1]
        for row in bind.execute(text("PRAGMA table_info(users)"))
    }
    cols_to_drop = [c for c in users_reseller_cols if c in existing_cols]
    if cols_to_drop:
        with op.batch_alter_table("users", schema=None) as batch_op:
            for col in cols_to_drop:
                batch_op.drop_column(col)

    # Drop resellers last (other tables FK into it)
    if _table_exists(bind, "resellers"):
        op.drop_table("resellers")
