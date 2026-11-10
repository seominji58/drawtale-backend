import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin


class User(IdMixin, TimestampMixin, Base):
    """A guardian or teacher account. Children never sign in.

    Deliberately stores no name, email or photo: social login keeps only the provider's
    member id (SocialAccount), which is all we need to recognise a returning user.
    """

    __tablename__ = "users"

    # When the user accepted our terms and privacy policy (the provider's consent screen
    # does not cover our own terms). A user cannot exist without it.
    terms_agreed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 어느 판의 약관 · 개인정보 처리방침에 동의했는지 (프론트 src/legal LEGAL_VERSION, 2026-11-03).
    # 그 전에 가입한 계정은 비어 있다
    consent_version: Mapped[str | None] = mapped_column(String(40), default=None)
    # 보호자 비밀번호(숫자 4자리)의 해시 — 설정 「보호자 칸」 · 아이 화면의 잠긴 보호자 기능을 연다
    # (pt_docs 20 3장, 2026-11-06). 비밀번호 자체는 남기지 않는다 (app/services/guardian_pin.py)
    guardian_pin_hash: Mapped[str | None] = mapped_column(String(160), default=None)
    # 연달아 틀린 횟수와, 다섯 번 틀려 잠시 막힌 때까지
    guardian_pin_failures: Mapped[int] = mapped_column(Integer, default=0)
    guardian_pin_locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # 보호자 소개 카드를 본 때 — 다른 기기에서 다시 뜨지 않게
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # 보호자 나이 확인 (본인인증, 2026-11-09). 확인한 때 · 방법 이름 · 포트원 인증 건 번호만 남긴다 , 
    # 이름 · 전화번호 · 생년월일 · CI 는 받자마자 버린다 (pt_docs policy 아동-개인정보-AI안전 3-3 「어떻게 확인했는지, 방법 이름만」).
    # 인증 건 번호는 한 건을 여러 계정에 쓰지 못하게 하려고 둔다 (unique)
    age_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    age_verification_method: Mapped[str | None] = mapped_column(String(40), default=None)
    age_verification_id: Mapped[str | None] = mapped_column(String(100), default=None, unique=True)

    social_accounts: Mapped[list["SocialAccount"]] = relationship(back_populates="user")


class SocialAccount(IdMixin, TimestampMixin, Base):
    __tablename__ = "social_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20))
    provider_user_id: Mapped[str] = mapped_column(String(128))

    user: Mapped[User] = relationship(back_populates="social_accounts")


class AuthToken(IdMixin, TimestampMixin, Base):
    """Opaque bearer token. Only its SHA-256 is stored, so a DB leak does not leak sessions."""

    __tablename__ = "auth_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
