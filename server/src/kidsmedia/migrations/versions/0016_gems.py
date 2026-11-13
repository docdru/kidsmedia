"""Gems 💎 (SPEC §16): `rules.gem_cost` and the per-profile `gem_ledger`.

Existing rules get no gem cost (NULL), so nothing changes for them. A partial unique index
books a `spend` once per profile, rule, file and day.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rules", sa.Column("gem_cost", sa.Integer))
    op.create_table(
        "gem_ledger",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("delta", sa.Integer, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("rule_id", sa.Integer, sa.ForeignKey("rules.id", ondelete="SET NULL")),
        sa.Column("media_id", sa.Integer, sa.ForeignKey("media.id", ondelete="SET NULL")),
        sa.Column("day", sa.String),
        sa.Column("title", sa.String),
        sa.Column("note", sa.String),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index(
        "uq_gem_ledger_spend",
        "gem_ledger",
        ["profile_id", "rule_id", "media_id", "day"],
        unique=True,
        sqlite_where=sa.text("kind = 'spend'"),
    )


def downgrade() -> None:
    op.drop_index("uq_gem_ledger_spend", table_name="gem_ledger")
    op.drop_table("gem_ledger")
    with op.batch_alter_table("rules") as batch:
        batch.drop_column("gem_cost")
