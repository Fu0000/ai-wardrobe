from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class AssetStatus(StrEnum):
    UPLOADING = "UPLOADING"
    READY = "READY"
    FAILED = "FAILED"
    DELETION_PENDING = "DELETION_PENDING"
    DELETED = "DELETED"


class AssetKind(StrEnum):
    USER_UPLOAD = "USER_UPLOAD"
    OPTIMIZATION_RESULT = "OPTIMIZATION_RESULT"
    SHARE_DERIVATIVE = "SHARE_DERIVATIVE"


class PhotoPurpose(StrEnum):
    OUTFIT_DIAGNOSIS = "OUTFIT_DIAGNOSIS"


class UserAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_assets"
    __table_args__ = (
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="size_nonnegative"),
        CheckConstraint("width IS NULL OR width > 0", name="width_positive"),
        CheckConstraint("height IS NULL OR height > 0", name="height_positive"),
        Index("ix_user_assets_user_status", "user_id", "status"),
        Index("uq_user_assets_object_key", "object_key", unique=True),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[AssetKind] = mapped_column(
        Enum(
            AssetKind,
            name="asset_kind",
            native_enum=False,
            create_constraint=True,
            length=40,
        ),
        nullable=False,
    )
    status: Mapped[AssetStatus] = mapped_column(
        Enum(
            AssetStatus,
            name="asset_status",
            native_enum=False,
            create_constraint=True,
            length=32,
        ),
        nullable=False,
        default=AssetStatus.UPLOADING,
        server_default=AssetStatus.UPLOADING.value,
    )
    bucket: Mapped[str] = mapped_column(String(120), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))

    source_photo: Mapped[SourcePhoto | None] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        uselist=False,
    )


class SourcePhoto(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "source_photos"
    __table_args__ = (Index("ix_source_photos_user_id", "user_id"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_assets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    purpose: Mapped[PhotoPurpose] = mapped_column(
        Enum(
            PhotoPurpose,
            name="photo_purpose",
            native_enum=False,
            create_constraint=True,
            length=40,
        ),
        nullable=False,
    )

    asset: Mapped[UserAsset] = relationship(back_populates="source_photo")
