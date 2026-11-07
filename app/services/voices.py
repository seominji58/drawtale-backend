"""캐릭터 목소리 판(S-13 「읽어주기」)에서 인물마다 목소리를 정한다.

2026-10-08 청취로 골랐다. 후보와 샘플은 hankan-story/tts-samples/voice-catalog/목록.md (git 아님).
종류마다 첫 번째가 고른 목소리이고, 뒤는 한 이야기에 같은 종류 인물이 또 나올 때 쓰는 예비다.
모두 ElevenLabs 공유 목소리라 계정에 추가하지 않고 쓴다. 단, 할머니 첫 번째는 Voice Design 으로
만들어 **이 팀 계정에 저장한 목소리**라 다른 계정 키로는 쓸 수 없다.
"""

from collections.abc import Iterable

# 대본에서 말하는 사람 이름. GPT 가 이 이름으로 쓴다 (story_writer.script_prompt)
NARRATOR = "나레이션"
PROTAGONIST = "주인공"

VOICE_TYPES: dict[str, tuple[str, ...]] = {
    # Yuna · Annie · Yura
    "아이-여": ("xi3rF0t7dg7uN2M0WUhr", "Lb7qkOn5hF8p7qfCDH8q", "F7wT70V3u09d2rY9pNa6"),
    # RyanQuin · Austin — 한국어 원어민 남자아이 목소리가 없어 미국 아이 목소리를 쓴다
    "아이-남": ("tJHJUEHzOkMoPmJJ5jo2", "Xb3zeLrTi6F4ziIcXdwk"),
    # Seulki · Bomisori · Jiyoung
    "어른-여": ("ksaI0TCD9BstzEzlxj4q", "7oLyBHyhxAjrctX6ZQlw", "AW5wrnG1jVizOYY7R1Oo"),
    # Bin · Harry Kim · Hyunmin
    "어른-남": ("jB1Cifc2UQbq1gR3wnb0", "BNr4zvrC1bGIdIstzjFQ", "Ml2fm7pJDDTZqQkeGpRM"),
    # Drawtale 할머니 (계정에 저장한 디자인 목소리) · Haemi
    "할머니": ("pWQEEiHM3L2FJiD8qjMW", "ZjAPD4f11zlnEnZpKDgo"),
    # Namchun · Bongpal
    "할아버지": ("5ON5Fnz24cnOozEQfGAm", "PLfpgtLkFW07fDYbUiRJ"),
    # Manbo · Cherry Twinkle
    "작은 동물": ("ZZ4xhVcc83kZBfNIlIIz", "XJ2fW4ybq7HouelYYGcL"),
    # Deoksu · Jin Geon Song
    "큰 동물": ("IAETYMYM3nJvjnlkVTKI", "BbsagRO6ohd8MKPS2Ob0"),
}

# 이름이 곧 종류인 인물. GPT 가 할머니를 「어른-여」로 적은 적이 있어 이름을 먼저 본다
KIND_BY_NAME = (
    ("할머니", "할머니"),
    ("할아버지", "할아버지"),
    ("엄마", "어른-여"),
    ("아빠", "어른-남"),
)

# 주인공 「나」는 아이가 그린 그림이다. 어른이 S-13 에서 고른다.
# 그림이나 이름으로 성별을 짐작하지 않는다
PROTAGONIST_TYPES = {"girl": "아이-여", "boy": "아이-남"}

# 대사 감정 → v4 태그. 태그는 소리 내 읽지 않는다 (v4 는 speed 값을 무시하고 태그를 따른다).
# 아이 대상이라 화내기 · 소리치기는 두지 않는다
EMOTION_TAGS = {
    "기쁨": "[happy]",
    "다정": "[warmly]",
    "신남": "[excited]",
    "놀람": "[surprised]",
    "슬픔": "[sad]",
    "속삭임": "[whispers]",
}


def cast_voices(
    cast: dict[str, str],
    protagonist: str,
    narrator_voice: str,
    speakers: Iterable[str] = (),
) -> dict[str, str]:
    """인물 이름 → 목소리 id. 나레이터와 다른 인물과 겹치지 않게 고른다.

    종류의 목소리를 다 썼거나 모르는 종류면 아직 안 쓴 아무 목소리를 쓴다.
    speakers 는 대본에서 말하는 사람들이다 — GPT 가 cast 에 빠뜨린 인물도 목소리를 받는다.
    """

    used = {narrator_voice}

    def pick(kind: str | None) -> str:
        own = VOICE_TYPES.get(kind or "", ())
        for voice in (*own, *(v for vs in VOICE_TYPES.values() for v in vs)):
            if voice not in used:
                used.add(voice)
                return voice
        return own[0] if own else narrator_voice

    voices = {NARRATOR: narrator_voice}
    voices[PROTAGONIST] = pick(PROTAGONIST_TYPES.get(protagonist, PROTAGONIST_TYPES["girl"]))
    for name, kind in (*cast.items(), *((s, "") for s in speakers)):
        if name not in voices:
            kind = next((k for word, k in KIND_BY_NAME if word in name), kind)
            voices[name] = pick(kind)
    return voices


def tagged(text: str, emotion: str) -> str:
    tag = EMOTION_TAGS.get(emotion)
    return f"{tag} {text}" if tag else text
