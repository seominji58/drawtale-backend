import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.common import JobStatus


class VoiceMode(StrEnum):
    """S-13 「읽어주기」. 나레이터 하나로 읽을지, 대사를 등장인물 목소리로 읽을지."""

    narration = "narration"
    characters = "characters"


class ProtagonistVoice(StrEnum):
    """S-13 「주인공 목소리」. 캐릭터 목소리 판에서 주인공 「나」의 대사를 읽는 목소리."""

    girl = "girl"
    boy = "boy"


class StoryCreateRequest(BaseModel):
    character_id: uuid.UUID
    place: str = Field(min_length=1, max_length=50, examples=["학교"])
    problem: str = Field(min_length=1, max_length=50, examples=["친구와 다퉜어요"])
    action: str = Field(min_length=1, max_length=50, examples=["먼저 사과해요"])
    result: str = Field(min_length=1, max_length=50, examples=["다시 사이좋게 놀아요"])
    voice_mode: VoiceMode = Field(
        default=VoiceMode.narration,
        description=(
            "narration: 나레이터 하나 · "
            "characters: 대사를 인물 목소리로 (이야기 글에 대사가 들어간다)"
        ),
    )
    protagonist_voice: ProtagonistVoice = Field(
        default=ProtagonistVoice.girl,
        description="캐릭터 목소리 판에서 주인공 대사 목소리. girl: 여자아이 · boy: 남자아이",
    )
    protagonist_name: str | None = Field(
        default=None,
        max_length=10,
        examples=["토토"],
        description=(
            "아이가 지은 주인공 이름. 캐릭터 목소리 판의 나레이션이 이 이름으로 3인칭으로 부른다"
        ),
    )


class StoryCreateResponse(BaseModel):
    story_id: uuid.UUID
    job_id: uuid.UUID
    status: JobStatus


class StoryResponse(BaseModel):
    id: uuid.UUID
    character_id: uuid.UUID
    status: JobStatus
    place: str
    problem: str
    action: str
    result: str
    voice_mode: VoiceMode
    protagonist_voice: ProtagonistVoice
    protagonist_name: str | None
    text: str | None
    audio_url: str | None
    animation_url: str | None
    created_at: datetime
    updated_at: datetime


class ActivityCreateRequest(BaseModel):
    """S-10 순서 맞추기 한 번의 결과."""

    attempts: int = Field(ge=1, le=100, description="「다 했어요」를 누른 횟수 (맞힌 마지막 포함)")
    completed: bool = Field(description="맞히고 S-11 로 갔는지")
    card_count: int = Field(ge=2, le=4, description="놓은 문장 카드 수")
    level: int | None = Field(default=None, ge=1, le=3, description="지원 수준")


class ActivityResponse(ActivityCreateRequest):
    id: uuid.UUID
    story_id: uuid.UUID
    created_at: datetime
