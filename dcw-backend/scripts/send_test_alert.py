#!/usr/bin/env python3
"""Send a synthetic compliance alert through the notifier dispatch path."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.config import settings
from app.domains.notifier.schemas import AlertStage, ComplianceAlert
from app.domains.notifier.subscriber import _dispatch_alert

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("dcw.scripts.send_test_alert")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Dispatch a test compliance alert")
    parser.add_argument(
        "--driver-id",
        default="TEST_DRIVER",
        help="Driver ID for the synthetic alert",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=settings.ALERT_DRY_RUN,
        help="Skip Twilio (log only)",
    )
    args = parser.parse_args()

    if args.dry_run:
        settings.ALERT_DRY_RUN = True

    alert = ComplianceAlert(
        tenant_id=settings.GEOTAB_DATABASE or "test_tenant",
        driver_id=args.driver_id,
        violation_type="DRIVING_LIMIT",
        severity=AlertStage.VIOLATION,
        rule_ref="§ 395.3(a)(3)(i)",
        description="Test alert — 11-hour driving limit exceeded (synthetic).",
        detected_at=datetime.now(timezone.utc),
        driver_phone=settings.TWILIO_TEST_TO_PHONE or None,
        dispatcher_phone=settings.TWILIO_TEST_DISPATCHER_PHONE or None,
    )

    logger.info(
        "Dispatching test alert (dry_run=%s, driver_phone=%s, dispatcher_phone=%s)",
        settings.ALERT_DRY_RUN,
        bool(alert.driver_phone),
        bool(alert.dispatcher_phone),
    )
    await _dispatch_alert(alert)
    logger.info("Done — check %s for log output", settings.ALERT_LOG_PATH)


if __name__ == "__main__":
    asyncio.run(main())
