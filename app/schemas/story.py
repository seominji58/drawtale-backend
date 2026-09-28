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
