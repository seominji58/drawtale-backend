from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin


class CardStory(IdMixin, TimestampMixin, Base):
    """미리 만든 카드 이야기 (pt_docs 13 1-1 · 09 8장 안). 아이 자료가 아니라 모든 계정이 같이 쓴다.

    조합(장소 · 문제 · 행동 · 결과) × 길이 × 읽어주기마다 하나. 사람이 읽고 approved 로 바꾼 것만 꺼내 준다.
    주인공 이름 자리는 `{이름}{조사:가}` 로 비어 있다 (services/card_stories.fill_name).
    """

    __tablename__ = "card_stories"
    __table_args__ = (
        UniqueConstraint("place", "problem", "action", "result", "length", "voice_mode", name="uq_card_story"),
    )

    place: Mapped[str] = mapped_column(String(50))
    problem: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(50))
    result: Mapped[str] = mapped_column(String(50))
    # short | long
    length: Mapped[str] = mapped_column(String(10))
    # narration | characters
    voice_mode: Mapped[str] = mapped_column(String(20))
    # 긴 이야기 {"book": Book.to_json(), "cast"} · 짧은 이야기 {"text"} 또는 {"sentences", "cast"}
    payload: Mapped[dict] = mapped_column(JSON)
    # draft(만들기만 함) | approved(사람이 읽고 통과) | rejected(다시 만들기)
    status: Mapped[str] = mapped_column(String(10), default="draft", index=True)
    # 만든 모델과 지시문 판 — 바꾸면 다시 만들고 다시 읽는다 (13 7장)
    model: Mapped[str] = mapped_column(String(60), default="")
    prompt_version: Mapped[str] = mapped_column(String(40), default="")
    reviewed_by: Mapped[str | None] = mapped_column(String(60), default=None)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # 검토 메모 (다시 만들 까닭 등)
    note: Mapped[str | None] = mapped_column(Text, default=None)
