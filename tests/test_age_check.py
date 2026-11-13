"""보호자 나이 확인 (포트원 V2 본인인증, 2026-11-09).

포트원 API 는 httpx.MockTransport 로 흉내 낸다. 실제로 부르지 않는다."""

from datetime import UTC, date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import User
from app.services import identity, social

KST = timezone(timedelta(hours=9))
REDIRECT = "http://localhost:5173/auth/kakao/callback"


def _birth(years_ago: int, days: int = 0) -> str:
    """오늘(한국 시각)로 만 years_ago 살이 되는 생일. days 만큼 늦추면 아직 생일 전"""

    today = datetime.now(KST).date()
    try:
        b = today.replace(year=today.year - years_ago)
    except ValueError:  # 2월 29일
        b = today.replace(year=today.year - years_ago, day=28)
    return (b + timedelta(days=days)).isoformat()


@pytest.fixture
def portone(monkeypatch):
    """포트원 키를 켜고 조회 응답을 정할 수 있게 한다."""

    s = get_settings()
    monkeypatch.setattr(s, "portone_api_secret", "secret-x")
    monkeypatch.setattr(s, "portone_store_id", "store-x")
    monkeypatch.setattr(s, "age_verification_required", None)
    state: dict = {"records": {}, "seen": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["seen"].append(request)
        rec = state["records"].get(request.url.path.rsplit("/", 1)[-1])
        if rec is None:
            return httpx.Response(404, json={"type": "IDENTITY_VERIFICATION_NOT_FOUND"})
        return httpx.Response(200, json=rec)

    monkeypatch.setattr(identity, "_TRANSPORT", httpx.MockTransport(handler))

    def add(vid, birth, status="VERIFIED", minutes_ago=1):
        at = (
            (datetime.now(UTC) - timedelta(minutes=minutes_ago))
            .isoformat()
            .replace("+00:00", "Z")
        )
        state["records"][vid] = {
            "status": status,
            "id": vid,
            "verifiedAt": at,
            "verifiedCustomer": {
                "name": "홍길동",
                "birthDate": birth,
                "phoneNumber": "01012345678",
                "ci": "ci",
                "di": "di",
            },
        }

    state["add"] = add
    return state


@pytest.fixture
def kakao(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "kakao_client_id", "kakao-id")
    monkeypatch.setattr(s, "kakao_client_secret", "kakao-secret")
    member = {"id": 777}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == social.PROVIDERS["kakao"].token_url:
            return httpx.Response(200, json={"access_token": "at"})
        return httpx.Response(200, json=member)

    monkeypatch.setattr(social, "_TRANSPORT", httpx.MockTransport(handler))
    return member


def _check(client, vid):
    return client.post("/api/v1/auth/age-check", json={"identity_verification_id": vid})


def _signup(client, ticket=None):
    body = {"code": "c", "redirect_uri": REDIRECT, "agreed": True}
    if ticket:
        body["age_ticket"] = ticket
    return client.post("/api/v1/auth/kakao", json=body)


def test_korean_age_counts_birthday():
    assert identity.korean_age(date(2012, 11, 10), date(2026, 11, 9)) == 13
    assert identity.korean_age(date(2012, 11, 9), date(2026, 11, 9)) == 14


@pytest.mark.parametrize(
    "years,days,ok", [(30, 0, True), (14, 0, True), (14, 1, False), (13, 0, False)]
)
def test_age_check_passes_from_14(anon_client, portone, years, days, ok):
    portone["add"]("idv-1", _birth(years, days))
    r = _check(anon_client, "idv-1")
    if ok:
        assert r.status_code == 200 and identity.read_age_ticket(r.json()["age_ticket"]) == "idv-1"
    else:
        assert r.status_code == 403 and r.json()["error"]["code"] == "AGE_TOO_YOUNG"
    # 서버가 포트원에 직접 물었고, 받은 개인정보는 돌려주지 않는다
    req = portone["seen"][-1]
    assert (
        req.headers["Authorization"] == "PortOne secret-x"
        and req.url.params["storeId"] == "store-x"
    )
    assert "홍길동" not in r.text and "0101" not in r.text


@pytest.mark.parametrize(
    "status,minutes,code",
    [
        ("READY", 1, "AGE_CHECK_FAILED"),
        ("FAILED", 1, "AGE_CHECK_FAILED"),
        ("VERIFIED", 90, "AGE_CHECK_EXPIRED"),
    ],
)
def test_unfinished_or_old_verification_is_refused(anon_client, portone, status, minutes, code):
    portone["add"]("idv-2", _birth(40), status=status, minutes_ago=minutes)
    assert _check(anon_client, "idv-2").json()["error"]["code"] == code


def test_unknown_id_and_missing_key(anon_client, portone, monkeypatch):
    assert _check(anon_client, "nope").json()["error"]["code"] == "AGE_CHECK_FAILED"
    monkeypatch.setattr(get_settings(), "portone_api_secret", "")
    r = _check(anon_client, "nope")
    assert r.status_code == 503 and r.json()["error"]["code"] == "AGE_CHECK_NOT_CONFIGURED"


def test_new_account_needs_the_age_ticket(anon_client, portone, kakao):
    assert _signup(anon_client).json()["error"]["code"] == "AGE_CHECK_REQUIRED"
    assert _signup(anon_client, "forged.sig").json()["error"]["code"] == "AGE_CHECK_REQUIRED"
    with SessionLocal() as db:
        assert db.scalar(select(User)) is None  # 계정이 생기지 않았다

    portone["add"]("idv-3", _birth(35))
    ticket = _check(anon_client, "idv-3").json()["age_ticket"]
    r = _signup(anon_client, ticket)
    assert r.status_code == 200
    with SessionLocal() as db:
        u = db.scalar(select(User))
        # 남기는 것은 때 · 방법 · 인증 건 번호뿐
        assert (
            u.age_verified_at
            and u.age_verification_method == "portone-inicis"
            and u.age_verification_id == "idv-3"
        )
    me = anon_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"}
    ).json()
    assert me["age_verified"] is True

    # 같은 인증 건으로 다른 계정은 못 만든다
    kakao["id"] = 888
    r = _signup(anon_client, ticket)
    assert r.status_code == 409 and r.json()["error"]["code"] == "AGE_CHECK_USED"


def test_existing_account_without_check_is_asked_once(anon_client, portone, kakao, monkeypatch):
    # 본인인증을 넣기 전에 가입한 계정
    monkeypatch.setattr(get_settings(), "age_verification_required", False)
    token = _signup(anon_client).json()["token"]
    monkeypatch.setattr(get_settings(), "age_verification_required", None)
    h = {"Authorization": f"Bearer {token}"}
    # 로그인은 그대로 되고, 프론트가 보고 묻게 age_verified=false
    assert _signup(anon_client).status_code == 200
    assert anon_client.get("/api/v1/auth/me", headers=h).json()["age_verified"] is False

    portone["add"]("idv-4", _birth(13))
    r = anon_client.post(
        "/api/v1/auth/me/age-check", headers=h, json={"identity_verification_id": "idv-4"}
    )
    assert r.json()["error"]["code"] == "AGE_TOO_YOUNG"
    portone["add"]("idv-5", _birth(50))
    r = anon_client.post(
        "/api/v1/auth/me/age-check", headers=h, json={"identity_verification_id": "idv-5"}
    )
    assert r.status_code == 200 and r.json()["age_verified"] is True


def test_not_required_without_key_in_local(anon_client, kakao):
    # 로컬에서 키가 없으면 지금처럼 가입된다 (화면 시험용)
    assert get_settings().age_check_required is False
    r = _signup(anon_client)
    assert r.status_code == 200
    me = anon_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"}
    ).json()
    assert me["age_verified"] is True


def test_production_without_key_does_not_lock_people_out(monkeypatch):
    # 키 없는 운영에 배포되면 가입 · 기존 계정이 막혔다 (11/9). 키가 있을 때만, 또는 true 로 정했을 때만 요구한다
    s = get_settings()
    monkeypatch.setattr(s, "app_env", "prod")
    monkeypatch.setattr(s, "portone_api_secret", "")
    monkeypatch.setattr(s, "age_verification_required", None)
    assert s.age_check_required is False
    monkeypatch.setattr(s, "age_verification_required", True)
    assert s.age_check_required is True
