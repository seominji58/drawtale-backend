import uuid

_STORY = {"place": "학교", "problem": "비가 와요", "action": "물어봤어요", "result": "웃었어요"}


def _story(client, png_bytes) -> str:
    res = client.post("/api/v1/characters", files={"image": ("draw.png", png_bytes, "image/png")})
    cid = res.json()["character_id"]
    res = client.post("/api/v1/stories", json={"character_id": cid, **_STORY})
    return res.json()["story_id"]


def test_record_and_list_activity(client, png_bytes):
    sid = _story(client, png_bytes)

    res = client.post(
        f"/api/v1/stories/{sid}/activity",
        json={"attempts": 1, "completed": False, "card_count": 4, "level": 2},
    )
    assert res.status_code == 201, res.text
    assert res.json()["story_id"] == sid

    # 다시 보기로 한 번 더 하면 기록이 하나 더 생긴다
    res = client.post(
        f"/api/v1/stories/{sid}/activity", json={"attempts": 3, "completed": True, "card_count": 4}
    )
    assert res.status_code == 201
    assert res.json()["level"] is None

    rows = client.get(f"/api/v1/stories/{sid}/activity").json()
    assert [(r["attempts"], r["completed"]) for r in rows] == [(1, False), (3, True)]


def test_activity_validation(client, png_bytes):
    sid = _story(client, png_bytes)
    for bad in (
        {"attempts": 0, "completed": True, "card_count": 4},
        {"attempts": 1, "completed": True, "card_count": 5},
        {"attempts": 1, "completed": True, "card_count": 4, "level": 4},
    ):
        res = client.post(f"/api/v1/stories/{sid}/activity", json=bad)
        assert res.status_code == 422, bad


def test_activity_unknown_story(client):
    body = {"attempts": 1, "completed": True, "card_count": 4}
    res = client.post(f"/api/v1/stories/{uuid.uuid4()}/activity", json=body)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "STORY_NOT_FOUND"
