import httpx
import pytest

from app.core.errors import AppError
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


def test_analyze_maps_ai_error_code():
    body = {"success": False, "message": "NO_CHARACTER_DETECTED: No drawn humanoid detected"}
    client = _client(lambda r: httpx.Response(200, json=body))

    with pytest.raises(AppError) as e:
        client.analyze(b"x", "draw.png")

    assert e.value.code == "NO_CHARACTER_DETECTED"
    assert "캐릭터" in e.value.message


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
