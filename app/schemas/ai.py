"""Backend <-> AI internal API contract (/internal/v1/*). Owned jointly with A1.

`AIAnalyzeRaw` mirrors what the AI server returns today; `AnalyzeResult` is the normalized
shape the rest of the Backend uses. Only `app/services/ai_client.py` knows about the raw format.
"""

from pydantic import BaseModel, Field

from app.schemas.common import AIJoint, BBox, Joint

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
    score: float | None = None


class AIMaskRaw(BaseModel):
    width: int
    height: int


class AIAnalyzeRaw(BaseModel):
    success: bool
    request_id: str | None = None
    bbox: AIBoxRaw | None = None
    joints: list[AIJointRaw] = Field(default_factory=list)
    # 캐릭터 검출 점수. 이 값을 보내지 않는 AI 서버도 있다
    confidence: float | None = None
    mask: AIMaskRaw | None = None
    message: str | None = None
    # Required by the contract; optional here until the AI server sends them.
    model_version: str | None = None
    pipeline_version: str | None = None
    coordinate_space: str | None = None
    processing_time_ms: int | None = None


class AnalyzeResult(AIMeta):
    bbox: BBox
    joints: list[AIJoint]
    confidence: float | None = None
    request_id: str | None = None


# --- POST /internal/v1/render (not implemented on the AI side yet) --------------------------


class RenderRequest(BaseModel):
    """AI 서버에 보내는 렌더링 요청. joints는 사용자 보정이 반영된 현재 관절이다."""

    request_id: str = Field(description="analyze 응답의 request_id")
    joints: list[Joint]
    motion: str = Field(default="wave_hello_gentle", examples=["wave_hello_gentle"])
    # 프레임 상한 (S-05 미리보기). **AI 서버가 아직 이 항목을 받지 않아 버려진다** —
    # /internal/v1/render 는 request_id · joints · motion 만 받는다 (2026-10-20 확인).
    # 동작 파일이 짧아져 상한 없이도 8초 안팎이라 급하지 않다
    max_frames: int | None = None


class RenderResult(BaseModel):
    """AI 서버가 돌려준 MP4 내용과 메타데이터."""

    content: bytes
    content_type: str = "video/mp4"
    processing_time_ms: int = 0
    model_version: str = UNKNOWN
