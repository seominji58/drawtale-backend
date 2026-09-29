import uuid

from pydantic import BaseModel, Field


class SocialLoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=2048, description="제공자가 돌려준 인가 코드")
    redirect_uri: str = Field(
        min_length=1,
        max_length=500,
        description="인가 요청 때 쓴 것과 같은 값",
        examples=["http://localhost:5173/auth/kakao/callback"],
    )
    state: str | None = Field(
        default=None, max_length=200, description="인가 요청의 state. 네이버는 토큰 교환에 필요하다"
    )
    agreed: bool = Field(
        default=False,
        description="이용약관·개인정보 처리방침에 동의했는지. 처음 가입할 때 true 여야 한다",
    )


class AuthResponse(BaseModel):
    token: str = Field(description="Authorization: Bearer 로 보낸다")
    account: str = Field(description="화면에 보일 계정 이름. 이름을 받지 않으므로 제공자 이름이다")


class MeResponse(BaseModel):
    user_id: uuid.UUID
    providers: list[str]
