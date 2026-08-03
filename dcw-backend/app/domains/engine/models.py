"""SQLAlchemy ORM models for the compliance engine layer.

Tables:
- ``audit_records`` — immutable cryptographically-linked compliance outputs.
- ``log_event_edits`` — full FMCSA § 395 edit audit trail.
- ``driver_profiles`` — mutable per-driver ruleset configuration (Phase 3+).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class DriverProfileRecord(Base):
    """Tenant-scoped driver profile used for daily ruleset selection.

    Mutable onboarding/config table (not append-only). Missing rows are
    treated as interstate 70/8 defaults by the repository layer.
    """

    __tablename__ = "driver_profiles"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id = Column(String(128), nullable=False)
    driver_id = Column(String(128), nullable=False)
    operating_authority = Column(
        String(32), nullable=False, server_default=text("'INTERSTATE'")
    )
    short_haul_eligible = Column(Boolean, nullable=False, server_default=text("false"))
    cdl_required = Column(Boolean, nullable=False, server_default=text("true"))
    cycle = Column(String(16), nullable=False, server_default=text("'70_8'"))
    home_terminal_timezone = Column(String(64), nullable=False)
    work_reporting_lat = Column(Float, nullable=True)
    work_reporting_lon = Column(Float, nullable=True)
    vehicle_weight_class = Column(String(64), nullable=True)
    hazmat_placard = Column(Boolean, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "driver_id", name="uq_driver_profiles_tenant_driver"),
        Index("ix_driver_profiles_tenant_driver", "tenant_id", "driver_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<DriverProfileRecord driver={self.driver_id} "
            f"authority={self.operating_authority} cycle={self.cycle}>"
        )



class AuditRecord(Base):
    """Immutable compliance evaluation output stored in ``audit_records``.

    Every record binds the SHA-256 inputs hash to a specific rule pack
    version, making compliance outputs fully reproducible (ADR-004).
    """

    __tablename__ = "audit_records"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id = Column(String(128), nullable=False, index=True)
    driver_id = Column(String(128), nullable=False, index=True)
    inputs_hash = Column(String(64), nullable=False, index=True)
    rule_pack_version = Column(String(64), nullable=False)

    # Compliance outputs
    driving_remaining_seconds = Column(Float, nullable=False)
    duty_window_remaining_seconds = Column(Float, nullable=False)
    break_required = Column(Boolean, nullable=False, default=False)
    weekly_hours_used = Column(Float, nullable=False)
    weekly_hours_remaining = Column(Float, nullable=False)
    is_compliant = Column(Boolean, nullable=False, default=True)
    violations = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    raw_output = Column(JSONB, nullable=False)

    evaluated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        Index(
            "ix_audit_tenant_driver_eval",
            "tenant_id",
            "driver_id",
            "evaluated_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditRecord driver={self.driver_id} "
            f"compliant={self.is_compliant} eval={self.evaluated_at}>"
        )


class LogEventEdit(Base):
    """FMCSA § 395 edit audit trail stored in ``log_event_edits``.

    Tracks every change to canonical HOS log records including editor
    identity, justification, and driver sign-off status.
    """

    __tablename__ = "log_event_edits"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id = Column(String(128), nullable=False, index=True)
    canonical_log_id = Column(
        UUID(as_uuid=True),
        ForeignKey("canonical_hos_logs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    edited_by = Column(String(256), nullable=False)
    editor_role = Column(String(64), nullable=False, default="DISPATCHER")
    field_changed = Column(String(128), nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    justification = Column(Text, nullable=False)
    driver_signed_off = Column(Boolean, nullable=False, default=False)
    driver_sign_off_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    def __repr__(self) -> str:
        return (
            f"<LogEventEdit log={self.canonical_log_id} "
            f"field={self.field_changed} by={self.edited_by}>"
        )
