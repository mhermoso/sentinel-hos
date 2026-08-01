"""Unit tests for DATABASE_URL normalization (managed Postgres TLS support)."""

from __future__ import annotations

from app.core.config import Settings, normalize_database_url


class TestNormalizeDatabaseURL:
    def test_digitalocean_managed_url_is_rewritten_for_asyncpg(self) -> None:
        url = "postgresql://doadmin:secret@db-host.k.db.ondigitalocean.com:25060/dcw?sslmode=require"
        assert normalize_database_url(url) == (
            "postgresql+asyncpg://doadmin:secret@db-host.k.db.ondigitalocean.com:25060/dcw?ssl=require"
        )

    def test_postgres_scheme_alias_is_rewritten(self) -> None:
        url = "postgres://u:p@host:5432/db"
        assert normalize_database_url(url) == "postgresql+asyncpg://u:p@host:5432/db"

    def test_already_normalized_url_is_unchanged(self) -> None:
        url = "postgresql+asyncpg://dcw_user:dcw_secure_password@localhost:5432/dcw_compliance_db"
        assert normalize_database_url(url) == url

    def test_sslmode_is_renamed_even_on_asyncpg_scheme(self) -> None:
        url = "postgresql+asyncpg://u:p@host:25060/db?sslmode=verify-full"
        assert normalize_database_url(url) == "postgresql+asyncpg://u:p@host:25060/db?ssl=verify-full"

    def test_other_query_params_are_preserved(self) -> None:
        url = "postgresql://u:p@host:25060/db?sslmode=require&application_name=dcw"
        assert normalize_database_url(url) == (
            "postgresql+asyncpg://u:p@host:25060/db?ssl=require&application_name=dcw"
        )


class TestSettingsValidator:
    def test_settings_normalizes_database_url(self) -> None:
        settings = Settings(
            DATABASE_URL="postgresql://doadmin:secret@db-host:25060/dcw?sslmode=require",
        )
        assert settings.DATABASE_URL == (
            "postgresql+asyncpg://doadmin:secret@db-host:25060/dcw?ssl=require"
        )
