"""보호자 목소리로 나레이션 (2026-11-02).

녹음은 저장하지 않고 ElevenLabs 로 넘긴다. 동의 세 가지가 다 있어야 받는다.
지우면 ElevenLabs 목소리와 그 목소리로 만든 음성 파일을 함께 지운다.
ElevenLabs 는 httpx.MockTransport 로 바꿔 실제로 부르지 않는다.
"""

import httpx
import pytest

from app.core.auth import issue_token
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import GuardianVoice, Story, User
from app.models.card_story import CardStory
from app.models.mixins import utcnow
from app.services import tts

CONSENT = {
    "agree_collect": "true",
    "agree_transfer": "true",
    "own_voice": "true",
    "consent_version": "2026-11-02",
}
WAV = b"RIFF....WAVEfmt "


def _token() -> str:
    with SessionLocal() as db:
        user = User(terms_agreed_at=utcnow())
        db.add(user)
        db.flush()
        token = issue_token(db, user)
        db.commit()
        return token


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def eleven(monkeypatch):
    """ElevenLabs 를 흉내 낸다. 받은 요청을 calls 에 남긴다."""

    s = get_settings()
    monkeypatch.setattr(s, "tts_provider", "elevenlabs")
    monkeypatch.setattr(s, "elevenlabs_api_key", "k")
    monkeypatch.setattr(s, "elevenlabs_voice_id", "default-voice")
    calls: list[tuple[str, str]] = []
    made = iter(["guardian-1", "guardian-2"])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/v1/voices/add":
            body = {"voice_id": next(made), "requires_verification": False}
            return httpx.Response(200, json=body)
        if request.method == "DELETE":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, content=b"ID3mp3")  # text-to-speech

    monkeypatch.setattr(tts, "_TRANSPORT", httpx.MockTransport(handler))
    return calls


def _register(client, token, **form):
    return client.post(
        "/api/v1/me/voice",
        headers=_auth(token),
        files={"audio": ("voice.wav", WAV, "audio/wav")},
        data={**CONSENT, **form},
    )


def _upload(client, token):
    res = client.post(
        "/api/v1/characters",
        files={"image": ("draw.png", _png(), "image/png")},
        headers=_auth(token),
    )
    assert res.status_code == 202, res.text
    return res.json()["character_id"]


def _png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (400, 600), "white").save(buf, format="PNG")
    return buf.getvalue()


def _story(client, token, cid, **kw):
    req = {"character_id": cid, "place": "숲", "problem": "길을 잃었어요",
           "action": "그 자리에서 기다렸어요", "result": "길을 찾았어요", **kw}
    res = client.post("/api/v1/stories", json=req, headers=_auth(token))
    assert res.status_code == 202, res.text
    return client.get(f"/api/v1/stories/{res.json()['story_id']}", headers=_auth(token)).json()


def test_needs_login(anon_client, eleven):
    audio = {"audio": ("v.wav", WAV, "audio/wav")}
    res = anon_client.post("/api/v1/me/voice", files=audio, data=CONSENT)
    assert res.status_code == 401
    assert eleven == []


@pytest.mark.parametrize("missing", ["agree_collect", "agree_transfer", "own_voice"])
def test_every_consent_is_required(client, eleven, missing):
    res = _register(client, _token(), **{missing: "false"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "CONSENT_REQUIRED"
    assert eleven == []  # 동의 전에는 ElevenLabs 로 아무것도 보내지 않는다


def test_unavailable_without_elevenlabs(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "tts_provider", "openai")
    res = _register(client, _token())
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "VOICE_UNAVAILABLE"


def test_register_keeps_only_voice_id(client, eleven, tmp_path):
    token = _token()
    res = _register(client, token)
    assert res.status_code == 201, res.text
    assert res.json()["registered"] is True
    assert ("POST", "/v1/voices/add") in eleven
    with SessionLocal() as db:
        row = db.query(GuardianVoice).one()
        assert row.elevenlabs_voice_id == "guardian-1"
        assert row.consent_version == "2026-11-02"
    # 녹음 파일은 어디에도 저장하지 않는다
    storage_dir = get_settings().local_storage_dir
    import os

    saved = [f for _, _, fs in os.walk(storage_dir) for f in fs if f.endswith((".wav", ".webm"))]
    assert saved == []
    assert client.get("/api/v1/me/voice", headers=_auth(token)).json()["registered"] is True


def test_story_reads_with_guardian_voice(client, eleven):
    token = _token()
    _register(client, token)
    cid = _upload(client, token)
    story = _story(client, token, cid, narrator="guardian")
    assert story["status"] == "succeeded"
    assert story["narrator"] == "guardian"
    assert ("POST", "/v1/text-to-speech/guardian-1") in eleven
    plain = _story(client, token, cid)
    assert plain["narrator"] == "default"
    assert ("POST", "/v1/text-to-speech/default-voice") in eleven


def test_guardian_without_voice_falls_back(client, eleven):
    """목소리가 없으면 아이 흐름을 막지 않고 기본 나레이터로."""

    token = _token()
    cid = _upload(client, token)
    story = _story(client, token, cid, narrator="guardian")
    assert story["status"] == "succeeded"
    assert story["narrator"] == "default"


def test_guardian_voice_skips_shared_card_audio(client, eleven):
    """미리 만든 카드 이야기여도 보호자 목소리 음성은 함께 쓰는 곳에 두지 않는다."""

    with SessionLocal() as db:
        db.add(CardStory(status="approved", place="숲", problem="길을 잃었어요",
                         action="그 자리에서 기다렸어요", result="길을 찾았어요", length="short",
                         voice_mode="narration", payload={"text": "미리 만든 숲 이야기예요."},
                         model="m", prompt_version="v"))
        db.commit()
    import os

    shared = os.path.join(get_settings().local_storage_dir, "card_audio")

    def files():
        if not os.path.exists(shared):
            return []
        return sorted(f for _, _, fs in os.walk(shared) for f in fs)

    before = files()
    token = _token()
    _register(client, token)
    cid = _upload(client, token)
    story = _story(client, token, cid, narrator="guardian")
    assert story["audio_url"]
    assert files() == before


def test_delete_removes_voice_and_its_audio(client, eleven):
    token = _token()
    _register(client, token)
    cid = _upload(client, token)
    story = _story(client, token, cid, narrator="guardian")
    assert story["audio_url"]
    assert client.delete("/api/v1/me/voice", headers=_auth(token)).status_code == 204
    assert ("DELETE", "/v1/voices/guardian-1") in eleven
    after = client.get(f"/api/v1/stories/{story['id']}", headers=_auth(token)).json()
    assert after["audio_url"] is None
    assert after["narrator"] == "default"
    with SessionLocal() as db:
        row = db.query(GuardianVoice).one()
        # 목소리는 지우고 동의 기록은 남긴다
        assert row.elevenlabs_voice_id is None and row.withdrawn_at is not None
        assert db.query(Story).one().narrator_voice_id is None
    assert client.get("/api/v1/me/voice", headers=_auth(token)).json()["registered"] is False


def test_re_record_replaces_old_voice(client, eleven):
    token = _token()
    _register(client, token)
    _register(client, token)
    assert ("DELETE", "/v1/voices/guardian-1") in eleven
    with SessionLocal() as db:
        assert db.query(GuardianVoice).one().elevenlabs_voice_id == "guardian-2"


def test_narration_keeps_name_but_does_not_use_it(client):
    """이름은 나레이션 판에서도 등록해 두지만 글은 「나는」 (2026-11-02)."""

    token = _token()
    cid = _upload(client, token)
    story = _story(client, token, cid, length="long", protagonist_name="보리")
    assert story["status"] == "succeeded"
    assert story["protagonist_name"] == "보리"
    assert "보리" not in story["text"]
    assert "나는" in story["text"]
