import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, SmallInteger, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin


class Story(IdMixin, TimestampMixin, Base):
    __tablename__ = "stories"

    character_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    place: Mapped[str] = mapped_column(String(50))
    problem: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(50))
    result: Mapped[str] = mapped_column(String(50))
    # S-13 「읽어주기」: narration | characters
    voice_mode: Mapped[str] = mapped_column(String(20), default="narration")
    # S-13 「주인공 목소리」: girl | boy
    protagonist_voice: Mapped[str] = mapped_column(String(10), default="girl")
    # 아이가 지은 주인공 이름 (S-05 다음).
    # 캐릭터 목소리 판에서 비어 있으면 GPT 가 지은 이름을 남긴다
    protagonist_name: Mapped[str | None] = mapped_column(String(20), default=None)
    # 캐릭터 목소리 판에서 정한 인물 이름 → ElevenLabs 목소리 id. 나레이션 판이면 없다
    voice_cast: Mapped[dict | None] = mapped_column(JSON, default=None)
    # 보호자 목소리로 읽을 때 그 ElevenLabs 목소리 id (요청 때 정해 둔다). 없으면 기본 나레이터.
    # 보호자가 목소리를 지우면 이 목소리로 만든 음성 파일도 지운다 (api/v1/voice.py)
    narrator_voice_id: Mapped[str | None] = mapped_column(String(64), default=None)
    # 이야기 길이: short(네 칸 → 네 문장) | long(쪽 단위 그림책, services/book.py)
    length: Mapped[str] = mapped_column(String(10), default="short")
    # 아이가 카드 대신 말로 덧붙인 단계 (place · problem · action · result).
    # 대본이 그 말을 그대로 살린다
    spoken: Mapped[list | None] = mapped_column(JSON, default=None)
    # 네 칸 사이에 더 받은 칸 {who, feel_problem, feel_result} — 인물 · 기분 카드 라벨.
    # 2026-10-27 로컬 시험 (회의에서 빼면 지운다). 긴 이야기 대본에만 쓴다
    extras: Mapped[dict | None] = mapped_column(JSON, default=None)
    # 아이 · 보호자가 정한 책 제목 (S-11 「다 했어요」 · S-12 책장 · S-13, 2026-11-04). 없으면 대본 제목 · 「{장소}에 간 날」
    title: Mapped[str | None] = mapped_column(String(40), default=None)
    # 긴 이야기 대본 — 제목, 쪽마다 단계 · 동작 · 글 · 아이 말, 순서 맞추기 쪽 (Book.to_json)
    book: Mapped[dict | None] = mapped_column(JSON, default=None)
    text: Mapped[str | None] = mapped_column(Text, default=None)
    audio_blob_path: Mapped[str | None] = mapped_column(String(500), default=None)
    animation_blob_path: Mapped[str | None] = mapped_column(String(500), default=None)
    # 배경을 잘라 낸 캐릭터 정지 그림의 이 이야기 사본 (2026-11-03).
    # 움직임 없는 쪽 · 완료 화면 · 표지에 쓴다.
    # 캐릭터 쪽 사본은 원본을 지울 때 같이 지워지므로, 영상처럼 이야기에 따로 둔다
    still_blob_path: Mapped[str | None] = mapped_column(String(500), default=None)


class StoryActivity(IdMixin, TimestampMixin, Base):
    """S-10 순서 맞추기를 한 번 한 기록. 같은 이야기를 다시 보면 기록이 하나 더 생긴다."""

    __tablename__ = "story_activities"

    story_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("stories.id", ondelete="CASCADE"), index=True
    )
    # 「다 했어요」를 누른 횟수. 맞힌 마지막 한 번도 센다
    attempts: Mapped[int] = mapped_column(Integer)
    # 맞히고 S-11 로 갔는지. 맞히기 전에 나가면 false
    completed: Mapped[bool] = mapped_column(Boolean)
    # 놓은 문장 카드 수 (Level 1 은 2장, 나머지는 4장)
    card_count: Mapped[int] = mapped_column(SmallInteger)
    # 그때의 지원 수준 (S-13). 없으면 모른다
    level: Mapped[int | None] = mapped_column(SmallInteger, default=None)
