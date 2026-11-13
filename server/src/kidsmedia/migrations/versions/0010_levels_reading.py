"""Activities v2 (SPEC §14.5): a level per child and the attempts of a read task.
Existing settings get 'normal'; existing done rows get NULL attempts.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMN, not batch mode, so the tables (and the plays history) aren't rebuilt.
    op.add_column(
        "activity_settings",
        sa.Column("level", sa.String, nullable=False, server_default="normal"),
    )
    op.add_column("activity_done", sa.Column("attempts", sa.Integer))


def downgrade() -> None:
    with op.batch_alter_table("activity_done") as batch:
        batch.drop_column("attempts")
    with op.batch_alter_table("activity_settings") as batch:
        batch.drop_column("level")
