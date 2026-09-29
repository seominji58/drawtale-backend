import pytest

from app.core.errors import AppError
from app.schemas.ai import RenderRequest, RenderResult
from app.schemas.common import JOINT_ORDER, Joint
from app.services.jobs import DEFAULT_MOTION, motion_for, render_with_fallback


@pytest.mark.parametrize(
    "action,motion",
    [
        # Frontend action choices (src/features/story/choices.ts)
        ("물어봤어요", "wave_hello_gentle"),
        ("달려갔어요", "jumping_gentle"),
        ("숨었어요", "wave_hello_gentle"),
        ("도와줬어요", "wave_hello_gentle"),
        ("기다렸어요", "wave_hello_gentle"),
        ("크게 불렀어요", "wave_hello_gentle"),
        # earlier example phrases
        ("먼저 사과해요", "wave_hello_gentle"),
        ("신나게 춤을 춰요", "jumping_gentle"),
    ],
)
def test_motion_for_frontend_choices(action, motion):
    assert motion_for(action) == motion


def test_only_gentle_motions_are_used():
    # Original motions squash arms drawn against the body (drawtale-ai README)
    assert DEFAULT_MOTION.endswith("_gentle")
    assert motion_for("아무 말") == DEFAULT_MOTION


class _AI:
    """Remembers requested motions; knows only the ones given."""

    def __init__(self, known):
        self.known, self.asked = known, []

    def render(self, req):
        self.asked.append(req.motion)
        if req.motion not in self.known:
            raise AppError("AI_ERROR", "x", 502, {"ai_code": "UNKNOWN_MOTION"})
        return RenderResult(
            content=b"mp4", content_type="video/mp4", processing_time_ms=1, model_version="m"
        )


def _req(motion):
    joints = [Joint(name=n, x=1, y=1) for n in JOINT_ORDER]
    return RenderRequest(request_id="r", joints=joints, motion=motion)


def test_falls_back_when_ai_lacks_gentle_motions():
    ai = _AI({"wave_hello"})
    assert render_with_fallback(ai, _req("wave_hello_gentle")).content == b"mp4"
    assert ai.asked == ["wave_hello_gentle", "wave_hello"]


def test_uses_gentle_when_ai_has_it():
    ai = _AI({"wave_hello_gentle"})
    render_with_fallback(ai, _req("wave_hello_gentle"))
    assert ai.asked == ["wave_hello_gentle"]


def test_other_ai_errors_are_not_swallowed():
    ai = _AI(set())
    with pytest.raises(AppError):
        render_with_fallback(ai, _req("wave_hello"))
