"""보호자 나이 확인: 포트원 V2 본인인증(KG이니시스) 결과를 조회해 만 14세 이상인지 본다 (2026-11-09).

프론트가 포트원 브라우저 SDK 로 인증 창을 열고(상점 아이디 · 채널 키는 공개 값), 끝나면 인증 건 번호만 넘긴다.
서버는 그 번호로 포트원 API 를 직접 불러 결과를 확인한다, 프론트가 「통과했다」고 말하는 것을 믿지 않는다.

받은 이름 · 전화번호 · 생년월일 · CI · DI 는 나이를 셈한 뒤 바로 버린다. 남기는 것은 확인한 때 · 방법 이름 ·
인증 건 번호(User.age_*)뿐이다 (pt_docs policy 아동-개인정보-AI안전 3-3).

가입 화면에서 인증한 결과는 계정이 생기기 전이라 「나이 확인증」(10분, 서명)으로 들고 다니다가
계정을 만들 때(소셜 로그인 · 소셜 가입) 낸다. 이미 있는 계정은 로그인한 채로 바로 기록한다.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.models import User
from app.models.mixins import utcnow

log = logging.getLogger(__name__)

METHOD = "portone-inicis"
# 시험에서 httpx.MockTransport 로 바꾼다
_TRANSPORT: httpx.BaseTransport | None = None
_TIMEOUT_SECONDS = 10
# 인증한 지 이보다 오래된 건은 받지 않는다 (남의 오래된 인증 번호를 가져다 쓰지 못하게)
MAX_AGE_MINUTES = 30
AGE_TICKET_SECONDS = 600
_KST = timezone(timedelta(hours=9))
_TICKET_KEY = b"age-ticket\n" + (get_settings().file_url_secret or secrets.token_hex(32)).encode()


def korean_age(birth: date, today: date) -> int:
    """만 나이"""

    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def _fetch(identity_verification_id: str) -> dict:
    s = get_settings()
    if not s.portone_api_secret:
        raise AppError(
            "AGE_CHECK_NOT_CONFIGURED",
            "지금은 본인인증을 할 수 없어요. 잠시 뒤에 다시 해 주세요.",
            503,
        )
    url = f"{s.portone_api_base.rstrip('/')}/identity-verifications/{quote(identity_verification_id, safe='')}"
    params = {"storeId": s.portone_store_id} if s.portone_store_id else None
    try:
        with httpx.Client(transport=_TRANSPORT, timeout=_TIMEOUT_SECONDS) as c:
            r = c.get(
                url, params=params, headers={"Authorization": f"PortOne {s.portone_api_secret}"}
            )
    except httpx.HTTPError as e:
        log.warning("portone identity verification lookup failed: %s", type(e).__name__)
        raise AppError(
            "AGE_CHECK_UNAVAILABLE", "본인인증 결과를 받지 못했어요. 다시 해 주세요.", 502
        ) from e
    if r.status_code == 404:
        raise AppError("AGE_CHECK_FAILED", "본인인증이 끝나지 않았어요. 다시 해 주세요.", 400)
    if r.status_code in (401, 403):
        # 키가 틀렸다, 사용자 탓이 아니다
        log.error("portone rejected the API secret (%s)", r.status_code)
        raise AppError(
            "AGE_CHECK_NOT_CONFIGURED",
            "지금은 본인인증을 할 수 없어요. 잠시 뒤에 다시 해 주세요.",
            503,
        )
    if r.status_code >= 400:
        log.warning("portone identity verification lookup %s", r.status_code)
        raise AppError(
            "AGE_CHECK_UNAVAILABLE", "본인인증 결과를 받지 못했어요. 다시 해 주세요.", 502
        )
    return r.json()


def check_age(identity_verification_id: str, now: datetime | None = None) -> None:
    """인증이 끝났고, 최근이고, 만 14세 이상이면 그냥 돌아온다. 아니면 AppError.
    나이 말고는 아무것도 돌려주지 않는다 (받은 개인정보가 이 함수 밖으로 나가지 않게)"""

    s = get_settings()
    data = _fetch(identity_verification_id)
    if data.get("status") != "VERIFIED":
        raise AppError("AGE_CHECK_FAILED", "본인인증이 끝나지 않았어요. 다시 해 주세요.", 400)
    now = now or utcnow()
    verified_at = data.get("verifiedAt")
    if verified_at:
        at = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
        if now - at > timedelta(minutes=MAX_AGE_MINUTES):
            raise AppError("AGE_CHECK_EXPIRED", "본인인증 시간이 지났어요. 다시 해 주세요.", 400)
    birth_raw = (data.get("verifiedCustomer") or {}).get("birthDate")
    try:
        birth = date.fromisoformat(birth_raw or "")
    except ValueError:
        # KG이니시스 통합인증은 생년월일을 준다. 없으면 나이를 알 수 없으니 통과시키지 않는다
        raise AppError(
            "AGE_CHECK_FAILED", "생년월일을 확인하지 못했어요. 다시 해 주세요.", 400
        ) from None
    if korean_age(birth, now.astimezone(_KST).date()) < s.age_verification_min_age:
        raise AppError(
            "AGE_TOO_YOUNG",
            f"만 {s.age_verification_min_age}세 이상의 보호자 또는 교사만 가입할 수 있어요.",
            403,
        )


def _used(db: Session, identity_verification_id: str, user: User | None = None) -> bool:
    other = db.scalar(select(User).where(User.age_verification_id == identity_verification_id))
    return other is not None and other is not user


def record(db: Session, user: User, identity_verification_id: str) -> None:
    """확인한 사실을 계정에 남긴다. 다른 계정에 이미 쓴 인증 건이면 거절. 호출한 쪽이 commit 한다"""

    if _used(db, identity_verification_id, user):
        raise AppError("AGE_CHECK_USED", "이미 다른 계정에 쓴 본인인증이에요. 다시 해 주세요.", 409)
    user.age_verified_at = utcnow()
    user.age_verification_method = METHOD
    user.age_verification_id = identity_verification_id


# ─── 가입 전 나이 확인증 (계정이 아직 없을 때) ───


def _sig(body: str) -> str:
    return hmac.new(_TICKET_KEY, body.encode(), hashlib.sha256).hexdigest()


def issue_age_ticket(identity_verification_id: str) -> str:
    payload = {"v": identity_verification_id, "exp": int(time.time()) + AGE_TICKET_SECONDS}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    return f"{body}.{_sig(body)}"


def read_age_ticket(ticket: str | None) -> str | None:
    """인증 건 번호. 서명이 틀리거나 기한이 지났으면 None"""

    body, _, sig = (ticket or "").partition(".")
    if not body or not hmac.compare_digest(_sig(body), sig):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body.encode()))
    except ValueError:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload.get("v")


def require_for_new_account(db: Session, user: User, age_ticket: str | None) -> None:
    """새 계정을 만들 때 부른다. 본인인증을 요구하지 않는 환경(로컬, 키 없음)이면 아무것도 안 한다"""

    if not get_settings().age_check_required:
        return
    found = read_age_ticket(age_ticket)
    if found is None:
        raise AppError("AGE_CHECK_REQUIRED", "가입하려면 먼저 본인인증을 해 주세요.", 400)
    record(db, user, found)
