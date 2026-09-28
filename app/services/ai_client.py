"""Client for the private AI service. Backend never imports AI code; it only speaks HTTP."""

import time

import httpx

from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.ai import AnalyzeRequest, AnalyzeResponse, RenderRequest, RenderResponse
from app.schemas.common import COORDINATE_SPACE, BBox, Joint, JointName


class HttpAIClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, body: dict) -> dict:
        try:
            res = httpx.post(f"{self.base_url}{path}", json=body, timeout=self.timeout)
        except httpx.TimeoutException as e:
            raise AppError("AI_TIMEOUT", "그림 분석 시간이 초과되었어요.", 504) from e
        except httpx.HTTPError as e:
            raise AppError("AI_UNAVAILABLE", "AI 서버에 연결할 수 없어요.", 503) from e
        if res.status_code >= 400:
            try:
                err = res.json().get("error", {})
            except ValueError:
                err = {}
            raise AppError(
                err.get("code", "AI_ERROR"),
                err.get("message", "AI 처리 중 오류가 발생했어요."),
                502,
                {"ai_status": res.status_code},
            )
        return res.json()

    def analyze(self, req: AnalyzeRequest) -> AnalyzeResponse:
        return AnalyzeResponse.model_validate(self._post("/internal/v1/analyze", req.model_dump()))

    def render(self, req: RenderRequest) -> RenderResponse:
        return RenderResponse.model_validate(self._post("/internal/v1/render", req.model_dump()))


# T-pose as ratios inside the bbox: (x, y)
_MOCK_POSE: dict[JointName, tuple[float, float]] = {
    JointName.hip: (0.50, 0.55),
    JointName.torso: (0.50, 0.38),
    JointName.neck: (0.50, 0.22),
    JointName.right_shoulder: (0.38, 0.25),
    JointName.right_elbow: (0.22, 0.32),
    JointName.right_hand: (0.08, 0.38),
    JointName.left_shoulder: (0.62, 0.25),
    JointName.left_elbow: (0.78, 0.32),
    JointName.left_hand: (0.92, 0.38),
    JointName.right_hip: (0.42, 0.57),
    JointName.right_knee: (0.40, 0.76),
    JointName.right_foot: (0.38, 0.96),
    JointName.left_hip: (0.58, 0.57),
    JointName.left_knee: (0.60, 0.76),
    JointName.left_foot: (0.62, 0.96),
}


class MockAIClient:
    """Returns contract-shaped fake results so Backend/Frontend work before the AI is ready."""

    model_version = "mock-v1"
    pipeline_version = "mock"

    def __init__(self, image_size: tuple[int, int] | None = None) -> None:
        self.image_size = image_size

    def analyze(self, req: AnalyzeRequest) -> AnalyzeResponse:
        start = time.perf_counter()
        w, h = self.image_size or (512, 512)
        bbox = BBox(x=w * 0.2, y=h * 0.1, width=w * 0.6, height=h * 0.8)
        joints = [
            Joint(
                name=name,
                x=round(bbox.x + rx * bbox.width, 1),
                y=round(bbox.y + ry * bbox.height, 1),
            )
            for name, (rx, ry) in _MOCK_POSE.items()
        ]
        return AnalyzeResponse(
            bbox=bbox,
            joints=joints,
            mask_uploaded=False,
            model_version=self.model_version,
            pipeline_version=self.pipeline_version,
            coordinate_space=COORDINATE_SPACE,
            processing_time_ms=int((time.perf_counter() - start) * 1000),
        )

    def render(self, req: RenderRequest) -> RenderResponse:
        return RenderResponse(
            format="png",
            duration_ms=0,
            output_uploaded=False,
            model_version=self.model_version,
            pipeline_version=self.pipeline_version,
            coordinate_space=COORDINATE_SPACE,
            processing_time_ms=0,
        )


def get_ai_client(image_size: tuple[int, int] | None = None) -> HttpAIClient | MockAIClient:
    s = get_settings()
    if s.ai_use_mock:
        return MockAIClient(image_size)
    return HttpAIClient(s.ai_service_url, s.ai_timeout_seconds)
