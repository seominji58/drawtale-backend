import pytest

from app.services.jobs import DEFAULT_MOTION, motion_for


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
