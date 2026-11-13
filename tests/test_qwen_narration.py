"""녹음 목소리(보호자 · 아이)는 나레이션만, 캐릭터 대사는 ElevenLabs 그대로 (2026-11-09).

GUARDIAN_VOICE_PROVIDER=qwen: 녹음 목소리는 자체 서버 drawtale-tts(Qwen3-TTS)가 읽는다.
기본 나레이터와 캐릭터 대사는 지금처럼 ElevenLabs 가 읽고, 한 쪽 안에서 둘을 drawtale-tts 가 잇는다.
drawtale-tts · ElevenLabs 모두 httpx.MockTransport 로 흉내 낸다.
"""

import base64
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import GuardianVoice
from app.services import jobs, tts
from app.services.book import Book, Page
from app.services.storage import get_storage
from app.services.voices import NARRATOR, PROTAGONIST, VOICE_TYPES
from tests.test_guardian_voice import _auth, _register, _story, _token, _upload

QWEN = "tts.example"


def _qv(n: int) -> str:
    return "qv_" + format(n, "x") * 32


@pytest.fixture
def both(monkeypatch):
    """drawtale-tts 와 ElevenLabs 를 함께 흉내 낸다."""

    s = get_settings()
    monkeypatch.setattr(s, "guardian_voice_provider", "qwen")
    monkeypatch.setattr(s, "qwen_tts_url", f"https://{QWEN}/")
    monkeypatch.setattr(s, "qwen_tts_token", "secret")
    monkeypatch.setattr(s, "tts_provider", "elevenlabs")
    monkeypatch.setattr(s, "elevenlabs_api_key", "k")
    monkeypatch.setattr(s, "elevenlabs_voice_id", "default-voice")
    state = {"calls": [], "made": iter([_qv(10), _qv(11)]), "reject": None}

    def handler(request: httpx.Request) -> httpx.Response:
        is_json = request.headers.get("content-type") == "application/json"
        body = json.loads(request.content) if is_json else None
        state["calls"].append((request.url.host, request.method, request.url.path, body, request))
        if request.url.host == QWEN:
            assert request.headers["authorization"] == "Bearer secret"
            if request.url.path == "/internal/v1/voices":
                if state["reject"]:
                    err = {"error": {"code": state["reject"], "message": "x"}}
                    return httpx.Response(400, json=err)
                return httpx.Response(201, json={"voice_id": next(state["made"]), "mode": "text"})
            if request.method == "DELETE":
                return httpx.Response(204)
            return httpx.Response(200, content=b"ID3qwen")
        if request.url.path.startswith("/v1/voices"):
            raise AssertionError("녹음 목소리를 ElevenLabs 로 보내면 안 된다")
        return httpx.Response(200, content=b"ID3eleven:" + request.url.path.encode())

    monkeypatch.setattr(tts, "_TRANSPORT", httpx.MockTransport(handler))
    return state


def _calls(state, host, path=None):
    return [c for c in state["calls"] if c[0] == host and (path is None or c[2] == path)]


def _book():
    pages = [
        Page(1, "place", "walk", [(NARRATOR, "숲에 갔어요.", "")]),
        Page(
            2,
            "problem",
            "idle",
            [(NARRATOR, "길을 잃었어요.", ""), (PROTAGONIST, "어디지?", "놀람")],
        ),
        Page(3, "action", "wave", [(NARRATOR, "물어봤어요.", ""), ("부엉이", "이쪽이야.", "다정")]),
    ]
    return Book("숲", pages, [1, 2, 3], "하루", {"부엉이": "작은 동물"})


def _story_obj(**kw):
    base = dict(
        id=uuid.uuid4(),
        voice_mode="characters",
        protagonist_voice="girl",
        voice_cast=None,
        narrator_voice_id=_qv(10),
    )
    return SimpleNamespace(**{**base, **kw})


# ---- 어느 업체가 읽나 ----


def test_recorded_voice_narrates_with_elevenlabs_characters(both):
    speech = tts.get_tts(_qv(10))
    assert isinstance(speech, tts.QwenSpeech) and speech.voice_id == _qv(10)
    assert speech.voice_types == VOICE_TYPES  # 인물 목소리는 ElevenLabs 목록 그대로


def test_default_narrator_stays_elevenlabs(both):
    speech = tts.get_tts()
    assert isinstance(speech, tts.ElevenLabsSpeech) and speech.voice_id == "default-voice"
    speech = tts.get_tts("guardian-1")  # 예전 ElevenLabs 녹음 목소리도 그대로
    assert isinstance(speech, tts.ElevenLabsSpeech) and speech.voice_id == "guardian-1"


def test_without_qwen_settings_falls_back_to_default(both, monkeypatch):
    monkeypatch.setattr(get_settings(), "qwen_tts_url", "")
    speech = tts.get_tts(_qv(10))
    assert isinstance(speech, tts.ElevenLabsSpeech) and speech.voice_id == "default-voice"


def test_without_elevenlabs_one_voice_reads_all(both, monkeypatch):
    monkeypatch.setattr(get_settings(), "elevenlabs_api_key", "")
    speech = tts.get_tts(_qv(10))
    assert isinstance(speech, tts.QwenSpeech) and speech.voice_types == {}


# ---- 쪽 음성 ----


def test_pages_mix_recorded_narration_and_elevenlabs_dialogue(both):
    story = _story_obj()
    paths = jobs.make_page_audio(story, get_storage(), _book())
    assert all(paths)
    # 1쪽(대사 없음)은 녹음 목소리로 한 번에
    spoken = _calls(both, QWEN, "/internal/v1/tts")
    assert [c[3]["voice_id"] for c in spoken] == [_qv(10)]
    assert spoken[0][3]["speed"] == tts.TTS_SPEED
    # 대사 쪽: 나레이션은 녹음 목소리, 대사는 ElevenLabs 가 만든 음성을 drawtale-tts 가 잇는다
    dialogues = sorted(
        (c[3] for c in _calls(both, QWEN, "/internal/v1/dialogue")),
        key=lambda b: b["lines"][0]["text"],
    )
    assert len(dialogues) == 2
    girl, owl = VOICE_TYPES["아이-여"][0], VOICE_TYPES["작은 동물"][0]
    for body, narration, voice in (
        (dialogues[0], "길을 잃었어요.", girl),
        (dialogues[1], "물어봤어요.", owl),
    ):
        first, second = body["lines"]
        assert first == {"voice_id": _qv(10), "text": narration}
        assert base64.b64decode(second["audio"]) == f"ID3eleven:/v1/text-to-speech/{voice}".encode()
        assert body["speed"] == 1.0  # ElevenLabs 대사 빠르기를 다시 바꾸지 않는다
    # ElevenLabs 에는 대사만, 감정 태그를 붙여서
    eleven = sorted(c[3]["text"] for c in _calls(both, "api.elevenlabs.io"))
    assert eleven == ["[surprised] 어디지?", "[warmly] 이쪽이야."]
    assert story.voice_cast[NARRATOR] == _qv(10)
    assert story.voice_cast[PROTAGONIST] == girl and story.voice_cast["부엉이"] == owl


def test_narration_mode_reads_whole_page_with_recorded_voice(both):
    story = _story_obj(voice_mode="narration")
    jobs.make_page_audio(story, get_storage(), _book())
    assert len(_calls(both, QWEN, "/internal/v1/tts")) == 3
    assert _calls(both, QWEN, "/internal/v1/dialogue") == []
    assert _calls(both, "api.elevenlabs.io") == []


def test_story_with_recorded_voice_and_default(client, both):
    token = _token()
    _register(client, token)
    cid = _upload(client, token)
    story = _story(client, token, cid, narrator="guardian")
    assert story["status"] == "succeeded" and story["narrator"] == "guardian"
    assert _calls(both, QWEN, "/internal/v1/tts")[-1][3]["voice_id"] == _qv(10)
    plain = _story(client, token, cid)
    assert plain["narrator"] == "default"
    assert [c[2] for c in _calls(both, "api.elevenlabs.io")] == ["/v1/text-to-speech/default-voice"]


# ---- 녹음 목소리 등록 · 지우기 ----


def test_register_sends_recording_and_ref_text(client, both):
    res = _register(client, _token(), ref_text="  오늘은 친구와 놀이터에 갔어요. ")
    assert res.status_code == 201, res.text
    req = _calls(both, QWEN, "/internal/v1/voices")[0][4]
    body = req.read().decode("utf-8", "ignore")
    assert "오늘은 친구와 놀이터에 갔어요." in body and 'name="audio"' in body
    with SessionLocal() as db:
        assert db.query(GuardianVoice).one().elevenlabs_voice_id == _qv(10)


def test_register_without_ref_text(client, both):
    assert _register(client, _token()).status_code == 201
    req = _calls(both, QWEN, "/internal/v1/voices")[0][4]
    assert 'name="ref_text"' not in req.read().decode("utf-8", "ignore")


@pytest.mark.parametrize("code", ["REF_TOO_SHORT", "REF_TOO_LONG", "INVALID_AUDIO"])
def test_bad_recording_tells_what_to_fix(client, both, code):
    both["reject"] = code
    res = _register(client, _token())
    assert res.status_code == 400 and res.json()["error"]["code"] == code
    with SessionLocal() as db:
        assert db.query(GuardianVoice).count() == 0


def test_ready_needs_url_and_token(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "guardian_voice_provider", "qwen")
    monkeypatch.setattr(s, "qwen_tts_url", "")
    assert tts.voice_cloning_ready() is False
    monkeypatch.setattr(s, "qwen_tts_url", "https://tts.example")
    monkeypatch.setattr(s, "qwen_tts_token", "secret")
    assert tts.voice_cloning_ready() is True


def test_delete_and_re_record(client, both):
    token = _token()
    _register(client, token)
    _register(client, token)  # 다시 녹음하면 예전 목소리를 지운다
    deletes = [c[2] for c in _calls(both, QWEN) if c[1] == "DELETE"]
    assert deletes == [f"/internal/v1/voices/{_qv(10)}"]
    assert client.delete("/api/v1/me/voice", headers=_auth(token)).status_code == 204
    assert [c[2] for c in _calls(both, QWEN) if c[1] == "DELETE"][
        -1
    ] == f"/internal/v1/voices/{_qv(11)}"


def test_old_elevenlabs_voice_is_deleted_at_elevenlabs(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "guardian_voice_provider", "qwen")
    monkeypatch.setattr(s, "elevenlabs_api_key", "k")
    seen = []

    def handler(r):
        seen.append((r.url.host, r.url.path))
        return httpx.Response(200, json={})

    monkeypatch.setattr(tts, "_TRANSPORT", httpx.MockTransport(handler))
    tts.delete_voice("guardian-1")
    assert seen == [("api.elevenlabs.io", "/v1/voices/guardian-1")]


def test_follows_modal_redirect(both, monkeypatch):
    """Modal 은 오래 걸리면 303 으로 다시 오라고 한다. 따라가서 결과를 받는다."""

    hops = []

    def handler(request):
        hops.append(str(request.url))
        if "__modal_attempt_token" not in str(request.url):
            location = f"https://{QWEN}/internal/v1/tts?__modal_attempt_token=x"
            return httpx.Response(303, headers={"location": location})
        return httpx.Response(200, content=b"ID3late")

    monkeypatch.setattr(tts, "_TRANSPORT", httpx.MockTransport(handler))
    assert tts.get_tts(_qv(10)).speak("안녕") == b"ID3late"
    assert len(hops) == 2


# ---- 아이 목소리 (/me/child-voice, dev 2026-11-09) ----


def test_child_voice_goes_to_qwen_with_ref_text(client, both):
    from tests.test_guardian_voice import _register_child

    token = _token()
    res = _register_child(client, token, ref_text="나는 숲에서 토끼를 만났어요.")
    assert res.status_code == 201, res.text
    req = _calls(both, QWEN, "/internal/v1/voices")[0][4]
    assert "나는 숲에서 토끼를 만났어요." in req.read().decode("utf-8", "ignore")
    with SessionLocal() as db:
        assert db.query(GuardianVoice).one().elevenlabs_voice_id == _qv(10)


def test_child_voice_sample_is_read_by_qwen(client, both):
    from tests.test_guardian_voice import _register_child

    token = _token()
    _register_child(client, token)
    res = client.get("/api/v1/me/child-voice/sample", headers=_auth(token))
    assert res.status_code == 200 and res.content == b"ID3qwen"
    assert _calls(both, QWEN, "/internal/v1/tts")[-1][3]["voice_id"] == _qv(10)
