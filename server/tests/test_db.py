import re
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import OperationalError

import kidsmedia
from kidsmedia.db import begin_immediate, make_engine, metadata, migrate


def test_migrations_create_the_spec_schema(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "km.db")
    migrate(engine)
    migrate(engine)  # idempotent
    tables = set(inspect(engine).get_table_names())
    assert set(metadata.tables) <= tables
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        assert conn.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"


def test_migration_matches_table_definitions(tmp_path: Path) -> None:
    """db.py and the migrations must describe the same columns."""
    engine = make_engine(tmp_path / "km.db")
    migrate(engine)
    insp = inspect(engine)
    for name, table in metadata.tables.items():
        migrated = {c["name"] for c in insp.get_columns(name)}
        assert migrated == {c.name for c in table.columns}, name


def test_upgrade_from_0001_keeps_rules_and_history(tmp_path: Path) -> None:
    """M6 adds columns to "rules"; rule_groups and plays must survive (AGENTS.md §7)."""
    from alembic import command
    from alembic.config import Config as AlembicConfig

    from kidsmedia.db import MIGRATIONS_DIR

    engine = make_engine(tmp_path / "km.db")
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0001")
        conn.execute(
            text("INSERT INTO profiles (id, name, created_at) VALUES (1, 'kid', '2026-11-01')")
        )
        conn.execute(
            text(
                "INSERT INTO groups (id, name, kind, color, resume, sort, created_at) "
                "VALUES (1, 'Moon', 'audiobook', '#000', 1, 0, '2026-11-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO rules (id, name, days, created_at) VALUES (1, 'r', '56', '2026-11-01')"
            )
        )
        conn.execute(text("INSERT INTO rule_groups (rule_id, group_id) VALUES (1, 1)"))
        conn.execute(
            text(
                "INSERT INTO plays (profile_id, group_id, title, day, seconds, started_at, last_at)"
                " "
                "VALUES (1, 1, 'ep', '2026-11-01', 60, '2026-11-01', '2026-11-01')"
            )
        )
    migrate(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM rule_groups")).scalar_one() == 1
        assert conn.execute(text("SELECT count(*) FROM plays")).scalar_one() == 1
        assert conn.execute(text("SELECT message_en FROM rules")).scalar_one() is None
        assert conn.execute(text("SELECT message_el FROM rules")).scalar_one() is None


def test_every_request_commits_before_its_response_is_sent() -> None:
    """get_conn must be function-scoped everywhere: with FastAPI's default scope the commit
    runs after the response is sent, and a fast client's next request misses the write
    (found by the e2e test: a just-paired tablet got 401 device_unknown)."""
    src = Path(kidsmedia.__file__).parent
    code = "".join(p.read_text() for p in src.rglob("*.py"))
    uses = re.findall(r"Depends\(get_conn[^)]*\)", code)
    assert len(uses) > 40
    assert sorted(set(uses)) == ['Depends(get_conn, scope="function")']


def _upgrade_to(engine: Engine, revision: str) -> None:
    from alembic import command
    from alembic.config import Config as AlembicConfig

    from kidsmedia.db import MIGRATIONS_DIR

    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)


def test_upgrade_to_0013_moves_earned_stars_into_the_ledger(tmp_path: Path) -> None:
    """Earned stars become ledger rows; a ledger already below zero (spent stars of a
    deleted world) is brought back to zero; duplicate pending requests are declined."""
    engine = make_engine(tmp_path / "km.db")
    _upgrade_to(engine, "0012")
    at = {"at": "2026-11-07 12:00:00.000000"}
    with engine.begin() as conn:
        for sql in (
            "INSERT INTO profiles (id, name, created_at) VALUES (1, 'a', :at), (2, 'b', :at)",
            "INSERT INTO worlds (id, slug, age_band, color, content, created_at, updated_at) "
            "VALUES (1, 'w', '6-7', '#000000', '{}', :at, :at)",
            "INSERT INTO activity_done (profile_id, world_id, position, kind, day, done_at,"
            " attempts) VALUES (1, 1, 1, 'draw', '2026-11-07', :at, NULL),"
            " (1, 1, 1, 'read', '2026-11-07', :at, 1), (1, 1, 2, 'read', '2026-11-08', :at, 2)",
            "INSERT INTO reward_ledger (profile_id, delta, kind, ref_id, created_at) "
            "VALUES (1, -3, 'swap', 1, :at), (2, -5, 'swap', 1, :at)",
            "INSERT INTO reward_wishes (id, profile_id, texts, stars, created_at) "
            "VALUES (1, 1, '{}', 3, :at)",
            "INSERT INTO wish_requests (id, profile_id, wish_id, status, created_at) "
            "VALUES (1, 1, 1, 'pending', :at), (2, 1, 1, 'pending', :at)",
        ):
            conn.execute(text(sql), at)
    migrate(engine)
    with engine.connect() as conn:
        sums = conn.execute(
            text("SELECT profile_id, SUM(delta) FROM reward_ledger GROUP BY profile_id")
        ).all()
        earn = conn.execute(
            text("SELECT delta, ref_id FROM reward_ledger WHERE kind = 'earn' ORDER BY id")
        ).all()
        statuses = conn.execute(text("SELECT status FROM wish_requests ORDER BY id")).scalars()
        assert {r[0]: r[1] for r in sums} == {1: 1, 2: 0}  # 1+2+1-3; profile 2 back to zero
        assert [tuple(r) for r in earn] == [(1, 1), (2, 2), (1, 3), (5, None)]
        assert list(statuses) == ["pending", "declined"]


def test_upgrade_to_0017_moves_photo_bonus_stars_to_their_done_task(tmp_path: Path) -> None:
    """§17.3: a photo's bonus row refers to the photo's done row; one whose photo has no done
    row (its world was deleted) gets NULL, so the old photo id isn't read as a done id. Other
    rows are untouched."""
    engine = make_engine(tmp_path / "km.db")
    _upgrade_to(engine, "0016")
    at = {"at": "2026-11-07 12:00:00.000000"}
    photo = (
        "INSERT INTO activity_photos (id, profile_id, done_id, world_id, world_name, position,"
        " kind, task_title, key, width, height, taken_at, created_at) VALUES"
    )
    with engine.begin() as conn:
        for sql in (
            "INSERT INTO profiles (id, name, created_at) VALUES (1, 'a', :at)",
            "INSERT INTO worlds (id, slug, age_band, color, content, created_at, updated_at) "
            "VALUES (1, 'w', '6-7', '#000000', '{}', :at, :at)",
            "INSERT INTO activity_done (id, profile_id, world_id, position, kind, day, done_at)"
            " VALUES (3, 1, 1, 1, 'draw', '2026-11-07', :at)",
            f"{photo} (7, 1, 3, 1, 'W', 1, 'draw', 'T', 'p7-00000000', 1, 1, :at, :at),"
            " (8, 1, NULL, 2, 'W', 1, 'draw', 'T', 'p8-00000000', 1, 1, :at, :at)",
            "INSERT INTO reward_ledger (profile_id, delta, kind, ref_id, created_at) VALUES"
            " (1, 1, 'earn', 3, :at), (1, 1, 'bonus', 7, :at), (1, 1, 'bonus', 8, :at),"
            " (1, -1, 'swap', 7, :at)",
        ):
            conn.execute(text(sql), at)
    migrate(engine)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT kind, ref_id FROM reward_ledger ORDER BY id")).all()
    assert [tuple(r) for r in rows] == [("earn", 3), ("bonus", 3), ("bonus", None), ("swap", 7)]


def test_begin_immediate_takes_the_write_lock(tmp_path: Path) -> None:
    """The read-check-write steps of rewards (§14.4) can't interleave: while one request
    holds the lock, a second can't begin its own."""
    engine = make_engine(tmp_path / "km.db")
    migrate(engine)
    with engine.connect() as first, engine.connect() as second:
        second.exec_driver_sql("PRAGMA busy_timeout=0")
        begin_immediate(first)
        first.execute(text("SELECT count(*) FROM reward_ledger")).scalar_one()
        with pytest.raises(OperationalError, match="locked"):
            begin_immediate(second)
        second.rollback()
        first.commit()
        begin_immediate(second)  # free again
        second.rollback()


def test_begin_immediate_inside_a_writing_transaction(tmp_path: Path) -> None:
    """After a write (e.g. require_device's last_seen_at) the lock is already held."""
    engine = make_engine(tmp_path / "km.db")
    migrate(engine)
    with engine.connect() as conn:
        conn.execute(
            text("INSERT INTO profiles (name, created_at) VALUES ('a', '2026-11-07 12:00:00')")
        )
        begin_immediate(conn)  # no "cannot start a transaction within a transaction"
        conn.rollback()
        assert conn.execute(text("SELECT count(*) FROM profiles")).scalar_one() == 0
