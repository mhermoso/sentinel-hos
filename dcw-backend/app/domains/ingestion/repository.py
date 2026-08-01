"""Ingestion repository — persists canonical HOS logs / GPS breadcrumbs
to PostgreSQL and caches active driver state in Redis.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional, Set

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import active_drivers_key, cursor_key, get_redis
from app.core.security import hash_canonical_log, hash_gps_breadcrumb
from app.domains.ingestion.models import CanonicalHOSLogRecord, GpsBreadcrumbRecord
from app.domains.ingestion.schemas import DCWCanonicalHOSLog, DCWGpsBreadcrumb

logger = logging.getLogger("dcw.ingestion.repository")


def _driver_resolution_cache_key(device_id: str, as_of: datetime) -> str:
    """Cache key for device→driver resolution at a specific as-of instant."""
    return f"{device_id}|{as_of.isoformat()}"


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

    async def persist_gps_breadcrumbs(
        self,
        crumbs: List[DCWGpsBreadcrumb],
    ) -> int:
        """Insert GPS breadcrumbs (append-only, dedup by tenant_id + raw_id)."""
        if not crumbs:
            return 0

        inserted = 0
        for crumb in crumbs:
            crumb_dict = crumb.model_dump(mode="json")
            inputs_hash = hash_gps_breadcrumb(crumb_dict)

            stmt = pg_insert(GpsBreadcrumbRecord).values(
                tenant_id=crumb.tenant_id,
                device_id=crumb.device_id,
                driver_id=crumb.driver_id,
                raw_id=crumb.raw_id,
                event_timestamp=crumb.event_timestamp,
                latitude=crumb.latitude,
                longitude=crumb.longitude,
                speed_kmh=crumb.speed_kmh,
                raw_payload=crumb.raw_payload,
                inputs_hash=inputs_hash,
            ).on_conflict_do_nothing(
                index_elements=["tenant_id", "raw_id"],
            )

            result = await self.session.execute(stmt)
            if result.rowcount:  # type: ignore[union-attr]
                inserted += result.rowcount  # type: ignore[union-attr]

        await self.session.flush()
        logger.info("Persisted %d/%d GPS breadcrumbs", inserted, len(crumbs))
        return inserted

    async def resolve_driver_for_device(
        self,
        tenant_id: str,
        device_id: str,
        as_of: datetime,
        cache: Optional[Dict[str, str]] = None,
    ) -> str:
        """Resolve driver_id from latest HOS log for device at or before ``as_of``.

        Falls back to ``unassigned:device:{device_id}``. Uses optional in-memory
        ``cache`` keyed by ``device_id|as_of`` so mid-batch device handoffs still
        resolve per breadcrumb timestamp (append-only rows cannot be corrected later).
        """
        cache_key = _driver_resolution_cache_key(device_id, as_of)
        if cache is not None and cache_key in cache:
            return cache[cache_key]

        stmt = (
            select(CanonicalHOSLogRecord.driver_id)
            .where(
                CanonicalHOSLogRecord.tenant_id == tenant_id,
                CanonicalHOSLogRecord.device_id == device_id,
                CanonicalHOSLogRecord.event_timestamp <= as_of,
            )
            .order_by(CanonicalHOSLogRecord.event_timestamp.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        driver_id = result.scalar_one_or_none()
        if driver_id:
            if cache is not None:
                cache[cache_key] = driver_id
            return driver_id
        return f"unassigned:device:{device_id}"

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

    async def get_gps_breadcrumbs_for_driver(
        self,
        tenant_id: str,
        driver_id: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> List[GpsBreadcrumbRecord]:
        """Fetch GPS breadcrumbs for a driver in ``[start_utc, end_utc)``."""
        stmt = (
            select(GpsBreadcrumbRecord)
            .where(
                GpsBreadcrumbRecord.tenant_id == tenant_id,
                GpsBreadcrumbRecord.driver_id == driver_id,
                GpsBreadcrumbRecord.event_timestamp >= start_utc,
                GpsBreadcrumbRecord.event_timestamp < end_utc,
            )
            .order_by(GpsBreadcrumbRecord.event_timestamp.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def _distinct_device_ids_for_driver_day(
        self,
        tenant_id: str,
        driver_id: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> List[str]:
        """Return distinct non-null device IDs from a driver's HOS logs in a day window."""
        stmt = (
            select(CanonicalHOSLogRecord.device_id)
            .where(
                CanonicalHOSLogRecord.tenant_id == tenant_id,
                CanonicalHOSLogRecord.driver_id == driver_id,
                CanonicalHOSLogRecord.event_timestamp >= start_utc,
                CanonicalHOSLogRecord.event_timestamp < end_utc,
                CanonicalHOSLogRecord.device_id.isnot(None),
            )
            .distinct()
        )
        result = await self.session.execute(stmt)
        return [device_id for device_id in result.scalars().all() if device_id]

    async def get_gps_breadcrumbs_for_driver_day_route(
        self,
        tenant_id: str,
        driver_id: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> List[GpsBreadcrumbRecord]:
        """Fetch GPS breadcrumbs for a driver's day route.

        Includes rows attributed to ``driver_id`` and *unassigned* crumbs on
        devices the driver used that day (from HOS logs), so device trails still
        render when ingest lacked a HOS match. Does **not** include crumbs
        attributed to other drivers on a shared device (handoff privacy).
        Results are ordered by timestamp and deduplicated by ``raw_id``.
        """
        device_ids = await self._distinct_device_ids_for_driver_day(
            tenant_id, driver_id, start_utc, end_utc
        )
        match_clauses = [GpsBreadcrumbRecord.driver_id == driver_id]
        if device_ids:
            match_clauses.append(
                and_(
                    GpsBreadcrumbRecord.device_id.in_(device_ids),
                    GpsBreadcrumbRecord.driver_id.like("unassigned:%"),
                )
            )

        stmt = (
            select(GpsBreadcrumbRecord)
            .where(
                GpsBreadcrumbRecord.tenant_id == tenant_id,
                GpsBreadcrumbRecord.event_timestamp >= start_utc,
                GpsBreadcrumbRecord.event_timestamp < end_utc,
                or_(*match_clauses),
            )
            .order_by(GpsBreadcrumbRecord.event_timestamp.asc())
        )
        result = await self.session.execute(stmt)
        seen_raw_ids: set[str] = set()
        deduped: List[GpsBreadcrumbRecord] = []
        for crumb in result.scalars().all():
            if crumb.raw_id in seen_raw_ids:
                continue
            seen_raw_ids.add(crumb.raw_id)
            deduped.append(crumb)
        return deduped

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
