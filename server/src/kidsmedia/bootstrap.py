"""First-run setup: directories, the single v1 profile and the admin account."""

import logging

from sqlalchemy import Engine, func, insert, select

from kidsmedia.clock import Clock, to_db
from kidsmedia.config import Settings
from kidsmedia.db import admin_users, profiles
from kidsmedia.security import hash_password

log = logging.getLogger("kidsmedia")

PLACEHOLDER_PASSWORDS = {"", "change-me"}


def bootstrap(engine: Engine, settings: Settings, clock: Clock) -> None:
    for d in (settings.media_dir, settings.thumbs_dir, settings.data_dir / "apk"):
        d.mkdir(parents=True, exist_ok=True)
    now = to_db(clock.now())
    with engine.begin() as conn:
        if conn.execute(select(profiles.c.id).where(profiles.c.id == 1)).first() is None:
            conn.execute(
                insert(profiles).values(
                    id=1, name="Child", locale=settings.default_locale, created_at=now
                )
            )
        if conn.execute(select(func.count()).select_from(admin_users)).scalar_one() == 0:
            if settings.admin_password in PLACEHOLDER_PASSWORDS:
                log.warning(
                    "No admin account yet and ADMIN_PASSWORD is not set (or still 'change-me'). "
                    "Set it in /opt/kidsmedia/.env and restart to create the admin."
                )
                return
            conn.execute(
                insert(admin_users).values(
                    username=settings.admin_user,
                    password_hash=hash_password(settings.admin_password),
                    created_at=now,
                )
            )
            log.info("Created admin account %r", settings.admin_user)
