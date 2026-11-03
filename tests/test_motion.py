import pytest

from app.core.errors import AppError
from app.schemas.ai import RenderRequest, RenderResult
from app.schemas.common import JOINT_ORDER, Joint
from app.services.jobs import DEFAULT_MOTION, motion_for, render_with_fallback


@pytest.mark.parametrize(
    "action,motion",
    [
        # 행동 카드 16장 (pt_docs 05 S-07, 2026-10-28 4차). 카드의 동작 열을 따른다 (2026-11-03)
        ("툭툭 털고 달려갔어요", "run"),
        ("어른을 불렀어요", "wave_hello_gentle"),
        ("천천히 다시 걸었어요", "walk"),
        ("잠깐 쉬며 기다렸어요", "wave_hello_gentle"),
        ("다가가서 도와줬어요", "walk"),
        ("손을 흔들어 웃게 했어요", "wave_hello_gentle"),
        ("어른에게 달려갔어요", "run"),
        ("옆에서 기다려 줬어요", "wave_hello_gentle"),
        ("그 자리에서 기다렸어요", "wave_hello_gentle"),
        ("어른에게 물어봤어요", "wave_hello_gentle"),
        ("주위를 살피며 걸었어요", "walk"),
        ("표지판으로 달려갔어요", "run"),
        ("숨었어요", "wave_hello_gentle"),
        ("밝은 곳으로 달려갔어요", "run"),
        ("살금살금 걸어갔어요", "walk"),
        ("가만히 기다렸어요", "wave_hello_gentle"),
        # earlier example phrases
        ("먼저 사과해요", "wave_hello_gentle"),
        ("신나게 춤을 춰요", "dab_gentle"),
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


@pytest.mark.parametrize("motion", ["walk", "run", "dab_gentle"])
def test_new_motions_fall_back_to_jump_when_ai_is_older(motion):
    # 백엔드가 AI 보다 먼저 배포돼도 이야기가 실패하지 않는다 (2026-11-03)
    ai = _AI({"jumping_gentle"})
    assert render_with_fallback(ai, _req(motion)).content == b"mp4"
    assert ai.asked == [motion, "jumping_gentle"]


def test_page_motions_use_every_registered_motion():
    from app.services.jobs import PAGE_MOTIONS, PRERENDER_MOTIONS

    assert PAGE_MOTIONS["walk"] == "walk" and PAGE_MOTIONS["run"] == "run"
    assert PAGE_MOTIONS["dance"] == "dab_gentle" and PAGE_MOTIONS["idle"] is None
    # 쪽에 나오는 동작(손 흔들기는 미리보기가 맡는다)은 모두 미리 그려 둔다
    assert {m for m in PAGE_MOTIONS.values() if m} - {"wave_hello_gentle"} <= set(PRERENDER_MOTIONS)
