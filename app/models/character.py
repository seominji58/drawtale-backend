import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin


class Character(IdMixin, TimestampMixin, Base):
    __tablename__ = "characters"

    status: Mapped[str] = mapped_column(String(20), default="pending")
    upload_blob_path: Mapped[str] = mapped_column(String(500))
    image_width: Mapped[int] = mapped_column(Integer)
    image_height: Mapped[int] = mapped_column(Integer)
    # 어른 설정 「원본 그림 보관」(S-13). 꺼져 있으면 그림을 떠날 때, 늦어도 보관 시간 뒤 지운다
    keep_original: Mapped[bool] = mapped_column(Boolean, default=False)
    # 원본(업로드 파일과 AI 세션)을 지운 때. upload_blob_path 는 기록으로 남긴다
    original_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # AI analyze result, as returned by the AI service
    bbox: Mapped[dict | None] = mapped_column(JSON, default=None)
    # 관절마다 {name, x, y, score}. score 는 AI 서버가 보내지 않으면 없다
    ai_joints: Mapped[list | None] = mapped_column(JSON, default=None)
    # 캐릭터 검출 점수 (0~1). 낮으면 프론트가 어른 확인을 권한다 (S-05-05)
    detection_confidence: Mapped[float | None] = mapped_column(Float, default=None)
    mask_blob_path: Mapped[str | None] = mapped_column(String(500), default=None)
    # AI 서버가 분석 결과(원본·마스크)를 보관하는 세션 id. 렌더링 요청에 필요하다.
    ai_request_id: Mapped[str | None] = mapped_column(String(64), default=None)
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
