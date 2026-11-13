from fastapi.testclient import TestClient
from sqlalchemy import select

from kidsmedia.clock import FixedClock
from kidsmedia.config import Settings
from kidsmedia.db import devices, make_engine


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_pairing_flow(admin: TestClient, settings: Settings) -> None:
    created = admin.post("/api/admin/devices", json={"name": "Kids tablet"}).json()
    assert created["status"] == "waiting_for_pairing"
    code = created["pairing"]["code"]
    assert len(code) == 6 and code.isdigit()

    r = admin.post("/api/pair", json={"code": code, "app_version": "1.0.0"})
    token = r.json()["token"]
    assert r.json()["profile_id"] == 1
    me = admin.get("/api/me", headers=bearer(token)).json()
    assert me["name"] == "Kids tablet" and me["time_zone"] == "Europe/Berlin"

    # single use
    assert admin.post("/api/pair", json={"code": code}).json()["reason"] == "invalid_pairing_code"
    listed = admin.get("/api/admin/devices").json()
    assert listed[0]["status"] == "active" and listed[0]["pairing"] is None
    assert listed[0]["app_version"] == "1.0.0"
    # stored hashed (SPEC §7)
    with make_engine(settings.db_path).connect() as conn:
        assert conn.execute(select(devices.c.token_hash)).scalar_one() != token


def test_code_expires_after_15_minutes(admin: TestClient, clock: FixedClock) -> None:
    code = admin.post("/api/admin/devices", json={}).json()["pairing"]["code"]
    clock.advance(minutes=15, seconds=1)
    assert admin.post("/api/pair", json={"code": code}).status_code == 400


def test_device_name_from_tablet_when_admin_gave_none(admin: TestClient) -> None:
    code = admin.post("/api/admin/devices", json={}).json()["pairing"]["code"]
    token = admin.post("/api/pair", json={"code": code, "device_name": "Lenovo Tab"}).json()[
        "token"
    ]
    assert admin.get("/api/me", headers=bearer(token)).json()["name"] == "Lenovo Tab"


def test_revoke_stops_the_token(admin: TestClient, device_token: str) -> None:
    device_id = admin.get("/api/me", headers=bearer(device_token)).json()["device_id"]
    assert admin.delete(f"/api/admin/devices/{device_id}").status_code == 204
    assert admin.get("/api/me", headers=bearer(device_token)).status_code == 401
    assert admin.get("/api/admin/devices").json()[0]["status"] == "revoked"


def test_pair_again_replaces_the_token(admin: TestClient, device_token: str) -> None:
    device_id = admin.get("/api/me", headers=bearer(device_token)).json()["device_id"]
    code = admin.post(f"/api/admin/devices/{device_id}/pairing-code").json()["pairing"]["code"]
    assert (
        admin.get("/api/me", headers=bearer(device_token)).status_code == 200
    )  # still valid meanwhile
    new_token = admin.post("/api/pair", json={"code": code}).json()["token"]
    assert admin.get("/api/me", headers=bearer(device_token)).status_code == 401
    assert admin.get("/api/me", headers=bearer(new_token)).status_code == 200


def test_bad_tokens(client: TestClient) -> None:
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers=bearer("nonsense")).json()["reason"] == "device_unknown"
    assert client.get("/api/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_pair_is_rate_limited(client: TestClient) -> None:
    for _ in range(5):
        client.post("/api/pair", json={"code": "000000"})
    assert client.post("/api/pair", json={"code": "000000"}).status_code == 429


def test_volume_cap_reaches_the_tablet(admin: TestClient, device_token: str) -> None:
    auth = bearer(device_token)
    device_id = admin.get("/api/me", headers=auth).json()["device_id"]
    url = f"/api/admin/devices/{device_id}"

    def library_cap() -> int | None:
        cap: int | None = admin.get("/api/library", headers=auth).json()["max_volume_percent"]
        return cap

    assert library_cap() is None
    r = admin.patch(url, json={"max_volume_percent": 60})
    assert r.status_code == 200 and r.json()["max_volume_percent"] == 60
    assert library_cap() == 60
    assert admin.get("/api/me", headers=auth).json()["max_volume_percent"] == 60

    # Renaming alone keeps the cap; an explicit null removes it.
    admin.patch(url, json={"name": "Living room"})
    assert admin.get("/api/admin/devices").json()[0]["max_volume_percent"] == 60
    admin.patch(url, json={"max_volume_percent": None})
    assert library_cap() is None


def test_volume_cap_is_validated(admin: TestClient, device_token: str) -> None:
    device_id = admin.get("/api/me", headers=bearer(device_token)).json()["device_id"]
    for bad in (0, 4, 101):
        r = admin.patch(f"/api/admin/devices/{device_id}", json={"max_volume_percent": bad})
        assert r.status_code == 422, bad
