"""Done activities in the admin (SPEC §17.3): the bonus star belongs to the done task.

Existing photo bonus rows (`kind = 'bonus'`, `ref_id` = an `activity_photos` id) get
`ref_id` = that photo's done id; a bonus whose photo or done row is gone gets `ref_id` NULL. No schema change: `reopen` is a new `reward_ledger.kind` value, and `kind` is a
plain string column.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every bonus row so far is a photo bonus (§15.3). It moves to the photo's done id; one
    # whose photo or done row is gone gets NULL, so its old photo id can never be mistaken
    # for a done id (the star stays in the balance).
    op.execute(
        """
        UPDATE reward_ledger SET ref_id = (
            SELECT p.done_id FROM activity_photos p
            WHERE p.id = reward_ledger.ref_id AND p.profile_id = reward_ledger.profile_id
        )
        WHERE kind = 'bonus'
        """
    )


def downgrade() -> None:
    """The photo a bonus belonged to can't be told any more: bonus rows of a done task go to
    its first photo, if it has one. `reopen` rows stay (they keep the balance right)."""
    op.execute(
        """
        UPDATE reward_ledger SET ref_id = (
            SELECT MIN(p.id) FROM activity_photos p
            WHERE p.done_id = reward_ledger.ref_id AND p.profile_id = reward_ledger.profile_id
        )
        WHERE kind = 'bonus' AND EXISTS (
            SELECT 1 FROM activity_photos p
            WHERE p.done_id = reward_ledger.ref_id AND p.profile_id = reward_ledger.profile_id
        )
        """
    )
