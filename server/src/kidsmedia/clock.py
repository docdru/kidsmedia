"""Time source. Everything time-dependent goes through a Clock so tests can freeze it."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


class Clock:
    def now(self) -> datetime:
        """Current time as an aware UTC datetime."""
        return datetime.now(UTC)

    def local(self, zone: ZoneInfo) -> datetime:
        return self.now().astimezone(zone)


class FixedClock(Clock):
    """A clock for tests: frozen until moved."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FixedClock needs an aware datetime")
        self._at = at

    def now(self) -> datetime:
        return self._at.astimezone(UTC)

    def set(self, at: datetime) -> None:
        self._at = at

    def advance(self, **delta: float) -> None:
        from datetime import timedelta

        self._at = self._at + timedelta(**delta)


def to_db(dt: datetime) -> datetime:
    """SQLite stores naive datetimes; we always store UTC."""
    return dt.astimezone(UTC).replace(tzinfo=None)


def from_db(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC)
