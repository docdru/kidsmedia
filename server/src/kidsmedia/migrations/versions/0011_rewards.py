"""Rewards (SPEC §14.4, §14.5): swaps, wishes, wish requests and the star ledger.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _profile_id() -> sa.Column[int]:
    return sa.Column(
        "profile_id",
        sa.Integer,
        sa.ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "reward_swaps",
        sa.Column("id", sa.Integer, primary_key=True),
        _profile_id(),
        sa.Column("stars", sa.Integer, nullable=False),
        sa.Column(
            "rule_id",
            sa.Integer,
            sa.ForeignKey("rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("extra_items", sa.Integer, nullable=False, server_default="0"),
        sa.Column("extra_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_per_day", sa.Integer),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "reward_wishes",
        sa.Column("id", sa.Integer, primary_key=True),
        _profile_id(),
        sa.Column("texts", sa.JSON, nullable=False),
        sa.Column("stars", sa.Integer, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "wish_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        _profile_id(),
        sa.Column(
            "wish_id",
            sa.Integer,
            sa.ForeignKey("reward_wishes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("decided_at", sa.DateTime),
    )
    op.create_table(
        "reward_ledger",
        sa.Column("id", sa.Integer, primary_key=True),
        _profile_id(),
        sa.Column("delta", sa.Integer, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("ref_id", sa.Integer),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("reward_ledger")
    op.drop_table("wish_requests")
    op.drop_table("reward_wishes")
    op.drop_table("reward_swaps")
