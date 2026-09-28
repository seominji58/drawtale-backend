"""Backend <-> AI internal API contract (/internal/v1/*). Owned jointly with A1."""

from pydantic import BaseModel, Field

from app.schemas.common import BBox, Joint


class AIMeta(BaseModel):
    model_version: str = Field(examples=["meta-animated-drawings-pretrained"])
    pipeline_version: str = Field(examples=["0.1.0"])
    coordinate_space: str = Field(examples=["image_px"])
    processing_time_ms: int


class AIHealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    model_version: str
    pipeline_version: str


class AnalyzeRequest(BaseModel):
    request_id: str
    image_url: str = Field(description="원본 이미지 읽기 URL (Blob SAS)")
    mask_upload_url: str | None = Field(
        default=None, description="마스크 PNG를 PUT으로 올릴 URL (Blob SAS)"
    )


class AnalyzeResponse(AIMeta):
    bbox: BBox
    joints: list[Joint]
    mask_uploaded: bool


class RenderRequest(BaseModel):
    request_id: str
    image_url: str
    mask_url: str | None
    joints: list[Joint]
    motion: str = Field(examples=["wave_hello"])
    output_upload_url: str | None = Field(
        default=None, description="결과 애니메이션을 PUT으로 올릴 URL (Blob SAS)"
    )


class RenderResponse(AIMeta):
    format: str = Field(examples=["gif"])
    duration_ms: int
    output_uploaded: bool
