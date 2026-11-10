"""이야기 텍스트를 음성으로 바꾼다 (OpenAI TTS 또는 ElevenLabs, `TTS_PROVIDER`).

글을 막 배우는 아이가 들을 음성이라 또박또박 천천히 읽어주는 것이 중요하다.
API 키가 없거나 호출이 실패하면 음성 없이 진행한다 (이야기와 애니메이션은 그대로 제공).
"""

import base64
import logging
import subprocess

import httpx
from openai import OpenAI

from app.core.config import get_settings
from app.services.story_writer import Line
from app.services.voices import NARRATOR, VOICE_TYPES, tagged

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


# ---- 소리 크기 맞추기 (11/10 사용자 「등장인물 ElevenLabs 목소리가 훨씬 크게 들려 크기 차이가 난다」) ----
# 등록한 보호자 · 아이 목소리는 녹음한 크기를 따라가 작고, ElevenLabs 기본 목소리(등장인물)는 크게 다듬어져 나온다.
# 한 파일 안의 나레이션 · 대사 크기를 고르게(dynaudnorm) 한 뒤 전체를 같은 크기(-16 LUFS, 말소리 표준)로 맞춘다.
# ffmpeg 는 imageio-ffmpeg 패키지에 든 것을 쓴다 (이미지에 따로 설치하지 않게). 실패하면 원래 소리 그대로
LOUDNESS_FILTER = "dynaudnorm=f=200:g=11:p=0.9:m=4,loudnorm=I=-16:TP=-1.5:LRA=11"


def even_loudness(mp3: bytes) -> bytes:
    if not mp3 or len(mp3) < 1024:
        return mp3
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        out = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-af", LOUDNESS_FILTER,
             "-ar", "44100", "-codec:a", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1"],
            input=mp3, capture_output=True, timeout=90, check=True,
        ).stdout
        return out if len(out) > 1024 else mp3
    except Exception as exc:  # noqa: BLE001 — 크기 맞추기는 덤이라 실패해도 원래 소리로 읽는다
        log.warning("소리 크기 맞추기 실패, 원래 소리로: %s", exc)
        return mp3


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
        return even_loudness(res.content)


ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
# 캐릭터 목소리 판: 여러 목소리를 한 번에 읽는다 (v4 로 확인, 2026-10-08)
ELEVENLABS_DIALOGUE_URL = "https://api.elevenlabs.io/v1/text-to-dialogue"
# 보호자 목소리(복제)는 원래 목소리에 더 가깝게 읽는다 (11/7 사용자 「목소리가 다르게 나오는 것 같아」).
# 기본 나레이터는 예전 그대로
CLONED_SIMILARITY = 0.9
CLONED_STABILITY = 0.5
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
        cloned: bool = False,
    ) -> None:
        self.api_key, self.voice_id, self.model_id, self.timeout = (
            api_key,
            voice_id,
            model_id,
            timeout,
        )
        # 보호자 목소리(Instant Voice Cloning) 로 읽는다
        self.cloned = cloned

    @property
    def voice_types(self) -> dict[str, tuple[str, ...]]:
        """캐릭터 목소리 판에서 고를 목소리 (voices.cast_voices)."""

        return VOICE_TYPES

    def with_voice(self, voice_id: str) -> "ElevenLabsSpeech":
        return ElevenLabsSpeech(self.api_key, voice_id, self.model_id, self.timeout)

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
                        "stability": CLONED_STABILITY if self.cloned else 0.6,
                        "similarity_boost": CLONED_SIMILARITY if self.cloned else 0.75,
                        "speed": TTS_SPEED,
                    },
                },
            )
            res.raise_for_status()
            return even_loudness(res.content)

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
            return even_loudness(res.content)


# ---- 녹음 목소리: 자체 서버 drawtale-tts (Qwen3-TTS, 2026-11-09) ----
# 백엔드는 drawtale-tts 코드를 들여오지 않고 HTTP 로만 부른다 (ai_client 와 같다).
# drawtale-tts 의 목소리 id 는 「qv_」로 시작한다. 업체를 바꾼 뒤에도 id 로 업체를 가린다.
QWEN_VOICE_PREFIX = "qv_"


def is_qwen_voice(voice_id: str | None) -> bool:
    return bool(voice_id) and voice_id.startswith(QWEN_VOICE_PREFIX)


def _qwen_ready() -> bool:
    s = get_settings()
    return bool(s.qwen_tts_url and s.qwen_tts_token)


def _qwen_client() -> httpx.Client:
    s = get_settings()
    return httpx.Client(
        base_url=s.qwen_tts_url.rstrip("/"),
        headers={"Authorization": f"Bearer {s.qwen_tts_token}"},
        timeout=s.qwen_tts_timeout_seconds,
        # Modal 은 오래 걸리는 요청에 303 으로 「다시 오라」고 한다. 따라가야 결과를 받는다
        follow_redirects=True,
        transport=_TRANSPORT,
    )


class QwenSpeech:
    """녹음 목소리로 나레이션을 읽는다 (drawtale-tts). ElevenLabs 판과 같은 모양이다.

    캐릭터 대사는 그대로 ElevenLabs(characters)가 인물 목소리로 읽고, 둘을 drawtale-tts 가
    한 파일로 잇는다 (백엔드에는 오디오 도구가 없다).
    characters 가 없으면 녹음 목소리 하나로 읽는다.
    """

    def __init__(self, voice_id: str, characters: ElevenLabsSpeech | None = None) -> None:
        self.voice_id = voice_id
        self.characters = characters

    @property
    def voice_types(self) -> dict[str, tuple[str, ...]]:
        return self.characters.voice_types if self.characters else {}

    def _post(self, path: str, body: dict) -> bytes:
        body = {**body, "language": "Korean", "speed": TTS_SPEED, "format": "mp3"}
        with _qwen_client() as http:
            res = http.post(path, json=body)
            res.raise_for_status()
            return even_loudness(res.content)

    def speak(self, text: str) -> bytes:
        return self._post("/internal/v1/tts", {"voice_id": self.voice_id, "text": text})

    def dialogue(self, lines: list[Line], voices: dict[str, str]) -> bytes:
        """나레이션은 녹음 목소리, 대사는 ElevenLabs 인물 목소리로 MP3 하나."""

        parts = []
        for line in lines:
            if not line.text.strip():
                continue
            voice = voices.get(line.speaker, self.voice_id)
            if line.speaker == NARRATOR or voice == self.voice_id or self.characters is None:
                parts.append({"voice_id": self.voice_id, "text": line.text})
            else:
                clip = self.characters.with_voice(voice).speak(tagged(line.text, line.emotion))
                parts.append({"audio": base64.b64encode(clip).decode()})
        # 빠르기는 녹음 목소리 쪽에서만 맞춘다 — ElevenLabs 대사는 이미 그 빠르기로 만들었다
        body = {"lines": parts, "language": "Korean", "speed": 1.0, "format": "mp3"}
        with _qwen_client() as http:
            res = http.post("/internal/v1/dialogue", json=body)
            res.raise_for_status()
            return even_loudness(res.content)


class VoiceRejected(Exception):
    """녹음 탓에 목소리를 만들 수 없을 때 (짧음 · 김 · 읽을 수 없음). 다시 녹음하면 된다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


# drawtale-tts 의 오류 code → 녹음한 사람에게 보여 줄 말
_QWEN_REJECTIONS = {
    "REF_TOO_SHORT": "녹음이 너무 짧아요. 안내 문장을 끝까지 읽어 주세요.",
    "REF_TOO_LONG": "녹음이 너무 길어요. 안내 문장만 읽어 주세요.",
    "INVALID_AUDIO": "녹음 파일을 읽을 수 없어요. 다시 녹음해 주세요.",
}


def _elevenlabs(voice_id: str | None = None) -> ElevenLabsSpeech | None:
    s = get_settings()
    voice_id = voice_id or s.elevenlabs_voice_id
    if not (s.tts_provider == "elevenlabs" and s.elevenlabs_api_key and voice_id):
        return None
    return ElevenLabsSpeech(
        s.elevenlabs_api_key, voice_id, s.elevenlabs_model_id, timeout=s.tts_timeout_seconds
    )


def get_tts(
    narrator_voice_id: str | None = None,
) -> TextToSpeech | ElevenLabsSpeech | QwenSpeech | None:
    """키가 없으면 None. 호출하는 쪽에서 음성 없이 진행한다.

    narrator_voice_id 가 있으면 나레이터를 그 목소리로 (녹음 목소리).
    「qv_」 목소리는 drawtale-tts 가 나레이션을 읽고, 대사는 ElevenLabs 가 읽는다.
    """

    settings = get_settings()
    if is_qwen_voice(narrator_voice_id):
        if _qwen_ready():
            return QwenSpeech(narrator_voice_id, characters=_elevenlabs())
        log.warning(
            "QWEN_TTS_URL · QWEN_TTS_TOKEN 이 없어 녹음 목소리 대신 기본 나레이터로 읽습니다"
        )
        narrator_voice_id = None
    if settings.tts_provider == "elevenlabs":
        voice_id = narrator_voice_id or settings.elevenlabs_voice_id
        if not (settings.elevenlabs_api_key and voice_id):
            log.warning("TTS_PROVIDER=elevenlabs 인데 키나 목소리 id 가 없어 음성 없이 진행합니다")
            return None
        return ElevenLabsSpeech(
            settings.elevenlabs_api_key,
            voice_id,
            settings.elevenlabs_model_id,
            timeout=settings.tts_timeout_seconds,
            cloned=bool(narrator_voice_id),
        )
    if narrator_voice_id:
        log.warning("보호자 목소리는 ElevenLabs 에서만 읽습니다. 기본 나레이터로 읽습니다")

    # TTS도 OpenAI를 쓰므로 전용 키가 없으면 OpenAI 키를 그대로 쓴다
    key = settings.tts_api_key or settings.openai_api_key
    if not key:
        return None
    return TextToSpeech(key, timeout=settings.tts_timeout_seconds)


# ---- 보호자 목소리 (2026-11-02) ----
# Instant Voice Cloning. 녹음 1~3분으로 목소리를 만든다. 녹음은 우리 서버에 남기지 않는다.
ELEVENLABS_ADD_VOICE_URL = "https://api.elevenlabs.io/v1/voices/add"
ELEVENLABS_VOICE_URL = "https://api.elevenlabs.io/v1/voices/{voice_id}"


def voice_provider(kind: str = "guardian") -> str:
    """새로 등록하는 녹음 목소리를 어디로 보내나 — 동의 화면이 받는 곳을 이것으로 보여 준다 (2026-11-10).

    kind: guardian(보호자) | child(아이). 아이는 CHILD_VOICE_PROVIDER 가 있으면 그것, 없으면 보호자와 같다.
    """

    s = get_settings()
    chosen = (s.child_voice_provider or s.guardian_voice_provider) if kind == "child" else s.guardian_voice_provider
    return "qwen" if chosen == "qwen" else "elevenlabs"


def voice_cloning_ready(kind: str = "guardian") -> bool:
    """녹음 목소리를 받을 수 있는지.

    qwen: drawtale-tts 주소 · 토큰이 있을 때. elevenlabs: ElevenLabs 로 읽고 키가 있을 때.
    """

    s = get_settings()
    if voice_provider(kind) == "qwen":
        return _qwen_ready()
    return s.tts_provider == "elevenlabs" and bool(s.elevenlabs_api_key)


def add_voice(
    name: str, audio: bytes, filename: str, content_type: str, ref_text: str | None = None,
    kind: str = "guardian",
) -> str:
    """녹음으로 목소리를 만들고 그 id 를 돌려준다.

    녹음 탓이면 VoiceRejected, 그 밖의 실패는 httpx 예외.
    ref_text: 녹음에서 읽은 글 (qwen 만 쓴다). 있으면 더 닮게 만든다.
    """

    s = get_settings()
    if voice_provider(kind) == "qwen":
        return _qwen_add_voice(audio, filename, content_type, ref_text)
    with httpx.Client(timeout=s.tts_timeout_seconds * 2, transport=_TRANSPORT) as http:
        res = http.post(
            ELEVENLABS_ADD_VOICE_URL,
            headers={"xi-api-key": s.elevenlabs_api_key},
            data={
                "name": name,
                "description": "drawtale guardian narrator",
                # 잡음 걷기는 끈다 (11/7): 브라우저도 잡음을 지운 녹음을 한 번 더 걸러 목소리 결이 바뀌었다.
                # 프론트가 날 녹음(44.1kHz)을 보내고, 화면이 조용한 곳에서 읽어 달라고 안내한다
                "remove_background_noise": "false",
            },
            files={"files": (filename, audio, content_type)},
        )
        res.raise_for_status()
        return res.json()["voice_id"]


def _qwen_add_voice(audio: bytes, filename: str, content_type: str, ref_text: str | None) -> str:
    data = {"ref_text": ref_text} if ref_text else {}
    with _qwen_client() as http:
        res = http.post(
            "/internal/v1/voices",
            files={"audio": (filename, audio, content_type)},
            data=data,
        )
        if res.status_code == 400:
            try:
                code = res.json()["error"]["code"]
            except (ValueError, KeyError, TypeError):
                code = ""
            if code in _QWEN_REJECTIONS:
                raise VoiceRejected(code, _QWEN_REJECTIONS[code])
        res.raise_for_status()
        return res.json()["voice_id"]


def delete_voice(voice_id: str) -> None:
    """목소리를 지운다. 이미 없으면 지운 것으로 본다.

    id 로 업체를 가린다 — 업체를 바꾼 뒤에도 예전 목소리를 그 업체에서 지운다.
    """

    if is_qwen_voice(voice_id):
        with _qwen_client() as http:
            res = http.delete(f"/internal/v1/voices/{voice_id}")
            if res.status_code != 404:
                res.raise_for_status()
        return

    s = get_settings()
    with httpx.Client(timeout=s.tts_timeout_seconds, transport=_TRANSPORT) as http:
        res = http.delete(
            ELEVENLABS_VOICE_URL.format(voice_id=voice_id),
            headers={"xi-api-key": s.elevenlabs_api_key},
        )
        if res.status_code != 404:
            res.raise_for_status()
