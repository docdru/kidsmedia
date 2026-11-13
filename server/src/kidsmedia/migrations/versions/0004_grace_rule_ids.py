"""Graces record every rule they cover, not only the most restrictive one (SPEC §3.3a).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMN (no table rebuild). Existing rows keep '[]' and fall back to
    # rule_id when read (usage.grace_rule_ids).
    op.add_column("graces", sa.Column("rule_ids", sa.JSON, nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("graces") as batch:
        batch.drop_column("rule_ids")
