"""Activities (SPEC §13): story worlds, activity days, the queue and done tasks.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worlds",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("slug", sa.String, nullable=False, unique=True),
        sa.Column("age_band", sa.String, nullable=False),
        sa.Column("color", sa.String, nullable=False),
        sa.Column("cover_key", sa.String),
        sa.Column("cover_aspect", sa.Float),
        sa.Column("content", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "activity_settings",
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("days", sa.String, nullable=False, server_default="012345"),
        sa.Column("unlock_time", sa.String, nullable=False, server_default="07:00"),
        sa.Column("queue", sa.JSON, nullable=False),
        sa.Column("queue_start", sa.String),
    )
    op.create_table(
        "activity_days_off",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_day", sa.String, nullable=False),
        sa.Column("to_day", sa.String, nullable=False),
        sa.Column("note", sa.String, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "activity_done",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "world_id",
            sa.Integer,
            sa.ForeignKey("worlds.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("day", sa.String, nullable=False),
        sa.Column("done_at", sa.DateTime, nullable=False),
        sa.Column("device_id", sa.Integer, sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.UniqueConstraint("profile_id", "world_id", "position", "kind"),
    )


def downgrade() -> None:
    op.drop_table("activity_done")
    op.drop_table("activity_days_off")
    op.drop_table("activity_settings")
    op.drop_table("worlds")
