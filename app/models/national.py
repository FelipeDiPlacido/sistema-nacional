import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class NGOStatus(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    ENABLED = "ENABLED"
    SUSPENDED = "SUSPENDED"


class NGOCertificationStatus(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
    ngo: Mapped[NGO] = relationship(back_populates="owner", uselist=False)


class NGO(Base):
    __tablename__ = "ngos"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default=NGOStatus.PENDING_REVIEW)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )
    owner: Mapped[User] = relationship(back_populates="ngo")
    certifications: Mapped[list[NGOCertification]] = relationship(
        back_populates="ngo", cascade="all, delete-orphan"
    )


class Certification(Base):
    __tablename__ = "certifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    ngo_certifications: Mapped[list[NGOCertification]] = relationship(
        back_populates="certification"
    )


class NGOCertification(Base):
    __tablename__ = "ngo_certifications"

    ngo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ngos.id"), primary_key=True)
    certification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("certifications.id"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(32), default=NGOCertificationStatus.PENDING_REVIEW)
    allowed_risk_levels: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    ngo: Mapped[NGO] = relationship(back_populates="certifications")
    certification: Mapped[Certification] = relationship(back_populates="ngo_certifications")
