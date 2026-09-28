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
    name: JointName
    x: float = Field(ge=0, description="원본 이미지 기준 x (px)")
    y: float = Field(ge=0, description="원본 이미지 기준 y (px)")


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


def validate_full_skeleton(joints: list[Joint]) -> list[Joint]:
    names = [j.name for j in joints]
    if sorted(names) != sorted(JOINT_ORDER) or len(names) != len(JOINT_ORDER):
        raise ValueError(f"joints must contain each of the {len(JOINT_ORDER)} joints exactly once")
    order = {n: i for i, n in enumerate(JOINT_ORDER)}
    return sorted(joints, key=lambda j: order[j.name])
