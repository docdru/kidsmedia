from fastapi.testclient import TestClient


def test_create_list_patch_delete(admin: TestClient) -> None:
    music = admin.post("/api/admin/groups", json={"name": " Music ", "kind": "music"}).json()
    story = admin.post(
        "/api/admin/groups", json={"name": "The Moon Pirates", "kind": "audiobook"}
    ).json()
    assert music["name"] == "Music"
    assert music["resume"] is False and story["resume"] is True  # SPEC §5.3 defaults
    assert music["color"] == "#0F7A70"
    assert [g["sort"] for g in (music, story)] == [0, 1]
    assert music["cover"] is None and music["cover_source"] == "auto"

    r = admin.patch(
        f"/api/admin/groups/{story['id']}", json={"name": "Moon Pirates", "color": "#112233"}
    )
    assert r.json()["name"] == "Moon Pirates" and r.json()["color"] == "#112233"
    assert admin.patch(f"/api/admin/groups/{story['id']}", json={"color": "red"}).status_code == 422

    assert [g["name"] for g in admin.get("/api/admin/groups").json()] == ["Music", "Moon Pirates"]
    assert admin.delete(f"/api/admin/groups/{music['id']}").status_code == 204
    assert admin.get(f"/api/admin/groups/{music['id']}").status_code == 404


def test_reorder(admin: TestClient) -> None:
    ids = [
        admin.post("/api/admin/groups", json={"name": n, "kind": "video"}).json()["id"]
        for n in "ABC"
    ]
    r = admin.post("/api/admin/groups/reorder", json={"ids": ids[::-1]})
    assert [g["name"] for g in r.json()] == ["C", "B", "A"]
    assert admin.post("/api/admin/groups/reorder", json={"ids": ids[:2]}).status_code == 422


def test_validation(admin: TestClient) -> None:
    assert admin.post("/api/admin/groups", json={"name": "", "kind": "music"}).status_code == 422
    assert admin.post("/api/admin/groups", json={"name": "X", "kind": "podcast"}).status_code == 422
