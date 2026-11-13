"""Passwords, tokens and rate limiting (SPEC §7)."""

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2id with the library's current defaults


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    """256-bit random token for device and admin sessions."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Tokens are high-entropy, so a plain SHA-256 is enough (no salt/KDF needed)."""
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def new_pairing_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


class RateLimiter:
    """At most `limit` events per `window` seconds per key, in memory.

    One process serves the app, so in-memory is sufficient; a restart resets it.
    """

    def __init__(self, limit: int, window: float) -> None:
        self.limit = limit
        self.window = window
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> bool:
        """Record an attempt. Returns False when the key is over the limit."""
        t = time.monotonic() if now is None else now
        with self._lock:
            q = self._events.setdefault(key, deque())
            while q and q[0] <= t - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(t)
            return True
