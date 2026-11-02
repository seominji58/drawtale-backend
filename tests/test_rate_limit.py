import uuid

import pytest

from app.core import rate_limit
from app.core.config import get_settings
from app.core.errors import AppError


@pytest.fixture(autouse=True)
def _limit_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    rate_limit.reset()
    yield
    rate_limit.reset()


def _upload(client, png_bytes):
    return client.post("/api/v1/characters", files={"image": ("a.png", png_bytes, "image/png")})


def test_guest_uploads_stop_at_limit_with_retry_after(client, png_bytes):
    allowed = rate_limit.PER_MINUTE["analyze"] * rate_limit.GUEST_FACTOR
    for _ in range(allowed):
        assert _upload(client, png_bytes).status_code == 202

    r = _upload(client, png_bytes)

    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMITED"
    assert 1 <= int(r.headers["Retry-After"]) <= rate_limit.WINDOW_SECONDS


def test_login_is_counted_by_ip_without_guest_factor(client):
    body = {"code": "x", "redirect_uri": "http://localhost:5173/auth/kakao/callback"}
    statuses = [
        client.post("/api/v1/auth/unknown", json=body).status_code
        for _ in range(rate_limit.PER_MINUTE["login"] + 1)
    ]

    assert statuses[-1] == 429
    assert 429 not in statuses[:-1]


def test_same_job_key_is_refused_until_released():
    key = f"story:{uuid.uuid4()}"
    rate_limit.claim(key)

    with pytest.raises(AppError) as e:
        rate_limit.claim(key)
    assert e.value.status_code == 429

    rate_limit.release(key)
    rate_limit.claim(key)


def test_guest_upload_has_no_shared_busy_key():
    rate_limit.claim(None)
    rate_limit.claim(None)


def test_key_is_released_when_job_fails():
    key = "story:fail"
    rate_limit.claim(key)

    def boom():
        raise RuntimeError

    with pytest.raises(RuntimeError):
        rate_limit.run_then_release(key, boom)

    rate_limit.claim(key)


def test_limit_can_be_switched_off(client, png_bytes, monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", False)
    allowed = rate_limit.PER_MINUTE["analyze"] * rate_limit.GUEST_FACTOR

    for _ in range(allowed + 1):
        assert _upload(client, png_bytes).status_code == 202
