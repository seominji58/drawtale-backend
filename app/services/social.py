"""Social login: exchange an authorization code for the provider's member id.

The frontend sends the user to the provider and hands us the returned `code`. We swap it for
an access token with the client secret (which only the backend has) and ask the provider who
the user is. Only the member id comes back out of this module — we request no scopes, so no
name, email or photo is ever received.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.core.errors import AppError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    token_url: str
    profile_url: str
    member_id: Callable[[dict], object]
    # Naver requires the `state` from the authorize request on the token request too
    needs_state: bool = False


PROVIDERS: dict[str, Provider] = {
    "kakao": Provider(
        name="kakao",
        label="카카오",
        token_url="https://kauth.kakao.com/oauth/token",
        profile_url="https://kapi.kakao.com/v2/user/me",
        member_id=lambda d: d["id"],
    ),
    "naver": Provider(
        name="naver",
        label="네이버",
        token_url="https://nid.naver.com/oauth2.0/token",
        profile_url="https://openapi.naver.com/v1/nid/me",
        member_id=lambda d: d["response"]["id"],
        needs_state=True,
    ),
    "google": Provider(
        name="google",
        label="Google",
        token_url="https://oauth2.googleapis.com/token",
        profile_url="https://openidconnect.googleapis.com/v1/userinfo",
        member_id=lambda d: d["sub"],
    ),
}

# Tests swap this for httpx.MockTransport. Production uses the real network.
_TRANSPORT: httpx.BaseTransport | None = None
_TIMEOUT_SECONDS = 10


def get_provider(name: str) -> Provider:
    provider = PROVIDERS.get(name)
    if provider is None:
        raise AppError("UNKNOWN_PROVIDER", "지원하지 않는 로그인 방식이에요.", 404)
    return provider


def _credentials(provider: Provider) -> tuple[str, str]:
    s = get_settings()
    client_id = getattr(s, f"{provider.name}_client_id")
    client_secret = getattr(s, f"{provider.name}_client_secret")
    if not client_id:
        raise AppError(
            "PROVIDER_NOT_CONFIGURED", f"{provider.label} 로그인이 아직 준비되지 않았어요.", 503
        )
    return client_id, client_secret


def _failed(provider: Provider, why: str) -> AppError:
    log.warning("%s login failed: %s", provider.name, why)
    message = f"{provider.label} 로그인을 마치지 못했어요. 다시 시도해 주세요."
    return AppError("OAUTH_FAILED", message, 400)


def fetch_member_id(provider: Provider, code: str, redirect_uri: str, state: str | None) -> str:
    client_id, client_secret = _credentials(provider)
    form = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,  # must equal the one used on the authorize request
    }
    if provider.needs_state:
        form["state"] = state or ""

    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS, transport=_TRANSPORT) as http:
            res = http.post(provider.token_url, data=form)
            # Naver answers 200 with an `error` field instead of an HTTP error
            token = res.json() if res.status_code < 500 else {}
            access_token = token.get("access_token")
            if res.status_code >= 400 or not access_token:
                raise _failed(provider, f"token HTTP {res.status_code} {token.get('error')}")

            res = http.get(
                provider.profile_url, headers={"Authorization": f"Bearer {access_token}"}
            )
            if res.status_code >= 400:
                raise _failed(provider, f"profile HTTP {res.status_code}")
            member_id = provider.member_id(res.json())
    except httpx.HTTPError as e:
        message = f"{provider.label}에 연결할 수 없어요. 잠시 후 다시 시도해 주세요."
        raise AppError("PROVIDER_UNAVAILABLE", message, 503) from e
    except (ValueError, KeyError, TypeError) as e:
        raise _failed(provider, f"unexpected response: {e!r}") from e

    if member_id in (None, ""):
        raise _failed(provider, "empty member id")
    return str(member_id)
