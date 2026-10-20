import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.common import JobStatus


class VoiceMode(StrEnum):
    """S-13 「읽어주기」. 나레이터 하나로 읽을지, 대사를 등장인물 목소리로 읽을지."""

    narration = "narration"
    characters = "characters"


class ProtagonistVoice(StrEnum):
    """S-13 「주인공 목소리」. 캐릭터 목소리 판에서 주인공 「나」의 대사를 읽는 목소리."""

    girl = "girl"
    boy = "boy"


class StoryLength(StrEnum):
    """이야기 길이.

    short: 네 칸 → 네 문장 · long: 네 칸을 뼈대로 쪽 단위 그림책 (services/book.py)
    """

    short = "short"
    long = "long"


class Stage(StrEnum):
    place = "place"
    problem = "problem"
    action = "action"
    result = "result"


class StoryCreateRequest(BaseModel):
    character_id: uuid.UUID
    place: str = Field(min_length=1, max_length=50, examples=["학교"])
    problem: str = Field(min_length=1, max_length=50, examples=["친구와 다퉜어요"])
    action: str = Field(min_length=1, max_length=50, examples=["먼저 사과해요"])
    result: str = Field(min_length=1, max_length=50, examples=["다시 사이좋게 놀아요"])
    voice_mode: VoiceMode = Field(
        default=VoiceMode.narration,
        description=(
            "narration: 나레이터 하나 · "
            "characters: 대사를 인물 목소리로 (이야기 글에 대사가 들어간다)"
        ),
    )
    protagonist_voice: ProtagonistVoice = Field(
        default=ProtagonistVoice.girl,
        description="캐릭터 목소리 판에서 주인공 대사 목소리. girl: 여자아이 · boy: 남자아이",
    )
    protagonist_name: str | None = Field(
        default=None,
        max_length=10,
        examples=["토토"],
        description=(
            "아이가 지은 주인공 이름. 캐릭터 목소리 판의 나레이션이 이 이름으로 3인칭으로 부른다"
        ),
    )
    length: StoryLength = Field(
        default=StoryLength.short,
        description=(
            "short: 네 문장 (지금까지) · long: 쪽 단위 그림책. 쪽 수는 서버 설정 STORY_PAGES"
        ),
    )
    spoken: list[Stage] = Field(
        default_factory=list,
        description=(
            "카드 글자 대신 아이가 말로 덧붙인 말을 보낸 단계. "
            "long 이면 대본이 그 말을 그대로 살리고 "
            "그 쪽에 child_words 로 표시한다"
        ),
    )


class BookLine(BaseModel):
    speaker: str
    text: str
    emotion: str = ""


class BookPage(BaseModel):
    page: int
    stage: Stage
    action: str = Field(
        description="그 쪽에서 캐릭터가 하는 동작: idle · walk · run · jump · wave · dance"
    )
    text: str = Field(description="화면에 보일 글. 대사는 “ ” 로 감쌌다")
    child_words: str = Field(
        default="", description="이 쪽에 그대로 들어간 아이 말. 없으면 빈 문자열"
    )
    lines: list[BookLine]


class Book(BaseModel):
    title: str
    pages: list[BookPage]
    order_pages: list[int] = Field(
        description="S-10 순서 맞추기에 쓸 쪽 번호. 장소 · 문제 · 행동 · 결과 순"
    )


class StoryCreateResponse(BaseModel):
    story_id: uuid.UUID
    job_id: uuid.UUID
    status: JobStatus


class StoryResponse(BaseModel):
    id: uuid.UUID
    character_id: uuid.UUID
    status: JobStatus
    place: str
    problem: str
    action: str
    result: str
    voice_mode: VoiceMode
    protagonist_voice: ProtagonistVoice
    protagonist_name: str | None
    length: StoryLength
    book: Book | None = Field(
        default=None, description="length=long 일 때만. text 는 쪽 글을 이어 붙인 것"
    )
    text: str | None
    audio_url: str | None
    animation_url: str | None
    created_at: datetime
    updated_at: datetime


class ActivityCreateRequest(BaseModel):
    """S-10 순서 맞추기 한 번의 결과."""

    attempts: int = Field(ge=1, le=100, description="「다 했어요」를 누른 횟수 (맞힌 마지막 포함)")
    completed: bool = Field(description="맞히고 S-11 로 갔는지")
    card_count: int = Field(ge=2, le=4, description="놓은 문장 카드 수")
    level: int | None = Field(default=None, ge=1, le=3, description="지원 수준")


class ActivityResponse(ActivityCreateRequest):
    id: uuid.UUID
    story_id: uuid.UUID
    created_at: datetime
