import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin


class Character(IdMixin, TimestampMixin, Base):
    __tablename__ = "characters"

    # 올린 사람. 로그인하지 않고도 쓸 수 있으므로(F-ACC-01) 비어 있을 수 있다.
    # 주인이 있는 그림은 그 사람만 보고 고칠 수 있다 (app/core/authz.py)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), default=None, index=True
    )
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
    # S-05 미리보기 MP4 (Meta 렌더, 분석 직후와 관절 보정 뒤). 원본을 지우면 같이 지운다
    preview_blob_path: Mapped[str | None] = mapped_column(String(500), default=None)
    # rendering | ready | failed. 미리보기를 만들지 않으면(목 AI, 원본 지움) 없다
    preview_status: Mapped[str | None] = mapped_column(String(10), default=None)
    # 긴 이야기 쪽 동작 영상을 미리 렌더해 둔 것 {동작: 경로}.
    # 미리보기에 이어서 만든다 (jobs.PRERENDER_MOTIONS).
    # None: 만들지 않음(예전 그림 · 목 AI · 원본 지움) · {}: 만드는 중 ·
    # 동작이 키로 있으면 끝남(실패는 값 None)
    motion_clips: Mapped[dict | None] = mapped_column(JSON, default=None)
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
