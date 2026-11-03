from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import rate_limit
from app.core.auth import (
    current_user,
    hash_token,
    issue_signup_ticket,
    issue_token,
    read_signup_ticket,
)
from app.core.errors import AppError
from app.db.session import get_db
from app.models import AuthToken, SocialAccount, User
from app.models.mixins import utcnow
from app.schemas.auth import (
    AuthResponse,
    MeResponse,
    SocialLoginRequest,
    SocialSignupRequest,
)
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


def _account(db: Session, provider: str, member_id: str) -> SocialAccount | None:
    return db.scalar(
        select(SocialAccount).where(
            SocialAccount.provider == provider, SocialAccount.provider_user_id == member_id
        )
    )


def _create_account(
    db: Session, provider: str, member_id: str, consent_version: str | None
) -> SocialAccount:
    user = User(terms_agreed_at=utcnow(), consent_version=consent_version)
    db.add(user)
    db.flush()
    account = SocialAccount(user_id=user.id, provider=provider, provider_user_id=member_id)
    db.add(account)
    db.flush()
    return account


@router.post(
    "/social-signup",
    response_model=AuthResponse,
    responses=_ERRORS,
    summary="처음 온 사람의 가입 — SIGNUP_REQUIRED 의 확인증 + 약관 동의 (2026-11-03)",
)
def social_signup(
    request: Request, body: SocialSignupRequest, db: Session = Depends(get_db)
) -> AuthResponse:
    """인가 코드는 한 번만 쓸 수 있어, 전에는 약관에 동의한 뒤 카카오 · 구글을 다시 거쳐야 했다.
    "/{provider}" 보다 먼저 둔다 (아니면 provider="social-signup" 로 잡힌다)"""

    rate_limit.check("login", request)
    if not body.agreed:
        raise AppError("TERMS_REQUIRED", "약관에 동의해야 가입할 수 있어요.", 400)
    found = read_signup_ticket(body.ticket)
    if found is None:
        raise AppError("SIGNUP_EXPIRED", "가입 시간이 지났어요. 다시 로그인해 주세요.", 400)
    provider, member_id = found
    p = get_provider(provider)

    # 확인증으로 두 번 눌러도 계정은 하나다
    account = _account(db, p.name, member_id) or _create_account(
        db, p.name, member_id, body.consent_version
    )
    token = issue_token(db, account.user)
    db.commit()
    return AuthResponse(token=token, account=f"{p.label} 계정")


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

    account = _account(db, p.name, member_id)
    if account is None:
        # The provider's consent screen does not cover our own terms. A first-time user must
        # come through the signup screen that asks for it (frontend S-16). The ticket lets that
        # screen finish without sending them through the provider again (/social-signup).
        if not body.agreed:
            raise AppError(
                "SIGNUP_REQUIRED",
                "처음 오셨네요. 약관에 동의한 뒤 가입해 주세요.",
                409,
                detail={"signup_ticket": issue_signup_ticket(p.name, member_id)},
            )
        account = _create_account(db, p.name, member_id, body.consent_version)

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


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={401: {"model": ErrorResponse}},
    summary="계정 탈퇴 — 그림 · 이야기 · 음성 · 영상 · 보호자 목소리 · 로그인 기록을 모두 지운다",
)
def delete_me(db: Session = Depends(get_db), user: User = Depends(current_user)) -> Response:
    """되돌릴 수 없다. 화면(S-13)이 한 번 묻고 부른다. 이 토큰도 함께 지워져 다음 요청은 401 (2026-11-03)."""

    from app.services.account import delete_account

    delete_account(db, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
