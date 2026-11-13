"""Stars in the ledger, one pending request per wish (SPEC §14.4, §14.5).

- Earned stars become `earn` rows in `reward_ledger` (one per `activity_done` row, +1, +2 for
  a read task right at the first try), so deleting a world no longer takes stars away.
- A profile whose ledger would sum below zero (stars of an already deleted world were spent)
  gets one `earn` row that brings it back to zero.
- A partial unique index allows one pending request per wish; older duplicates (possible
  before) keep the oldest pending one, the others are declined.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE wish_requests SET status = 'declined', decided_at = created_at
        WHERE status = 'pending' AND id NOT IN (
            SELECT MIN(id) FROM wish_requests WHERE status = 'pending'
            GROUP BY profile_id, wish_id
        )
        """
    )
    op.create_index(
        "uq_wish_requests_pending",
        "wish_requests",
        ["profile_id", "wish_id"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
    )
    op.execute(
        """
        INSERT INTO reward_ledger (profile_id, delta, kind, ref_id, created_at)
        SELECT profile_id, CASE WHEN kind = 'read' AND attempts = 1 THEN 2 ELSE 1 END,
               'earn', id, done_at
        FROM activity_done ORDER BY id
        """
    )
    op.execute(
        """
        INSERT INTO reward_ledger (profile_id, delta, kind, ref_id, created_at)
        SELECT profile_id, -SUM(delta), 'earn', NULL, MIN(created_at)
        FROM reward_ledger GROUP BY profile_id HAVING SUM(delta) < 0
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM reward_ledger WHERE kind = 'earn'")
    op.drop_index("uq_wish_requests_pending", table_name="wish_requests")
