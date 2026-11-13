"""M6: parent's reason texts per rule, app releases for self-update.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMN, not batch mode: batch would rebuild "rules", and dropping
    # the old table would cascade into rule_groups and adjustments.
    op.add_column("rules", sa.Column("message_en", sa.String))
    op.add_column("rules", sa.Column("message_de", sa.String))
    op.create_table(
        "app_releases",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("version_name", sa.String, nullable=False),
        sa.Column("version_code", sa.Integer, nullable=False, unique=True),
        sa.Column("file", sa.String, nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String, nullable=False),
        sa.Column("uploaded_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_releases")
    with op.batch_alter_table("rules") as batch:
        batch.drop_column("message_de")
        batch.drop_column("message_en")
