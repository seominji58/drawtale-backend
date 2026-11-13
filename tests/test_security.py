"""보안 (2026-11-02).

파일 주소 서명 · 요청 수 제한 · 이야기 하루 한도 · 로그아웃 · 픽셀 수 · 보안 헤더.
"""

import io
import time

import pytest
from PIL import Image

from app.core import rate_limit
from app.core.auth import issue_token
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import User
from app.models.mixins import utcnow
from app.services.storage import file_signature, get_storage

STORY = {
    "place": "숲",
    "problem": "길을 잃었어요",
    "action": "그 자리에서 기다렸어요",
    "result": "길을 찾았어요",
}


@pytest.fixture
def limits(monkeypatch):
    """요청 수 제한을 켜고 매번 처음부터 센다."""

    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    rate_limit.reset()
    yield get_settings()
    rate_limit.reset()


def _png(w=400, h=600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def _upload(client, headers=None):
    files = {"image": ("d.png", _png(), "image/png")}
    res = client.post("/api/v1/characters", files=files, headers=headers or {})
    assert res.status_code == 202, res.text
    return res.json()["character_id"]


def _token() -> str:
    with SessionLocal() as db:
        user = User(terms_agreed_at=utcnow())
        db.add(user)
        db.flush()
        token = issue_token(db, user)
        db.commit()
        return token


# ---- /files 서명 ----

def test_files_need_signature(client):
    storage = get_storage()
    storage.save("uploads/secret-test.png", _png(10, 10))
    url = storage.url("uploads/secret-test.png")
    path = url.split("/files/", 1)[1].split("?", 1)[0]
    assert client.get(f"/files/{path}").status_code == 404  # 주소만으로는 안 열린다
    assert client.get(url).status_code == 200  # API 가 내준 주소는 열린다
    exp = int(time.time()) - 10
    expired = f"/files/{path}?exp={exp}&sig={file_signature(path, exp)}"
    assert client.get(expired).status_code == 404  # 기한 지남
    good = url.split("sig=")[1]
    assert client.get(url.replace(good, "0" * len(good))).status_code == 404  # 서명 틀림
    other = f"/files/uploads/other.png?{url.split('?', 1)[1]}"
    assert client.get(other).status_code == 404  # 다른 파일에 같은 서명


# ---- 이야기 하루 한도 (분당 · 동시 제한은 tests/test_rate_limit.py) ----

def test_member_daily_limit(client, limits, monkeypatch):
    # 가입 없이는 쓸 수 없어 늘 계정 한도로 센다 (2026-11-03)
    monkeypatch.setattr(limits, "member_stories_per_day", 1)
    cid = _upload(client)
    assert client.post("/api/v1/stories", json={"character_id": cid, **STORY}).status_code == 202
    assert client.post("/api/v1/stories", json={"character_id": cid, **STORY}).status_code == 429
    # 다른 계정은 따로 센다
    token = _token()
    mine = _upload(client, {"Authorization": f"Bearer {token}"})
    res = client.post("/api/v1/stories", json={"character_id": mine, **STORY},
                      headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 202


def test_server_daily_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_stories_per_day", 1)
    cid = _upload(client)
    assert client.post("/api/v1/stories", json={"character_id": cid, **STORY}).status_code == 202
    res = client.post("/api/v1/stories", json={"character_id": cid, **STORY})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "DAILY_LIMIT"


def test_logout_revokes_token(client):
    token = _token()
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/me", headers=auth).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=auth).status_code == 204
    assert client.get("/api/v1/auth/me", headers=auth).status_code == 401
    assert client.post("/api/v1/auth/logout", headers=auth).status_code == 204  # 두 번째도 204
    assert client.post("/api/v1/auth/logout").status_code == 204


# ---- 그림 픽셀 수 ----

def test_too_many_pixels(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_image_pixels", 100 * 100)
    res = client.post("/api/v1/characters", files={"image": ("d.png", _png(400, 600), "image/png")})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "FILE_TOO_LARGE"


# ---- 보안 헤더 · 배포 판단 ----

def test_security_headers(client):
    res = client.get("/health")
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["Referrer-Policy"] == "no-referrer"


def test_production_flag(monkeypatch):
    s = get_settings()
    for env, prod in [("local", False), ("dev", False), ("prod", True), ("Production", True)]:
        monkeypatch.setattr(s, "app_env", env)
        assert s.is_production is prod
