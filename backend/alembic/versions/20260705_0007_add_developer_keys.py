"""Add hashed developer API keys.

Revision ID: 20260705_0007
Revises: 20260705_0006
Create Date: 2026-07-05
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260705_0007"
down_revision: str | Sequence[str] | None = "20260705_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "developer_keys",
        sa.Column(
            "hashed_key",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "key_prefix",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "owner_name",
            sa.String(length=160),
            nullable=False,
        ),
        sa.Column(
            "owner_email",
            sa.String(length=254),
            nullable=True,
        ),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.String(length=80)),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cardinality(scopes) > 0",
            name="ck_developer_keys_scopes_not_empty",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_developer_keys_active_expires",
        "developer_keys",
        ["is_active", "expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_developer_keys_expires_at"),
        "developer_keys",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_developer_keys_key_prefix"),
        "developer_keys",
        ["key_prefix"],
        unique=False,
    )
    op.create_index(
        "ux_developer_keys_hashed_key",
        "developer_keys",
        ["hashed_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ux_developer_keys_hashed_key",
        table_name="developer_keys",
    )
    op.drop_index(
        op.f("ix_developer_keys_key_prefix"),
        table_name="developer_keys",
    )
    op.drop_index(
        op.f("ix_developer_keys_expires_at"),
        table_name="developer_keys",
    )
    op.drop_index(
        "ix_developer_keys_active_expires",
        table_name="developer_keys",
    )
    op.drop_table("developer_keys")
