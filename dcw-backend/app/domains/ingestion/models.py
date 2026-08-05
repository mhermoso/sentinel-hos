"""SQLAlchemy ORM models for ingestion tables.

``canonical_hos_logs`` — immutable HOS duty-status event store (ADR-003).
``gps_breadcrumbs`` — immutable GPS trail points (ADR-007); engine never reads.
``fleets`` — mutable registry of telematics connections (one row per fleet).
``driver_roster`` — mutable provider-agnostic driver contact/assignment cache.

SQL UPDATE and DELETE are blocked by PostgreSQL triggers attached in ``init_db``
for the append-only tables; ``fleets`` and ``driver_roster`` are mutable with
no append-only trigger.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


class DriverRosterRecord(Base):
    """Mutable per-driver roster row (contact + unit assignment cache).

    Keyed by ``(provider, tenant_id, external_driver_id)``. No provider-specific
    columns — dashboard filters use only these canonical fields.
    """

    __tablename__ = "driver_roster"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    provider = Column(String(32), nullable=False)
    tenant_id = Column(String(128), nullable=False)
    external_driver_id = Column(String(128), nullable=False)
    first_name = Column(String(256), nullable=True)
    last_name = Column(String(256), nullable=True)
    display_name = Column(String(512), nullable=True)
    phone_e164 = Column(String(32), nullable=True)
    current_device_id = Column(String(128), nullable=True)
    unit_label = Column(String(256), nullable=True)
    is_active = Column(Boolean, nullable=False, server_default=text("TRUE"))
    profile_complete = Column(Boolean, nullable=False, server_default=text("FALSE"))
    has_unit_assignment = Column(Boolean, nullable=False, server_default=text("FALSE"))
    synced_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "tenant_id",
            "external_driver_id",
            name="uq_driver_roster_provider_tenant_external",
        ),
        Index(
            "ix_driver_roster_tenant_external",
            "tenant_id",
            "external_driver_id",
        ),
        Index("ix_driver_roster_tenant_active", "tenant_id", "is_active"),
    )

    def __repr__(self) -> str:
        return (
            f"<DriverRosterRecord provider={self.provider} "
            f"driver={self.external_driver_id} active={self.is_active}>"
        )


class FleetRecord(Base):
    """Registry row for one telematics connection (fleet_id == tenant_id).

    Mutable configuration — never store credentials in ``config_json``.
    """

    __tablename__ = "fleets"

    fleet_id = Column(String(128), primary_key=True)
    provider = Column(String(32), nullable=False)
    display_name = Column(String(255), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True, server_default=text("TRUE"))
    config_json = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )

    def __repr__(self) -> str:
        return (
            f"<FleetRecord fleet_id={self.fleet_id} "
            f"provider={self.provider} enabled={self.enabled}>"
        )


class CanonicalHOSLogRecord(Base):
    """Append-only HOS log record stored in PostgreSQL."""

    __tablename__ = "canonical_hos_logs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id = Column(String(128), nullable=False, index=True)
    driver_id = Column(String(128), nullable=False, index=True)
    driver_name = Column(String(256), nullable=True)
    raw_id = Column(String(256), nullable=False)
    status = Column(String(16), nullable=False)
    event_timestamp = Column(
        DateTime(timezone=True), nullable=False, index=True
    )
    device_id = Column(String(128), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    odometer_km = Column(Float, nullable=True)
    annotation = Column(Text, nullable=True)
    raw_payload = Column(JSONB, nullable=False)
    inputs_hash = Column(String(64), nullable=False, index=True)
    ingested_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        Index(
            "ix_canonical_tenant_driver_ts",
            "tenant_id",
            "driver_id",
            "event_timestamp",
        ),
        Index(
            "ix_canonical_dedup",
            "tenant_id",
            "raw_id",
            unique=True,
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<CanonicalHOSLogRecord driver={self.driver_id} "
            f"status={self.status} ts={self.event_timestamp}>"
        )


class GpsBreadcrumbRecord(Base):
    """Append-only GPS breadcrumb (Geotab LogRecord / future providers)."""

    __tablename__ = "gps_breadcrumbs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id = Column(String(128), nullable=False, index=True)
    device_id = Column(String(128), nullable=False)
    driver_id = Column(String(128), nullable=False)
    raw_id = Column(String(256), nullable=False)
    event_timestamp = Column(DateTime(timezone=True), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    speed_kmh = Column(Float, nullable=True)
    raw_payload = Column(JSONB, nullable=False)
    inputs_hash = Column(String(64), nullable=False, index=True)
    ingested_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        Index(
            "ix_gps_breadcrumbs_dedup",
            "tenant_id",
            "raw_id",
            unique=True,
        ),
        Index(
            "ix_gps_breadcrumbs_tenant_driver_ts",
            "tenant_id",
            "driver_id",
            "event_timestamp",
        ),
        Index(
            "ix_gps_breadcrumbs_tenant_device_ts",
            "tenant_id",
            "device_id",
            "event_timestamp",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<GpsBreadcrumbRecord device={self.device_id} "
            f"driver={self.driver_id} ts={self.event_timestamp}>"
        )
