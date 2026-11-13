"""Database: SQLite through SQLAlchemy Core, schema per SPEC §6.1.

The tables here describe the *current* schema and are used by the code.
Changes to them always come with an Alembic migration in
``kidsmedia/migrations/versions``.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Connection,
    CursorResult,
    DateTime,
    Engine,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    event,
    text,
)

metadata = MetaData()

profiles = Table(
    "profiles",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, nullable=False),
    Column("locale", String, nullable=False, server_default="en"),
    Column("created_at", DateTime, nullable=False),
)

admin_users = Table(
    "admin_users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String, nullable=False, unique=True),
    Column("password_hash", String, nullable=False),
    Column("locale", String),  # decision 13: remembered admin language, NULL = follow browser
    Column("created_at", DateTime, nullable=False),
)

admin_sessions = Table(
    "admin_sessions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False),
    Column("token_hash", String, nullable=False, unique=True),
    Column("csrf_token", String, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("expires_at", DateTime, nullable=False),
)

groups = Table(
    "groups",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, nullable=False),
    Column("kind", String, nullable=False),  # music | audiobook | video
    Column("color", String, nullable=False),
    Column("resume", Boolean, nullable=False),
    Column("offline", Boolean, nullable=False, server_default="0"),  # M7: tablets download it
    Column("cover_source", String, nullable=False, server_default="auto"),  # auto | item | upload
    Column("cover_media_id", Integer, ForeignKey("media.id", ondelete="SET NULL")),
    Column("cover_key", String),  # only for uploaded covers; auto/item resolve at read time
    Column("cover_aspect", Float),
    Column("sort", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime, nullable=False),
)

media = Table(
    "media",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("group_id", Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
    Column("title", String, nullable=False),
    Column("path", String, nullable=False),  # relative to data/media
    Column("mime", String, nullable=False),
    Column("size", Integer, nullable=False),
    Column("duration_s", Float, nullable=False),
    Column("width", Integer),
    Column("height", Integer),
    Column("faststart", Boolean),  # mp4 only: moov atom before mdat
    Column("has_embedded_art", Boolean, nullable=False, server_default="0"),
    Column("sort", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime, nullable=False),
    # pending | embedded | frame | upload | none
    Column("thumb_source", String, nullable=False, server_default="pending"),
    Column("thumb_key", String),
    Column("thumb_aspect", Float),
    Column("thumb_frame_ms", Integer),
    Column("offline", Boolean, nullable=False, server_default="0"),  # M7: single items too
)

rules = Table(
    "rules",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE")),  # NULL = all
    Column("name", String, nullable=False),
    Column("enabled", Boolean, nullable=False, server_default="1"),
    Column("days", String, nullable=False),  # e.g. "0123456", Mon=0
    Column("all_groups", Boolean, nullable=False, server_default="0"),
    Column("mode", String, nullable=False, server_default="limit"),  # limit | block
    Column("max_items", Integer),
    Column("max_minutes", Integer),
    Column("lockouts", JSON, nullable=False, server_default="[]"),  # [["19:30","07:00"], ...]
    Column("grace_minutes", Integer, nullable=False, server_default="0"),
    # §3.3: a started file uses up an item only after this many seconds of playing.
    Column("count_after_seconds", Integer, nullable=False, server_default="10"),
    # §3.3: a counted file's listening budget per day, in % of its length. NULL = unlimited.
    Column("replay_percent", Integer, server_default="120"),
    # §16.2: gems a listen of a file in the scope costs (1-20). NULL = no gems needed.
    Column("gem_cost", Integer),
    # Parent's own words for the reason screen (M6). Sent as params; the app prefers them.
    Column("message_en", String),
    Column("message_de", String),
    Column("message_el", String),
    Column("created_at", DateTime, nullable=False),
)

rule_groups = Table(
    "rule_groups",
    metadata,
    Column("rule_id", Integer, ForeignKey("rules.id", ondelete="CASCADE"), nullable=False),
    Column("group_id", Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False),
    PrimaryKeyConstraint("rule_id", "group_id"),
)

graces = Table(
    "graces",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
    Column("media_id", Integer, ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
    Column("rule_id", Integer, ForeignKey("rules.id", ondelete="CASCADE")),
    # Every rule this grace covers (§3.3a); rule_id is the most restrictive of them.
    Column("rule_ids", JSON, nullable=False, server_default="[]"),
    Column("day", String, nullable=False),
    Column("granted_at", DateTime, nullable=False),
    Column("start_position_ms", Integer, nullable=False),
    Column("until_position_ms", Integer, nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("ended_at", DateTime),
)

plays = Table(
    "plays",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
    Column("media_id", Integer, ForeignKey("media.id", ondelete="SET NULL")),
    Column("group_id", Integer, nullable=False),  # snapshot, no FK: survives deletion
    Column("title", String, nullable=False),  # snapshot
    Column("day", String, nullable=False),  # YYYY-MM-DD in TZ
    Column("seconds", Float, nullable=False, server_default="0"),
    Column("started_at", DateTime, nullable=False),
    Column("last_at", DateTime, nullable=False),
    UniqueConstraint("profile_id", "media_id", "day"),
)

play_sessions = Table(
    "play_sessions",
    metadata,
    Column("id", String, primary_key=True),
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
    Column("device_id", Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
    Column("media_id", Integer, ForeignKey("media.id", ondelete="SET NULL")),
    Column("day", String, nullable=False),
    Column("started_at", DateTime, nullable=False),
    Column("last_heartbeat_at", DateTime, nullable=False),
    Column("last_total_seconds", Float, nullable=False, server_default="0"),
)

positions = Table(
    "positions",
    metadata,
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
    Column("media_id", Integer, ForeignKey("media.id", ondelete="CASCADE"), nullable=False),
    Column("position_ms", Integer, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    PrimaryKeyConstraint("profile_id", "media_id"),
)

devices = Table(
    "devices",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
    Column("name", String, nullable=False),
    Column("token_hash", String, unique=True),  # NULL until paired
    Column("locale", String),
    Column("app_version", String),
    Column("created_at", DateTime, nullable=False),
    Column("paired_at", DateTime),
    Column("last_seen_at", DateTime),
    Column("revoked_at", DateTime),
    # Volume cap for the tablet's media stream, in % of its maximum; NULL = no cap.
    Column("max_volume_percent", Integer),
)

pairing_codes = Table(
    "pairing_codes",
    metadata,
    Column("code", String, primary_key=True),
    Column("device_id", Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
    Column("expires_at", DateTime, nullable=False),
)

adjustments = Table(
    "adjustments",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
    Column("day", String, nullable=False),
    Column("rule_id", Integer, ForeignKey("rules.id", ondelete="CASCADE"), nullable=False),
    Column("extra_items", Integer, nullable=False, server_default="0"),
    Column("extra_minutes", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime, nullable=False),
)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    return engine


def migrate(engine: Engine) -> None:
    """Bring the database to the newest schema (alembic upgrade head)."""
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def insert_id(result: CursorResult[Any]) -> int:
    pk = result.inserted_primary_key
    if pk is None:  # pragma: no cover - only for INSERTs without a primary key
        raise RuntimeError("no primary key returned")
    return int(pk[0])


def connect(engine: Engine) -> Iterator[Connection]:
    """One transaction per request: committed on success, rolled back on error.

    Handlers may call ``conn.commit()`` early (e.g. before scheduling a background job
    that reads the new rows); SQLAlchemy then starts a new transaction automatically.
    """
    with engine.connect() as conn:
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        conn.commit()


def begin_immediate(conn: Connection) -> None:
    """Take SQLite's write lock now, for a read-check-write step that must be atomic (e.g. a
    balance check before spending stars): no other request can write between the check and
    the write; a second one waits (busy_timeout) and then sees the first one's rows.

    pysqlite opens a transaction implicitly only before an INSERT/UPDATE/DELETE, so if the
    request's connection is already in one (e.g. `require_device` updated `last_seen_at`),
    it already holds the write lock and nothing is needed. Otherwise this emits
    `BEGIN IMMEDIATE`; the request's commit/rollback (``connect``) ends it as usual.
    """
    dbapi = cast(sqlite3.Connection, conn.connection.dbapi_connection)
    if not dbapi.in_transaction:
        conn.exec_driver_sql("BEGIN IMMEDIATE")


app_releases = Table(
    "app_releases",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("version_name", String, nullable=False),
    Column("version_code", Integer, nullable=False, unique=True),
    Column("file", String, nullable=False),  # name under data/apk/, never the upload's name
    Column("size", Integer, nullable=False),
    Column("sha256", String, nullable=False),
    Column("uploaded_at", DateTime, nullable=False),
)

offline_events = Table(
    "offline_events",
    metadata,
    Column("event_id", String, primary_key=True),  # client-generated UUID (M7)
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
    Column("received_at", DateTime, nullable=False),
)

# ---- activities (SPEC §13.6) --------------------------------------------------------------

worlds = Table(
    "worlds",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("slug", String, nullable=False, unique=True),
    Column("age_band", String, nullable=False),
    Column("color", String, nullable=False),
    Column("cover_key", String),
    Column("cover_aspect", Float),
    Column("content", JSON, nullable=False),  # the pack's texts + chapters, validated
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

activity_settings = Table(
    "activity_settings",
    metadata,
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
    Column("days", String, nullable=False, server_default="012345"),  # weekdays, Mon=0
    Column("unlock_time", String, nullable=False, server_default="07:00"),
    Column("queue", JSON, nullable=False),  # world ids in order
    Column("queue_start", String),  # ISO date counting starts from; NULL = empty queue
    Column("level", String, nullable=False, server_default="normal"),  # easier|normal|harder
    Column("windows", JSON, nullable=False, server_default="[]"),  # opening times (§14.4a)
    Column("langs", JSON, nullable=False, server_default="{}"),  # story language (§14.7)
)

activity_days_off = Table(
    "activity_days_off",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
    Column("from_day", String, nullable=False),
    Column("to_day", String, nullable=False),
    Column("note", String, nullable=False, server_default=""),
    Column("created_at", DateTime, nullable=False),
)

activity_done = Table(
    "activity_done",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
    Column("world_id", Integer, ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False),
    Column("position", Integer, nullable=False),  # chapter, 1-based
    Column("kind", String, nullable=False),
    Column("day", String, nullable=False),
    Column("done_at", DateTime, nullable=False),
    Column("device_id", Integer, ForeignKey("devices.id", ondelete="SET NULL")),
    # read: rounds through the questions (§14.2); calc: 1 + the wrong answers (§15.2)
    Column("attempts", Integer),
    UniqueConstraint("profile_id", "world_id", "position", "kind"),
)

activity_photos = Table(
    "activity_photos",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
    Column("done_id", Integer, ForeignKey("activity_done.id", ondelete="SET NULL")),
    # Snapshots (§15.3), no FK: a photo outlives its world. Names in the story language.
    Column("world_id", Integer),
    Column("world_name", String, nullable=False),
    Column("position", Integer, nullable=False),
    Column("kind", String, nullable=False),  # draw | write
    Column("task_title", String, nullable=False),
    Column("key", String, nullable=False),  # p<id>-<sha256[:8]>; files <key>-sm|lg.webp
    Column("width", Integer, nullable=False),  # of the stored lg picture
    Column("height", Integer, nullable=False),
    Column("taken_at", DateTime, nullable=False),  # done_at of the upload, else the upload time
    Column("created_at", DateTime, nullable=False),
)

# ---- rewards (SPEC §14.4, §14.5) ----------------------------------------------------------

reward_swaps = Table(
    "reward_swaps",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
    Column("stars", Integer, nullable=False),
    Column("rule_id", Integer, ForeignKey("rules.id", ondelete="CASCADE"), nullable=False),
    Column("extra_items", Integer, nullable=False, server_default="0"),
    Column("extra_minutes", Integer, nullable=False, server_default="0"),
    Column("max_per_day", Integer),  # NULL = no daily limit
    Column("enabled", Boolean, nullable=False, server_default="1"),
    Column("created_at", DateTime, nullable=False),
)

reward_wishes = Table(
    "reward_wishes",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
    Column("texts", JSON, nullable=False),  # {"de": {"name": "…"}, …}
    Column("stars", Integer, nullable=False),
    Column("enabled", Boolean, nullable=False, server_default="1"),
    Column("created_at", DateTime, nullable=False),
)

wish_requests = Table(
    "wish_requests",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
    Column("wish_id", Integer, ForeignKey("reward_wishes.id", ondelete="CASCADE"), nullable=False),
    Column("status", String, nullable=False),  # pending | approved | declined
    Column("created_at", DateTime, nullable=False),
    Column("decided_at", DateTime),
    # At most one pending request per wish (§14.4), also under concurrent requests.
    Index(
        "uq_wish_requests_pending",
        "profile_id",
        "wish_id",
        unique=True,
        sqlite_where=text("status = 'pending'"),
    ),
)

reward_ledger = Table(
    "reward_ledger",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
    Column("delta", Integer, nullable=False),  # stars; earning is positive, spending negative
    Column("kind", String, nullable=False),  # earn | bonus | reopen | swap | wish
    # activity_done.id (earn, bonus and reopen, §17.3; an old bonus of a photo without a done
    # row: activity_photos.id), reward_swaps.id or wish_requests.id; no FK: history stays
    Column("ref_id", Integer),
    Column("created_at", DateTime, nullable=False),
)

# ---- gems (SPEC §16.1) --------------------------------------------------------------------

gem_ledger = Table(
    "gem_ledger",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("profile_id", Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
    Column("delta", Integer, nullable=False),  # gems; adding is positive, spending negative
    Column("kind", String, nullable=False),  # admin | spend (later: the app earns them)
    Column("rule_id", Integer, ForeignKey("rules.id", ondelete="SET NULL")),  # spend only
    Column("media_id", Integer, ForeignKey("media.id", ondelete="SET NULL")),  # spend only
    Column("day", String),  # spend only: the day of the listen (the session's day, §3.3)
    Column("title", String),  # spend only: snapshot of the file's title
    Column("note", String),  # admin only, e.g. "Room tidied"
    Column("created_at", DateTime, nullable=False),
    # A listen is paid once per rule, file and day (§16.2), also under concurrent requests.
    Index(
        "uq_gem_ledger_spend",
        "profile_id",
        "rule_id",
        "media_id",
        "day",
        unique=True,
        sqlite_where=text("kind = 'spend'"),
    ),
)
