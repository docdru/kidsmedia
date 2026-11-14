"""Request limits (SPEC §7): body size, and whose X-Forwarded-For is believed."""

import asyncio
from collections.abc import Iterable, Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.types import Message, Scope
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from kidsmedia.__main__ import DEFAULT_FORWARDED_ALLOW_IPS
from kidsmedia.limits import MAX_BODY_BYTES, RAW_BODY_ROUTES, is_raw_body_route
from kidsmedia.security import RateLimiter

TOO_LARGE = b'{"code":"' + b"1" * MAX_BODY_BYTES + b'"}'


def test_large_json_body_is_refused_before_login(client: TestClient) -> None:
    for path in ("/api/pair", "/api/admin/login", "/api/play/heartbeat"):
        r = client.post(path, content=TOO_LARGE, headers={"content-type": "application/json"})
        assert r.status_code == 413, path
        assert r.json() == {"reason": "body_too_large", "params": {"max_mb": 2}}


def test_large_body_without_content_length_is_refused(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:  # sent chunked: no Content-Length to check up front
        yield b'{"code":"'
        for _ in range(MAX_BODY_BYTES // 65536 + 1):
            yield b"1" * 65536
        yield b'"}'

    r = client.post("/api/pair", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert r.json()["reason"] == "body_too_large"


def test_normal_bodies_pass(client: TestClient) -> None:
    r = client.post("/api/pair", json={"code": "000000"})
    assert r.json()["reason"] == "invalid_pairing_code"


def test_upload_routes_keep_their_own_limits(client: TestClient) -> None:
    """The raw-body routes aren't cut off at 2 MB: they check the login first (401 here)
    and count the bytes themselves."""
    body = b"x" * (MAX_BODY_BYTES + 1)
    assert client.put("/api/admin/groups/1/upload?filename=a.mp3", content=body).status_code == 401
    assert (
        client.post("/api/activities/photo?world_id=1&position=1&kind=draw", content=body)
    ).status_code == 401


def _api_routes(routes: Iterable[object]) -> Iterator[APIRoute]:
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        elif (router := getattr(route, "original_router", None)) is not None:
            yield from _api_routes(router.routes)  # newer FastAPI keeps included routers nested


def test_raw_body_route_list_matches_the_app(app: FastAPI) -> None:
    """Every route that reads the request itself (no body model) is on the list, and
    nothing else: a new upload route must be added to limits.py and the nginx snippet."""
    raw = set()
    for route in _api_routes(app.routes):
        if route.body_field is not None or route.dependant.request_param_name is None:
            continue
        for method in (route.methods or set()) & {"POST", "PUT", "PATCH"}:
            path = route.path
            for name in route.param_convertors:
                path = path.replace("{" + name + "}", "7")
            assert is_raw_body_route(method, path), f"{method} {route.path}"
            raw.add((method, route.path))
    assert len(raw) == len(RAW_BODY_ROUTES)


def _client_seen_by_app(forwarded_for: str, peer: str) -> str:
    seen: dict[str, str] = {}

    async def inner(scope: Scope, receive: object, send: object) -> None:
        seen["host"] = scope["client"][0]

    async def receive() -> Message:
        return {"type": "http.request"}

    async def send(message: Message) -> None:
        pass

    scope: Any = {
        "type": "http",
        "client": (peer, 40000),
        "headers": [(b"x-forwarded-for", forwarded_for.encode())],
    }
    app: Any = inner  # uvicorn's own ASGI types are stricter than starlette's
    wrapped: Any = ProxyHeadersMiddleware(app, trusted_hosts=DEFAULT_FORWARDED_ALLOW_IPS)
    asyncio.run(wrapped(scope, receive, send))
    return seen["host"]


def test_forged_forwarded_for_is_not_believed() -> None:
    """nginx on the host reaches the container from Docker's bridge. What the client put
    into X-Forwarded-For itself must never become the address the limits count by."""
    # The shipped snippet sends only the real address.
    assert _client_seen_by_app("203.0.113.7", "172.17.0.1") == "203.0.113.7"
    # An older snippet appended it to the client's own value: the last one counts.
    assert _client_seen_by_app("6.6.6.6, 203.0.113.7", "172.17.0.1") == "203.0.113.7"
    # Not from a proxy at all: the header is ignored.
    assert _client_seen_by_app("6.6.6.6", "198.51.100.9") == "198.51.100.9"


def test_rate_limiter_forgets_expired_keys() -> None:
    limiter = RateLimiter(limit=5, window=60)
    for i in range(RateLimiter.SWEEP_ABOVE + 50):
        limiter.hit(f"address-{i}", now=0.0)
    limiter.hit("later", now=61.0)
    assert set(limiter._events) == {"later"}


def test_rate_limiter_full_records_nothing() -> None:
    limiter = RateLimiter(limit=2, window=60)
    assert not limiter.full("k", now=0.0)
    limiter.hit("k", now=0.0)
    limiter.hit("k", now=1.0)
    assert limiter.full("k", now=2.0) and limiter.full("k", now=3.0)
    assert not limiter.full("k", now=61.5)
