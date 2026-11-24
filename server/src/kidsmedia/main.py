"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from kidsmedia import __version__, running_version, speech
from kidsmedia.bootstrap import bootstrap
from kidsmedia.clock import Clock
from kidsmedia.config import Settings, get_settings
from kidsmedia.db import make_engine, migrate
from kidsmedia.errors import install_error_handler
from kidsmedia.limits import BodyLimitMiddleware
from kidsmedia.oidc import OidcClient
from kidsmedia.routes import (
    activities,
    activities_admin,
    admin_auth,
    app_update,
    device_api,
    devices,
    gems_admin,
    groups,
    logs,
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
    return Health(status="ok", version=running_version())


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
        # Read aloud (SPEC §14.3): the example packs' recordings of imported worlds, then
        # tidy up and render what is missing, queued worlds first.
        try:
            examples = activities_admin.example_packs(settings)
            folders = {slug: ex.folder for slug, ex in examples.items()}
            with engine.connect() as conn:
                speech.install_all_recorded(conn, settings, folders)
        except Exception:  # read aloud is optional: never keep the app from starting
            speech.log.exception("speech: can't copy the example packs' recordings")
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
    app.state.oidc = OidcClient(settings)  # single sign-on (SPEC §7.1); unused while off
    app.state.pair_limiter = RateLimiter(limit=5, window=60)
    # Wrong pairing codes from all addresses together (SPEC §7): a 6-digit code must not
    # depend on per-address limits alone. 20 a minute is 300 guesses in a code's 15 minutes.
    app.state.pair_failure_limiter = RateLimiter(limit=20, window=60)
    install_error_handler(app)
    app.add_middleware(BodyLimitMiddleware)

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
        logs,
        speech_routes,
    ):
        app.include_router(module.router)

    # The admin SPA is mounted last so API routes always win.
    if settings.admin_dist is not None and settings.admin_dist.is_dir():
        app.mount("/", StaticFiles(directory=settings.admin_dist, html=True), name="admin")

    return app
