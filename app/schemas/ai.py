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


# --- POST /internal/v1/render (not implemented on the AI side yet) --------------------------


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
    format: str = Field(examples=["mp4"])
    duration_ms: int
    output_uploaded: bool
