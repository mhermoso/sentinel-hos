"""Centralized configuration for the DCW backend.

All settings are loaded from environment variables (or .env file) using
Pydantic Settings.  Every layer references this single ``settings`` instance.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> str:
    """Normalize a Postgres connection URL for SQLAlchemy + asyncpg.

    Managed providers (e.g. DigitalOcean) hand out libpq-style URLs such as
    ``postgresql://user:pass@host:25060/db?sslmode=require``. SQLAlchemy
    requires the ``postgresql+asyncpg://`` scheme, and asyncpg rejects the
    libpq ``sslmode`` keyword in favor of ``ssl``.
    """
    parts = urlsplit(url)
    scheme = parts.scheme
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"
    query = [
        ("ssl" if key == "sslmode" else key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class Settings(BaseSettings):
    """Application-wide settings sourced from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── General ──────────────────────────────────────────────────────────
    APP_NAME: str = "Driver Compliance Watch Managed SaaS"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    SECRET_KEY: str = "default_secret_key"

    # ── PostgreSQL 16 ────────────────────────────────────────────────────
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "dcw_user"
    POSTGRES_PASSWORD: str = "dcw_secure_password"
    POSTGRES_DB: str = "dcw_compliance_db"
    DATABASE_URL: str = (
        "postgresql+asyncpg://dcw_user:dcw_secure_password@localhost:5432/dcw_compliance_db"
    )

    @field_validator("DATABASE_URL")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)

    # ── Redis 7.2 ────────────────────────────────────────────────────────
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Geotab Telematics ────────────────────────────────────────────────
    GEOTAB_SERVER: str = "my.geotab.com"
    GEOTAB_DATABASE: str = ""
    GEOTAB_USERNAME: str = ""
    GEOTAB_PASSWORD: str = ""

    # ── Twilio Alerting ──────────────────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_API_KEY_SID: str = ""
    TWILIO_API_KEY_SECRET: str = ""
    TWILIO_FROM_PHONE_NUMBER: str = ""
    TWILIO_TEST_TO_PHONE: str = Field(
        default="",
        description="Override driver phone for validation (E.164)",
    )
    TWILIO_TEST_DISPATCHER_PHONE: str = Field(
        default="",
        description="Override dispatcher phone for validation (E.164)",
    )

    # ── Alert logging / dry-run ──────────────────────────────────────────
    ALERT_LOG_PATH: str = "logs/compliance-alerts.log"
    ALERT_DRY_RUN: bool = Field(
        default=True,
        description="When true, log alerts but skip Twilio dispatch",
    )

    # ── Ops event log (dashboard Logs page) ──────────────────────────────
    OPS_LOG_PATH: str = Field(
        default="logs/ops-events.log",
        description="Append-only JSONL sink for dcw.* operational log events",
    )

    # ── Stripe Billing ───────────────────────────────────────────────────
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STARTER_TIER_PRICE_PER_VEHICLE: float = 8.00
    PRO_TIER_PRICE_PER_VEHICLE: float = 18.00

    # ── Motive / Samsara (future) ────────────────────────────────────────
    SAMSARA_API_TOKEN: str = ""
    MOTIVE_API_KEY: str = ""

    # ── Compliance Engine ────────────────────────────────────────────────
    DEFAULT_RULE_PACK_VERSION: str = "fmcsa-us-property@1.3.0"
    DEFAULT_HOME_TERMINAL_TIMEZONE: str = Field(
        default="America/Chicago",
        description="Home terminal IANA timezone for 34h restart 1–5 AM validation (ADR-005)",
    )
    WEEKLY_CYCLE_DAYS: int = Field(
        default=8,
        description="Rolling window for 60/70-hour rule: 7-day (60h) or 8-day (70h)",
    )
    WEEKLY_CYCLE_LIMIT_HOURS: float = Field(
        default=70.0,
        description="Cumulative duty-hour ceiling for the rolling weekly window",
    )

    # ── Ingestion Poller ─────────────────────────────────────────────────
    POLL_INTERVAL_SECONDS: int = 120
    FEED_RESULTS_LIMIT: int = 5000
    HISTORY_BACKFILL_ON_STARTUP: bool = Field(
        default=True,
        description=(
            "When true, the ARQ worker runs a one-shot Geotab Get backfill for "
            "the last HISTORY_BACKFILL_DAYS (HOS) / GPS_BACKFILL_DAYS (LogRecord) "
            "before incremental GetFeed polling. Guarded by a Redis flag."
        ),
    )
    HISTORY_BACKFILL_DAYS: int = Field(
        default=30,
        description="DutyStatusLog lookback window for startup history backfill",
    )
    GPS_BACKFILL_DAYS: int = Field(
        default=30,
        description="LogRecord GPS lookback window for startup history backfill",
    )


settings = Settings()
