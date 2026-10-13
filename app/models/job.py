import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin


class Job(IdMixin, TimestampMixin, Base):
    __tablename__ = "jobs"

    type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    character_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("characters.id", ondelete="CASCADE"), default=None, index=True
    )
    story_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("stories.id", ondelete="CASCADE"), default=None, index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(50), default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
