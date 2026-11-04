import uuid

_STORY = {"place": "학교", "problem": "비가 와요", "action": "물어봤어요", "result": "웃었어요"}


def _story(client, png_bytes) -> str:
    res = client.post("/api/v1/characters", files={"image": ("draw.png", png_bytes, "image/png")})
    cid = res.json()["character_id"]
    res = client.post("/api/v1/stories", json={"character_id": cid, **_STORY})
    return res.json()["story_id"]


def test_rename_story(client, png_bytes):
    sid = _story(client, png_bytes)
    assert client.get(f"/api/v1/stories/{sid}").json()["title"] is None

    res = client.patch(f"/api/v1/stories/{sid}", json={"title": "  용감한   도토리  "})
    assert res.status_code == 200, res.text
    assert res.json()["title"] == "용감한 도토리"
    # 다시 받아도 (다른 기기) 같은 제목
    assert client.get(f"/api/v1/stories/{sid}").json()["title"] == "용감한 도토리"


def test_rename_validation(client, png_bytes):
    sid = _story(client, png_bytes)
    for bad in ({"title": ""}, {"title": "   "}, {"title": "가" * 21}, {}):
        assert client.patch(f"/api/v1/stories/{sid}", json=bad).status_code == 422, bad


def test_rename_unknown_story(client):
    res = client.patch(f"/api/v1/stories/{uuid.uuid4()}", json={"title": "제목"})
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "STORY_NOT_FOUND"
