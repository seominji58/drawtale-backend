"""보호자 비밀번호 · 보호자 소개 카드 (pt_docs 20 3장, 2026-11-06)."""

from datetime import timedelta

from app.db.session import SessionLocal
from app.models import AuthToken, User
from app.models.mixins import utcnow

PIN = "/api/v1/me/guardian-pin"
CHECK = "/api/v1/me/guardian-pin/check"


def _me(client) -> dict:
    return client.get("/api/v1/auth/me").json()


def _age_tokens(minutes: int) -> None:
    """로그인한 지 오래된 토큰으로 만든다 (잊은 비밀번호 새로 정하기 시험)."""

    with SessionLocal() as db:
        for row in db.query(AuthToken).all():
            row.created_at = utcnow() - timedelta(minutes=minutes)
        db.commit()


def test_create_and_check(client):
    assert _me(client)["guardian_pin_set"] is False
    res = client.post(CHECK, json={"pin": "1234"})
    assert res.status_code == 409 and res.json()["error"]["code"] == "PIN_NOT_SET"

    assert client.put(PIN, json={"pin": "2580"}).status_code == 204
    assert _me(client)["guardian_pin_set"] is True
    assert client.post(CHECK, json={"pin": "2580"}).json() == {"ok": True}

    res = client.post(CHECK, json={"pin": "0000"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "PIN_WRONG"
    assert res.json()["error"]["detail"]["tries_left"] == 4


def test_pin_is_not_stored_plain(client):
    client.put(PIN, json={"pin": "2580"})
    with SessionLocal() as db:
        stored = db.query(User).one().guardian_pin_hash
    assert stored and "2580" not in stored


def test_only_four_digits(client):
    for bad in ("123", "12345", "abcd", "12a4", "", " 123"):
        assert client.put(PIN, json={"pin": bad}).status_code == 422, bad
        assert client.post(CHECK, json={"pin": bad}).status_code == 422, bad


def test_five_wrong_locks(client):
    client.put(PIN, json={"pin": "2580"})
    for left in (4, 3, 2, 1):
        res = client.post(CHECK, json={"pin": "1111"})
        assert res.json()["error"]["detail"]["tries_left"] == left
    res = client.post(CHECK, json={"pin": "1111"})
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "PIN_LOCKED"
    assert int(res.headers["Retry-After"]) > 0
    # 막힌 동안은 맞는 비밀번호도 받지 않는다
    assert client.post(CHECK, json={"pin": "2580"}).status_code == 429

    # 시간이 지나면 다시 받는다
    with SessionLocal() as db:
        db.query(User).one().guardian_pin_locked_until = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.post(CHECK, json={"pin": "2580"}).status_code == 200


def test_right_pin_resets_count(client):
    client.put(PIN, json={"pin": "2580"})
    for _ in range(4):
        client.post(CHECK, json={"pin": "1111"})
    client.post(CHECK, json={"pin": "2580"})
    res = client.post(CHECK, json={"pin": "1111"})
    assert res.json()["error"]["detail"]["tries_left"] == 4


def test_change_needs_current_pin(client):
    client.put(PIN, json={"pin": "2580"})
    _age_tokens(60)
    res = client.put(PIN, json={"pin": "1357"})
    assert res.status_code == 403 and res.json()["error"]["code"] == "PIN_REQUIRED"
    res = client.put(PIN, json={"pin": "1357", "current_pin": "0000"})
    assert res.status_code == 403 and res.json()["error"]["code"] == "PIN_WRONG"
    assert client.put(PIN, json={"pin": "1357", "current_pin": "2580"}).status_code == 204
    assert client.post(CHECK, json={"pin": "1357"}).status_code == 200


def test_forgot_pin_after_fresh_login(client):
    """잊었으면 다시 로그인 — 로그인한 지 10분 안의 토큰이면 지금 비밀번호 없이 새로 정한다."""

    client.put(PIN, json={"pin": "2580"})
    _age_tokens(5)
    assert client.put(PIN, json={"pin": "1357"}).status_code == 204
    assert client.post(CHECK, json={"pin": "1357"}).status_code == 200


def test_needs_login(anon_client):
    assert anon_client.put(PIN, json={"pin": "2580"}).status_code == 401
    assert anon_client.post(CHECK, json={"pin": "2580"}).status_code == 401
    assert anon_client.post("/api/v1/me/onboarded").status_code == 401


def test_onboarded(client):
    assert _me(client)["onboarded"] is False
    assert client.post("/api/v1/me/onboarded").status_code == 204
    assert client.post("/api/v1/me/onboarded").status_code == 204
    assert _me(client)["onboarded"] is True
