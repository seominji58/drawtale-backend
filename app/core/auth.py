import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.session import get_db
from app.models import AuthToken, User
from app.models.mixins import utcnow


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(db: Session, user: User) -> str:
    """Create a new bearer token for the user. The caller commits."""

    token = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(days=get_settings().auth_token_days)
    db.add(AuthToken(user_id=user.id, token_hash=hash_token(token), expires_at=expires_at))
    return token


# 처음 온 사람의 가입 확인증. 카카오 · 구글 인가 코드는 한 번만 쓸 수 있어서, 약관 동의 뒤
# 제공자 화면을 다시 거치지 않도록 확인한 회원번호를 잠깐 서명해 둔다 (2026-11-03)
SIGNUP_TICKET_SECONDS = 600
_TICKET_KEY = (
    b"signup-ticket\n" + (get_settings().file_url_secret or secrets.token_hex(32)).encode()
)


def _ticket_sig(body: str) -> str:
    return hmac.new(_TICKET_KEY, body.encode(), hashlib.sha256).hexdigest()


def issue_signup_ticket(provider: str, member_id: str) -> str:
    payload = {"p": provider, "m": member_id, "exp": int(time.time()) + SIGNUP_TICKET_SECONDS}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    return f"{body}.{_ticket_sig(body)}"


def read_signup_ticket(ticket: str) -> tuple[str, str] | None:
    """(제공자, 회원번호). 서명이 틀리거나 기한이 지났으면 None."""

    body, _, sig = ticket.partition(".")
    if not body or not hmac.compare_digest(_ticket_sig(body), sig):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body.encode()))
    except ValueError:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload["p"], payload["m"]


def aware(at: datetime) -> datetime:
    """SQLite (tests) returns naive datetimes; compare in UTC either way"""

    return at if at.tzinfo is not None else at.replace(tzinfo=utcnow().tzinfo)


def _token_for(authorization: str | None, db: Session) -> AuthToken | None:
    """Authorization 헤더의 살아 있는 토큰 행. 없거나 틀리거나 지났으면 None."""

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(token)))
    if row is None or aware(row.expires_at) <= utcnow():
        return None
    return row


def _user_for(authorization: str | None, db: Session) -> User | None:
    """Authorization 헤더에서 사용자를 찾는다. 토큰이 없거나 틀리면 None."""

    row = _token_for(authorization, db)
    return None if row is None else db.get(User, row.user_id)


def current_token(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
) -> AuthToken:
    """로그인한 토큰 행 — 언제 로그인했는지(created_at)가 필요한 곳 (보호자 비밀번호 새로 정하기)"""

    row = _token_for(authorization, db)
    if row is None:
        raise AppError("UNAUTHORIZED", "로그인이 필요해요.", 401)
    return row


def current_user(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
) -> User:
    """Dependency for endpoints that need a signed-in guardian or teacher."""

    user = _user_for(authorization, db)
    if user is None:
        raise AppError("UNAUTHORIZED", "로그인이 필요해요.", 401)
    return user


def optional_user(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
) -> User | None:
    """로그인해도 되고 안 해도 되는 곳에 쓴다.

    아이는 계정 없이도 끝까지 갈 수 있어야 한다 (F-ACC-01). 토큰이 있으면 그 계정의
    것으로 기록하고, 없으면 주인 없는 자료로 둔다.
    """

    return _user_for(authorization, db)
