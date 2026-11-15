"""Chapters open by doing ideas, not by date (SPEC §13.2, v0.22).

Adds `activity_settings.chapter_needed` (default 2), `max_new_per_day` (NULL = no limit) and
`progress`, and drops the date schedule: `days`, `unlock_time`, `queue_start` and the
`activity_days_off` table.

Nothing the child has is taken away: the date schedule is worked out once more as of today,
the worlds before the current one leave the queue, and every chapter of the current world
that was open by date stays open, with its date as its opening day.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-15
"""

import json
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HORIZON_DAYS = 400  # as the old schedule


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _old_progress(
    conn: sa.Connection, profile_id: int, days: str, queue: list[int], start: str | None
) -> tuple[list[int], dict[str, Any]]:
    """The queue after the old housekeeping and the open chapters of the current world, by
    the old date schedule (§13.2 before v0.22) as of today. Unlock times are ignored: a
    chapter of today counts as open."""
    if not queue or not start:
        return queue, {}
    weekdays = {int(c) for c in days if c.isdigit()}
    offs = [
        (date.fromisoformat(r.from_day), date.fromisoformat(r.to_day))
        for r in conn.execute(
            sa.text("SELECT from_day, to_day FROM activity_days_off WHERE profile_id = :p"),
            {"p": profile_id},
        )
    ]
    counts = {}
    for r in conn.execute(sa.text("SELECT id, content FROM worlds")):
        counts[r.id] = len((_json(r.content) or {}).get("chapters") or [])
    queue = [i for i in queue if i in counts]
    total = sum(counts[i] for i in queue)
    dates: list[date] = []
    day, end = date.fromisoformat(start), date.fromisoformat(start) + timedelta(HORIZON_DAYS)
    while weekdays and len(dates) < total and day < end:
        if day.weekday() in weekdays and not any(a <= day <= b for a, b in offs):
            dates.append(day)
        day += timedelta(days=1)
    today = date.today()
    spans, i = [], 0
    for world_id in queue:
        spans.append((world_id, dates[i : i + counts[world_id]]))
        i += counts[world_id]
    current = None
    for index, (_, span) in enumerate(spans):
        if span and span[0] <= today:
            current = index
    if current is None:
        return queue, {}  # the first world hadn't started: it starts fresh
    world_id, span = spans[current]
    opened = [d.isoformat() for d in span if d <= today]
    return queue[current:], {"world": world_id, "opened": opened, "opens": {}}


def upgrade() -> None:
    op.add_column(
        "activity_settings",
        sa.Column("chapter_needed", sa.Integer, nullable=False, server_default="2"),
    )
    op.add_column("activity_settings", sa.Column("max_new_per_day", sa.Integer))
    op.add_column(
        "activity_settings",
        sa.Column("progress", sa.JSON, nullable=False, server_default="{}"),
    )
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT profile_id, days, queue, queue_start FROM activity_settings")
    ).all()
    for r in rows:
        queue, progress = _old_progress(
            conn, r.profile_id, r.days, [int(i) for i in _json(r.queue) or []], r.queue_start
        )
        conn.execute(
            sa.text(
                "UPDATE activity_settings SET queue = :q, progress = :p WHERE profile_id = :id"
            ),
            {"q": json.dumps(queue), "p": json.dumps(progress), "id": r.profile_id},
        )
    with op.batch_alter_table("activity_settings") as batch:
        batch.drop_column("days")
        batch.drop_column("unlock_time")
        batch.drop_column("queue_start")
    op.drop_table("activity_days_off")


def downgrade() -> None:
    """Back to dates: the current world starts again today, Mon-Sat (days off are lost)."""
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
    with op.batch_alter_table("activity_settings") as batch:
        batch.add_column(sa.Column("days", sa.String, nullable=False, server_default="012345"))
        batch.add_column(
            sa.Column("unlock_time", sa.String, nullable=False, server_default="07:00")
        )
        batch.add_column(sa.Column("queue_start", sa.String))
        batch.drop_column("progress")
        batch.drop_column("max_new_per_day")
        batch.drop_column("chapter_needed")
    op.execute(
        "UPDATE activity_settings SET queue_start = date('now', 'localtime') "
        "WHERE queue IS NOT NULL AND queue != '[]'"
    )
