from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import AuthToken, SocialAccount, User
from app.services import social

REDIRECT = "http://localhost:5173/auth/{}/callback"

# What each provider's profile endpoint returns for the member id "12345"
_PROFILE = {
    "kakao": {"id": 12345},
    "google": {"sub": "12345"},
}


@pytest.fixture
def providers(monkeypatch):
    """Configure all three providers and fake their HTTP endpoints. Returns seen requests."""

    s = get_settings()
    for name in social.PROVIDERS:
        monkeypatch.setattr(s, f"{name}_client_id", f"{name}-id")
        monkeypatch.setattr(s, f"{name}_client_secret", f"{name}-secret")

    seen: list[httpx.Request] = []
    state = {"token_status": 200, "token_body": {"access_token": "at"}}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for p in social.PROVIDERS.values():
            if str(request.url) == p.token_url:
                return httpx.Response(state["token_status"], json=state["token_body"])
            if str(request.url) == p.profile_url:
                assert request.headers["Authorization"] == "Bearer at"
                return httpx.Response(200, json=_PROFILE[p.name])
        return httpx.Response(404)

    monkeypatch.setattr(social, "_TRANSPORT", httpx.MockTransport(handler))
    return {"seen": seen, "state": state}


def _login(client, provider, agreed=False):
    body = {
        "code": "c0de",
        "redirect_uri": REDIRECT.format(provider),
        "agreed": agreed,
    }
    return client.post(f"/api/v1/auth/{provider}", json=body)


def _count(model) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_first_login_requires_terms(client, providers):
    res = _login(client, "kakao")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "SIGNUP_REQUIRED"
    assert _count(User) == 0


def test_signup_ticket_finishes_without_second_provider_trip(client, providers):
    ticket = _login(client, "kakao").json()["error"]["detail"]["signup_ticket"]
    trips = len(providers["seen"])

    res = client.post("/api/v1/auth/social-signup", json={"ticket": ticket, "agreed": True})

    assert res.status_code == 200, res.text
    assert res.json()["account"] == "카카오 계정"
    assert len(providers["seen"]) == trips  # 카카오에 다시 묻지 않았다
    # 같은 확인증을 또 보내도 계정은 하나, 다음부터는 바로 로그인
    again = client.post("/api/v1/auth/social-signup", json={"ticket": ticket, "agreed": True})
    assert again.status_code == 200
    assert _login(client, "kakao").status_code == 200
    assert _count(User) == 1 and _count(SocialAccount) == 1


def test_signup_ticket_needs_terms_and_a_valid_signature(client, providers, monkeypatch):
    ticket = _login(client, "google").json()["error"]["detail"]["signup_ticket"]

    no_terms = client.post("/api/v1/auth/social-signup", json={"ticket": ticket, "agreed": False})
    assert no_terms.json()["error"]["code"] == "TERMS_REQUIRED"

    body, _, sig = ticket.partition(".")
    forged = client.post(
        "/api/v1/auth/social-signup", json={"ticket": f"{body}x.{sig}", "agreed": True}
    )
    assert forged.json()["error"]["code"] == "SIGNUP_EXPIRED"

    from app.core import auth as core_auth

    monkeypatch.setattr(core_auth.time, "time", lambda: 10**10)
    late = client.post("/api/v1/auth/social-signup", json={"ticket": ticket, "agreed": True})
    assert late.json()["error"]["code"] == "SIGNUP_EXPIRED"
    assert _count(User) == 0


@pytest.mark.parametrize("provider,label", [("kakao", "카카오"), ("google", "Google")])
def test_signup_then_login(client, providers, provider, label):
    res = _login(client, provider, agreed=True)
    assert res.status_code == 200, res.text
    assert res.json()["account"] == f"{label} 계정"

    # A returning user signs in without the terms flag, and it is the same user
    again = _login(client, provider)
    assert again.status_code == 200, again.text
    assert _count(User) == 1 and _count(SocialAccount) == 1 and _count(AuthToken) == 2

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {again.json()['token']}"})
    assert me.status_code == 200
    assert me.json()["providers"] == [provider]


def test_token_request_carries_secret_and_redirect(client, providers):
    _login(client, "kakao", agreed=True)
    token_req = providers["seen"][0]
    form = {k: v[0] for k, v in parse_qs(token_req.content.decode()).items()}
    assert form["client_secret"] == "kakao-secret"
    assert form["redirect_uri"] == REDIRECT.format("kakao")
    assert form["grant_type"] == "authorization_code"


def test_only_the_hash_of_the_token_is_stored(client, providers):
    token = _login(client, "google", agreed=True).json()["token"]
    with SessionLocal() as db:
        stored = db.scalar(select(AuthToken.token_hash))
    assert stored != token and len(stored) == 64


@pytest.mark.parametrize(
    "status,body",
    [(400, {"error": "invalid_grant"}), (200, {"error": "invalid_request"})],  # some answer 200
)
def test_provider_rejects_code(client, providers, status, body):
    providers["state"].update(token_status=status, token_body=body)
    res = _login(client, "kakao", agreed=True)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "OAUTH_FAILED"
    assert _count(User) == 0


def test_unknown_and_unconfigured_provider(client, monkeypatch):
    assert _login(client, "facebook").json()["error"]["code"] == "UNKNOWN_PROVIDER"
    monkeypatch.setattr(get_settings(), "kakao_client_id", "")
    res = _login(client, "kakao")
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "PROVIDER_NOT_CONFIGURED"


def test_me_requires_a_valid_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    bad = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "UNAUTHORIZED"
