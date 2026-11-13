"""Opening times for Ideas (SPEC §14.4a, §14.5): `activity_settings.windows`.
Existing settings get '[]' (always open, as before).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMN, not batch mode, so the table isn't rebuilt.
    op.add_column(
        "activity_settings",
        sa.Column("windows", sa.JSON, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    with op.batch_alter_table("activity_settings") as batch:
        batch.drop_column("windows")
