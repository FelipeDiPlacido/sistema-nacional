import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    ngo: Mapped[NGO] = relationship(back_populates="owner", uselist=False)


class NGO(Base):
    __tablename__ = "ngos"
    __table_args__ = (Index("ix_ngos_status", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default=NGOStatus.PENDING_REVIEW)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    ngo: Mapped[NGO] = relationship(back_populates="certifications")
    certification: Mapped[Certification] = relationship(back_populates="ngo_certifications")


class NationalResource(Base):
    __tablename__ = "national_resources"
    __table_args__ = (
        CheckConstraint("total_quantity >= 0", name="ck_national_resources_total_nonnegative"),
        CheckConstraint(
            "committed_quantity >= 0", name="ck_national_resources_committed_nonnegative"
        ),
        CheckConstraint(
            "committed_quantity <= total_quantity",
            name="ck_national_resources_commitment_within_total",
        ),
        Index("ix_national_resources_is_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(64))
    unit: Mapped[str] = mapped_column(String(80))
    total_quantity: Mapped[int] = mapped_column(Integer)
    committed_quantity: Mapped[int] = mapped_column(Integer, default=0)
    required_certification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("certifications.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    required_certification: Mapped[Certification] = relationship()


class AllocationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class ResourceAllocation(Base):
    __tablename__ = "resource_allocations"
    __table_args__ = (Index("ix_resource_allocations_ngo_id", "ngo_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_key: Mapped[uuid.UUID] = mapped_column(unique=True)
    project_reference: Mapped[str] = mapped_column(String(160), unique=True)
    ngo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ngos.id"))
    risk_level: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=AllocationStatus.ACTIVE)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items: Mapped[list[ResourceAllocationItem]] = relationship(
        back_populates="allocation", cascade="all, delete-orphan"
    )


class ResourceAllocationItem(Base):
    __tablename__ = "resource_allocation_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_resource_allocation_items_quantity_positive"),
        Index("ix_resource_allocation_items_allocation_id", "allocation_id"),
        Index("ix_resource_allocation_items_resource_id", "resource_id"),
        UniqueConstraint(
            "allocation_id",
            "resource_id",
            name="uq_resource_allocation_items_allocation_resource",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    allocation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("resource_allocations.id"))
    resource_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("national_resources.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    allocation: Mapped[ResourceAllocation] = relationship(back_populates="items")
    resource: Mapped[NationalResource] = relationship()
