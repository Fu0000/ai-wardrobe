from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from sqlalchemy import Boolean, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DELETION_PENDING = "DELETION_PENDING"
    DELETED = "DELETED"


class IdentityProvider(StrEnum):
    WECHAT = "WECHAT"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    status: Mapped[UserStatus] = mapped_column(
        Enum(
            UserStatus,
            name="user_status",
            native_enum=False,
            create_constraint=True,
            length=32,
        ),
        nullable=False,
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
    )

    identities: Mapped[list[UserIdentity]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    profile: Mapped[UserProfile | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )


class UserIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_identities"
    __table_args__ = (
        Index(
            "uq_user_identities_provider_subject_hash",
            "provider",
            "provider_subject_hash",
            unique=True,
        ),
        Index("ix_user_identities_user_id", "user_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[IdentityProvider] = mapped_column(
        Enum(
            IdentityProvider,
            name="identity_provider",
            native_enum=False,
            create_constraint=True,
            length=32,
        ),
        nullable=False,
    )
    provider_subject_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_subject_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    union_subject_hash: Mapped[str | None] = mapped_column(String(64))
    union_subject_encrypted: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="identities")


class UserProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    display_name: Mapped[str | None] = mapped_column(String(80))
    avatar_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "user_assets.id",
            name="fk_user_profiles_avatar_asset_id_user_assets",
            ondelete="SET NULL",
        )
    )
    consent_version: Mapped[str | None] = mapped_column(String(40))
    has_ai_processing_consent: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    user: Mapped[User] = relationship(back_populates="profile")
