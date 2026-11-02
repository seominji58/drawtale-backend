from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import rate_limit
from app.core.auth import current_user, hash_token, issue_token
from app.core.errors import AppError
from app.db.session import get_db
from app.models import AuthToken, SocialAccount, User
from app.models.mixins import utcnow
from app.schemas.auth import AuthResponse, MeResponse, SocialLoginRequest
from app.schemas.common import ErrorResponse
from app.services.social import fetch_member_id, get_provider

router = APIRouter(prefix="/auth", tags=["auth"])

_ERRORS = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="로그아웃 — 이 토큰을 서버에서 지운다 (2026-11-02)",
)
def logout(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
) -> Response:
    """전에는 프론트가 토큰을 버리기만 해서, 공용 기기에 남은 토큰이 30일 동안 살아 있었다.
    토큰이 없거나 이미 지워졌어도 204 — 로그아웃은 늘 성공한 것으로 본다.
    "/{provider}" 보다 먼저 둔다 (아니면 provider="logout" 로 잡힌다)"""

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() == "bearer" and token:
        row = db.scalar(select(AuthToken).where(AuthToken.token_hash == hash_token(token)))
        if row is not None:
            db.delete(row)
            db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{provider}",
    response_model=AuthResponse,
    responses=_ERRORS,
    summary="소셜 로그인 (kakao · google) — 인가 코드 교환",
)
def social_login(
    request: Request, provider: str, body: SocialLoginRequest, db: Session = Depends(get_db)
) -> AuthResponse:
    rate_limit.check("login", request)
    p = get_provider(provider)
    member_id = fetch_member_id(p, body.code, body.redirect_uri)

    account = db.scalar(
        select(SocialAccount).where(
            SocialAccount.provider == p.name, SocialAccount.provider_user_id == member_id
        )
    )
    if account is None:
        # The provider's consent screen does not cover our own terms. A first-time user must
        # come through the signup screen that asks for it (frontend S-16).
        if not body.agreed:
            raise AppError("SIGNUP_REQUIRED", "처음 오셨네요. 약관에 동의한 뒤 가입해 주세요.", 409)
        user = User(terms_agreed_at=utcnow())
        db.add(user)
        db.flush()
        account = SocialAccount(user_id=user.id, provider=p.name, provider_user_id=member_id)
        db.add(account)
        db.flush()

    token = issue_token(db, account.user)
    db.commit()
    return AuthResponse(token=token, account=f"{p.label} 계정")


@router.get(
    "/me",
    response_model=MeResponse,
    responses={401: {"model": ErrorResponse}},
    summary="현재 로그인한 계정 확인",
)
def me(user: User = Depends(current_user)) -> MeResponse:
    return MeResponse(user_id=user.id, providers=[a.provider for a in user.social_accounts])
