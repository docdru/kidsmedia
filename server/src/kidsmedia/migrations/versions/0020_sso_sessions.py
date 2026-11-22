"""Single sign-on sessions (SPEC §7.1).

Adds `admin_sessions.method` ("password" for every existing session) and
`admin_sessions.sso_groups`. With OIDC_ISSUER set, password sessions stop counting.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "admin_sessions",
        sa.Column("method", sa.String, nullable=False, server_default="password"),
    )
    op.add_column("admin_sessions", sa.Column("sso_groups", sa.JSON))


def downgrade() -> None:
    with op.batch_alter_table("admin_sessions") as batch:
        batch.drop_column("sso_groups")
        batch.drop_column("method")
