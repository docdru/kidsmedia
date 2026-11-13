"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from kidsmedia import __version__, speech
from kidsmedia.bootstrap import bootstrap
from kidsmedia.clock import Clock
from kidsmedia.config import Settings, get_settings
from kidsmedia.db import make_engine, migrate
from kidsmedia.errors import install_error_handler
from kidsmedia.routes import (
    activities,
    activities_admin,
    admin_auth,
    app_update,
    device_api,
    devices,
    gems_admin,
    groups,
    media,
    offline,
    play,
    rewards,
    rewards_admin,
    rules_admin,
)
from kidsmedia.routes import speech as speech_routes
from kidsmedia.security import RateLimiter


class Health(BaseModel):
    status: str
    version: str


router = APIRouter()


@router.get("/healthz", response_model=Health, tags=["meta"])
def healthz() -> Health:
    """Liveness probe used by Docker HEALTHCHECK and monitoring."""
    return Health(status="ok", version=__version__)


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    settings = settings or get_settings()
    clock = clock or Clock()

    engine = make_engine(settings.db_path)
    migrate(engine)
    bootstrap(engine, settings, clock)
    app_update.import_bundled_apk(engine, settings, clock)

    worker = speech.SpeechWorker(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Read aloud (SPEC §14.3): tidy up, then render what is missing, queued worlds first.
        if settings.speech_worker:
            speech.log_voices(settings)
            try:
                with engine.connect() as conn:
                    worker.prune(conn)
                    worker.enqueue_all(conn)
            except Exception:  # read aloud is optional: never keep the app from starting
                speech.log.exception("speech: can't queue the spoken texts")
            worker.start()
        try:
            yield
        finally:
            worker.stop()

    app = FastAPI(title="Kids Media", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.speech = worker
    app.state.clock = clock
    app.state.engine = engine
    app.state.login_limiter = RateLimiter(limit=5, window=60)
    app.state.pair_limiter = RateLimiter(limit=5, window=60)
    install_error_handler(app)

    app.include_router(router)
    for module in (
        admin_auth,
        groups,
        media,
        rules_admin,
        devices,
        device_api,
        play,
        app_update,
        offline,
        activities,
        activities_admin,
        rewards,
        rewards_admin,
        gems_admin,
        speech_routes,
    ):
        app.include_router(module.router)

    # The admin SPA is mounted last so API routes always win.
    if settings.admin_dist is not None and settings.admin_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.admin_dist, html=True), name="admin")

    return app
