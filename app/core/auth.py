import hashlib
import secrets
from datetime import timedelta

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


def _user_for(authorization: str | None, db: Session) -> User | None:
    """Authorization 헤더에서 사용자를 찾는다. 토큰이 없거나 틀리면 None."""

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(token)))
    if row is None:
        return None
    # SQLite (tests) returns naive datetimes; compare in UTC either way
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=utcnow().tzinfo)
    if expires_at <= utcnow():
        return None
    return db.get(User, row.user_id)


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
