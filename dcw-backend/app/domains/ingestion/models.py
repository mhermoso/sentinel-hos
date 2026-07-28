"""SQLAlchemy ORM model for the canonical_hos_logs append-only table.

The table is the immutable event store described in the architecture spec.
SQL UPDATE and DELETE are blocked by a PostgreSQL trigger created in the
Alembic migration.
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
