"""Devices get max_volume_percent: a cap on the tablet's media volume in % of its
maximum (SPEC §5.1). NULL, the default for existing tablets, means no cap.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("max_volume_percent", sa.Integer))


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("max_volume_percent")
