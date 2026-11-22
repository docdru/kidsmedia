"""Single sign-on with OpenID Connect, e.g. Authelia (SPEC §7.1)."""

import base64
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import insert, select

from kidsmedia.clock import FixedClock
from kidsmedia.config import Settings
from kidsmedia.db import admin_sessions, admin_users, make_engine
from kidsmedia.main import create_app
from kidsmedia.oidc import OidcClient
from kidsmedia.security import hash_token
from tests.conftest import ADMIN_PASSWORD

ISSUER = "https://auth.example.com"
SECRET = "s3cr/et+value"  # characters that must be form-encoded in Basic auth


class FakeAuthelia:
    """Discovery, token and userinfo endpoints, as an httpx transport."""

    def __init__(self) -> None:
        self.username = "papa"
        self.groups = ["family"]
        self.code = "the-code"
        self.token_requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/api/oidc/authorization",
                    "token_endpoint": f"{ISSUER}/api/oidc/token",
                    "userinfo_endpoint": f"{ISSUER}/api/oidc/userinfo",
                },
            )
        if path == "/api/oidc/token":
            self.token_requests.append(request)
            form = parse_qs(request.content.decode())
            if form.get("code") != [self.code] or not form.get("code_verifier"):
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "at-1", "token_type": "Bearer"})
        if path == "/api/oidc/userinfo":
            if request.headers.get("Authorization") != "Bearer at-1":
                return httpx.Response(401)
            return httpx.Response(
                200, json={"sub": "x", "preferred_username": self.username, "groups": self.groups}
            )
        return httpx.Response(404)


@pytest.fixture
def idp() -> FakeAuthelia:
    return FakeAuthelia()


@pytest.fixture
def sso_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        tz="Europe/Berlin",
        accel_redirect=False,
        public_url="https://media.example.com",
        admin_password=ADMIN_PASSWORD,
        speech_worker=False,
        oidc_issuer=ISSUER + "/",
        oidc_client_secret=SECRET,
        oidc_allowed_users="mama, papa",
        oidc_allowed_groups="",
    )


def _app(settings: Settings, clock: FixedClock, idp: FakeAuthelia) -> FastAPI:
    app = create_app(settings, clock)
    app.state.oidc = OidcClient(settings, transport=httpx.MockTransport(idp))
    return app


@pytest.fixture
def sso(sso_settings: Settings, clock: FixedClock, idp: FakeAuthelia) -> Iterator[TestClient]:
    with TestClient(_app(sso_settings, clock, idp), base_url="https://media.example.com") as c:
        yield c


def _sign_in(c: TestClient, code: str = "the-code") -> httpx.Response:
    r = c.get("/api/admin/oidc/login", follow_redirects=False)
    assert r.status_code == 302
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    callback: httpx.Response = c.get(
        "/api/admin/oidc/callback", params={"code": code, "state": state}, follow_redirects=False
    )
    return callback


def test_auth_config_without_sso(client: TestClient) -> None:
    assert client.get("/api/admin/auth").json() == {"password": True, "sso_label": None}


def test_auth_config_with_sso(sso: TestClient) -> None:
    assert sso.get("/api/admin/auth").json() == {"password": False, "sso_label": "Authelia"}


def test_login_redirects_to_the_provider_with_pkce(sso: TestClient) -> None:
    r = sso.get("/api/admin/oidc/login", follow_redirects=False)
    assert r.status_code == 302
    url = urlsplit(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{ISSUER}/api/oidc/authorization"
    q = parse_qs(url.query)
    assert q["client_id"] == ["kidsmedia"]
    assert q["redirect_uri"] == ["https://media.example.com/api/admin/oidc/callback"]
    assert q["code_challenge_method"] == ["S256"]
    assert set(q["scope"][0].split()) == {"openid", "profile", "groups"}
    cookie = r.headers["set-cookie"].lower()
    assert "km_oidc=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "secure" in cookie and "path=/api/admin/oidc" in cookie


def test_allowed_user_becomes_admin(sso: TestClient, idp: FakeAuthelia) -> None:
    r = _sign_in(sso)
    assert r.status_code == 200 and 'url=/"' in r.text
    assert "samesite=strict" in r.headers["set-cookie"].lower()
    me = sso.get("/api/admin/me").json()
    assert me["username"] == "papa"
    # The admin works as usual, CSRF included.
    sso.headers["X-CSRF-Token"] = me["csrf_token"]
    assert sso.post("/api/admin/devices", json={"name": "Tablet"}).status_code == 201
    # Client credentials go form-encoded in Basic auth, the PKCE verifier in the body.
    auth = idp.token_requests[0].headers["Authorization"]
    assert base64.b64decode(auth.removeprefix("Basic ")).decode() == "kidsmedia:s3cr%2Fet%2Bvalue"


def test_allowed_group_becomes_admin(
    sso_settings: Settings, clock: FixedClock, idp: FakeAuthelia
) -> None:
    settings = sso_settings.model_copy(
        update={"oidc_allowed_users": "", "oidc_allowed_groups": "kidsmedia-admins"}
    )
    idp.username, idp.groups = "oma", ["kidsmedia-admins", "family"]
    with TestClient(_app(settings, clock, idp), base_url="https://media.example.com") as c:
        assert _sign_in(c).status_code == 200
        assert c.get("/api/admin/me").json()["username"] == "oma"


def test_other_accounts_are_turned_away(sso: TestClient, idp: FakeAuthelia) -> None:
    idp.username = "neighbour"
    r = _sign_in(sso)
    assert r.status_code == 303 and r.headers["location"] == "/?sso_error=not_allowed"
    assert sso.get("/api/admin/me").status_code == 401


def test_wrong_state_is_refused(sso: TestClient) -> None:
    sso.get("/api/admin/oidc/login", follow_redirects=False)
    r = sso.get(
        "/api/admin/oidc/callback",
        params={"code": "the-code", "state": "forged"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/?sso_error=failed"
    assert sso.get("/api/admin/me").status_code == 401


def test_callback_without_the_start_cookie_is_refused(sso: TestClient) -> None:
    r = sso.get(
        "/api/admin/oidc/callback",
        params={"code": "the-code", "state": "x"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/?sso_error=failed"


def test_bad_code_fails(sso: TestClient) -> None:
    assert _sign_in(sso, code="stolen").headers["location"] == "/?sso_error=failed"


def test_provider_error_fails(sso: TestClient) -> None:
    sso.get("/api/admin/oidc/login", follow_redirects=False)
    r = sso.get(
        "/api/admin/oidc/callback", params={"error": "access_denied"}, follow_redirects=False
    )
    assert r.headers["location"] == "/?sso_error=failed"


def test_password_login_is_off_with_sso(sso: TestClient) -> None:
    r = sso.post("/api/admin/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert r.status_code == 403 and r.json()["reason"] == "password_login_disabled"


def test_sso_routes_are_off_without_sso(client: TestClient) -> None:
    assert client.get("/api/admin/oidc/login", follow_redirects=False).status_code == 404
    assert client.get("/api/admin/oidc/callback", follow_redirects=False).status_code == 404


def test_password_sessions_stop_counting_when_sso_is_turned_on(
    settings: Settings, sso_settings: Settings, clock: FixedClock, idp: FakeAuthelia
) -> None:
    sso_settings = sso_settings.model_copy(update={"data_dir": settings.data_dir})
    with TestClient(create_app(settings, clock)) as c:
        r = c.post("/api/admin/login", json={"username": "admin", "password": ADMIN_PASSWORD})
        cookie = r.cookies["km_session"]
    with TestClient(_app(sso_settings, clock, idp), base_url="https://media.example.com") as c:
        c.cookies.set("km_session", cookie)
        assert c.get("/api/admin/me").status_code == 401


def test_removed_account_loses_its_session(
    sso_settings: Settings, clock: FixedClock, idp: FakeAuthelia
) -> None:
    with TestClient(_app(sso_settings, clock, idp), base_url="https://media.example.com") as c:
        assert _sign_in(c).status_code == 200
        cookie = c.cookies["km_session"]
    fewer = sso_settings.model_copy(update={"oidc_allowed_users": "mama"})
    with TestClient(_app(fewer, clock, idp), base_url="https://media.example.com") as c:
        c.cookies.set("km_session", cookie)
        assert c.get("/api/admin/me").status_code == 401


def test_existing_account_is_reused(
    sso: TestClient, sso_settings: Settings, idp: FakeAuthelia
) -> None:
    idp.username = "mama"
    _sign_in(sso)
    sso.headers["X-CSRF-Token"] = sso.get("/api/admin/me").json()["csrf_token"]
    sso.patch("/api/admin/me", json={"locale": "de"})
    sso.post("/api/admin/logout")
    _sign_in(sso)
    assert sso.get("/api/admin/me").json()["locale"] == "de"  # same row, language kept


def test_sso_account_has_no_usable_password(settings: Settings, clock: FixedClock) -> None:
    """An account first created by SSO can't log in with any password once SSO is off."""
    app = create_app(settings, clock)
    with app.state.engine.begin() as conn:
        conn.execute(
            insert(admin_users).values(username="papa", password_hash="", created_at=clock.now())
        )
    with TestClient(app) as c:
        r = c.post("/api/admin/login", json={"username": "papa", "password": "x"})
        assert r.status_code == 401


def test_sso_session_row_is_marked(sso: TestClient, sso_settings: Settings) -> None:
    _sign_in(sso)
    token = sso.cookies["km_session"]
    with make_engine(sso_settings.db_path).connect() as conn:
        row = conn.execute(
            select(admin_sessions.c.method, admin_sessions.c.sso_groups).where(
                admin_sessions.c.token_hash == hash_token(token)
            )
        ).one()
    assert row.method == "sso" and row.sso_groups == ["family"]
