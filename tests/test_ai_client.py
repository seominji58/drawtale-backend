import httpx
import pytest

from app.core.errors import AppError
from app.schemas.ai import RenderRequest
from app.schemas.common import JOINT_ORDER
from app.services.ai_client import HttpAIClient

# Response shape the A1 AI server returns today (drawtale-ai runtime/ai_server/schemas.py)
_A1_OK = {
    "success": True,
    "bbox": {"left": 10, "top": 20, "right": 210, "bottom": 420},
    "joints": [{"name": n.value, "x": 100 + i, "y": 200 + i} for i, n in enumerate(JOINT_ORDER)],
    "mask": {"width": 200, "height": 400},
    "message": "Analysis complete",
}


def _client(handler) -> HttpAIClient:
    return HttpAIClient("http://ai:8001", timeout=5, transport=httpx.MockTransport(handler))


def test_analyze_normalizes_a1_response():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["multipart"] = b'name="file"' in request.content
        return httpx.Response(200, json=_A1_OK)

    res = _client(handler).analyze(b"png-bytes", "draw.png")

    assert seen == {"path": "/internal/v1/analyze", "multipart": True}
    assert res.bbox.model_dump() == {"x": 10, "y": 20, "width": 200, "height": 400}
    assert [j.name for j in res.joints] == JOINT_ORDER
    assert res.model_version == "unknown"


def test_analyze_keeps_scores():
    joints = [{**j, "score": 0.5} for j in _A1_OK["joints"]]
    joints[2]["score"] = 1.02  # 히트맵 최댓값이 1을 살짝 넘기도 한다
    body = {**_A1_OK, "joints": joints, "confidence": 0.81}
    res = _client(lambda r: httpx.Response(200, json=body)).analyze(b"x", "draw.png")

    assert res.confidence == 0.81
    assert [j.score for j in res.joints][:3] == [0.5, 0.5, 1.0]


def test_analyze_without_scores():
    res = _client(lambda r: httpx.Response(200, json=_A1_OK)).analyze(b"x", "draw.png")

    assert res.confidence is None
    assert all(j.score is None for j in res.joints)


def test_analyze_maps_ai_error_code():
    body = {"success": False, "message": "NO_CHARACTER_DETECTED: No drawn humanoid detected"}
    client = _client(lambda r: httpx.Response(200, json=body))

    with pytest.raises(AppError) as e:
        client.analyze(b"x", "draw.png")

    assert e.value.code == "NO_CHARACTER_DETECTED"
    assert "캐릭터" in e.value.message


@pytest.mark.parametrize("ai_code", ["MULTIPLE_CHARACTERS", "LOW_CONFIDENCE"])
def test_analyze_maps_drawing_rejections(ai_code):
    body = {"success": False, "message": f"{ai_code}: details"}
    client = _client(lambda r: httpx.Response(200, json=body))

    with pytest.raises(AppError) as e:
        client.analyze(b"x", "draw.png")

    assert e.value.code == ai_code
    assert "다시" in e.value.message


def test_analyze_hides_unknown_ai_error():
    body = {"success": False, "message": "Traceback: something internal"}
    client = _client(lambda r: httpx.Response(200, json=body))

    with pytest.raises(AppError) as e:
        client.analyze(b"x", "draw.png")

    assert e.value.code == "AI_ERROR"
    assert "Traceback" not in e.value.message


def test_analyze_rejects_incomplete_skeleton():
    body = {**_A1_OK, "joints": _A1_OK["joints"][:14]}
    client = _client(lambda r: httpx.Response(200, json=body))

    with pytest.raises(AppError) as e:
        client.analyze(b"x", "draw.png")

    assert e.value.code == "AI_INVALID_RESPONSE"


def test_analyze_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(AppError) as e:
        _client(handler).analyze(b"x", "draw.png")

    assert e.value.code == "AI_UNAVAILABLE"


def test_render_with_sprite_json():
    import base64

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["sprite"] = b"sprite=true" in request.content or b'name="sprite"' in request.content
        return httpx.Response(200, json={
            "video": base64.b64encode(b"mp4").decode(),
            "sprite": base64.b64encode(b"webp").decode(),
            "sprite_frames": 15, "sprite_box": [0.1, 0.2, 0.9, 0.95],
            "sprite_cell": 512, "sprite_cols": 8,
        }, headers={"X-Processing-Time-Ms": "9"})

    res = _client(handler).render(RenderRequest(request_id="r", joints=[], motion="walk"))
    assert seen["sprite"]
    assert (res.content, res.sprite) == (b"mp4", b"webp")
    assert (res.sprite_frames, res.processing_time_ms) == (15, 9)
    assert res.sprite_box == [0.1, 0.2, 0.9, 0.95]


def test_render_old_ai_returns_mp4_only():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"mp4", headers={"content-type": "video/mp4"})

    res = _client(handler).render(RenderRequest(request_id="r", joints=[], motion="walk"))
    assert res.content == b"mp4" and res.sprite is None
