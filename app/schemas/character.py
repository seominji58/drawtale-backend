import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import AIJoint, BBox, JobStatus, Joint


class CharacterCreateResponse(BaseModel):
    character_id: uuid.UUID
    job_id: uuid.UUID
    status: JobStatus


class Analysis(BaseModel):
    bbox: BBox
    mask_url: str | None
    cutout_url: str | None = Field(
        default=None, description="배경을 투명하게 잘라 낸 캐릭터 PNG. 없으면 원본 (2026-11-03)"
    )
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
    # S-05 미리보기 MP4 (Meta 렌더). 준비되기 전 · 실패 · 목 AI 면 null
    preview_url: str | None
    # rendering(만드는 중, 다시 물어볼 것) | ready | failed. 만들지 않으면 null
    preview_status: str | None
    image_width: int
    image_height: int
    analysis: Analysis | None
    joints: list[Joint] | None
    joints_corrected: bool
    # 형태 (pt_docs 19). 관절 이름 · 개수가 형태마다 다르다 — GET /api/v1/forms 의 joints
    form: str = "biped"
    # AI 가 관절을 실제로 찾았나. false 면 형태의 기본 뼈대 — 화면이 관절 고치기로 보낸다. 예전 캐릭터는 null
    joints_found: bool | None = None
    keep_original: bool
    original_deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class JointsUpdateRequest(BaseModel):
    joints: list[Joint]

    @field_validator("joints")
    @classmethod
    def _no_duplicates(cls, v: list[Joint]) -> list[Joint]:
        # 그 캐릭터 형태의 관절인지는 API 가 캐릭터를 읽은 뒤 본다 (validate_full_skeleton(v, forms.joint_order(form)))
        if len({j.name for j in v}) != len(v):
            raise ValueError("joints must not repeat a name")
        return v
