"""이야기 텍스트를 음성으로 바꾼다 (OpenAI TTS 또는 ElevenLabs, `TTS_PROVIDER`).

글을 막 배우는 아이가 들을 음성이라 또박또박 천천히 읽어주는 것이 중요하다.
API 키가 없거나 호출이 실패하면 음성 없이 진행한다 (이야기와 애니메이션은 그대로 제공).
"""

import logging

import httpx
from openai import OpenAI

from app.core.config import get_settings
from app.services.story_writer import Line
from app.services.voices import NARRATOR, tagged

log = logging.getLogger(__name__)

TTS_MODEL = "gpt-4o-mini-tts"
# 여러 목소리 중 밝고 또렷한 편. 아이가 듣기 편하다.
TTS_VOICE = "nova"
# 아이가 따라올 수 있도록 천천히 읽는다 (1.0이 보통 속도)
TTS_SPEED = 0.9

VOICE_INSTRUCTIONS = (
    "어린이에게 동화를 읽어주듯 밝고 다정하게 읽어 주세요. "
    "또박또박 천천히, 문장 사이를 충분히 쉬어 주세요."
)


class TextToSpeech:
    def __init__(self, api_key: str, timeout: float = 60.0) -> None:
        self.client = OpenAI(api_key=api_key, timeout=timeout)

    def speak(self, text: str) -> bytes:
        """텍스트를 MP3 음성으로 바꾼다."""

        res = self.client.audio.speech.create(
            model=TTS_MODEL,
            voice=TTS_VOICE,
            input=text,
            speed=TTS_SPEED,
            instructions=VOICE_INSTRUCTIONS,
            response_format="mp3",
        )
        return res.content


ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
# 캐릭터 목소리 판: 여러 목소리를 한 번에 읽는다 (v4 로 확인, 2026-10-08)
ELEVENLABS_DIALOGUE_URL = "https://api.elevenlabs.io/v1/text-to-dialogue"
# 테스트가 httpx.MockTransport 로 바꾼다
_TRANSPORT: httpx.BaseTransport | None = None


class ElevenLabsSpeech:
    """ElevenLabs 로 MP3 를 만든다. OpenAI 판과 같은 `speak(text) -> bytes` 모양이다."""

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        model_id: str,
        timeout: float = 60.0,
    ) -> None:
        self.api_key, self.voice_id, self.model_id, self.timeout = (
            api_key,
            voice_id,
            model_id,
            timeout,
        )

    def speak(self, text: str) -> bytes:
        with httpx.Client(timeout=self.timeout, transport=_TRANSPORT) as http:
            res = http.post(
                ELEVENLABS_URL.format(voice_id=self.voice_id),
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": self.api_key, "accept": "audio/mpeg"},
                json={
                    "text": text,
                    "model_id": self.model_id,
                    # OpenAI 판과 같이 조금 천천히, 또박또박 (speed 1.0 이 보통)
                    "voice_settings": {
                        "stability": 0.6,
                        "similarity_boost": 0.75,
                        "speed": TTS_SPEED,
                    },
                },
            )
            res.raise_for_status()
            return res.content

    def dialogue(self, lines: list[Line], voices: dict[str, str]) -> bytes:
        """나레이션은 나레이터, 대사는 인물 목소리로 MP3 하나를 만든다.

        voices 는 말하는 사람 → 목소리 id (voices.cast_voices). 없는 사람은 나레이터가 읽는다.
        대사 앞에는 감정 태그를 붙인다 — v4 는 태그를 소리 내 읽지 않는다.
        """

        with httpx.Client(timeout=self.timeout, transport=_TRANSPORT) as http:
            res = http.post(
                ELEVENLABS_DIALOGUE_URL,
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": self.api_key, "accept": "audio/mpeg"},
                json={
                    "model_id": self.model_id,
                    "inputs": [
                        {
                            "text": line.text
                            if line.speaker == NARRATOR
                            else tagged(line.text, line.emotion),
                            "voice_id": voices.get(line.speaker, self.voice_id),
                        }
                        for line in lines
                    ],
                },
            )
            res.raise_for_status()
            return res.content


def get_tts() -> TextToSpeech | ElevenLabsSpeech | None:
    """키가 없으면 None. 호출하는 쪽에서 음성 없이 진행한다."""

    settings = get_settings()
    if settings.tts_provider == "elevenlabs":
        if not (settings.elevenlabs_api_key and settings.elevenlabs_voice_id):
            log.warning("TTS_PROVIDER=elevenlabs 인데 키나 목소리 id 가 없어 음성 없이 진행합니다")
            return None
        return ElevenLabsSpeech(
            settings.elevenlabs_api_key,
            settings.elevenlabs_voice_id,
            settings.elevenlabs_model_id,
            timeout=settings.tts_timeout_seconds,
        )

    # TTS도 OpenAI를 쓰므로 전용 키가 없으면 OpenAI 키를 그대로 쓴다
    key = settings.tts_api_key or settings.openai_api_key
    if not key:
        return None
    return TextToSpeech(key, timeout=settings.tts_timeout_seconds)
