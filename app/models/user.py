import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
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
