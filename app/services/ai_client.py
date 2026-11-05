"""Client for the private AI service. Backend never imports AI code; it only speaks HTTP."""

import base64
import json
import logging
import re
import time

import httpx
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.errors import AppError
from app.services import forms
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
            [AIJoint(name=j.name, x=j.x, y=j.y, score=_unit(j.score)) for j in raw.joints],
            forms.joint_order(raw.form),
        )
    except (ValueError, ValidationError, AppError) as e:
        log.warning("AI returned an invalid skeleton: %s", e)
        raise AppError("AI_INVALID_RESPONSE", "AI 응답 형식이 올바르지 않아요.", 502) from e
    b = raw.bbox
    return AnalyzeResult(
        bbox=BBox(x=b.left, y=b.top, width=b.right - b.left, height=b.bottom - b.top),
        joints=joints,
        confidence=_unit(raw.confidence),
        request_id=raw.request_id,
        form=raw.form,
        joints_found=raw.joints_found,
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

    def forms(self) -> list[dict]:
        """형태 정의 전부 (AI GET /internal/v1/forms, pt_docs 19). 예전 AI 서버면 404 → 호출하는 쪽이 2족형만 쓴다."""

        res = self.client.get("/internal/v1/forms")
        res.raise_for_status()
        return list(res.json()["forms"])

    def analyze(self, image: bytes, filename: str, form: str = "biped", facing: str | None = None) -> AnalyzeResult:
        # 2족형은 form 을 보내지 않는다 — 형태를 모르는 예전 AI 서버도 그대로 돈다.
        # facing: 옆모습 형태의 머리 쪽 (left · right). 없으면 AI 가 추측한다 (「얼굴이 반대쪽이에요」, 11/4)
        data = {**({"form": form} if form != "biped" else {}), **({"facing": facing} if facing else {})}
        res = self._post("/internal/v1/analyze", files={"file": (filename, image)}, **({"data": data} if data else {}))
        try:
            raw = AIAnalyzeRaw.model_validate(self._json(res))
        except ValidationError as e:
            raise AppError("AI_INVALID_RESPONSE", "AI 응답 형식이 올바르지 않아요.", 502) from e
        return normalize_analyze(raw)

    def cutout(self, request_id: str) -> bytes | None:
        """분석할 때 만들어 둔, 배경을 잘라 낸 PNG. 없거나 실패하면 None (원본으로 대신한다)."""

        try:
            res = self.client.get(f"/internal/v1/sessions/{request_id}/cutout")
        except httpx.HTTPError:
            log.warning("could not fetch cutout for %s", request_id)
            return None
        if res.status_code != 200 or not res.headers.get("content-type", "").startswith("image/"):
            return None
        return res.content

    def render(self, req: RenderRequest) -> RenderResult:
        """성공하면 MP4 본문이, 실패하면 JSON 오류가 돌아온다."""

        res = self._post(
            "/internal/v1/render",
            data={
                "request_id": req.request_id,
                "joints": json.dumps([j.model_dump(mode="json") for j in req.joints]),
                "motion": req.motion,
                **({"max_frames": req.max_frames} if req.max_frames else {}),
                **({"sprite": "true"} if req.sprite else {}),
            },
        )
        if res.headers.get("content-type", "").startswith("application/json"):
            body = self._json(res)
            if "video" not in body:
                raise _ai_error(body.get("message"))
            # sprite=true 응답: MP4 와 투명 프레임 묶음을 함께 (drawtale-ai renderer, 2026-11-03)
            return RenderResult(
                content=base64.b64decode(body["video"]),
                sprite=base64.b64decode(body["sprite"]) if body.get("sprite") else None,
                sprite_frames=int(body.get("sprite_frames") or 0),
                sprite_box=body.get("sprite_box"),
                sprite_in_place=bool(body.get("sprite_in_place")),
                processing_time_ms=int(res.headers.get("X-Processing-Time-Ms", 0)),
                model_version=res.headers.get("X-Model-Version", UNKNOWN),
            )

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


# 목 AI 의 4족형 (drawtale-ai forms/quadruped.json 과 관절 이름 · 부모 · 동작이 같다). 옆모습, 머리는 오른쪽
_MOCK_QUADRUPED_POSE: dict[str, tuple[float, float]] = {
    "hip": (0.30, 0.45), "torso": (0.70, 0.35), "neck": (0.76, 0.20),
    "right_hip_b": (0.32, 0.55), "right_knee_b": (0.32, 0.75), "right_foot_b": (0.32, 0.96),
    "left_hip_b": (0.20, 0.55), "left_knee_b": (0.20, 0.75), "left_foot_b": (0.20, 0.96),
    "right_hip_f": (0.80, 0.55), "right_knee_f": (0.80, 0.75), "right_foot_f": (0.80, 0.96),
    "left_hip_f": (0.66, 0.55), "left_knee_f": (0.66, 0.75), "left_foot_f": (0.66, 0.96),
    "tail": (0.14, 0.40), "tail_mid": (0.08, 0.30), "tail_tip": (0.04, 0.18),
}
_MOCK_QUADRUPED_FORM: dict = {
    "id": "quadruped", "label": "4족형", "group": "nonhumanoid", "kid_label": "네 발로 걷는 친구",
    "examples": ["강아지", "고양이", "공룡"], "view": "side", "ready": True,
    "joints": [{"name": n, "parent": p, "label": n} for n, p in (
        ("hip", "root"), ("torso", "hip"), ("neck", "torso"),
        ("right_hip_b", "root"), ("right_knee_b", "right_hip_b"), ("right_foot_b", "right_knee_b"),
        ("left_hip_b", "root"), ("left_knee_b", "left_hip_b"), ("left_foot_b", "left_knee_b"),
        ("right_hip_f", "root"), ("right_knee_f", "right_hip_f"), ("right_foot_f", "right_knee_f"),
        ("left_hip_f", "root"), ("left_knee_f", "left_hip_f"), ("left_foot_f", "left_knee_f"),
        ("tail", "hip"), ("tail_mid", "tail"), ("tail_tip", "tail_mid"))],
    "motions": {"walk": "quadruped/walk.yaml", "run": "quadruped/run.yaml",
                "jumping_gentle": "quadruped/jumping_gentle.yaml", "breathe": "quadruped/breathe.yaml",
                "shiver": "quadruped/shiver.yaml", "search": "quadruped/breathe.yaml"},
    "hero": {"fit": "width", "facing": "auto"},
}


class MockAIClient:
    """Returns contract-shaped fake results so Backend/Frontend work before the AI is ready."""

    model_version = "mock-v1"
    pipeline_version = "mock"

    def __init__(self, image_size: tuple[int, int] | None = None) -> None:
        self.image_size = image_size

    def delete_session(self, request_id: str) -> None:
        return None

    def cutout(self, request_id: str) -> bytes | None:
        return None

    def forms(self) -> list[dict]:
        """2족형 사본 + 시험용 4족형 (관절 이름 · 동작만 AI 와 같게)."""
        return [forms._BIPED, _MOCK_QUADRUPED_FORM]

    def analyze(self, image: bytes, filename: str, form: str = "biped", facing: str | None = None) -> AnalyzeResult:
        start = time.perf_counter()
        w, h = self.image_size or (512, 512)
        bbox = BBox(x=w * 0.2, y=h * 0.1, width=w * 0.6, height=h * 0.8)
        pose = _MOCK_QUADRUPED_POSE if form == "quadruped" else _MOCK_POSE
        if form == "quadruped" and facing == "left":     # 목 4족형은 머리가 오른쪽 — 왼쪽이면 좌우로
            pose = {n: (1 - x, y) for n, (x, y) in pose.items()}
        joints = [
            AIJoint(
                name=name,
                x=round(bbox.x + rx * bbox.width, 1),
                y=round(bbox.y + ry * bbox.height, 1),
                score=0.9,
            )
            for name, (rx, ry) in pose.items()
        ]
        return AnalyzeResult(
            bbox=bbox,
            joints=joints,
            confidence=0.95,
            request_id="mock-session",
            form=form,
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
