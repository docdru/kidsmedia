"""Alembic environment. Migrations run in-process at startup (kidsmedia.db.migrate)."""

from alembic import context
from sqlalchemy import Connection, create_engine

from kidsmedia.db import metadata

config = context.config


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    url = config.get_main_option("sqlalchemy.url") or "sqlite:////data/kidsmedia.db"
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    with create_engine(url).begin() as conn:
        _run(conn)


run()
