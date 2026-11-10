from pydantic import BaseModel, Field

_PIN_PATTERN = r"^[0-9]{4}$"


class GuardianPinSetRequest(BaseModel):
    pin: str = Field(pattern=_PIN_PATTERN, description="숫자 4자리", examples=["2580"])
    current_pin: str | None = Field(
        default=None,
        pattern=_PIN_PATTERN,
        description="이미 있으면 지금 비밀번호. 잊었으면 다시 로그인한 뒤 10분 안에는 없어도 된다",
    )


class GuardianPinCheckRequest(BaseModel):
    pin: str = Field(pattern=_PIN_PATTERN, description="숫자 4자리", examples=["2580"])


class GuardianPinCheckResponse(BaseModel):
    ok: bool = True
