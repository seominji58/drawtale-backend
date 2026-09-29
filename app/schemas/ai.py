"""Backend <-> AI internal API contract (/internal/v1/*). Owned jointly with A1.

`AIAnalyzeRaw` mirrors what the AI server returns today; `AnalyzeResult` is the normalized
shape the rest of the Backend uses. Only `app/services/ai_client.py` knows about the raw format.
"""

from pydantic import BaseModel, Field

from app.schemas.common import BBox, Joint

UNKNOWN = "unknown"


class AIMeta(BaseModel):
    model_version: str = Field(examples=["meta-animated-drawings-pretrained"])
    pipeline_version: str = Field(examples=["0.1.0"])
    coordinate_space: str = Field(examples=["image_px"])
    processing_time_ms: int


class AIHealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    model_loaded: bool | None = None
    model_version: str | None = None
    pipeline_version: str | None = None


# --- POST /internal/v1/analyze (multipart `file`) -------------------------------------------


class AIBoxRaw(BaseModel):
    left: float
    top: float
    right: float
    bottom: float


class AIJointRaw(BaseModel):
    name: str
    x: float
    y: float


class AIMaskRaw(BaseModel):
    width: int
    height: int


class AIAnalyzeRaw(BaseModel):
    success: bool
    request_id: str | None = None
    bbox: AIBoxRaw | None = None
    joints: list[AIJointRaw] = Field(default_factory=list)
    mask: AIMaskRaw | None = None
    message: str | None = None
    # Required by the contract; optional here until the AI server sends them.
    model_version: str | None = None
    pipeline_version: str | None = None
    coordinate_space: str | None = None
    processing_time_ms: int | None = None


class AnalyzeResult(AIMeta):
    bbox: BBox
    joints: list[Joint]
    request_id: str | None = None


# --- POST /internal/v1/render (not implemented on the AI side yet) --------------------------


class RenderRequest(BaseModel):
    """AI 서버에 보내는 렌더링 요청. joints는 사용자 보정이 반영된 현재 관절이다."""

    request_id: str = Field(description="analyze 응답의 request_id")
    joints: list[Joint]
    motion: str = Field(default="wave_hello", examples=["wave_hello"])


class RenderResult(BaseModel):
    """AI 서버가 돌려준 MP4 내용과 메타데이터."""

    content: bytes
    content_type: str = "video/mp4"
    processing_time_ms: int = 0
    model_version: str = UNKNOWN
