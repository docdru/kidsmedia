"""Read aloud: the rendered speech files for the tablet and the admin, and the admin's status
of voices and files (SPEC §14.3).

Like media, the bytes are served via X-Accel-Redirect from `/_media/_speech/`, so nginx needs
no change; with ACCEL_REDIRECT=false the app serves them itself.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import Connection

from kidsmedia import speech
from kidsmedia.config import Settings
from kidsmedia.deps import Admin, Device, get_conn, get_settings, require_admin, require_device
from kidsmedia.errors import ApiError

router = APIRouter(prefix="/api")

CACHE = "private, max-age=31536000, immutable"  # content-addressed: a key never changes
AUDIO: dict[int | str, dict[str, Any]] = {200: {"content": {"audio/mp4": {}}}}


def get_worker(request: Request) -> speech.SpeechWorker:
    worker: speech.SpeechWorker = request.app.state.speech
    return worker


def serve_speech(settings: Settings, key: str) -> Response:
    """One speech file. The key format is checked strictly, which also rules out paths."""
    if not speech.KEY_RE.match(key):
        raise ApiError(404, "speech_not_found")
    path = speech.speech_file(settings, key)
    if not path.is_file():
        raise ApiError(404, "speech_not_found")
    if settings.accel_redirect:
        return Response(
            headers={"X-Accel-Redirect": f"/_media/_speech/{key}.m4a", "Cache-Control": CACHE},
            media_type="audio/mp4",
        )
    return FileResponse(path, media_type="audio/mp4", headers={"Cache-Control": CACHE})


@router.get("/speech/{key}.m4a", tags=["device: activities"], responses=AUDIO)
def device_speech(
    key: str,
    _: Device = Depends(require_device),
    settings: Settings = Depends(get_settings),
) -> Response:
    """A rendered text (the `audio` URLs in GET /api/activities)."""
    return serve_speech(settings, key)


@router.get("/admin/speech", response_model=speech.SpeechStatus, tags=["admin: activities"])
def speech_status(
    _: Admin = Depends(require_admin),
    conn: Connection = Depends(get_conn, scope="function"),
    settings: Settings = Depends(get_settings),
    worker: speech.SpeechWorker = Depends(get_worker),
) -> speech.SpeechStatus:
    """The voice per language with its licence and attribution, and how many of the spoken
    texts of all worlds are rendered, pending or failed (since the last start)."""
    return speech.status(conn, settings, worker)


@router.get("/admin/speech/{key}.m4a", tags=["admin: activities"], responses=AUDIO)
def admin_speech(
    key: str,
    _: Admin = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> Response:
    """A rendered text, for the editor's play buttons (the `audio` URLs in WorldFull)."""
    return serve_speech(settings, key)
