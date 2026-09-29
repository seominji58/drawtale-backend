from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import current_user, issue_token
from app.core.errors import AppError
from app.db.session import get_db
from app.models import SocialAccount, User
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
    "/{provider}",
    response_model=AuthResponse,
    responses=_ERRORS,
    summary="소셜 로그인 (kakao · naver · google) — 인가 코드 교환",
)
def social_login(
    provider: str, body: SocialLoginRequest, db: Session = Depends(get_db)
) -> AuthResponse:
    p = get_provider(provider)
    member_id = fetch_member_id(p, body.code, body.redirect_uri, body.state)

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
