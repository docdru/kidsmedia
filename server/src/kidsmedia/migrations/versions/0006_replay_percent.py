"""Rules get replay_percent: a counted file's listening budget per day, in % of its
length (SPEC §3.3). Existing rules get 120 %; NULL means unlimited replays.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rules", sa.Column("replay_percent", sa.Integer, server_default="120"))


def downgrade() -> None:
    with op.batch_alter_table("rules") as batch:
        batch.drop_column("replay_percent")
