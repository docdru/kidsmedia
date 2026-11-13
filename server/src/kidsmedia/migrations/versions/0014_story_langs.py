"""Story language per queued world (SPEC §14.7): `activity_settings.langs`.
Existing settings get '{}' (every story in the default language, as before).

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMN, not batch mode, so the table isn't rebuilt.
    op.add_column(
        "activity_settings",
        sa.Column("langs", sa.JSON, nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    with op.batch_alter_table("activity_settings") as batch:
        batch.drop_column("langs")
