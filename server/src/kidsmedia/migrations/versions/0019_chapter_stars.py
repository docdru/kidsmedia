"""Stars for a finished chapter (SPEC §14.4, v0.24).

Adds `activity_settings.chapter_stars` (default 3). Stars are no longer earned per done task:
only a chapter with every idea done earns `chapter_stars` (a `chapter` row in the ledger), and
the parent's bonus stars. Stars already earned per task stay (their `earn` rows are kept).

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "activity_settings",
        sa.Column("chapter_stars", sa.Integer, nullable=False, server_default="3"),
    )


def downgrade() -> None:
    with op.batch_alter_table("activity_settings") as batch:
        batch.drop_column("chapter_stars")
