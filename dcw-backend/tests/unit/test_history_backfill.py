"""Unit tests for history backfill Redis key / settings defaults."""

from app.core.config import Settings
from app.domains.ingestion.history_backfill import bootstrap_key


def test_bootstrap_key_includes_days_and_tenant() -> None:
    assert bootstrap_key("b_b_bros_transport", 30) == (
        "bootstrap:geotab-history:30d:v1:b_b_bros_transport"
    )


def test_history_backfill_settings_default_to_30_days() -> None:
    s = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/db",
    )
    assert s.HISTORY_BACKFILL_ON_STARTUP is True
    assert s.HISTORY_BACKFILL_DAYS == 30
    assert s.GPS_BACKFILL_DAYS == 30
