"""DCW Ingestion & Normalization Module for MyGeotab HOS Data.

Continuous-streams raw HOS DutyStatusLog data from MyGeotab API using GetFeed,
validates and sanitizes incoming payloads, and maps them into canonical
Pydantic v2 data models for the Driver Compliance Watch engine.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple
import sys

import mygeotab
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Configure structured JSON / clear format logger
logger = logging.getLogger("dcw.geotab_ingestor")
handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter(
    '{"time": "%(asctime)s", "level": "%(levelname)s", "name": "%(name)s", "message": "%(message)s"}'
)
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


class CanonicalDutyStatus(str, Enum):
    """Canonical Hours of Service (HOS) duty statuses for DCW engine."""

    OFF_DUTY = "OFF"
    SLEEPER_BERTH = "SB"
    DRIVING = "D"
    ON_DUTY = "ON"
    YARD_MOVE = "YM"
    PERSONAL_CONVEYANCE = "PC"
    UNKNOWN = "UNKNOWN"


class DCWCanonicalHOSLog(BaseModel):
    """Canonical data model representing an HOS Log event in DCW."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    tenant_id: str = Field(..., description="Unique customer database identifier")
    driver_id: str = Field(..., description="Normalized driver ID")
    driver_name: Optional[str] = Field(None, description="Driver's full name")
    raw_id: str = Field(..., description="MyGeotab record ID")
    status: CanonicalDutyStatus
    event_timestamp: datetime = Field(..., description="UTC timestamp of HOS status change")
    device_id: Optional[str] = Field(None, description="Assigned vehicle device ID")
    latitude: Optional[float] = Field(None, ge=-90.0, le=90.0)
    longitude: Optional[float] = Field(None, ge=-180.0, le=180.0)
    odometer_km: Optional[float] = Field(None, ge=0.0)
    annotation: Optional[str] = Field(None, max_length=500)
    raw_payload: Dict[str, Any] = Field(..., description="Snapshot of original Geotab JSON payload")

    @field_validator("event_timestamp", mode="before")
    @classmethod
    def parse_datetime(cls, value: Any) -> datetime:
        """Standardize ISO string or Geotab datetime to UTC timezone-aware datetime object."""
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt
        elif isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return value


class GeotabSettings(BaseSettings):
    """Environment configuration settings for Geotab ingestion."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    geotab_server: str = Field("my.geotab.com", alias="GEOTAB_SERVER")
    geotab_database: str = Field(..., alias="GEOTAB_DATABASE")
    geotab_username: str = Field(..., alias="GEOTAB_USERNAME")
    geotab_password: str = Field(..., alias="GEOTAB_PASSWORD")


def sanitize_raw_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively scrub sensitive keys (e.g. passwords, tokens) from raw payload dicts."""
    sanitized: Dict[str, Any] = {}
    sensitive_keys = {"password", "sessionid", "credentials", "token", "secret", "auth"}
    for k, v in payload.items():
        if k.lower() in sensitive_keys:
            sanitized[k] = "[MASKED]"
        elif isinstance(v, dict):
            sanitized[k] = sanitize_raw_payload(v)
        elif isinstance(v, list):
            sanitized[k] = [
                sanitize_raw_payload(item) if isinstance(item, dict) else item for item in v
            ]
        else:
            sanitized[k] = v
    return sanitized


def map_geotab_log_to_canonical(raw_log: Dict[str, Any], tenant_id: str, driver_name: Optional[str] = None) -> DCWCanonicalHOSLog:
    """Map a raw MyGeotab DutyStatusLog dict to a DCWCanonicalHOSLog model.

    Args:
        raw_log: The raw dictionary returned by MyGeotab GetFeed API.
        tenant_id: Customer database/tenant identifier.

    Returns:
        DCWCanonicalHOSLog: Validated canonical log instance.
    """
    raw_id = str(raw_log.get("id", ""))
    
    # Driver ID extraction
    driver_dict = raw_log.get("driver")
    driver_id = "UNKNOWN_DRIVER"
    if isinstance(driver_dict, dict) and driver_dict.get("id"):
        driver_id = str(driver_dict["id"])

    # Device ID extraction
    device_dict = raw_log.get("device")
    device_id = None
    if isinstance(device_dict, dict) and device_dict.get("id"):
        device_id = str(device_dict["id"])

    # Status & Origin mapping logic
    # Geotab status strings: "Driving", "Off", "SleeperBerth", "On"
    # Special origins: "YardMove", "PersonalConveyance"
    origin = raw_log.get("origin")
    status_str = raw_log.get("status")

    if origin == "YardMove":
        canonical_status = CanonicalDutyStatus.YARD_MOVE
    elif origin == "PersonalConveyance":
        canonical_status = CanonicalDutyStatus.PERSONAL_CONVEYANCE
    elif status_str in ("Driving", "D", "INT_D"):
        canonical_status = CanonicalDutyStatus.DRIVING
    elif status_str in ("Off", "OFF"):
        canonical_status = CanonicalDutyStatus.OFF_DUTY
    elif status_str in ("SleeperBerth", "SB"):
        canonical_status = CanonicalDutyStatus.SLEEPER_BERTH
    elif status_str in ("On", "ON"):
        canonical_status = CanonicalDutyStatus.ON_DUTY
    else:
        canonical_status = CanonicalDutyStatus.UNKNOWN

    # Location extraction (Geotab geometry quirk: x is Longitude, y is Latitude)
    location = raw_log.get("location")
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    if isinstance(location, dict):
        if "location" in location and isinstance(location["location"], dict):
            latitude = location["location"].get("y")
            longitude = location["location"].get("x")
        else:
            latitude = location.get("y")
            longitude = location.get("x")

    # Comment / Annotation sanitization
    comment = raw_log.get("comment")
    annotation: Optional[str] = None
    if comment and isinstance(comment, str):
        annotation = comment.strip()[:500]

    # Odometer extraction if present (e.g. state or distance metrics)
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


class GeotabIngestor:
    """Manages MyGeotab API session connection, continuous GetFeed polling, and data normalization."""

    def __init__(self, settings: GeotabSettings) -> None:
        """Initialize ingestor with validated settings.

        Args:
            settings: GeotabSettings containing target server and credentials.
        """
        self.settings = settings
        self.api: Optional[mygeotab.API] = None
        self._cursor: str = "0000000000000000"

    async def connect(self) -> None:
        """Authenticate with the MyGeotab API and establish session context.

        Handles authentication exceptions with standard security shielding.
        """
        logger.info(
            "Connecting to MyGeotab API",
            extra={"database": self.settings.geotab_database, "server": self.settings.geotab_server},
        )
        try:
            self.api = mygeotab.API(
                username=self.settings.geotab_username,
                password=self.settings.geotab_password,
                database=self.settings.geotab_database,
                server=self.settings.geotab_server,
            )
            # Authenticate asynchronously using loop.run_in_executor
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.api.authenticate)
            logger.info("Successfully authenticated with MyGeotab server")
        except mygeotab.AuthenticationException:
            logger.error("Authentication failed for MyGeotab user (credentials masked)")
            raise
        except mygeotab.MyGeotabException as exc:
            logger.error("MyGeotab SDK exception during connect", extra={"error": str(exc)})
            raise
        except Exception as exc:
            logger.error("Unexpected failure connecting to MyGeotab", extra={"error": str(exc)})
            raise

    async def fetch_hos_feed(
        self, from_version: str = "0000000000000000"
    ) -> Tuple[List[DCWCanonicalHOSLog], str]:
        """Fetch a batch of DutyStatusLog records via GetFeed API call.

        Args:
            from_version: Cursor version token string.

        Returns:
            Tuple[List[DCWCanonicalHOSLog], str]: Valid canonical logs and next version token.
        """
        if self.api is None:
            await self.connect()
            assert self.api is not None

        try:
            loop = asyncio.get_running_loop()
            # Execute api.call_async or call inside executor for MyGeotab SDK compatibility
            feed_response = await loop.run_in_executor(
                None,
                lambda: self.api.call(
                    "GetFeed",
                    typeName="DutyStatusLog",
                    fromVersion=from_version,
                    resultsLimit=5000,
                ),
            )
        except mygeotab.AuthenticationException:
            logger.warning("Session expired or invalid during fetch_hos_feed. Re-authenticating...")
            await self.connect()
            return await self.fetch_hos_feed(from_version=from_version)
        except mygeotab.MyGeotabException as exc:
            logger.error("MyGeotab API error fetching DutyStatusLog feed", extra={"error": str(exc)})
            raise

        records = feed_response.get("result", feed_response.get("data", []))
        to_version = feed_response.get("toVersion", from_version)

        valid_logs: List[DCWCanonicalHOSLog] = []

        for record in records:
            record_id = record.get("id", "UNKNOWN_ID")
            try:
                canonical_log = map_geotab_log_to_canonical(record, tenant_id=self.settings.geotab_database)
                valid_logs.append(canonical_log)
            except ValidationError as ve:
                logger.warning(
                    "Validation failed for DutyStatusLog record; isolated to DLQ",
                    extra={"record_id": record_id, "errors": ve.errors()},
                )
            except Exception as exc:
                logger.warning(
                    "Unexpected parsing failure for DutyStatusLog record; isolated to DLQ",
                    extra={"record_id": record_id, "error": str(exc)},
                )

        return valid_logs, to_version

    async def start_stream(
        self,
        callback: Callable[[List[DCWCanonicalHOSLog]], None],
        initial_version: str = "0000000000000000",
    ) -> None:
        """Start continuous async streaming loop for HOS logs.

        Args:
            callback: Async or synchronous function invoked when new valid canonical logs arrive.
            initial_version: Starting cursor version token.
        """
        self._cursor = initial_version
        logger.info("Starting continuous HOS ingestion stream", extra={"initial_cursor": self._cursor})

        backoff = 1
        max_backoff = 60

        while True:
            try:
                logs, next_version = await self.fetch_hos_feed(from_version=self._cursor)
                self._cursor = next_version

                if logs:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(logs)
                    else:
                        callback(logs)
                    backoff = 1  # Reset backoff on success
                else:
                    # Adaptive sleep: wait 10 seconds if no records found
                    await asyncio.sleep(10)

            except (mygeotab.MyGeotabException, Exception) as exc:
                logger.error(
                    "Error encountered in stream loop. Applying backoff.",
                    extra={"error": str(exc), "backoff_seconds": backoff},
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)


if __name__ == "__main__":
    import os

    print("--- DCW Geotab Ingestor Module Demonstration ---")
    
    # 1. Demonstrate validation & conversion logic with mock data
    mock_payload = {
        "id": "b123",
        "driver": {"id": "b45"},
        "status": "Driving",
        "dateTime": "2026-07-28T12:00:00.000Z",
        "device": {"id": "b99"},
        "location": {"x": -84.3880, "y": 33.7490},
        "comment": " Routine driving log check ",
        "origin": "Admin",
    }
    
    canonical = map_geotab_log_to_canonical(mock_payload, tenant_id="demo_fleet")
    print("\nMock Geotab Payload successfully mapped to Canonical Model:")
    print(f"Tenant ID:       {canonical.tenant_id}")
    print(f"Driver ID:       {canonical.driver_id}")
    print(f"Raw Log ID:      {canonical.raw_id}")
    print(f"Canonical Status:{canonical.status} ({canonical.status.value})")
    print(f"Timestamp UTC:   {canonical.event_timestamp}")
    print(f"Device ID:       {canonical.device_id}")
    print(f"Lat / Long:      {canonical.latitude}, {canonical.longitude}")
    print(f"Annotation:      {canonical.annotation}")
    print("\nModule initialized successfully.")
