"""M7: offline flags on groups and media, de-duplication of offline play events.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMN (no table rebuild, so no cascades into plays or rules).
    op.add_column("groups", sa.Column("offline", sa.Boolean, nullable=False, server_default="0"))
    op.add_column("media", sa.Column("offline", sa.Boolean, nullable=False, server_default="0"))
    op.create_table(
        "offline_events",
        sa.Column("event_id", sa.String, primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("received_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("offline_events")
    with op.batch_alter_table("media") as batch:
        batch.drop_column("offline")
    with op.batch_alter_table("groups") as batch:
        batch.drop_column("offline")
