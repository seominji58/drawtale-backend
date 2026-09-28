import uuid

from sqlalchemy import ForeignKey, String, Text, Uuid
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
    text: Mapped[str | None] = mapped_column(Text, default=None)
    audio_blob_path: Mapped[str | None] = mapped_column(String(500), default=None)
    animation_blob_path: Mapped[str | None] = mapped_column(String(500), default=None)
