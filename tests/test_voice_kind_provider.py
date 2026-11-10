"""보호자 · 아이 녹음 목소리 업체를 따로 (2026-11-10): 보호자 ElevenLabs, 아이 qwen."""

from app.core.config import get_settings
from app.services import tts


def _set(monkeypatch, guardian: str, child: str) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "guardian_voice_provider", guardian)
    monkeypatch.setattr(s, "child_voice_provider", child)


def test_child_follows_guardian_when_empty(monkeypatch):
    _set(monkeypatch, "qwen", "")
    assert tts.voice_provider("guardian") == "qwen"
    assert tts.voice_provider("child") == "qwen"


def test_split_guardian_elevenlabs_child_qwen(monkeypatch):
    _set(monkeypatch, "elevenlabs", "qwen")
    assert tts.voice_provider("guardian") == "elevenlabs"
    assert tts.voice_provider("child") == "qwen"


def test_child_add_voice_goes_to_qwen(monkeypatch):
    _set(monkeypatch, "elevenlabs", "qwen")
    called = {}
    monkeypatch.setattr(tts, "_qwen_add_voice", lambda *a, **k: called.setdefault("qwen", "qv_test"))
    assert tts.add_voice("child-x", b"RIFF", "v.wav", "audio/wav", kind="child") == "qv_test"
