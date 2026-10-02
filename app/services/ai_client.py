"""Client for the private AI service. Backend never imports AI code; it only speaks HTTP."""

import json
import logging
import re
import time

import httpx
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.ai import (
    UNKNOWN,
    AIAnalyzeRaw,
    AnalyzeResult,
    RenderRequest,
    RenderResult,
)
from app.schemas.common import (
    COORDINATE_SPACE,
    AIJoint,
    BBox,
    JointName,
    validate_full_skeleton,
)

log = logging.getLogger(__name__)

# AI error code -> (Backend code, message shown to the user)
_AI_ERRORS: dict[str, tuple[str, str]] = {
    "NO_CHARACTER_DETECTED": (
        "NO_CHARACTER_DETECTED",
        "그림에서 사람 모양 캐릭터를 찾지 못했어요. 캐릭터가 잘 보이게 다시 찍어 주세요.",
    ),
    "INVALID_IMAGE": (
        "INVALID_IMAGE",
        "이미지를 읽을 수 없어요. 다른 그림으로 다시 시도해 주세요.",
    ),
    "INVALID_BBOX": (
        "NO_CHARACTER_DETECTED",
        "그림에서 캐릭터 위치를 찾지 못했어요. 캐릭터가 잘 보이게 다시 찍어 주세요.",
    ),
    # drawtale-ai feat/joint-confidence. 아이에게 다른 그림을 부탁하는 경우다
    "MULTIPLE_CHARACTERS": (
        "MULTIPLE_CHARACTERS",
        "그림에 사람이 여러 명 있어요. 한 명만 그린 그림으로 다시 해 주세요.",
    ),
    "LOW_CONFIDENCE": (
        "LOW_CONFIDENCE",
        "그림에서 사람 모양을 알아보기 어려워요. 사람을 크게 그려서 다시 찍어 주세요.",
    ),
    "MODEL_UNAVAILABLE": (
        "AI_UNAVAILABLE",
        "AI 서버가 준비되지 않았어요. 잠시 후 다시 시도해 주세요.",
    ),
}
_CODE_PREFIX = re.compile(r"^([A-Z][A-Z_]+):")


def _ai_error(message: str | None) -> AppError:
    match = _CODE_PREFIX.match(message or "")
    ai_code = match.group(1) if match else None
    code, user_message = _AI_ERRORS.get(
        ai_code, ("AI_ERROR", "그림 분석 중 오류가 발생했어요. 다시 시도해 주세요.")
    )
    log.warning("AI analyze failed: %s", message)
    return AppError(code, user_message, 502, {"ai_code": ai_code})


def _unit(score: float | None) -> float | None:
    """모델 점수를 0~1 로 자른다. 점수가 없으면 None 그대로 둔다."""
    return None if score is None else min(1.0, max(0.0, score))


def normalize_analyze(raw: AIAnalyzeRaw) -> AnalyzeResult:
    """Convert the AI server's response into the Backend's contract shape."""
    if not raw.success:
        raise _ai_error(raw.message)
    if raw.bbox is None:
        raise AppError("AI_INVALID_RESPONSE", "AI 응답 형식이 올바르지 않아요.", 502)
    try:
        joints = validate_full_skeleton(
            [AIJoint(name=j.name, x=j.x, y=j.y, score=_unit(j.score)) for j in raw.joints]
        )
    except (ValueError, ValidationError) as e:
        log.warning("AI returned an invalid skeleton: %s", e)
        raise AppError("AI_INVALID_RESPONSE", "AI 응답 형식이 올바르지 않아요.", 502) from e
    b = raw.bbox
    return AnalyzeResult(
        bbox=BBox(x=b.left, y=b.top, width=b.right - b.left, height=b.bottom - b.top),
        joints=joints,
        confidence=_unit(raw.confidence),
        request_id=raw.request_id,
        model_version=raw.model_version or UNKNOWN,
        pipeline_version=raw.pipeline_version or UNKNOWN,
        coordinate_space=raw.coordinate_space or UNKNOWN,
        processing_time_ms=raw.processing_time_ms or 0,
    )


class HttpAIClient:
    def __init__(
        self, base_url: str, timeout: float, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    def _post(self, path: str, **kwargs) -> httpx.Response:
        start = time.perf_counter()
        try:
            res = self.client.post(path, **kwargs)
        except httpx.TimeoutException as e:
            message = "그림 분석 시간이 초과되었어요. 다시 시도해 주세요."
            raise AppError("AI_TIMEOUT", message, 504) from e
        except httpx.HTTPError as e:
            raise AppError("AI_UNAVAILABLE", "AI 서버에 연결할 수 없어요.", 503) from e
        elapsed_ms = (time.perf_counter() - start) * 1000
        log.info("AI %s -> %s in %.0f ms", path, res.status_code, elapsed_ms)
        if res.status_code >= 400:
            log.warning("AI %s HTTP %s: %s", path, res.status_code, res.text[:500])
            detail = {"ai_status": res.status_code}
            raise AppError("AI_ERROR", "AI 처리 중 오류가 발생했어요.", 502, detail)
        return res

    def _json(self, res: httpx.Response) -> dict:
        try:
            return res.json()
        except ValueError as e:
            raise AppError("AI_INVALID_RESPONSE", "AI 응답 형식이 올바르지 않아요.", 502) from e

    def analyze(self, image: bytes, filename: str) -> AnalyzeResult:
        res = self._post("/internal/v1/analyze", files={"file": (filename, image)})
        try:
            raw = AIAnalyzeRaw.model_validate(self._json(res))
        except ValidationError as e:
            raise AppError("AI_INVALID_RESPONSE", "AI 응답 형식이 올바르지 않아요.", 502) from e
        return normalize_analyze(raw)

    def render(self, req: RenderRequest) -> RenderResult:
        """성공하면 MP4 본문이, 실패하면 JSON 오류가 돌아온다."""

        res = self._post(
            "/internal/v1/render",
            data={
                "request_id": req.request_id,
                "joints": json.dumps([j.model_dump(mode="json") for j in req.joints]),
                "motion": req.motion,
            },
        )
        if res.headers.get("content-type", "").startswith("application/json"):
            raise _ai_error(self._json(res).get("message"))

        return RenderResult(
            content=res.content,
            content_type=res.headers.get("content-type", "video/mp4"),
            processing_time_ms=int(res.headers.get("X-Processing-Time-Ms", 0)),
            model_version=res.headers.get("X-Model-Version", UNKNOWN),
        )

    def delete_session(self, request_id: str) -> None:
        """AI 서버의 임시 파일을 정리한다. 실패해도 서비스에는 영향이 없다."""

        try:
            self.client.delete(f"/internal/v1/sessions/{request_id}")
        except httpx.HTTPError:
            log.warning("could not delete AI session %s", request_id)


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

    def delete_session(self, request_id: str) -> None:
        return None

    def analyze(self, image: bytes, filename: str) -> AnalyzeResult:
        start = time.perf_counter()
        w, h = self.image_size or (512, 512)
        bbox = BBox(x=w * 0.2, y=h * 0.1, width=w * 0.6, height=h * 0.8)
        joints = [
            AIJoint(
                name=name,
                x=round(bbox.x + rx * bbox.width, 1),
                y=round(bbox.y + ry * bbox.height, 1),
                score=0.9,
            )
            for name, (rx, ry) in _MOCK_POSE.items()
        ]
        return AnalyzeResult(
            bbox=bbox,
            joints=joints,
            confidence=0.95,
            request_id="mock-session",
            model_version=self.model_version,
            pipeline_version=self.pipeline_version,
            coordinate_space=COORDINATE_SPACE,
            processing_time_ms=int((time.perf_counter() - start) * 1000),
        )

    def render(self, req: RenderRequest) -> RenderResult:
        # 실제 MP4 대신 빈 내용을 돌려준다. 호출 흐름만 검증하는 용도다.
        return RenderResult(
            content=b"",
            content_type="video/mp4",
            processing_time_ms=0,
            model_version=self.model_version,
        )


def get_ai_client(image_size: tuple[int, int] | None = None) -> HttpAIClient | MockAIClient:
    s = get_settings()
    if s.ai_use_mock:
        return MockAIClient(image_size)
    return HttpAIClient(s.ai_service_url, s.ai_timeout_seconds)
