#!/usr/bin/env python3
"""Preview Geotab + Samsara driver/vehicle rosters and report field completeness.

Read-only Phase 1 tool — no schema or UI changes. Credentials come from
``app.core.config.settings`` (``GEOTAB_*``, ``SAMSARA_API_TOKEN``). Missing
providers are skipped with a warning.

Outputs (gitignored under ``data/roster_preview/``):

* ``{provider}_{fleet}_raw_sample.json`` — small redacted samples
* ``{provider}_{fleet}_completeness.json`` — aggregate fill-rate report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Allow running as ``python scripts/preview_driver_roster.py`` from dcw-backend/
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("dcw.scripts.preview_driver_roster")

DEFAULT_OUTPUT_DIR = _ROOT / "data" / "roster_preview"
SAMPLE_SIZE = 15
MISSING_EXAMPLE_LIMIT = 10
_NO_VEHICLE_SENTINEL = "0"
_GEOTAB_NO_USER = "NoUserId"


# ── Redaction helpers ────────────────────────────────────────────────────


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _redact_phone(value: Any) -> Any:
    if not _nonempty(value):
        return value
    digits = re.sub(r"\D", "", str(value))
    if len(digits) <= 4:
        return "***"
    return f"***{digits[-4:]}"


def _redact_email(value: Any) -> Any:
    if not _nonempty(value):
        return value
    text = str(value)
    if "@" not in text:
        return "***"
    local, _, domain = text.partition("@")
    keep = local[-2:] if len(local) >= 2 else local
    return f"***{keep}@{domain}"


def _redact_license(value: Any) -> Any:
    if not _nonempty(value):
        return value
    text = str(value)
    if len(text) <= 4:
        return "***"
    return f"***{text[-4:]}"


def _redact_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow-copied record with PII fields masked."""
    out = dict(record)
    for key in ("phone", "phoneNumber", "phone_number"):
        if key in out:
            out[key] = _redact_phone(out[key])
    for key in ("email",):
        if key in out:
            out[key] = _redact_email(out[key])
    for key in ("licenseNumber", "license_number"):
        if key in out:
            out[key] = _redact_license(out[key])
    return out


def _fleet_slug(provider: str, fleet_id: str) -> str:
    safe = re.sub(r"[^\w.-]+", "_", fleet_id.strip()) or "unknown"
    return f"{provider}_{safe}"


def _split_display_name(name: str | None) -> tuple[str | None, str | None]:
    """Soft heuristic: first token → first, remainder → last."""
    if not name:
        return None, None
    parts = name.strip().split()
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


# ── Completeness ─────────────────────────────────────────────────────────


def _build_completeness(
    *,
    provider: str,
    fleet_id: str,
    drivers: list[dict[str, Any]],
    name_mode: str,
) -> dict[str, Any]:
    """Aggregate name/phone/assignment fill rates for a provider roster."""
    fetched_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    drivers_total = len(drivers)
    drivers_is_person = 0
    with_first_name = 0
    with_last_name = 0
    with_full_name = 0
    with_phone = 0
    with_current_or_recent_vehicle = 0
    complete_contact = 0
    complete_and_assigned = 0
    missing_examples: list[dict[str, Any]] = []

    for driver in drivers:
        if not driver.get("is_person", True):
            continue
        drivers_is_person += 1

        first = _as_str(driver.get("first_name"))
        last = _as_str(driver.get("last_name"))
        full = _as_str(driver.get("full_name")) or _as_str(driver.get("name"))
        phone = _as_str(driver.get("phone"))
        assigned = bool(driver.get("has_vehicle_assignment"))

        has_first = bool(first)
        has_last = bool(last)
        has_full = bool(full)
        has_phone = bool(phone)

        if has_first:
            with_first_name += 1
        if has_last:
            with_last_name += 1
        if has_full or (has_first and has_last):
            with_full_name += 1
        if has_phone:
            with_phone += 1
        if assigned:
            with_current_or_recent_vehicle += 1

        if name_mode == "first_last":
            name_ok = has_first and has_last
        else:
            # Samsara: usable display name (+ optional soft first/last split)
            name_ok = has_full

        is_complete = name_ok and has_phone
        if is_complete:
            complete_contact += 1
        if is_complete and assigned:
            complete_and_assigned += 1

        missing: list[str] = []
        if name_mode == "first_last":
            if not has_first:
                missing.append("first_name")
            if not has_last:
                missing.append("last_name")
        elif not has_full:
            missing.append("name")
        if not has_phone:
            missing.append("phone")
        if not assigned:
            missing.append("vehicle_assignment")
        if missing and len(missing_examples) < MISSING_EXAMPLE_LIMIT:
            missing_examples.append({"id": driver.get("id"), "missing": missing})

    return {
        "provider": provider,
        "fleet_id": fleet_id,
        "fetched_at": fetched_at,
        "name_mode": name_mode,
        "drivers_total": drivers_total,
        "drivers_is_person": drivers_is_person,
        "with_first_name": with_first_name,
        "with_last_name": with_last_name,
        "with_full_name": with_full_name,
        "with_phone": with_phone,
        "with_current_or_recent_vehicle": with_current_or_recent_vehicle,
        "complete_contact": complete_contact,
        "complete_and_assigned": complete_and_assigned,
        "missing_examples": missing_examples,
    }


def _pct(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "n/a"
    return f"{100.0 * numerator / denominator:.1f}%"


def _print_completeness_table(report: dict[str, Any]) -> None:
    base = int(report["drivers_is_person"]) or int(report["drivers_total"])
    rows = [
        ("drivers_total", report["drivers_total"], ""),
        ("drivers_is_person", report["drivers_is_person"], ""),
        ("with_first_name", report["with_first_name"], _pct(report["with_first_name"], base)),
        ("with_last_name", report["with_last_name"], _pct(report["with_last_name"], base)),
        ("with_full_name", report["with_full_name"], _pct(report["with_full_name"], base)),
        ("with_phone", report["with_phone"], _pct(report["with_phone"], base)),
        (
            "with_vehicle_assignment",
            report["with_current_or_recent_vehicle"],
            _pct(report["with_current_or_recent_vehicle"], base),
        ),
        ("complete_contact", report["complete_contact"], _pct(report["complete_contact"], base)),
        (
            "complete_and_assigned",
            report["complete_and_assigned"],
            _pct(report["complete_and_assigned"], base),
        ),
    ]
    print()
    print(f"=== {report['provider']} / {report['fleet_id']} (name_mode={report['name_mode']}) ===")
    print(f"{'metric':<28} {'count':>8} {'of persons':>12}")
    print("-" * 50)
    for label, count, pct in rows:
        print(f"{label:<28} {count:>8} {pct:>12}")
    if report.get("missing_examples"):
        print("missing examples (first 10):")
        for ex in report["missing_examples"]:
            print(f"  id={ex['id']} missing={','.join(ex['missing'])}")
    print()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    logger.info("Wrote %s", path)


# ── Geotab ───────────────────────────────────────────────────────────────


def _geotab_credentials_configured() -> bool:
    return bool(
        settings.GEOTAB_USERNAME
        and settings.GEOTAB_PASSWORD
        and settings.GEOTAB_DATABASE
    )


def _fetch_geotab_users(api: Any) -> list[dict[str, Any]]:
    """Fetch driver Users; prefer ``isDriver`` search, fall back to client filter."""
    import mygeotab.serializers as geo_serializers

    raw_users: list[Any] = []
    try:
        raw_users = api.get("User", search={"isDriver": True})
        logger.info("Geotab User search isDriver=true returned %d rows", len(raw_users))
    except Exception as exc:
        logger.warning("Geotab UserSearch isDriver failed (%s); fetching all User", exc)
        raw_users = api.get("User")
        logger.info("Geotab User (all) returned %d rows", len(raw_users))

    users: list[dict[str, Any]] = []
    for raw in raw_users:
        plain = json.loads(geo_serializers.json_serialize(raw))
        if not isinstance(plain, dict):
            continue
        # When fallback fetched everyone, keep only isDriver=True when present.
        if plain.get("isDriver") is False:
            continue
        users.append(plain)
    return users


def _fetch_geotab_devices(api: Any) -> list[dict[str, Any]]:
    import mygeotab.serializers as geo_serializers

    raw_devices = api.get("Device")
    devices: list[dict[str, Any]] = []
    for raw in raw_devices:
        plain = json.loads(geo_serializers.json_serialize(raw))
        if isinstance(plain, dict):
            devices.append(plain)
    logger.info("Geotab Device returned %d rows", len(devices))
    return devices


def _geotab_driver_id_from_log(plain: dict[str, Any]) -> str:
    driver_ref = plain.get("driver")
    if isinstance(driver_ref, dict) and driver_ref.get("id"):
        return str(driver_ref["id"])
    if isinstance(driver_ref, str) and driver_ref and driver_ref != _GEOTAB_NO_USER:
        return driver_ref
    return _GEOTAB_NO_USER


def _geotab_device_id_from_log(plain: dict[str, Any]) -> str | None:
    device = plain.get("device")
    if isinstance(device, dict) and device.get("id"):
        return str(device["id"])
    if isinstance(device, str) and device:
        return device
    return None


def _fetch_geotab_assignment_signal(
    api: Any,
    hours: int,
) -> tuple[set[str], dict[str, str], dict[str, Any]]:
    """Infer recent driver↔device links from DutyStatusLog.

    Returns:
        (drivers_with_recent_device, device_to_last_driver, stats)
    """
    import mygeotab.serializers as geo_serializers

    now = datetime.now(UTC)
    from_date = now - timedelta(hours=hours)
    from_date_iso = from_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    logger.info("Fetching Geotab DutyStatusLog from %s (assignment signal)", from_date_iso)

    try:
        raw_logs = api.get("DutyStatusLog", search={"fromDate": from_date_iso})
    except Exception as exc:
        logger.warning("Geotab DutyStatusLog fetch failed: %s", exc)
        return set(), {}, {"error": str(exc), "logs_fetched": 0}

    drivers_with_device: set[str] = set()
    device_to_driver: dict[str, str] = {}
    real_driver_ids: set[str] = set()
    no_user_count = 0

    # Sort ascending so later events overwrite device→driver map.
    serialized: list[dict[str, Any]] = []
    for raw in raw_logs:
        plain = json.loads(geo_serializers.json_serialize(raw))
        if isinstance(plain, dict):
            serialized.append(plain)
    serialized.sort(key=lambda r: str(r.get("dateTime") or r.get("date") or ""))

    for plain in serialized:
        driver_id = _geotab_driver_id_from_log(plain)
        device_id = _geotab_device_id_from_log(plain)
        if driver_id == _GEOTAB_NO_USER:
            no_user_count += 1
            continue
        real_driver_ids.add(driver_id)
        if device_id:
            drivers_with_device.add(driver_id)
            device_to_driver[device_id] = driver_id

    stats = {
        "lookback_hours": hours,
        "logs_fetched": len(serialized),
        "distinct_real_drivers": len(real_driver_ids),
        "no_user_or_missing_driver_logs": no_user_count,
        "drivers_with_device_link": len(drivers_with_device),
        "devices_with_last_driver": len(device_to_driver),
    }
    logger.info("Geotab assignment signal: %s", stats)
    return drivers_with_device, device_to_driver, stats


def preview_geotab(*, hours: int, include_pii: bool, output_dir: Path) -> dict[str, Any] | None:
    """Pull Geotab User/Device + recent HOS assignment signal; write reports."""
    if not _geotab_credentials_configured():
        logger.warning(
            "Skipping Geotab — set GEOTAB_USERNAME, GEOTAB_PASSWORD, and GEOTAB_DATABASE"
        )
        return None

    import mygeotab

    fleet_id = settings.GEOTAB_DATABASE
    logger.info(
        "Authenticating with MyGeotab (server=%s, database=%s)",
        settings.GEOTAB_SERVER,
        fleet_id,
    )
    api = mygeotab.API(
        username=settings.GEOTAB_USERNAME,
        password=settings.GEOTAB_PASSWORD,
        database=settings.GEOTAB_DATABASE,
        server=settings.GEOTAB_SERVER,
    )
    api.authenticate()
    logger.info("Geotab authenticated successfully")

    users = _fetch_geotab_users(api)
    devices = _fetch_geotab_devices(api)
    drivers_with_device, device_to_driver, assignment_stats = _fetch_geotab_assignment_signal(
        api, hours=hours
    )

    normalized_drivers: list[dict[str, Any]] = []
    for user in users:
        uid = _as_str(user.get("id"))
        if not uid:
            continue
        first = _as_str(user.get("firstName"))
        last = _as_str(user.get("lastName"))
        name = _as_str(user.get("name"))
        full = f"{first or ''} {last or ''}".strip() or name
        phone = _as_str(user.get("phoneNumber"))
        is_driver = user.get("isDriver")
        is_person = is_driver is not False
        has_assignment = uid in drivers_with_device
        normalized_drivers.append(
            {
                "id": uid,
                "first_name": first,
                "last_name": last,
                "full_name": full,
                "name": name,
                "phone": phone,
                "email": _as_str(user.get("email")),
                "isDriver": is_driver,
                "licenseNumber": _as_str(user.get("licenseNumber")),
                "is_person": is_person,
                "has_vehicle_assignment": has_assignment,
            }
        )

    vehicle_sample = [
        {
            "id": _as_str(d.get("id")),
            "name": _as_str(d.get("name")),
            "vehicleIdentificationNumber": _as_str(d.get("vehicleIdentificationNumber")),
            "serialNumber": _as_str(d.get("serialNumber")),
            "last_known_driver_id": device_to_driver.get(str(d.get("id"))) if d.get("id") else None,
        }
        for d in devices[:SAMPLE_SIZE]
    ]

    report = _build_completeness(
        provider="geotab",
        fleet_id=fleet_id,
        drivers=normalized_drivers,
        name_mode="first_last",
    )
    report["vehicles_total"] = len(devices)
    report["assignment_signal"] = assignment_stats

    slug = _fleet_slug("geotab", fleet_id)
    driver_sample = normalized_drivers[:SAMPLE_SIZE]
    if not include_pii:
        driver_sample = [_redact_record(r) for r in driver_sample]
    raw_sample = {
        "provider": "geotab",
        "fleet_id": fleet_id,
        "include_pii": include_pii,
        "drivers_sample": driver_sample,
        "vehicles_sample": vehicle_sample,
        "device_to_last_driver_sample": dict(list(device_to_driver.items())[:SAMPLE_SIZE]),
    }
    _write_json(output_dir / f"{slug}_raw_sample.json", raw_sample)
    _write_json(output_dir / f"{slug}_completeness.json", report)
    _print_completeness_table(report)
    return report


# ── Samsara ──────────────────────────────────────────────────────────────


def _samsara_credentials_configured() -> bool:
    return bool(settings.SAMSARA_API_TOKEN)


def _driver_dict_from_sdk(driver: Any) -> dict[str, Any]:
    if hasattr(driver, "model_dump"):
        return driver.model_dump(by_alias=True, exclude_unset=True)
    if isinstance(driver, dict):
        return driver
    return {}


def _vehicle_assignment_from_driver(plain: dict[str, Any]) -> str | None:
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


async def _list_all_samsara_drivers(client: Any) -> list[dict[str, Any]]:
    """List active + deactivated drivers (API defaults to active-only)."""
    drivers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for activation in ("active", "deactivated"):
        pager = await client.drivers.list(driver_activation_status=activation, limit=100)
        async for driver in pager:
            plain = _driver_dict_from_sdk(driver)
            uid = _as_str(plain.get("id"))
            if uid and uid in seen:
                continue
            if uid:
                seen.add(uid)
            # Ensure activation status is present even if SDK omitted it.
            plain.setdefault("driverActivationStatus", activation)
            drivers.append(plain)
    logger.info("Samsara drivers.list returned %d rows (active+deactivated)", len(drivers))
    return drivers


async def _list_all_samsara_vehicles(client: Any) -> list[dict[str, Any]]:
    vehicles: list[dict[str, Any]] = []
    pager = await client.vehicles.list(limit=100)
    async for vehicle in pager:
        if hasattr(vehicle, "model_dump"):
            vehicles.append(vehicle.model_dump(by_alias=True, exclude_unset=True))
        elif isinstance(vehicle, dict):
            vehicles.append(vehicle)
    logger.info("Samsara vehicles.list returned %d rows", len(vehicles))
    return vehicles


async def _samsara_recent_hos_vehicle_map(
    client: Any,
    hours: int,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Map driver_id → most recent non-sentinel vehicle.id from HOS logs."""
    now = datetime.now(UTC)
    start = now - timedelta(hours=hours)
    start_str = start.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    end_str = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    driver_to_vehicle: dict[str, str] = {}
    logs_seen = 0
    after: str | None = None
    try:
        while True:
            response = await client.hours_of_service.get_hos_logs(
                start_time=start_str,
                end_time=end_str,
                after=after,
            )
            data = getattr(response, "data", None) or []
            for group in data:
                driver_obj = getattr(group, "driver", None)
                if driver_obj is None:
                    continue
                driver_plain = (
                    driver_obj.model_dump(by_alias=True, exclude_unset=True)
                    if hasattr(driver_obj, "model_dump")
                    else {}
                )
                driver_id = _as_str(driver_plain.get("id"))
                if not driver_id:
                    continue
                hos_logs = getattr(group, "hos_logs", None) or getattr(group, "hosLogs", None) or []
                for entry in hos_logs:
                    logs_seen += 1
                    entry_plain = (
                        entry.model_dump(by_alias=True, exclude_unset=True)
                        if hasattr(entry, "model_dump")
                        else entry
                        if isinstance(entry, dict)
                        else {}
                    )
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
        return {}, {"error": str(exc), "lookback_hours": hours, "logs_fetched": logs_seen}

    stats = {
        "lookback_hours": hours,
        "logs_fetched": logs_seen,
        "drivers_with_hos_vehicle": len(driver_to_vehicle),
    }
    logger.info("Samsara HOS assignment signal: %s", stats)
    return driver_to_vehicle, stats


async def preview_samsara(*, hours: int, include_pii: bool, output_dir: Path) -> dict[str, Any] | None:
    """Pull Samsara drivers/vehicles (+ optional HOS vehicle signal); write reports."""
    if not _samsara_credentials_configured():
        logger.warning("Skipping Samsara — set SAMSARA_API_TOKEN")
        return None

    from samsara import AsyncSamsara
    from samsara.core.api_error import ApiError

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
    fleet_id = settings.SAMSARA_FLEET_ID or (f"samsara:{org_id}" if org_id else "samsara:unknown")
    logger.info("Connected to Samsara org %s (org_id=%s, fleet_id=%s)", org_name, org_id, fleet_id)

    raw_drivers = await _list_all_samsara_drivers(client)
    raw_vehicles = await _list_all_samsara_vehicles(client)
    hos_vehicle_map, hos_stats = await _samsara_recent_hos_vehicle_map(client, hours=hours)

    normalized_drivers: list[dict[str, Any]] = []
    static_assigned_count = 0
    for plain in raw_drivers:
        uid = _as_str(plain.get("id"))
        if not uid:
            continue
        name = _as_str(plain.get("name"))
        first, last = _split_display_name(name)
        phone = _as_str(plain.get("phone"))
        activation = _as_str(plain.get("driverActivationStatus") or plain.get("driver_activation_status"))
        is_person = (activation or "active").lower() == "active"
        roster_vehicle = _vehicle_assignment_from_driver(plain)
        if roster_vehicle:
            static_assigned_count += 1
        hos_vehicle = hos_vehicle_map.get(uid)
        assigned_vehicle = roster_vehicle or hos_vehicle
        normalized_drivers.append(
            {
                "id": uid,
                "name": name,
                "first_name": first,
                "last_name": last,
                "full_name": name,
                "phone": phone,
                "username": _as_str(plain.get("username")),
                "driverActivationStatus": activation,
                "licenseNumber": _as_str(plain.get("licenseNumber") or plain.get("license_number")),
                "roster_vehicle_id": roster_vehicle,
                "hos_vehicle_id": hos_vehicle,
                "is_person": is_person,
                "has_vehicle_assignment": bool(assigned_vehicle),
            }
        )

    vehicle_sample = [
        {
            "id": _as_str(v.get("id")),
            "name": _as_str(v.get("name")),
            "vin": _as_str(v.get("vin")),
        }
        for v in raw_vehicles[:SAMPLE_SIZE]
    ]

    report = _build_completeness(
        provider="samsara",
        fleet_id=fleet_id,
        drivers=normalized_drivers,
        name_mode="display_name",
    )
    report["vehicles_total"] = len(raw_vehicles)
    report["assignment_signal"] = {
        **hos_stats,
        "drivers_with_roster_vehicle": static_assigned_count,
        "note": (
            "SDK Driver exposes staticAssignedVehicle; currentVehicle captured if API returns it. "
            "has_vehicle_assignment = roster vehicle OR recent HOS vehicle.id != '0'."
        ),
    }

    slug = _fleet_slug("samsara", fleet_id)
    driver_sample = normalized_drivers[:SAMPLE_SIZE]
    if not include_pii:
        driver_sample = [_redact_record(r) for r in driver_sample]
    raw_sample = {
        "provider": "samsara",
        "fleet_id": fleet_id,
        "org_name": org_name,
        "include_pii": include_pii,
        "drivers_sample": driver_sample,
        "vehicles_sample": vehicle_sample,
    }
    _write_json(output_dir / f"{slug}_raw_sample.json", raw_sample)
    _write_json(output_dir / f"{slug}_completeness.json", report)
    _print_completeness_table(report)
    return report


# ── Postgres cross-check ─────────────────────────────────────────────────


async def _crosscheck_tenant(tenant_id: str) -> dict[str, Any] | None:
    """Count distinct driver_id buckets in canonical_hos_logs for one tenant."""
    from sqlalchemy import func, select

    from app.core.database import async_session_factory
    from app.domains.ingestion.models import CanonicalHOSLogRecord

    try:
        async with async_session_factory() as session:
            stmt = (
                select(
                    CanonicalHOSLogRecord.driver_id,
                    func.count().label("event_count"),
                )
                .where(CanonicalHOSLogRecord.tenant_id == tenant_id)
                .group_by(CanonicalHOSLogRecord.driver_id)
            )
            result = await session.execute(stmt)
            rows = result.all()
    except Exception as exc:
        logger.warning("Postgres cross-check failed for tenant %s: %s", tenant_id, exc)
        return None

    unassigned: list[str] = []
    unknown: list[str] = []
    real: list[str] = []
    for driver_id, _count in rows:
        did = str(driver_id)
        if did.startswith("unassigned:"):
            unassigned.append(did)
        elif did == "UNKNOWN_DRIVER":
            unknown.append(did)
        else:
            real.append(did)

    return {
        "tenant_id": tenant_id,
        "distinct_driver_ids": len(rows),
        "real_driver_ids": len(real),
        "unassigned_prefix_ids": len(unassigned),
        "unknown_driver_ids": len(unknown),
        "unassigned_examples": unassigned[:5],
    }


async def run_db_crosscheck(fleet_ids: list[str], output_dir: Path) -> dict[str, Any] | None:
    """Optional DB noise check — never fails the preview when Postgres is down."""
    if not fleet_ids:
        return None
    logger.info("Running optional Postgres cross-check for tenants: %s", fleet_ids)

    try:
        per_tenant: list[dict[str, Any]] = []
        for fleet_id in fleet_ids:
            summary = await _crosscheck_tenant(fleet_id)
            if summary is not None:
                per_tenant.append(summary)
                print(
                    f"DB cross-check {fleet_id}: "
                    f"real={summary['real_driver_ids']} "
                    f"unassigned:={summary['unassigned_prefix_ids']} "
                    f"UNKNOWN_DRIVER={summary['unknown_driver_ids']} "
                    f"(distinct={summary['distinct_driver_ids']})"
                )
    except Exception as exc:
        logger.warning("Skipping DB cross-check — %s", exc)
        return None

    if not per_tenant:
        logger.warning("Postgres cross-check produced no results (DB down or empty?)")
        return None

    payload = {
        "fetched_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tenants": per_tenant,
    }
    _write_json(output_dir / "db_crosscheck.json", payload)
    return payload


# ── CLI ──────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview Geotab + Samsara driver/vehicle roster completeness (Phase 1)"
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=48,
        help="HOS lookback window for assignment signal (default: 48)",
    )
    parser.add_argument(
        "--include-pii",
        action="store_true",
        help="Write unredacted phones/emails/licenses into raw_sample JSON (local only)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Skip optional Postgres unassigned/UNKNOWN_DRIVER cross-check",
    )
    parser.add_argument(
        "--providers",
        choices=("all", "geotab", "samsara"),
        default="all",
        help="Which providers to preview (default: all configured)",
    )
    args = parser.parse_args()

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    reports: list[dict[str, Any]] = []
    fleet_ids: list[str] = []

    run_geotab = args.providers in ("all", "geotab")
    run_samsara = args.providers in ("all", "samsara")

    if run_geotab:
        geotab_report = preview_geotab(
            hours=args.hours,
            include_pii=args.include_pii,
            output_dir=output_dir,
        )
        if geotab_report:
            reports.append(geotab_report)
            fleet_ids.append(str(geotab_report["fleet_id"]))

    if run_samsara:
        samsara_report = asyncio.run(
            preview_samsara(
                hours=args.hours,
                include_pii=args.include_pii,
                output_dir=output_dir,
            )
        )
        if samsara_report:
            reports.append(samsara_report)
            fleet_ids.append(str(samsara_report["fleet_id"]))

    if not args.skip_db and fleet_ids:
        asyncio.run(run_db_crosscheck(fleet_ids, output_dir))

    if not reports:
        logger.error("No provider reports produced — configure GEOTAB_* and/or SAMSARA_API_TOKEN")
        sys.exit(1)

    summary_path = output_dir / "summary.json"
    _write_json(
        summary_path,
        {
            "fetched_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "providers": reports,
        },
    )
    logger.info("Preview complete — %d provider report(s) in %s", len(reports), output_dir)


if __name__ == "__main__":
    main()
