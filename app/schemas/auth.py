import uuid

from pydantic import BaseModel, Field

# 프론트 src/legal 의 LEGAL_VERSION. 「agreed」 는 아래 세 가지(이용약관 · 개인정보 수집 · 이용 ·
# 만 14세 이상 보호자 · 교사)에 모두 동의했다는 뜻이다 — 화면이 항목마다 따로 받는다 (2026-11-03)
_CONSENT_VERSION = Field(
    default=None,
    max_length=40,
    description="동의한 약관 판 (예: 2026-11-03-draft). 가입할 때 기록한다",
)


# 가입 화면에서 본인인증을 마치고 받은 나이 확인증 (POST /auth/age-check, 10분, 2026-11-09).
# 새 계정을 만들 때만 본다, 본인인증을 요구하는 환경이면 없을 때 AGE_CHECK_REQUIRED
_AGE_TICKET = Field(default=None, max_length=1000, description="POST /auth/age-check 의 age_ticket (10분)")


class SocialLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=2048, description="제공자가 돌려준 인가 코드")
    redirect_uri: str = Field(
        min_length=1,
        max_length=500,
        description="인가 요청 때 쓴 것과 같은 값",
        examples=["http://localhost:5173/auth/kakao/callback"],
    )
    agreed: bool = Field(
        default=False,
        description="이용약관·개인정보 처리방침에 동의했는지. 처음 가입할 때 true 여야 한다",
    )
    consent_version: str | None = _CONSENT_VERSION
    age_ticket: str | None = _AGE_TICKET


class SocialSignupRequest(BaseModel):
    ticket: str = Field(
        min_length=1,
        max_length=1000,
        description="SIGNUP_REQUIRED 응답의 detail.signup_ticket (10분)",
    )
    agreed: bool = Field(description="이용약관·개인정보 처리방침에 동의했는지. true 여야 한다")
    consent_version: str | None = _CONSENT_VERSION
    age_ticket: str | None = _AGE_TICKET


class AgeCheckRequest(BaseModel):
    identity_verification_id: str = Field(
        min_length=1,
        max_length=100,
        description="포트원 브라우저 SDK requestIdentityVerification 에 넘긴 본인인증 건 ID",
    )


class AgeCheckResponse(BaseModel):
    age_ticket: str = Field(description="가입(소셜 로그인 · 소셜 가입)에 함께 보낸다. 10분")


class AuthResponse(BaseModel):
    token: str = Field(description="Authorization: Bearer 로 보낸다")
    account: str = Field(description="화면에 보일 계정 이름. 이름을 받지 않으므로 제공자 이름이다")


class MeResponse(BaseModel):
    user_id: uuid.UUID
    providers: list[str]
    # 보호자 비밀번호를 만들었는지 · 보호자 소개 카드를 봤는지 (pt_docs 20 3장, 2026-11-06)
    guardian_pin_set: bool = False
    onboarded: bool = False
    # 보호자 나이 확인을 했는지 (2026-11-09). 본인인증을 요구하지 않는 환경이면 늘 true.
    # false 면 프론트가 다른 화면보다 먼저 본인인증을 받는다 (예전에 가입한 계정)
    age_verified: bool = True
