from datetime import datetime

from pydantic import BaseModel, Field


class GuardianVoiceResponse(BaseModel):
    registered: bool = Field(description="지금 쓸 수 있는 보호자 목소리가 있는지")
    available: bool = Field(description="이 서버가 목소리를 받을 수 있는지 (ElevenLabs 설정)")
    consent_version: str | None = None
    consented_at: datetime | None = None
