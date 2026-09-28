import uuid

from sqlalchemy import JSON, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin


class Character(IdMixin, TimestampMixin, Base):
    __tablename__ = "characters"

    status: Mapped[str] = mapped_column(String(20), default="pending")
    upload_blob_path: Mapped[str] = mapped_column(String(500))
    image_width: Mapped[int] = mapped_column(Integer)
    image_height: Mapped[int] = mapped_column(Integer)

    # AI analyze result, as returned by the AI service
    bbox: Mapped[dict | None] = mapped_column(JSON, default=None)
    ai_joints: Mapped[list | None] = mapped_column(JSON, default=None)
    mask_blob_path: Mapped[str | None] = mapped_column(String(500), default=None)
    model_version: Mapped[str | None] = mapped_column(String(100), default=None)
    pipeline_version: Mapped[str | None] = mapped_column(String(50), default=None)
    coordinate_space: Mapped[str | None] = mapped_column(String(20), default=None)
    processing_time_ms: Mapped[int | None] = mapped_column(Integer, default=None)

    corrections: Mapped[list["JointCorrection"]] = relationship(
        back_populates="character", order_by="JointCorrection.created_at"
    )


class JointCorrection(IdMixin, TimestampMixin, Base):
    """History of user joint edits. The latest row is the character's current skeleton."""

    __tablename__ = "joint_corrections"

    character_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    joints: Mapped[list] = mapped_column(JSON)

    character: Mapped[Character] = relationship(back_populates="corrections")
