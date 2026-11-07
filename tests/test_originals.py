import uuid
from datetime import timedelta

from app.core.errors import AppError
from app.db import session as db_session
from app.models import Character
from app.models.mixins import utcnow
from app.services import jobs
from app.services.originals import purge_expired_originals
from app.services.storage import get_storage

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
    # 목 AI 는 원본을 animation 으로 준다. 주소의 서명 · 기한(?exp=&sig=)이 아니라 파일로 비교한다
    assert first["animation_url"].split("?")[0] == image_url.split("?")[0]

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


def test_cutout_is_saved_and_deleted_with_original(client, png_bytes, monkeypatch):
    """배경을 잘라 낸 정지 그림 (2026-11-03). 아이 그림 사본이라 원본과 함께 지운다."""

    from app.services.ai_client import MockAIClient

    class CutoutAI(MockAIClient):
        def cutout(self, request_id):
            return b"png-cutout"

    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: CutoutAI(size))
    cid = _upload(client, png_bytes)
    url = client.get(f"/api/v1/characters/{cid}").json()["analysis"]["cutout_url"]
    assert url and f"cutouts/{cid}.png" in url

    assert client.delete(f"/api/v1/characters/{cid}/original").status_code == 204
    assert client.get(f"/api/v1/characters/{cid}").json()["analysis"]["cutout_url"] is None


def test_no_cutout_when_ai_has_none(client, png_bytes):
    cid = _upload(client, png_bytes)
    assert client.get(f"/api/v1/characters/{cid}").json()["analysis"]["cutout_url"] is None


def test_story_keeps_its_own_still_after_original_is_deleted(client, png_bytes, monkeypatch):
    """이야기는 잘라 낸 정지 그림을 자기 폴더에 복사해 둔다. 원본을 지워도 남는다."""

    from app.services.ai_client import MockAIClient

    class CutoutAI(MockAIClient):
        def cutout(self, request_id):
            return b"png-cutout"

    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: CutoutAI(size))
    cid = _upload(client, png_bytes)
    sid = client.post("/api/v1/stories", json={"character_id": cid, **_STORY}).json()["story_id"]
    assert client.delete(f"/api/v1/characters/{cid}/original").status_code == 204

    still = client.get(f"/api/v1/stories/{sid}").json()["still_url"]
    assert still and f"results/{sid}/still.png" in still
    path = still.split("/files/", 1)[1].split("?")[0]
    assert get_storage().read(path) == b"png-cutout"


def test_file_urls_stay_the_same_for_a_while(monkeypatch):
    # 같은 파일은 10분 단위로 같은 주소, 브라우저 캐시 · 주소 비교가 초 경계에서 흔들리지 않게 (11/6)
    from app.services import storage

    base = 1_800_000_000          # 10분 단위 경계
    assert storage.stable_expiry(24, base + 1) == storage.stable_expiry(24, base + 599)
    assert storage.stable_expiry(24, base + 1) != storage.stable_expiry(24, base + 601)
    for now in (base, base + 1, base + 599):   # 열리는 시간은 늘 24시간 이상, 24시간 10분 이하
        left = storage.stable_expiry(24, now) - now
        assert 24 * 3600 <= left <= 24 * 3600 + storage.URL_EXPIRY_STEP

    s = storage.get_storage()
    monkeypatch.setattr(storage.time, "time", lambda: base + 10)
    first = s.url("uploads/a.png")
    monkeypatch.setattr(storage.time, "time", lambda: base + 500)
    assert s.url("uploads/a.png") == first
