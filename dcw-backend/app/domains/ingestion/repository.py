"""Ingestion repository — persists canonical HOS logs to PostgreSQL
and caches active driver state in Redis.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Set

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import active_drivers_key, cursor_key, get_redis
from app.core.security import hash_canonical_log
from app.domains.ingestion.models import CanonicalHOSLogRecord
from app.domains.ingestion.schemas import DCWCanonicalHOSLog

logger = logging.getLogger("dcw.ingestion.repository")


class IngestionRepository:
    """Data-access layer for the ingestion domain."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── PostgreSQL Persistence ───────────────────────────────────────────

    async def persist_canonical_logs(
        self,
        logs: List[DCWCanonicalHOSLog],
    ) -> int:
        """Insert canonical HOS logs into PostgreSQL (append-only, dedup by raw_id).

        Returns the number of rows actually inserted (skips duplicates).
        """
        if not logs:
            return 0

        inserted = 0
        for log in logs:
            # Compute inputs hash
            log_dict = log.model_dump(mode="json")
            inputs_hash = hash_canonical_log(log_dict)

            stmt = pg_insert(CanonicalHOSLogRecord).values(
                tenant_id=log.tenant_id,
                driver_id=log.driver_id,
                driver_name=log.driver_name,
                raw_id=log.raw_id,
                status=log.status.value,
                event_timestamp=log.event_timestamp,
                device_id=log.device_id,
                latitude=log.latitude,
                longitude=log.longitude,
                odometer_km=log.odometer_km,
                annotation=log.annotation,
                raw_payload=log.raw_payload,
                inputs_hash=inputs_hash,
            ).on_conflict_do_nothing(
                index_elements=["tenant_id", "raw_id"],
            )

            result = await self.session.execute(stmt)
            if result.rowcount:  # type: ignore[union-attr]
                inserted += result.rowcount  # type: ignore[union-attr]

        await self.session.flush()
        logger.info("Persisted %d/%d canonical HOS logs", inserted, len(logs))
        return inserted

    async def get_driver_timeline(
        self,
        tenant_id: str,
        driver_id: str,
        limit: int = 500,
    ) -> List[CanonicalHOSLogRecord]:
        """Fetch a driver's HOS timeline ordered by event_timestamp (newest first)."""
        stmt = (
            select(CanonicalHOSLogRecord)
            .where(
                CanonicalHOSLogRecord.tenant_id == tenant_id,
                CanonicalHOSLogRecord.driver_id == driver_id,
            )
            .order_by(CanonicalHOSLogRecord.event_timestamp.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ── Redis State Caching ──────────────────────────────────────────────

    @staticmethod
    async def update_active_drivers(driver_ids: Set[str]) -> None:
        """Add driver IDs to the Redis active_drivers set."""
        if not driver_ids:
            return
        redis = await get_redis()
        key = active_drivers_key()
        await redis.sadd(key, *driver_ids)
        # Expire after 24 hours to auto-clean stale entries
        await redis.expire(key, 86400)
        logger.debug("Updated active_drivers set with %d driver(s)", len(driver_ids))

    @staticmethod
    async def get_active_driver_ids() -> Set[str]:
        """Return the current set of active driver IDs from Redis."""
        redis = await get_redis()
        return await redis.smembers(active_drivers_key())

    @staticmethod
    async def save_cursor(provider: str, tenant_id: str, cursor_value: str) -> None:
        """Persist the polling cursor token in Redis for stateless resume."""
        redis = await get_redis()
        key = cursor_key(provider, tenant_id)
        await redis.set(key, cursor_value)
        logger.debug("Saved cursor %s = %s", key, cursor_value)

    @staticmethod
    async def load_cursor(provider: str, tenant_id: str) -> Optional[str]:
        """Load the last saved polling cursor from Redis."""
        redis = await get_redis()
        key = cursor_key(provider, tenant_id)
        return await redis.get(key)
