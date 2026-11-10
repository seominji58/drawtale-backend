import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin


class GuardianVoice(IdMixin, TimestampMixin, Base):
    """보호자 목소리 — 이야기 나레이터를 이 목소리로 바꾼다 (2026-11-02, 보호자 모드).

    녹음 파일은 저장하지 않는다. 등록할 때 ElevenLabs(미국)로 바로 넘기고 목소리 id 만 둔다.
    동의 기록은 목소리를 지운 뒤에도 남긴다 (언제 동의하고 언제 거둬들였는지).
    kind: guardian 은 보호자 본인 목소리, child 는 보호자가 동의하고 녹음한 아이 목소리 (2026-11-09 교수님 지시,
    시연용 · 나중에 엔진 교체). 계정마다 종류별로 하나.
    """

    __tablename__ = "guardian_voices"
    __table_args__ = (UniqueConstraint("user_id", "kind", name="uq_guardian_voices_user_kind"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(10), default="guardian", server_default="guardian")
    # ElevenLabs 목소리 id. 지우면 None (동의 기록은 남는다)
    elevenlabs_voice_id: Mapped[str | None] = mapped_column(String(64), default=None)
    # 동의한 안내문 판 (프론트 동의 화면의 판과 같다)
    consent_version: Mapped[str] = mapped_column(String(40))
    consented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
