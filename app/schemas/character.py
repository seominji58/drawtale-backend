import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import AIJoint, BBox, JobStatus, Joint, validate_full_skeleton


class CharacterCreateResponse(BaseModel):
    character_id: uuid.UUID
    job_id: uuid.UUID
    status: JobStatus


class Analysis(BaseModel):
    bbox: BBox
    mask_url: str | None
    # AI 가 처음 짚은 관절. 관절마다 score 가 있다 (예전 분석이면 null)
    joints: list[AIJoint]
    confidence: float | None = Field(
        default=None, description="캐릭터 검출 점수 (0~1). 예전 분석이면 null"
    )
    model_version: str
    pipeline_version: str
    coordinate_space: str
    processing_time_ms: int


class CharacterResponse(BaseModel):
    id: uuid.UUID
    status: JobStatus
    # 원본을 지웠으면 null
    image_url: str | None
    image_width: int
    image_height: int
    analysis: Analysis | None
    joints: list[Joint] | None
    joints_corrected: bool
    keep_original: bool
    original_deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class JointsUpdateRequest(BaseModel):
    joints: list[Joint]

    @field_validator("joints")
    @classmethod
    def _full_skeleton(cls, v: list[Joint]) -> list[Joint]:
        return validate_full_skeleton(v)
