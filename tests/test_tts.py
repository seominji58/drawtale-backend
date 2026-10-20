import json

import httpx

from app.core.config import get_settings
from app.services import tts
from app.services.story_writer import Line


def test_openai_is_the_default(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "tts_provider", "openai")
    monkeypatch.setattr(s, "openai_api_key", "")
    monkeypatch.setattr(s, "tts_api_key", "")
    assert tts.get_tts() is None  # 키가 없으면 음성 없이


def test_elevenlabs_needs_key_and_voice(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "tts_provider", "elevenlabs")
    monkeypatch.setattr(s, "elevenlabs_api_key", "k")
    monkeypatch.setattr(s, "elevenlabs_voice_id", "")
    assert tts.get_tts() is None


def test_elevenlabs_request(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "tts_provider", "elevenlabs")
    monkeypatch.setattr(s, "elevenlabs_api_key", "secret-key")
    monkeypatch.setattr(s, "elevenlabs_voice_id", "voice123")
    monkeypatch.setattr(s, "elevenlabs_model_id", "eleven_multilingual_v2")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["xi-api-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"ID3mp3")

    monkeypatch.setattr(tts, "_TRANSPORT", httpx.MockTransport(handler))
    speech = tts.get_tts()
    assert isinstance(speech, tts.ElevenLabsSpeech)
    assert speech.speak("오늘 나는 숲에 갔어요.") == b"ID3mp3"
    assert seen["url"].startswith("https://api.elevenlabs.io/v1/text-to-speech/voice123")
    assert seen["key"] == "secret-key"
    assert seen["body"]["text"] == "오늘 나는 숲에 갔어요."
    assert seen["body"]["model_id"] == "eleven_multilingual_v2"


def test_elevenlabs_dialogue(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"ID3mp3")

    monkeypatch.setattr(tts, "_TRANSPORT", httpx.MockTransport(handler))
    speech = tts.ElevenLabsSpeech("secret-key", "narrator", "eleven_v4")
    lines = [
        Line("나레이션", "나는 친구에게"),
        Line("나", "미안해.", "다정"),
        Line("친구", "괜찮아.", "기쁨"),
        Line("모르는사람", "안녕."),
    ]
    voices = {"나레이션": "narrator", "나": "me", "친구": "friend"}
    assert speech.dialogue(lines, voices) == b"ID3mp3"
    assert seen["url"].startswith("https://api.elevenlabs.io/v1/text-to-dialogue")
    assert seen["body"]["model_id"] == "eleven_v4"
    inputs = seen["body"]["inputs"]
    assert [i["voice_id"] for i in inputs] == ["narrator", "me", "friend", "narrator"]
    # 대사에만 감정 태그가 붙는다
    texts = [i["text"] for i in inputs]
    assert texts == ["나는 친구에게", "[warmly] 미안해.", "[happy] 괜찮아.", "안녕."]
