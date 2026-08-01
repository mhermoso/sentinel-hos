"""Geotab telematics adapter — proven integration from geotab_ingestor.py.

Refactored from the tested standalone ``geotab_ingestor.py`` to fit the
``BaseTelematicsAdapter`` interface while preserving all mapping logic,
status/origin handling, and Geotab location quirks (x = Longitude, y = Latitude).

Also maps Geotab ``LogRecord`` GPS breadcrumbs (ADR-007).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import mygeotab
from pydantic import ValidationError

from app.core.config import settings
from app.domains.ingestion.adapters import BaseTelematicsAdapter
from app.domains.ingestion.geotab_users import build_geotab_driver_name_map
from app.domains.ingestion.normalizer import (
    normalize_coordinates,
    normalize_timestamp,
    sanitize_raw_payload,
)
from app.domains.ingestion.schemas import (
    CanonicalDutyStatus,
    DCWCanonicalHOSLog,
    DCWGpsBreadcrumb,
)

logger = logging.getLogger("dcw.adapters.geotab")


# ── Mapping Logic (from tested geotab_ingestor.py) ──────────────────────


def _map_geotab_status(
    status_str: Optional[str],
    origin: Optional[str],
) -> CanonicalDutyStatus:
    """Map Geotab status + origin strings to canonical duty status.

    Geotab status strings: ``Driving``, ``Off``, ``SleeperBerth``, ``On``,
    plus exemption statuses ``PC`` / ``YM`` / ``INT_PC`` (modern BBB feeds).
    Origin fallback: ``YardMove``, ``PersonalConveyance``.

    Motion-only exemption events (``DrivingWhileInExemption``, etc.) stay
    ``UNKNOWN`` — they are not duty-status changes.
    """
    # Status-based exemptions (modern Geotab / BBB Bros)
    if status_str in (
        "PC",
        "INT_PC",
        "PersonalConveyance",
        "EnginePowerupPC",
        "EngineShutdownPC",
    ):
        return CanonicalDutyStatus.PERSONAL_CONVEYANCE
    if status_str in ("YM", "YardMove", "INT_YM"):
        return CanonicalDutyStatus.YARD_MOVE

    # Origin-based fallback (legacy Geotab)
    if origin == "YardMove":
        return CanonicalDutyStatus.YARD_MOVE
    if origin == "PersonalConveyance":
        return CanonicalDutyStatus.PERSONAL_CONVEYANCE

    if status_str in ("Driving", "D", "INT_D"):
        return CanonicalDutyStatus.DRIVING
    if status_str in ("Off", "OFF"):
        return CanonicalDutyStatus.OFF_DUTY
    if status_str in ("SleeperBerth", "SB"):
        return CanonicalDutyStatus.SLEEPER_BERTH
    if status_str in ("On", "ON"):
        return CanonicalDutyStatus.ON_DUTY
    return CanonicalDutyStatus.UNKNOWN


def _extract_location(raw_log: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Extract latitude/longitude from Geotab's quirky location structure.

    Geotab geometry: x is Longitude, y is Latitude.
    """
    location = raw_log.get("location")
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    if isinstance(location, dict):
        # Handle nested {"location": {"x": ..., "y": ...}} structure
        if "location" in location and isinstance(location["location"], dict):
            latitude = location["location"].get("y")
            longitude = location["location"].get("x")
        else:
            latitude = location.get("y")
            longitude = location.get("x")
    return latitude, longitude


def _extract_driver_id(raw_log: Dict[str, Any]) -> str:
    """Resolve driver ID from Geotab DutyStatusLog (dict ref, string ref, or device fallback)."""
    driver_ref = raw_log.get("driver")
    if isinstance(driver_ref, dict) and driver_ref.get("id"):
        return str(driver_ref["id"])
    if isinstance(driver_ref, str) and driver_ref and driver_ref != "NoUserId":
        return driver_ref

    device_dict = raw_log.get("device")
    if isinstance(device_dict, dict) and device_dict.get("id"):
        return f"unassigned:device:{device_dict['id']}"

    return "UNKNOWN_DRIVER"


def map_geotab_log_to_canonical(
    raw_log: Dict[str, Any],
    tenant_id: str,
    driver_name: Optional[str] = None,
) -> DCWCanonicalHOSLog:
    """Map a raw MyGeotab DutyStatusLog dict to a DCWCanonicalHOSLog model.

    This function is a direct port of the tested ``map_geotab_log_to_canonical``
    from ``geotab_ingestor.py``.
    """
    raw_id = str(raw_log.get("id", ""))

    driver_id = _extract_driver_id(raw_log)

    # Device ID extraction
    device_dict = raw_log.get("device")
    device_id = None
    if isinstance(device_dict, dict) and device_dict.get("id"):
        device_id = str(device_dict["id"])

    # Status mapping
    canonical_status = _map_geotab_status(
        raw_log.get("status"), raw_log.get("origin")
    )

    # Location extraction
    latitude, longitude = _extract_location(raw_log)

    # Comment / Annotation sanitization
    comment = raw_log.get("comment")
    annotation: Optional[str] = None
    if comment and isinstance(comment, str):
        annotation = comment.strip()[:500]

    # Odometer: Geotab DutyStatusLog.odometer is meters. Stored in the
    # legacy column/field ``odometer_km`` (name is historical; value is meters).
    odometer: Optional[float] = None
    if "odometer" in raw_log and isinstance(raw_log["odometer"], (int, float)):
        odometer = float(raw_log["odometer"])

    sanitized_payload = sanitize_raw_payload(raw_log)

    return DCWCanonicalHOSLog(
        tenant_id=tenant_id,
        driver_id=driver_id,
        driver_name=driver_name,
        raw_id=raw_id,
        status=canonical_status,
        event_timestamp=raw_log.get("dateTime"),
        device_id=device_id,
        latitude=latitude,
        longitude=longitude,
        odometer_km=odometer,
        annotation=annotation,
        raw_payload=sanitized_payload,
    )


def _extract_log_record_device_id(raw: Dict[str, Any]) -> Optional[str]:
    """Extract device id from a Geotab LogRecord."""
    device = raw.get("device")
    if isinstance(device, dict) and device.get("id"):
        return str(device["id"])
    if isinstance(device, str) and device:
        return device
    return None


def _extract_log_record_lat_lon(
    raw: Dict[str, Any],
) -> Tuple[Optional[float], Optional[float]]:
    """Extract lat/lon from LogRecord (top-level y/x or nested location)."""
    lat = raw.get("latitude", raw.get("y"))
    lon = raw.get("longitude", raw.get("x"))
    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon)
        except (TypeError, ValueError):
            pass
    return _extract_location(raw)


def map_geotab_log_record_to_breadcrumb(
    raw: Dict[str, Any],
    tenant_id: str,
    driver_id: str,
) -> DCWGpsBreadcrumb:
    """Map a raw MyGeotab LogRecord dict to ``DCWGpsBreadcrumb``.

    GPS is rounded to 4 decimals and timestamps truncated to 1s (ADR-007).
    ``driver_id`` must already be resolved by the caller (device attribution).
    """
    raw_id = str(raw.get("id", ""))
    device_id = _extract_log_record_device_id(raw)
    if not device_id:
        raise ValueError("LogRecord missing device id")

    latitude, longitude = _extract_log_record_lat_lon(raw)
    if latitude is None or longitude is None:
        raise ValueError("LogRecord missing latitude/longitude")

    latitude, longitude = normalize_coordinates(latitude, longitude)
    if latitude is None or longitude is None:
        raise ValueError("LogRecord GPS normalized to None")

    event_ts = raw.get("dateTime")
    crumb = DCWGpsBreadcrumb(
        tenant_id=tenant_id,
        device_id=device_id,
        driver_id=driver_id,
        raw_id=raw_id,
        event_timestamp=event_ts,
        latitude=latitude,
        longitude=longitude,
        speed_kmh=float(raw["speed"]) if isinstance(raw.get("speed"), (int, float)) else None,
        raw_payload=sanitize_raw_payload(raw),
    )
    return crumb.model_copy(
        update={"event_timestamp": normalize_timestamp(crumb.event_timestamp)}
    )


# ── Adapter Class ────────────────────────────────────────────────────────


class GeotabAdapter(BaseTelematicsAdapter):
    """MyGeotab API adapter using GetFeed for continuous HOS log polling.

    Wraps the proven ``mygeotab`` SDK calls from the tested ingestor module.
    Also polls ``LogRecord`` GPS breadcrumbs for route maps (ADR-007).
    """

    provider_name = "geotab"

    def __init__(self) -> None:
        self.api: Optional[mygeotab.API] = None
        self._driver_names: Dict[str, str] = {}

    async def connect(self) -> None:
        """Authenticate with the MyGeotab API."""
        logger.info(
            "Connecting to MyGeotab API (database=%s, server=%s)",
            settings.GEOTAB_DATABASE,
            settings.GEOTAB_SERVER,
        )
        try:
            self.api = mygeotab.API(
                username=settings.GEOTAB_USERNAME,
                password=settings.GEOTAB_PASSWORD,
                database=settings.GEOTAB_DATABASE,
                server=settings.GEOTAB_SERVER,
            )
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.api.authenticate)
            logger.info("Successfully authenticated with MyGeotab")
        except mygeotab.AuthenticationException:
            logger.error("Authentication failed for MyGeotab (credentials masked)")
            raise
        except mygeotab.MyGeotabException as exc:
            logger.error("MyGeotab SDK error during connect: %s", exc)
            raise

    async def refresh_driver_names(self) -> Dict[str, str]:
        """Load id→name from MyGeotab User into the adapter cache."""
        if self.api is None:
            await self.connect()
            assert self.api is not None
        self._driver_names = await asyncio.to_thread(
            build_geotab_driver_name_map, self.api
        )
        logger.info("Loaded %d Geotab driver names into adapter cache", len(self._driver_names))
        return self._driver_names

    async def fetch_feed(
        self,
        tenant_id: str,
        from_cursor: str,
    ) -> Tuple[List[DCWCanonicalHOSLog], str]:
        """Fetch a batch of DutyStatusLog records via GetFeed API.

        Args:
            tenant_id: Customer database / tenant identifier.
            from_cursor: Version token for incremental feed.

        Returns:
            Tuple of (validated canonical logs, next version token).
        """
        if self.api is None:
            await self.connect()
            assert self.api is not None

        if not self._driver_names:
            try:
                await self.refresh_driver_names()
            except Exception as exc:
                logger.warning("Driver name preload failed; continuing without names: %s", exc)

        try:
            loop = asyncio.get_running_loop()
            feed_response = await loop.run_in_executor(
                None,
                lambda: self.api.call(
                    "GetFeed",
                    typeName="DutyStatusLog",
                    fromVersion=from_cursor,
                    resultsLimit=settings.FEED_RESULTS_LIMIT,
                ),
            )
        except mygeotab.AuthenticationException:
            logger.warning("Geotab session expired — re-authenticating…")
            await self.connect()
            return await self.fetch_feed(tenant_id, from_cursor)
        except mygeotab.MyGeotabException as exc:
            logger.error("Geotab API error fetching DutyStatusLog feed: %s", exc)
            raise

        records = feed_response.get("result", feed_response.get("data", []))
        to_version = feed_response.get("toVersion", from_cursor)

        valid_logs: List[DCWCanonicalHOSLog] = []

        for record in records:
            record_id = record.get("id", "UNKNOWN_ID")
            try:
                driver_id = _extract_driver_id(record)
                canonical_log = map_geotab_log_to_canonical(
                    record,
                    tenant_id=tenant_id,
                    driver_name=self._driver_names.get(driver_id),
                )
                valid_logs.append(canonical_log)
            except ValidationError as ve:
                logger.warning(
                    "Validation failed for record %s; isolated to DLQ: %s",
                    record_id,
                    ve.errors(),
                )
            except Exception as exc:
                logger.warning(
                    "Unexpected parsing failure for record %s: %s",
                    record_id,
                    exc,
                )

        return valid_logs, str(to_version)

    async def fetch_log_record_feed(
        self,
        tenant_id: str,
        from_cursor: str,
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Fetch a batch of raw LogRecord dicts via GetFeed.

        Returns raw records (not breadcrumbs) so the poller can resolve
        device→driver attribution before mapping.
        """
        if self.api is None:
            await self.connect()
            assert self.api is not None

        try:
            loop = asyncio.get_running_loop()
            feed_response = await loop.run_in_executor(
                None,
                lambda: self.api.call(
                    "GetFeed",
                    typeName="LogRecord",
                    fromVersion=from_cursor,
                    resultsLimit=settings.FEED_RESULTS_LIMIT,
                ),
            )
        except mygeotab.AuthenticationException:
            logger.warning("Geotab session expired — re-authenticating…")
            await self.connect()
            return await self.fetch_log_record_feed(tenant_id, from_cursor)
        except mygeotab.MyGeotabException as exc:
            logger.error("Geotab API error fetching LogRecord feed: %s", exc)
            raise

        records = feed_response.get("result", feed_response.get("data", []))
        to_version = feed_response.get("toVersion", from_cursor)
        return list(records), str(to_version)
