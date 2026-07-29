"""Geotab telematics adapter — proven integration from geotab_ingestor.py.

Refactored from the tested standalone ``geotab_ingestor.py`` to fit the
``BaseTelematicsAdapter`` interface while preserving all mapping logic,
status/origin handling, and Geotab location quirks (x = Longitude, y = Latitude).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import mygeotab
from pydantic import ValidationError

from app.core.config import settings
from app.domains.ingestion.adapters import BaseTelematicsAdapter
from app.domains.ingestion.normalizer import sanitize_raw_payload
from app.domains.ingestion.schemas import CanonicalDutyStatus, DCWCanonicalHOSLog

logger = logging.getLogger("dcw.adapters.geotab")


# ── Mapping Logic (from tested geotab_ingestor.py) ──────────────────────


def _map_geotab_status(
    status_str: Optional[str],
    origin: Optional[str],
) -> CanonicalDutyStatus:
    """Map Geotab status + origin strings to canonical duty status.

    Geotab status strings: "Driving", "Off", "SleeperBerth", "On"
    Special origins: "YardMove", "PersonalConveyance"
    """
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

    # Odometer extraction
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


# ── Adapter Class ────────────────────────────────────────────────────────


class GeotabAdapter(BaseTelematicsAdapter):
    """MyGeotab API adapter using GetFeed for continuous HOS log polling.

    Wraps the proven ``mygeotab`` SDK calls from the tested ingestor module.
    """

    provider_name = "geotab"

    def __init__(self) -> None:
        self.api: Optional[mygeotab.API] = None

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
                canonical_log = map_geotab_log_to_canonical(
                    record, tenant_id=tenant_id
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
