"""보호자 비밀번호 · 보호자 소개 카드 (pt_docs 20 3장 「기능 드러내기」, 2026-11-06).

설정은 누구나 톱니로 연다. 아이가 바꾸면 안 되는 것(보호자 모드 · 사진 올리기 · 목소리 · 계정 …)은
「보호자 칸」에 두고 이 비밀번호로 연다. 아이 화면의 잠긴 보호자 기능도 같은 비밀번호 창을 쓴다.

- 비밀번호는 계정에 해시로 — 기기를 바꿔도 같다.
- 잊으면 다시 로그인(카카오 · 구글)한다. 로그인한 지 10분 안의 토큰이면
  지금 비밀번호 없이 새로 정할 수 있다.
- 연달아 다섯 번 틀리면 5분 동안 받지 않는다 (429 PIN_LOCKED, Retry-After).
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.auth import aware, current_token
from app.core.errors import AppError
from app.db.session import get_db
from app.models import AuthToken, User
from app.models.mixins import utcnow
from app.schemas.common import ErrorResponse
from app.schemas.guardian import (
    GuardianPinCheckRequest,
    GuardianPinCheckResponse,
    GuardianPinSetRequest,
)
from app.services import guardian_pin

router = APIRouter(prefix="/me", tags=["guardian"])

_ERRORS = {
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
}
# 다시 로그인한 뒤 지금 비밀번호 없이 새로 정할 수 있는 시간
FRESH_LOGIN = timedelta(minutes=10)


def _user(db: Session, token: AuthToken) -> User:
    user = db.get(User, token.user_id)
    if user is None:
        raise AppError("UNAUTHORIZED", "로그인이 필요해요.", 401)
    return user


def _locked(user: User) -> None:
    left = guardian_pin.locked_seconds(user)
    if left:
        raise AppError(
            "PIN_LOCKED",
            "비밀번호를 여러 번 틀렸어요. 잠시 뒤에 다시 해 주세요.",
            429,
            detail={"retry_after": left},
            headers={"Retry-After": str(left)},
        )


def _wrong(db: Session, user: User) -> None:
    db.commit()
    _locked(user)  # 방금 다섯 번째로 틀렸으면 잠금으로 알린다
    raise AppError(
        "PIN_WRONG",
        "비밀번호가 맞지 않아요.",
        403,
        detail={"tries_left": guardian_pin.tries_left(user)},
    )


@router.put(
    "/guardian-pin",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERRORS,
    summary="보호자 비밀번호 만들기 · 바꾸기",
)
def set_pin(
    body: GuardianPinSetRequest,
    db: Session = Depends(get_db),
    token: AuthToken = Depends(current_token),
) -> Response:
    """처음이면 그대로 만든다. 이미 있으면 지금 비밀번호(current_pin)가 맞아야 바꾼다 —
    잊었으면 다시 로그인한 뒤 10분 안에는 current_pin 없이 새로 정한다."""

    user = _user(db, token)
    if user.guardian_pin_hash:
        if body.current_pin is None:
            if utcnow() - aware(token.created_at) > FRESH_LOGIN:
                raise AppError(
                    "PIN_REQUIRED",
                    "지금 비밀번호를 넣거나, 다시 로그인한 뒤 새로 정해 주세요.",
                    403,
                )
        else:
            _locked(user)
            if not guardian_pin.check(user, body.current_pin):
                _wrong(db, user)
    user.guardian_pin_hash = guardian_pin.make_hash(body.pin)
    user.guardian_pin_failures = 0
    user.guardian_pin_locked_until = None
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/guardian-pin/check",
    response_model=GuardianPinCheckResponse,
    responses=_ERRORS,
    summary="보호자 비밀번호 확인 — 맞으면 200, 틀리면 403 PIN_WRONG, 다섯 번째는 429 PIN_LOCKED",
)
def check_pin(
    body: GuardianPinCheckRequest,
    db: Session = Depends(get_db),
    token: AuthToken = Depends(current_token),
) -> GuardianPinCheckResponse:
    user = _user(db, token)
    if not user.guardian_pin_hash:
        raise AppError("PIN_NOT_SET", "보호자 비밀번호를 먼저 만들어 주세요.", 409)
    _locked(user)
    if not guardian_pin.check(user, body.pin):
        _wrong(db, user)
    db.commit()
    return GuardianPinCheckResponse()


@router.post(
    "/onboarded",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={401: {"model": ErrorResponse}},
    summary="보호자 소개 카드를 봤다 — 다른 기기에서 다시 띄우지 않는다",
)
def onboarded(db: Session = Depends(get_db), token: AuthToken = Depends(current_token)) -> Response:
    user = _user(db, token)
    if user.onboarded_at is None:
        user.onboarded_at = utcnow()
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
