"""Rules get message_el: the parent's own reason text in Greek (SPEC §3.2, decision 30).
NULL, the default for existing rules, means the app's built-in text.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMN, not batch mode: batch would rebuild "rules" (see 0002).
    op.add_column("rules", sa.Column("message_el", sa.String))


def downgrade() -> None:
    with op.batch_alter_table("rules") as batch:
        batch.drop_column("message_el")
