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


def current_user(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
) -> User:
    """Dependency for endpoints that need a signed-in guardian or teacher."""

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AppError("UNAUTHORIZED", "로그인이 필요해요.", 401)
    row = db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(token)))
    # SQLite (tests) returns naive datetimes; compare in UTC either way
    expires_at = row.expires_at if row else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=utcnow().tzinfo)
    if row is None or expires_at <= utcnow():
        raise AppError("UNAUTHORIZED", "로그인이 필요해요.", 401)
    user = db.get(User, row.user_id)
    if user is None:
        raise AppError("UNAUTHORIZED", "로그인이 필요해요.", 401)
    return user
