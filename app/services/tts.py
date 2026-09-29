"""이야기 텍스트를 음성으로 바꾼다 (OpenAI TTS).

대상이 발달장애 아동이므로 또박또박 천천히 읽어주는 것이 중요하다.
API 키가 없거나 호출이 실패하면 음성 없이 진행한다 (이야기와 애니메이션은 그대로 제공).
"""

import logging

from openai import OpenAI

from app.core.config import get_settings

log = logging.getLogger(__name__)

TTS_MODEL = "gpt-4o-mini-tts"
# 여러 목소리 중 밝고 또렷한 편. 아이가 듣기 편하다.
TTS_VOICE = "nova"
# 발달장애 아동이 따라올 수 있도록 천천히 읽는다 (1.0이 보통 속도)
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


def get_tts() -> TextToSpeech | None:
    """키가 없으면 None. 호출하는 쪽에서 음성 없이 진행한다."""

    settings = get_settings()
    # TTS도 OpenAI를 쓰므로 전용 키가 없으면 OpenAI 키를 그대로 쓴다
    key = settings.tts_api_key or settings.openai_api_key
    if not key:
        return None
    return TextToSpeech(key, timeout=settings.tts_timeout_seconds)
