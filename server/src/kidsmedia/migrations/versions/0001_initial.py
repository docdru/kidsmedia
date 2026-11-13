"""Initial schema (SPEC §6.1).

Revision ID: 0001
Revises:
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("locale", sa.String, nullable=False, server_default="en"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String, nullable=False, unique=True),
        sa.Column("password_hash", sa.String, nullable=False),
        sa.Column("locale", sa.String),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("admin_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String, nullable=False, unique=True),
        sa.Column("csrf_token", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "groups",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("color", sa.String, nullable=False),
        sa.Column("resume", sa.Boolean, nullable=False),
        sa.Column("cover_source", sa.String, nullable=False, server_default="auto"),
        sa.Column("cover_media_id", sa.Integer, sa.ForeignKey("media.id", ondelete="SET NULL")),
        sa.Column("cover_key", sa.String),
        sa.Column("cover_aspect", sa.Float),
        sa.Column("sort", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "media",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "group_id", sa.Integer, sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("path", sa.String, nullable=False),
        sa.Column("mime", sa.String, nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
        sa.Column("duration_s", sa.Float, nullable=False),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("faststart", sa.Boolean),
        sa.Column("has_embedded_art", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("sort", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("thumb_source", sa.String, nullable=False, server_default="pending"),
        sa.Column("thumb_key", sa.String),
        sa.Column("thumb_aspect", sa.Float),
        sa.Column("thumb_frame_ms", sa.Integer),
    )
    op.create_index("ix_media_group_sort", "media", ["group_id", "sort"])
    op.create_table(
        "rules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("profile_id", sa.Integer, sa.ForeignKey("profiles.id", ondelete="CASCADE")),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("days", sa.String, nullable=False),
        sa.Column("all_groups", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("mode", sa.String, nullable=False, server_default="limit"),
        sa.Column("max_items", sa.Integer),
        sa.Column("max_minutes", sa.Integer),
        sa.Column("lockouts", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("grace_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "rule_groups",
        sa.Column(
            "rule_id", sa.Integer, sa.ForeignKey("rules.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "group_id", sa.Integer, sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.PrimaryKeyConstraint("rule_id", "group_id"),
    )
    op.create_table(
        "graces",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "media_id", sa.Integer, sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("rule_id", sa.Integer, sa.ForeignKey("rules.id", ondelete="CASCADE")),
        sa.Column("day", sa.String, nullable=False),
        sa.Column("granted_at", sa.DateTime, nullable=False),
        sa.Column("start_position_ms", sa.Integer, nullable=False),
        sa.Column("until_position_ms", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("ended_at", sa.DateTime),
    )
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("token_hash", sa.String, unique=True),
        sa.Column("locale", sa.String),
        sa.Column("app_version", sa.String),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("paired_at", sa.DateTime),
        sa.Column("last_seen_at", sa.DateTime),
        sa.Column("revoked_at", sa.DateTime),
    )
    op.create_table(
        "plays",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("media_id", sa.Integer, sa.ForeignKey("media.id", ondelete="SET NULL")),
        sa.Column("group_id", sa.Integer, nullable=False),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("day", sa.String, nullable=False),
        sa.Column("seconds", sa.Float, nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("last_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("profile_id", "media_id", "day"),
    )
    op.create_index("ix_plays_day", "plays", ["profile_id", "day"])
    op.create_table(
        "play_sessions",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id", sa.Integer, sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("media_id", sa.Integer, sa.ForeignKey("media.id", ondelete="SET NULL")),
        sa.Column("day", sa.String, nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime, nullable=False),
        sa.Column("last_total_seconds", sa.Float, nullable=False, server_default="0"),
    )
    op.create_table(
        "positions",
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "media_id", sa.Integer, sa.ForeignKey("media.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("position_ms", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.PrimaryKeyConstraint("profile_id", "media_id"),
    )
    op.create_table(
        "pairing_codes",
        sa.Column("code", sa.String, primary_key=True),
        sa.Column(
            "device_id", sa.Integer, sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "adjustments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.String, nullable=False),
        sa.Column(
            "rule_id", sa.Integer, sa.ForeignKey("rules.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("extra_items", sa.Integer, nullable=False, server_default="0"),
        sa.Column("extra_minutes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    for table in (
        "adjustments",
        "pairing_codes",
        "positions",
        "play_sessions",
        "plays",
        "devices",
        "graces",
        "rule_groups",
        "rules",
        "media",
        "groups",
        "admin_sessions",
        "admin_users",
        "profiles",
    ):
        op.drop_table(table)
