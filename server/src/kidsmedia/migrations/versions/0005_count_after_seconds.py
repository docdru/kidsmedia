"""Rules get count_after_seconds: a started file uses up an item only after it has been
played this long today (SPEC §3.3). Existing rules get the default of 10 s.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rules",
        sa.Column("count_after_seconds", sa.Integer, nullable=False, server_default="10"),
    )


def downgrade() -> None:
    with op.batch_alter_table("rules") as batch:
        batch.drop_column("count_after_seconds")
