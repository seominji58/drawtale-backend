import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import JobStatus


class StoryCreateRequest(BaseModel):
    character_id: uuid.UUID
    place: str = Field(min_length=1, max_length=50, examples=["학교"])
    problem: str = Field(min_length=1, max_length=50, examples=["친구와 다퉜어요"])
    action: str = Field(min_length=1, max_length=50, examples=["먼저 사과해요"])
    result: str = Field(min_length=1, max_length=50, examples=["다시 사이좋게 놀아요"])


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
