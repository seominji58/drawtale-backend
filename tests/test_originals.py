import uuid
from datetime import timedelta

from app.core.errors import AppError
from app.db import session as db_session
from app.models import Character
from app.models.mixins import utcnow
from app.services import jobs
from app.services.originals import purge_expired_originals

_STORY = {"place": "학교", "problem": "비가 와요", "action": "물어봤어요", "result": "웃었어요"}


def _upload(client, png_bytes, keep: bool | None = None) -> str:
    data = {} if keep is None else {"keep_original": str(keep).lower()}
    res = client.post(
        "/api/v1/characters", files={"image": ("draw.png", png_bytes, "image/png")}, data=data
    )
    assert res.status_code == 202, res.text
    return res.json()["character_id"]


def _file(client, url: str) -> int:
    return client.get(url.replace("http://127.0.0.1:8000", "")).status_code


def test_keep_original_defaults_to_false(client, png_bytes):
    cid = _upload(client, png_bytes)
    character = client.get(f"/api/v1/characters/{cid}").json()

    assert character["keep_original"] is False
    assert character["original_deleted_at"] is None
    assert _file(client, character["image_url"]) == 200


def test_delete_original(client, png_bytes):
    cid = _upload(client, png_bytes)
    image_url = client.get(f"/api/v1/characters/{cid}").json()["image_url"]

    res = client.post("/api/v1/stories", json={"character_id": cid, **_STORY})
    first = client.get(f"/api/v1/stories/{res.json()['story_id']}").json()
    assert first["animation_url"] == image_url  # 목 AI 는 원본을 animation 으로 준다

    assert client.delete(f"/api/v1/characters/{cid}/original").status_code == 204
    assert client.delete(f"/api/v1/characters/{cid}/original").status_code == 204  # 여러 번 괜찮다

    character = client.get(f"/api/v1/characters/{cid}").json()
    assert character["image_url"] is None
    assert character["original_deleted_at"] is not None
    assert character["joints"] is not None  # 관절은 남는다
    assert _file(client, image_url) == 404

    first = client.get(f"/api/v1/stories/{first['id']}").json()
    assert first["text"] and first["animation_url"] is None

    # 지운 뒤에도 같은 그림으로 이야기를 만들 수 있다. MP4 만 없다
    res = client.post("/api/v1/stories", json={"character_id": cid, **_STORY})
    second = client.get(f"/api/v1/stories/{res.json()['story_id']}").json()
    assert second["status"] == "succeeded"
    assert second["animation_url"] is None


def test_failed_analysis_deletes_original(client, png_bytes, monkeypatch):
    class FailingAI:
        def analyze(self, image, filename):
            raise AppError("LOW_CONFIDENCE", "그림에서 사람 모양을 알아보기 어려워요.", 502)

    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: FailingAI())
    dropped = _upload(client, png_bytes)
    kept = _upload(client, png_bytes, keep=True)

    assert client.get(f"/api/v1/characters/{dropped}").json()["image_url"] is None
    assert client.get(f"/api/v1/characters/{kept}").json()["image_url"] is not None


def test_purge_expired_originals(client, png_bytes):
    old = _upload(client, png_bytes)
    kept = _upload(client, png_bytes, keep=True)
    fresh = _upload(client, png_bytes)

    with db_session.SessionLocal() as db:
        for cid in (old, kept):
            db.get(Character, uuid.UUID(cid)).created_at = utcnow() - timedelta(hours=25)
        db.commit()

    assert purge_expired_originals() == 1
    assert client.get(f"/api/v1/characters/{old}").json()["image_url"] is None
    assert client.get(f"/api/v1/characters/{kept}").json()["image_url"] is not None
    assert client.get(f"/api/v1/characters/{fresh}").json()["image_url"] is not None
    assert purge_expired_originals() == 0
