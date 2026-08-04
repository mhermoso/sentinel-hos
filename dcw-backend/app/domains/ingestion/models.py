"""SQLAlchemy ORM models for append-only ingestion tables.

``canonical_hos_logs`` — immutable HOS duty-status event store (ADR-003).
``gps_breadcrumbs`` — immutable GPS trail points (ADR-007); engine never reads.

SQL UPDATE and DELETE are blocked by PostgreSQL triggers attached in ``init_db``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.core.database import Base


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
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        Index(
            "ix_canonical_tenant_driver_ts",
            "tenant_id",
            "driver_id",
            "event_timestamp",
        ),
        # Geotab re-emits the same DutyStatusLog id on edits; allow one row
        # per distinct content hash so superseding versions append.
        Index(
            "ix_canonical_dedup",
            "tenant_id",
            "raw_id",
            "inputs_hash",
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
        default=lambda: datetime.now(timezone.utc),
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
