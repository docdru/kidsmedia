"""Photos of drawings and stories (SPEC §15.3): `activity_photos`.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity_photos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "done_id",
            sa.Integer,
            sa.ForeignKey("activity_done.id", ondelete="SET NULL"),
        ),
        sa.Column("world_id", sa.Integer),  # no FK: photos outlive their world
        sa.Column("world_name", sa.String, nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("task_title", sa.String, nullable=False),
        sa.Column("key", sa.String, nullable=False),
        sa.Column("width", sa.Integer, nullable=False),
        sa.Column("height", sa.Integer, nullable=False),
        sa.Column("taken_at", sa.DateTime, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("activity_photos")
