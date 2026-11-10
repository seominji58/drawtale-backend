from datetime import datetime

from pydantic import BaseModel, Field


class GuardianVoiceResponse(BaseModel):
    registered: bool = Field(description="지금 쓸 수 있는 보호자 목소리가 있는지")
    available: bool = Field(description="이 서버가 목소리를 받을 수 있는지 (녹음 목소리 업체 설정)")
    provider: str = Field(
        default="elevenlabs",
        description="새로 등록하면 녹음을 보내는 곳: elevenlabs (ElevenLabs, 미국) | qwen (Modal, 미국 — 자체 서버 drawtale-tts). 동의 화면 문구가 이것을 따른다",
    )
    consent_version: str | None = None
    consented_at: datetime | None = None
