"""S-05 미리보기 MP4 (분석 직후 Meta 렌더)."""

from types import SimpleNamespace

from app.core.config import get_settings
from app.schemas.ai import RenderResult
from app.services import jobs
from app.services.storage import get_storage

JOINTS = [{"name": "hip", "x": 1, "y": 2}]


class FakeAI:
    def __init__(self, content=b"mp4", fail=False):
        self.content, self.fail, self.requests = content, fail, []

    def render(self, req):
        self.requests.append(req)
        if self.fail:
            raise RuntimeError("render failed")
        return RenderResult(
            content=self.content, content_type="video/mp4", processing_time_ms=1, model_version="t"
        )


def _character(**kw):
    base = dict(
        id="c1",
        ai_request_id="r1",
        image_width=10,
        image_height=10,
        corrections=[],
        ai_joints=JOINTS,
    )
    return SimpleNamespace(**{**base, **kw})


def test_preview_saved_with_frame_cap(monkeypatch):
    ai = FakeAI()
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: ai)
    path = jobs.make_preview(_character(), get_storage())
    assert path.startswith("previews/c1-") and path.endswith(".mp4")
    assert get_storage().read(path) == b"mp4"
    assert ai.requests[0].max_frames == jobs.PREVIEW_MAX_FRAMES
    assert ai.requests[0].motion == jobs.DEFAULT_MOTION


def test_preview_failure_does_not_fail_analysis(monkeypatch):
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: FakeAI(fail=True))
    assert jobs.make_preview(_character(), get_storage()) is None


def test_no_preview_without_session_or_content(monkeypatch):
    monkeypatch.setattr(jobs, "get_ai_client", lambda size=None: FakeAI(content=b""))
    assert jobs.make_preview(_character(ai_request_id=None), get_storage()) is None
    assert jobs.make_preview(_character(), get_storage()) is None  # 목 AI 는 빈 내용


def test_flow_with_mock_ai_has_no_preview(client, png_bytes):
    assert get_settings().ai_use_mock
    res = client.post("/api/v1/characters", files={"image": ("d.png", png_bytes, "image/png")})
    character = client.get(f"/api/v1/characters/{res.json()['character_id']}").json()
    assert character["status"] == "succeeded"
    assert character["preview_url"] is None  # 프론트는 브라우저 애니메이션으로 대신한다
    assert character["preview_status"] is None  # 목 AI 는 만들지 않는다 — 기다리지 않게


def test_wants_preview(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "ai_use_mock", False)
    assert jobs.wants_preview(_character(original_deleted_at=None))
    assert not jobs.wants_preview(_character(original_deleted_at="지움"))
    assert not jobs.wants_preview(_character(ai_request_id=None, original_deleted_at=None))
    monkeypatch.setattr(s, "ai_use_mock", True)
    assert not jobs.wants_preview(_character(original_deleted_at=None))
