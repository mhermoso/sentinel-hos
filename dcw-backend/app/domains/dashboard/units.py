"""Unit list/detail enrichment from vehicle_roster + driver_roster + HOS/GPS."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.dashboard.driver_names import resolve_driver_name
from app.domains.dashboard.schemas import (
    UnitAssigneeResponse,
    UnitDetailResponse,
    UnitGpsPointResponse,
    UnitListItemResponse,
)
from app.domains.ingestion.models import CanonicalHOSLogRecord, GpsBreadcrumbRecord
from app.domains.ingestion.roster import is_real_person_driver_id
from app.domains.ingestion.roster_repository import RosterRepository
from app.domains.ingestion.schemas import DriverRosterEntry, VehicleRosterEntry
from app.domains.ingestion.vehicle_roster_repository import VehicleRosterRepository


async def _latest_status_for_driver(
    session: AsyncSession,
    tenant_id: str,
    driver_id: str,
) -> str | None:
    stmt = (
        select(CanonicalHOSLogRecord.status)
        .where(
            CanonicalHOSLogRecord.tenant_id == tenant_id,
            CanonicalHOSLogRecord.driver_id == driver_id,
        )
        .order_by(CanonicalHOSLogRecord.event_timestamp.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _latest_real_driver_for_device(
    session: AsyncSession,
    tenant_id: str,
    device_id: str,
) -> str | None:
    """Latest non-sentinel driver_id seen on HOS for this device."""
    stmt = (
        select(CanonicalHOSLogRecord.driver_id)
        .where(
            CanonicalHOSLogRecord.tenant_id == tenant_id,
            CanonicalHOSLogRecord.device_id == device_id,
        )
        .order_by(CanonicalHOSLogRecord.event_timestamp.desc())
        .limit(50)
    )
    result = await session.execute(stmt)
    for driver_id in result.scalars().all():
        if driver_id and is_real_person_driver_id(str(driver_id)):
            return str(driver_id)
    return None


async def _latest_gps_for_device(
    session: AsyncSession,
    tenant_id: str,
    device_id: str,
) -> GpsBreadcrumbRecord | None:
    stmt = (
        select(GpsBreadcrumbRecord)
        .where(
            GpsBreadcrumbRecord.tenant_id == tenant_id,
            GpsBreadcrumbRecord.device_id == device_id,
        )
        .order_by(GpsBreadcrumbRecord.event_timestamp.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _assignees_for_device(
    device_id: str,
    drivers: list[DriverRosterEntry],
) -> list[DriverRosterEntry]:
    return [d for d in drivers if d.current_device_id == device_id]


def resolve_current_driver_id(
    vehicle: VehicleRosterEntry,
    assignees: list[DriverRosterEntry],
    hos_last_driver_id: str | None,
) -> str | None:
    """Prefer vehicle cache → single roster assignee → latest HOS person."""
    if vehicle.current_driver_id and is_real_person_driver_id(vehicle.current_driver_id):
        return vehicle.current_driver_id
    if len(assignees) == 1:
        return assignees[0].external_driver_id
    return hos_last_driver_id


def _driver_display(
    driver_id: str | None,
    roster_by_id: dict[str, DriverRosterEntry],
) -> str | None:
    if not driver_id:
        return None
    roster = roster_by_id.get(driver_id)
    return resolve_driver_name(driver_id, roster.display_name if roster else None)


async def list_units_for_tenant(
    session: AsyncSession,
    tenant_id: str,
) -> list[UnitListItemResponse]:
    """Build enriched unit rows for the active fleet."""
    vehicles = await VehicleRosterRepository(session).list_for_tenant(tenant_id)
    drivers = await RosterRepository(session).list_for_tenant(tenant_id)
    roster_by_id = {d.external_driver_id: d for d in drivers}

    items: list[UnitListItemResponse] = []
    for vehicle in vehicles:
        assignees = _assignees_for_device(vehicle.external_device_id, drivers)
        hos_last = await _latest_real_driver_for_device(
            session, tenant_id, vehicle.external_device_id
        )
        current_driver_id = resolve_current_driver_id(vehicle, assignees, hos_last)
        status = None
        if current_driver_id:
            status = await _latest_status_for_driver(session, tenant_id, current_driver_id)
        gps = await _latest_gps_for_device(session, tenant_id, vehicle.external_device_id)
        items.append(
            UnitListItemResponse(
                device_id=vehicle.external_device_id,
                name=vehicle.name,
                vin=vehicle.vin,
                current_driver_id=current_driver_id,
                current_driver_name=_driver_display(current_driver_id, roster_by_id),
                current_status=status,
                assignee_count=len(assignees),
                last_gps_at=gps.event_timestamp if gps else None,
                last_gps_lat=float(gps.latitude) if gps else None,
                last_gps_lon=float(gps.longitude) if gps else None,
            )
        )

    items.sort(
        key=lambda u: (
            0 if u.name else 1,
            (u.name or "").lower(),
            u.device_id,
        )
    )
    return items


async def get_unit_detail(
    session: AsyncSession,
    tenant_id: str,
    device_id: str,
) -> UnitDetailResponse | None:
    """Return detail for one unit, or None when not in vehicle_roster."""
    vehicle = await VehicleRosterRepository(session).get_by_external_id(tenant_id, device_id)
    if vehicle is None:
        return None

    drivers = await RosterRepository(session).list_for_tenant(tenant_id)
    roster_by_id = {d.external_driver_id: d for d in drivers}
    assignees = _assignees_for_device(device_id, drivers)
    hos_last = await _latest_real_driver_for_device(session, tenant_id, device_id)
    current_driver_id = resolve_current_driver_id(vehicle, assignees, hos_last)
    status = None
    if current_driver_id:
        status = await _latest_status_for_driver(session, tenant_id, current_driver_id)
    gps = await _latest_gps_for_device(session, tenant_id, device_id)
    last_gps: UnitGpsPointResponse | None = None
    if gps is not None:
        last_gps = UnitGpsPointResponse(
            latitude=float(gps.latitude),
            longitude=float(gps.longitude),
            event_timestamp=gps.event_timestamp,
            driver_id=str(gps.driver_id) if gps.driver_id else None,
        )

    return UnitDetailResponse(
        device_id=device_id,
        name=vehicle.name,
        vin=vehicle.vin,
        provider=vehicle.provider,
        current_driver_id=current_driver_id,
        current_driver_name=_driver_display(current_driver_id, roster_by_id),
        current_status=status,
        assignees=[
            UnitAssigneeResponse(
                driver_id=a.external_driver_id,
                display_name=resolve_driver_name(a.external_driver_id, a.display_name),
                phone_e164=a.phone_e164,
                is_active=a.is_active,
            )
            for a in sorted(
                assignees,
                key=lambda a: (resolve_driver_name(a.external_driver_id, a.display_name) or "").lower(),
            )
        ],
        last_gps=last_gps,
    )
