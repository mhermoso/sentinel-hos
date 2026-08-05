"""Samsara telematics adapter — HOS logs + GPS vehicle-stats feed via the SDK.

Implements ``BaseTelematicsAdapter`` for Fleet B (Samsara) using the pinned
``samsara-api`` SDK (``AsyncSamsara`` client, Fern-generated).

**HOS** (``GET /fleet/hos/logs``) is a time-window + page-cursor API, not a
Geotab-style version feed, so ``fetch_feed`` uses a watermark strategy:

- ``from_cursor`` is an ISO-8601 UTC watermark (or ``""`` on first poll).
- Each cycle fetches ``start = max(watermark, now - SAMSARA_RESCAN_HOURS)``
  through ``end = now captured before pagination`` to pick up late Samsara
  Driver App uploads; re-fetched rows dedup on ``(tenant_id, raw_id)``.
- **Pagination binds the exact query-parameter strings** (live-verified):
  ``startTime``/``endTime`` are serialized ONCE per cycle and reused verbatim
  on every ``after`` page — the API returns HTTP 400 "Parameters differ from
  previous paginated request" if any parameter changes mid-pagination.
- On a rate-limit or HTTP error mid-pagination the logs collected so far are
  returned with the cursor UNCHANGED, so the next poll safely re-fetches.

Entries carry no stable provider id, so ``raw_id`` is synthetic:
``samsara:{driver_id}:{logStartTime}:{hosStatusType}``.

**GPS** (``GET /fleet/vehicles/stats/feed?types=gps``) uses the SDK method
``AsyncSamsara.vehicle_stats.get_vehicle_stats_feed``. The feed cursor is a
native ``endCursor`` stored verbatim in Redis as
``cursor:samsara-gps:{fleet_id}``. Each ``gps[]`` datapoint maps to
``DCWGpsBreadcrumb`` with ``raw_id = samsara:gps:{vehicle_id}:{time}`` and
``speed_kmh = speedMilesPerHour * 1.609344``. Driver attribution is done by
the poller via ``resolve_driver_for_device`` (same pattern as Geotab LogRecord).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import ValidationError
from samsara import AsyncSamsara
from samsara.core.api_error import ApiError
from samsara.errors import TooManyRequestsError

from app.core.config import settings
from app.domains.ingestion.adapters import BaseTelematicsAdapter
from app.domains.ingestion.normalizer import (
    normalize_coordinates,
    normalize_timestamp,
    sanitize_raw_payload,
)
from app.domains.ingestion.roster import (
    derive_has_unit_assignment,
    derive_profile_complete,
    nonempty_str,
    normalize_phone_e164,
    split_display_name,
)
from app.domains.ingestion.schemas import (
    CanonicalDutyStatus,
    DCWCanonicalHOSLog,
    DCWGpsBreadcrumb,
    DriverRosterEntry,
    VehicleRosterEntry,
)

logger = logging.getLogger("dcw.adapters.samsara")


# ── Mapping Logic ────────────────────────────────────────────────────────

_SAMSARA_STATUS_MAP: dict[str, CanonicalDutyStatus] = {
    "offDuty": CanonicalDutyStatus.OFF_DUTY,
    "sleeperBed": CanonicalDutyStatus.SLEEPER_BERTH,  # Samsara spelling — not "sleeperBerth"
    "driving": CanonicalDutyStatus.DRIVING,
    "onDuty": CanonicalDutyStatus.ON_DUTY,
    "yardMove": CanonicalDutyStatus.YARD_MOVE,
    "personalConveyance": CanonicalDutyStatus.PERSONAL_CONVEYANCE,
}

# Live-verified: vehicle.id == "0" means "no vehicle assigned".
_NO_VEHICLE_SENTINEL = "0"

# Statute mile → kilometre (live-verified GPS field is ``speedMilesPerHour``).
_MPH_TO_KMH = 1.609344


def _driver_dict_from_sdk(driver: Any) -> dict[str, Any]:
    if hasattr(driver, "model_dump"):
        return driver.model_dump(by_alias=True, exclude_unset=True)
    if isinstance(driver, dict):
        return driver
    return {}


def samsara_vehicle_assignment_from_driver(plain: dict[str, Any]) -> str | None:
    """Prefer currentVehicle when present; else staticAssignedVehicle."""
    current = plain.get("currentVehicle") or plain.get("current_vehicle")
    if isinstance(current, dict) and current.get("id"):
        vehicle_id = str(current["id"])
        if vehicle_id != _NO_VEHICLE_SENTINEL:
            return vehicle_id
    if isinstance(current, str) and current and current != _NO_VEHICLE_SENTINEL:
        return current

    static = plain.get("staticAssignedVehicle") or plain.get("static_assigned_vehicle")
    if isinstance(static, dict) and static.get("id"):
        vehicle_id = str(static["id"])
        if vehicle_id != _NO_VEHICLE_SENTINEL:
            return vehicle_id
    if isinstance(static, str) and static and static != _NO_VEHICLE_SENTINEL:
        return static
    return None


def map_samsara_vehicle_to_roster_entry(
    vehicle: dict[str, Any],
    *,
    tenant_id: str,
    current_driver_id: str | None = None,
) -> VehicleRosterEntry | None:
    """Map a Samsara vehicle dict to ``VehicleRosterEntry``."""
    vehicle_id = nonempty_str(vehicle.get("id"))
    if not vehicle_id or vehicle_id == _NO_VEHICLE_SENTINEL:
        return None
    return VehicleRosterEntry(
        provider="samsara",
        tenant_id=tenant_id,
        external_device_id=vehicle_id,
        name=nonempty_str(vehicle.get("name")),
        vin=nonempty_str(vehicle.get("vin")),
        current_driver_id=nonempty_str(current_driver_id),
    )


def map_samsara_driver_to_roster_entry(
    driver: dict[str, Any],
    *,
    tenant_id: str,
    hos_vehicle_id: str | None = None,
    unit_label: str | None = None,
) -> DriverRosterEntry | None:
    """Map a Samsara `/fleet/drivers` dict to ``DriverRosterEntry``.

    Completeness uses display name + phone (first/last are soft-split).
    Unit assignment = roster vehicle OR recent HOS vehicle.
    """
    uid = nonempty_str(driver.get("id"))
    if not uid:
        return None

    name = nonempty_str(driver.get("name"))
    first, last = split_display_name(name)
    phone = normalize_phone_e164(driver.get("phone"))
    activation = nonempty_str(
        driver.get("driverActivationStatus") or driver.get("driver_activation_status")
    )
    is_active = (activation or "active").lower() == "active"
    roster_vehicle = samsara_vehicle_assignment_from_driver(driver)
    device_id = roster_vehicle or nonempty_str(hos_vehicle_id)

    return DriverRosterEntry(
        provider="samsara",
        tenant_id=tenant_id,
        external_driver_id=uid,
        first_name=first,
        last_name=last,
        display_name=name,
        phone_e164=phone,
        current_device_id=device_id,
        unit_label=nonempty_str(unit_label),
        is_active=is_active,
        profile_complete=derive_profile_complete(
            first_name=first,
            last_name=last,
            display_name=name,
            phone_e164=phone,
            require_first_last=False,
        ),
        has_unit_assignment=derive_has_unit_assignment(device_id),
    )


def _map_samsara_status(status_str: str | None) -> CanonicalDutyStatus:
    """Map a Samsara ``hosStatusType`` string to canonical duty status."""
    if status_str is None:
        return CanonicalDutyStatus.UNKNOWN
    return _SAMSARA_STATUS_MAP.get(status_str, CanonicalDutyStatus.UNKNOWN)


def _extract_device_id(entry: dict[str, Any]) -> str | None:
    """Extract vehicle/device id, treating the ``"0"`` sentinel as missing."""
    vehicle = entry.get("vehicle")
    if isinstance(vehicle, dict) and vehicle.get("id"):
        vehicle_id = str(vehicle["id"])
        if vehicle_id != _NO_VEHICLE_SENTINEL:
            return vehicle_id
    return None


def _extract_location(entry: dict[str, Any]) -> tuple[float | None, float | None]:
    """Extract lat/lon from ``logRecordedLocation``, treating 0,0 as missing."""
    location = entry.get("logRecordedLocation")
    if not isinstance(location, dict):
        return None, None
    latitude = location.get("latitude")
    longitude = location.get("longitude")
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return None, None
    if latitude == 0 and longitude == 0:
        return None, None
    return float(latitude), float(longitude)


def map_samsara_log_to_canonical(
    fleet_id: str,
    driver: dict[str, Any],
    entry: dict[str, Any],
) -> DCWCanonicalHOSLog:
    """Map one Samsara HOS log entry (with its driver context) to canonical form.

    Args:
        fleet_id: Tenant id for the Samsara fleet (``samsara:{org_id}``).
        driver: The ``driver`` object from the ``/fleet/hos/logs`` group
            (``{"id": ..., "name": ...}``).
        entry: One element of that driver's ``hosLogs`` array (camelCase keys).
    """
    driver_id = str(driver["id"]) if driver.get("id") else "UNKNOWN_DRIVER"
    driver_name_raw = driver.get("name")
    driver_name = str(driver_name_raw) if driver_name_raw else None

    status_raw = entry.get("hosStatusType")
    # Missing logStartTime surfaces as a ValidationError on event_timestamp.
    log_start_time: Any = entry.get("logStartTime")

    # Entries have no stable provider id — synthesize a deterministic one.
    # Edited logs (status changed later) create a second row at the same
    # timestamp; both are kept (append-only).
    raw_id = f"samsara:{driver_id}:{log_start_time}:{status_raw}"

    latitude, longitude = _extract_location(entry)

    remark = entry.get("remark")
    annotation: str | None = None
    if remark and isinstance(remark, str):
        annotation = remark.strip()[:500]

    sanitized_payload = sanitize_raw_payload({**entry, "driver": dict(driver)})

    return DCWCanonicalHOSLog(
        tenant_id=fleet_id,
        driver_id=driver_id,
        driver_name=driver_name,
        raw_id=raw_id,
        status=_map_samsara_status(status_raw if isinstance(status_raw, str) else None),
        event_timestamp=log_start_time,
        device_id=_extract_device_id(entry),
        latitude=latitude,
        longitude=longitude,
        odometer_km=None,
        annotation=annotation,
        raw_payload=sanitized_payload,
        inputs_hash=None,
    )


def map_samsara_gps_to_breadcrumb(
    fleet_id: str,
    vehicle_id: str,
    gps_entry: dict[str, Any],
    driver_id: str,
) -> DCWGpsBreadcrumb:
    """Map one Samsara vehicle-stats ``gps[]`` datapoint to ``DCWGpsBreadcrumb``.

    Args:
        fleet_id: Tenant id for the Samsara fleet (``samsara:{org_id}``).
        vehicle_id: Samsara vehicle id (long numeric string; used as ``device_id``).
        gps_entry: One element of the vehicle's ``gps`` array (camelCase keys
            from ``model_dump(by_alias=True)``), including ``time``,
            ``latitude``, ``longitude``, ``speedMilesPerHour``.
        driver_id: Already-resolved driver id (or ``unassigned:device:{id}``).
    """
    if not vehicle_id:
        raise ValueError("Samsara GPS datapoint missing vehicle id")

    event_time = gps_entry.get("time")
    if event_time is None:
        raise ValueError("Samsara GPS datapoint missing time")

    latitude_raw = gps_entry.get("latitude")
    longitude_raw = gps_entry.get("longitude")
    if latitude_raw is None or longitude_raw is None:
        raise ValueError("Samsara GPS datapoint missing latitude/longitude")
    if not isinstance(latitude_raw, (int, float)) or not isinstance(longitude_raw, (int, float)):
        raise TypeError("Samsara GPS datapoint latitude/longitude must be numeric")
    if latitude_raw == 0 and longitude_raw == 0:
        raise ValueError("Samsara GPS datapoint has 0,0 sentinel coordinates")

    latitude, longitude = normalize_coordinates(float(latitude_raw), float(longitude_raw))
    if latitude is None or longitude is None:
        raise ValueError("Samsara GPS normalized to None")

    speed_mph = gps_entry.get("speedMilesPerHour")
    speed_kmh: float | None = None
    if isinstance(speed_mph, (int, float)):
        speed_kmh = float(speed_mph) * _MPH_TO_KMH

    # Synthetic raw_id — GPS feed datapoints have no stable provider id.
    raw_id = f"samsara:gps:{vehicle_id}:{event_time}"

    crumb = DCWGpsBreadcrumb(
        tenant_id=fleet_id,
        device_id=vehicle_id,
        driver_id=driver_id,
        raw_id=raw_id,
        event_timestamp=event_time,
        latitude=latitude,
        longitude=longitude,
        speed_kmh=speed_kmh,
        raw_payload=sanitize_raw_payload({"vehicleId": vehicle_id, **gps_entry}),
        inputs_hash=None,
    )
    return crumb.model_copy(update={"event_timestamp": normalize_timestamp(crumb.event_timestamp)})


def _to_rfc3339(dt: datetime) -> str:
    """Serialize a datetime to an RFC 3339 UTC string (second precision)."""
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_watermark(cursor: str) -> datetime | None:
    """Parse the ISO watermark cursor; empty or malformed values yield None."""
    if not cursor:
        return None
    try:
        parsed = datetime.fromisoformat(cursor)
    except ValueError:
        logger.warning("Malformed Samsara watermark cursor %r — falling back to rescan window", cursor)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


# ── Adapter Class ────────────────────────────────────────────────────────


class SamsaraAdapter(BaseTelematicsAdapter):
    """Samsara API adapter for HOS watermark polls and GPS vehicle-stats feed."""

    provider_name = "samsara"

    def __init__(self) -> None:
        self.client: AsyncSamsara | None = None
        self.fleet_id: str = settings.SAMSARA_FLEET_ID

    async def connect(self) -> None:
        """Build the async SDK client and validate the token via /me.

        Resolves ``self.fleet_id`` to ``samsara:{org_id}`` when
        ``SAMSARA_FLEET_ID`` is unset. Raises on invalid token or when the
        organization id cannot be determined. Never logs the token.
        """
        if not settings.SAMSARA_API_TOKEN:
            raise RuntimeError("SAMSARA_API_TOKEN is not configured")

        logger.info("Connecting to Samsara API (base=%s)", settings.SAMSARA_API_BASE)
        client = AsyncSamsara(
            token=settings.SAMSARA_API_TOKEN,
            base_url=settings.SAMSARA_API_BASE,
        )
        try:
            org_response = await client.organization_info.get_organization_info()
        except ApiError as exc:
            logger.error("Samsara token validation failed (status=%s)", exc.status_code)
            raise

        org = org_response.data
        org_id = org.id if org is not None else None
        org_name = org.name if org is not None else None

        if not self.fleet_id:
            if not org_id:
                raise RuntimeError("Samsara organization id unavailable — cannot derive fleet_id")
            self.fleet_id = f"samsara:{org_id}"

        self.client = client
        logger.info(
            "Connected to Samsara org %s (org_id=%s, fleet_id=%s)",
            org_name,
            org_id,
            self.fleet_id,
        )

    async def fetch_feed(
        self,
        tenant_id: str,
        from_cursor: str,
    ) -> tuple[list[DCWCanonicalHOSLog], str]:
        """Fetch HOS logs for the rescan window, paginating to completion.

        Args:
            tenant_id: Fleet/tenant identifier (``samsara:{org_id}``).
            from_cursor: ISO-8601 UTC watermark from the previous successful
                cycle, or ``""`` on first poll.

        Returns:
            Tuple of (validated canonical logs, next watermark). The watermark
            advances to the frozen ``endTime`` only when pagination completes;
            on rate-limit / HTTP errors mid-pagination the partial batch is
            returned with ``from_cursor`` unchanged so the next poll re-fetches.
        """
        if self.client is None:
            await self.connect()
            assert self.client is not None

        now = datetime.now(UTC)
        rescan_floor = now - timedelta(hours=settings.SAMSARA_RESCAN_HOURS)
        watermark = _parse_watermark(from_cursor)
        window_start = max(watermark, rescan_floor) if watermark is not None else rescan_floor

        # CRITICAL: serialize once and reuse these exact strings on every
        # `after` page — Samsara binds pagination to the verbatim parameter
        # strings and returns HTTP 400 if they change mid-pagination.
        start_str = _to_rfc3339(window_start)
        end_str = _to_rfc3339(now)

        valid_logs: list[DCWCanonicalHOSLog] = []
        after: str | None = None
        page = 0

        while True:
            page += 1
            try:
                response = await self.client.hours_of_service.get_hos_logs(
                    start_time=start_str,
                    end_time=end_str,
                    after=after,
                )
            except TooManyRequestsError:
                logger.warning(
                    "Samsara rate limit hit on page %d — returning %d logs without advancing cursor",
                    page,
                    len(valid_logs),
                )
                return valid_logs, from_cursor
            except ApiError as exc:
                logger.error(
                    "Samsara API error (status=%s) on page %d — returning %d logs without advancing cursor",
                    exc.status_code,
                    page,
                    len(valid_logs),
                )
                return valid_logs, from_cursor
            except httpx.HTTPError as exc:
                logger.error(
                    "Samsara HTTP transport error on page %d (%s) — returning %d logs without advancing cursor",
                    page,
                    type(exc).__name__,
                    len(valid_logs),
                )
                return valid_logs, from_cursor

            for group in response.data:
                driver_dict: dict[str, Any] = (
                    group.driver.model_dump(by_alias=True, exclude_unset=True) if group.driver is not None else {}
                )
                for log_entry in group.hos_logs or []:
                    entry_dict = log_entry.model_dump(by_alias=True, exclude_unset=True)
                    try:
                        valid_logs.append(map_samsara_log_to_canonical(tenant_id, driver_dict, entry_dict))
                    except ValidationError as ve:
                        logger.warning(
                            "Validation failed for Samsara entry (driver=%s, logStartTime=%s): %s",
                            driver_dict.get("id"),
                            entry_dict.get("logStartTime"),
                            ve.errors(),
                        )
                    except Exception as exc:
                        logger.warning(
                            "Unexpected parsing failure for Samsara entry (driver=%s): %s",
                            driver_dict.get("id"),
                            exc,
                        )

            pagination = response.pagination
            # endCursor is "" when hasNextPage is false (live-verified).
            if not pagination.has_next_page or not pagination.end_cursor:
                break
            after = pagination.end_cursor

        logger.info(
            "Samsara HOS fetch complete: %d logs across %d page(s), window %s → %s",
            len(valid_logs),
            page,
            start_str,
            end_str,
        )
        return valid_logs, end_str

    async def fetch_gps_feed(
        self,
        tenant_id: str,
        from_cursor: str,
    ) -> tuple[list[dict[str, Any]], str]:
        """Fetch GPS datapoints via ``vehicle_stats.get_vehicle_stats_feed``.

        Calls ``GET /fleet/vehicles/stats/feed?types=gps``. The Redis cursor is
        the native ``pagination.endCursor`` stored verbatim under
        ``cursor:samsara-gps:{fleet_id}``. An empty ``from_cursor`` omits
        ``after`` so the first poll returns the most recent fix per vehicle.

        Returns raw flattened records (not breadcrumbs) so the poller can
        resolve device→driver attribution before mapping — same shape as
        Geotab ``fetch_log_record_feed``. Each record is::

            {"vehicleId": "<id>", "vehicleName": <optional>, ...gps fields}

        On rate-limit / HTTP errors mid-pagination the records collected so
        far are returned with ``from_cursor`` unchanged.
        """
        if self.client is None:
            await self.connect()
            assert self.client is not None

        records: list[dict[str, Any]] = []
        after: str | None = from_cursor or None
        page = 0
        next_cursor = from_cursor

        while True:
            page += 1
            try:
                response = await self.client.vehicle_stats.get_vehicle_stats_feed(
                    types="gps",
                    after=after,
                )
            except TooManyRequestsError:
                logger.warning(
                    "Samsara GPS rate limit on page %d (tenant=%s) — returning %d records without advancing cursor",
                    page,
                    tenant_id,
                    len(records),
                )
                return records, from_cursor
            except ApiError as exc:
                logger.error(
                    "Samsara GPS API error (status=%s) on page %d (tenant=%s) — "
                    "returning %d records without advancing cursor",
                    exc.status_code,
                    page,
                    tenant_id,
                    len(records),
                )
                return records, from_cursor
            except httpx.HTTPError as exc:
                logger.error(
                    "Samsara GPS HTTP error on page %d (tenant=%s, %s) — returning %d records without advancing cursor",
                    page,
                    tenant_id,
                    type(exc).__name__,
                    len(records),
                )
                return records, from_cursor

            for vehicle in response.data:
                vehicle_dict = vehicle.model_dump(by_alias=True, exclude_unset=True)
                vehicle_id = vehicle_dict.get("id")
                if vehicle_id is None:
                    logger.warning("Samsara GPS vehicle row missing id — skipped")
                    continue
                vehicle_id_str = str(vehicle_id)
                vehicle_name = vehicle_dict.get("name")
                for gps_point in vehicle_dict.get("gps") or []:
                    if not isinstance(gps_point, dict):
                        continue
                    flat: dict[str, Any] = {
                        "vehicleId": vehicle_id_str,
                        **gps_point,
                    }
                    if vehicle_name is not None:
                        flat["vehicleName"] = vehicle_name
                    records.append(flat)

            pagination = response.pagination
            if pagination.end_cursor:
                next_cursor = pagination.end_cursor
            if not pagination.has_next_page or not pagination.end_cursor:
                break
            after = pagination.end_cursor

        logger.info(
            "Samsara GPS fetch complete (tenant=%s): %d datapoints across %d page(s), cursor=%s",
            tenant_id,
            len(records),
            page,
            next_cursor or "(empty)",
        )
        return records, next_cursor

    async def _list_all_drivers(self) -> list[dict[str, Any]]:
        assert self.client is not None
        drivers: list[dict[str, Any]] = []
        seen: set[str] = set()
        for activation in ("active", "deactivated"):
            pager = await self.client.drivers.list(
                driver_activation_status=activation, limit=100
            )
            async for driver in pager:
                plain = _driver_dict_from_sdk(driver)
                uid = nonempty_str(plain.get("id"))
                if uid and uid in seen:
                    continue
                if uid:
                    seen.add(uid)
                plain.setdefault("driverActivationStatus", activation)
                drivers.append(plain)
        return drivers

    async def _list_all_vehicles(self) -> list[dict[str, Any]]:
        assert self.client is not None
        vehicles: list[dict[str, Any]] = []
        pager = await self.client.vehicles.list(limit=100)
        async for vehicle in pager:
            plain = _driver_dict_from_sdk(vehicle)
            if plain:
                vehicles.append(plain)
        return vehicles

    async def _recent_hos_vehicle_map(self, hours: int) -> dict[str, str]:
        """Map driver_id → most recent non-sentinel vehicle id from HOS logs."""
        assert self.client is not None
        now = datetime.now(UTC)
        start = now - timedelta(hours=hours)
        start_str = start.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        end_str = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")

        driver_to_vehicle: dict[str, str] = {}
        after: str | None = None
        try:
            while True:
                response = await self.client.hours_of_service.get_hos_logs(
                    start_time=start_str,
                    end_time=end_str,
                    after=after,
                )
                data = getattr(response, "data", None) or []
                for group in data:
                    driver_obj = getattr(group, "driver", None)
                    if driver_obj is None:
                        continue
                    driver_plain = _driver_dict_from_sdk(driver_obj)
                    driver_id = nonempty_str(driver_plain.get("id"))
                    if not driver_id:
                        continue
                    hos_logs = (
                        getattr(group, "hos_logs", None)
                        or getattr(group, "hosLogs", None)
                        or []
                    )
                    for entry in hos_logs:
                        entry_plain = _driver_dict_from_sdk(entry)
                        vehicle = entry_plain.get("vehicle")
                        if isinstance(vehicle, dict) and vehicle.get("id"):
                            vid = str(vehicle["id"])
                            if vid != _NO_VEHICLE_SENTINEL:
                                driver_to_vehicle[driver_id] = vid
                pagination = getattr(response, "pagination", None)
                end_cursor = getattr(pagination, "end_cursor", None) if pagination else None
                has_next = getattr(pagination, "has_next_page", False) if pagination else False
                if not has_next or not end_cursor:
                    break
                after = end_cursor
        except Exception as exc:
            logger.warning("Samsara HOS assignment signal failed: %s", exc)
            return driver_to_vehicle
        return driver_to_vehicle

    async def fetch_vehicle_roster(self, tenant_id: str) -> list[VehicleRosterEntry]:
        """Fetch Samsara vehicles as canonical vehicle roster DTOs.

        ``current_driver_id`` is filled from the inverse of driver static/current
        vehicle assignments (and recent HOS vehicle map as a fallback).
        """
        if self.client is None:
            await self.connect()
            assert self.client is not None

        hours = settings.ROSTER_ASSIGNMENT_LOOKBACK_HOURS
        raw_vehicles = await self._list_all_vehicles()
        raw_drivers = await self._list_all_drivers()
        hos_vehicle_map = await self._recent_hos_vehicle_map(hours)

        vehicle_to_driver: dict[str, str] = {}
        for plain in raw_drivers:
            uid = nonempty_str(plain.get("id"))
            if not uid:
                continue
            vehicle_id = samsara_vehicle_assignment_from_driver(plain) or hos_vehicle_map.get(uid)
            if vehicle_id:
                vehicle_to_driver[vehicle_id] = uid

        entries: list[VehicleRosterEntry] = []
        for vehicle in raw_vehicles:
            vehicle_id = nonempty_str(vehicle.get("id"))
            entry = map_samsara_vehicle_to_roster_entry(
                vehicle,
                tenant_id=tenant_id,
                current_driver_id=vehicle_to_driver.get(vehicle_id) if vehicle_id else None,
            )
            if entry is not None:
                entries.append(entry)
        logger.info(
            "Samsara vehicle roster: %d vehicles (%d with driver) tenant=%s",
            len(entries),
            sum(1 for e in entries if e.current_driver_id),
            tenant_id,
        )
        return entries

    async def fetch_driver_roster(self, tenant_id: str) -> list[DriverRosterEntry]:
        """Fetch Samsara drivers + vehicle assignment as roster DTOs.

        Does not filter or alter HOS ingestion — roster is a separate cache.
        """
        if self.client is None:
            await self.connect()
            assert self.client is not None

        hours = settings.ROSTER_ASSIGNMENT_LOOKBACK_HOURS
        raw_drivers = await self._list_all_drivers()
        raw_vehicles = await self._list_all_vehicles()
        hos_vehicle_map = await self._recent_hos_vehicle_map(hours)
        vehicle_labels = {
            str(v["id"]): nonempty_str(v.get("name"))
            for v in raw_vehicles
            if v.get("id") is not None
        }

        entries: list[DriverRosterEntry] = []
        for plain in raw_drivers:
            uid = nonempty_str(plain.get("id"))
            roster_vehicle = samsara_vehicle_assignment_from_driver(plain)
            hos_vehicle = hos_vehicle_map.get(uid) if uid else None
            device_id = roster_vehicle or hos_vehicle
            entry = map_samsara_driver_to_roster_entry(
                plain,
                tenant_id=tenant_id,
                hos_vehicle_id=hos_vehicle,
                unit_label=vehicle_labels.get(device_id) if device_id else None,
            )
            if entry is not None:
                entries.append(entry)

        logger.info(
            "Samsara driver roster: %d drivers (%d active, %d with unit) tenant=%s",
            len(entries),
            sum(1 for e in entries if e.is_active),
            sum(1 for e in entries if e.has_unit_assignment),
            tenant_id,
        )
        return entries
