import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.schemas.common import BBox, JobStatus, Joint, validate_full_skeleton


class CharacterCreateResponse(BaseModel):
    character_id: uuid.UUID
    job_id: uuid.UUID
    status: JobStatus


class Analysis(BaseModel):
    bbox: BBox
    mask_url: str | None
    joints: list[Joint]
    model_version: str
    pipeline_version: str
    coordinate_space: str
    processing_time_ms: int


class CharacterResponse(BaseModel):
    id: uuid.UUID
    status: JobStatus
    image_url: str
    image_width: int
    image_height: int
    analysis: Analysis | None
    joints: list[Joint] | None
    joints_corrected: bool
    created_at: datetime
    updated_at: datetime


class JointsUpdateRequest(BaseModel):
    joints: list[Joint]

    @field_validator("joints")
    @classmethod
    def _full_skeleton(cls, v: list[Joint]) -> list[Joint]:
        return validate_full_skeleton(v)
