"""Request body size limit (SPEC §7).

FastAPI reads a JSON body completely, before validation and before the auth check, so
without a limit one large request from anyone makes the server use that much memory.
This middleware refuses bodies over MAX_BODY_BYTES on every route except the raw-body
upload routes, which check the login first and count the bytes themselves while they
stream. nginx applies the same limits (deploy/nginx/kidsmedia.conf); this one doesn't
depend on its configuration.
"""

import json
import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# The largest JSON bodies are a story world from the admin's editor (a few hundred kB)
# and a batch of offline plays.
MAX_BODY_BYTES = 2 * 1024 * 1024

# Routes that take a raw body and enforce their own limit. Keep in step with the routes
# (tests/test_limits.py compares this list with the app) and with the nginx snippet.
RAW_BODY_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PUT", re.compile(r"/api/admin/groups/\d+/upload")),
    ("PUT", re.compile(r"/api/admin/groups/\d+/cover")),
    ("PUT", re.compile(r"/api/admin/media/\d+/thumb")),
    ("PUT", re.compile(r"/api/admin/app/apk")),
    ("PUT", re.compile(r"/api/admin/worlds/import")),
    ("PUT", re.compile(r"/api/admin/worlds/\d+/cover")),
    ("PUT", re.compile(r"/api/admin/worlds/\d+/chapters/\d+/image")),
    ("POST", re.compile(r"/api/activities/photo")),
)


def is_raw_body_route(method: str, path: str) -> bool:
    return any(m == method and pattern.fullmatch(path) for m, pattern in RAW_BODY_ROUTES)


class _BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_raw_body_route(scope["method"], scope["path"]):
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.limit:
            await self._refuse(send)  # before a single body byte is read
            return

        received = 0
        exceeded = False
        started = False

        # A body without Content-Length (chunked), or one that is longer than it says.
        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    exceeded = True
                    raise _BodyTooLarge
            return message

        # FastAPI answers an error while it reads the body with its own 400, so the 413
        # is put in that answer's place here.
        async def guarded_send(message: Message) -> None:
            nonlocal started
            if exceeded and not started:
                if message["type"] == "http.response.start":
                    started = True
                    await self._refuse(send)
                return
            if exceeded:
                return  # the rest of the replaced answer
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            if started:
                return
            await self._refuse(send)

    async def _refuse(self, send: Send) -> None:
        body = json.dumps(
            {"reason": "body_too_large", "params": {"max_mb": self.limit // (1024 * 1024)}}
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
