from enum import StrEnum

from pydantic import BaseModel, Field


class JointName(StrEnum):
    """Project-standard 15 joints (Meta AnimatedDrawings skeleton without `root`)."""

    hip = "hip"
    torso = "torso"
    neck = "neck"
    right_shoulder = "right_shoulder"
    right_elbow = "right_elbow"
    right_hand = "right_hand"
    left_shoulder = "left_shoulder"
    left_elbow = "left_elbow"
    left_hand = "left_hand"
    right_hip = "right_hip"
    right_knee = "right_knee"
    right_foot = "right_foot"
    left_hip = "left_hip"
    left_knee = "left_knee"
    left_foot = "left_foot"


JOINT_ORDER: list[JointName] = list(JointName)

# All coordinates are pixels of the ORIGINAL uploaded image, origin top-left, x right, y down.
COORDINATE_SPACE = "image_px"


class Joint(BaseModel):
    # 형태마다 관절 이름이 다르다 (2족형은 JointName, 4족형은 right_hip_b … — pt_docs 19). 검사는 validate_full_skeleton
    name: str = Field(min_length=1, max_length=40)
    x: float = Field(ge=0, description="원본 이미지 기준 x (px)")
    y: float = Field(ge=0, description="원본 이미지 기준 y (px)")


class AIJoint(Joint):
    """AI 가 짚은 관절. 사용자 보정값에는 점수가 없다."""

    score: float | None = Field(
        default=None, ge=0, le=1, description="모델이 이 관절을 확신하는 정도 (0~1)"
    )


class BBox(BaseModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class JobStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class JobType(StrEnum):
    analyze = "analyze"
    story = "story"


class ErrorInfo(BaseModel):
    code: str = Field(examples=["AI_TIMEOUT"])
    message: str = Field(examples=["그림 분석 시간이 초과되었어요. 다시 시도해 주세요."])
    detail: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorInfo


def validate_full_skeleton(joints: list[Joint], order: list[str] | None = None) -> list[Joint]:
    """그 형태의 관절이 빠짐없이 한 번씩 있는지. order 를 주지 않으면 2족형(JOINT_ORDER). 형태의 순서로 정렬한다."""
    want = list(order) if order is not None else [str(n) for n in JOINT_ORDER]
    names = [str(j.name) for j in joints]
    if sorted(names) != sorted(want) or len(names) != len(want):
        raise ValueError(f"joints must contain each of the {len(want)} joints exactly once")
    index = {n: i for i, n in enumerate(want)}
    return sorted(joints, key=lambda j: index[str(j.name)])
