import json

import httpx

from app.core.config import get_settings
from app.services import tts


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
