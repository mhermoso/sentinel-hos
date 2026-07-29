"""Async SQLAlchemy engine and session factory for PostgreSQL 16.

Provides the declarative ``Base`` for all ORM models and a scoped
``async_session_factory`` used throughout the application.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models in the DCW system."""

    pass


async def get_session() -> AsyncSession:
    """FastAPI dependency that yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session  # type: ignore[misc]
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create all tables (development convenience — use Alembic in production)."""
    # Import ORM models so they register with Base.metadata before create_all.
    import app.domains.engine.models  # noqa: F401
    import app.domains.ingestion.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_append_only_trigger)


def _ensure_append_only_trigger(connection) -> None:
    """Attach append-only trigger to canonical_hos_logs if not already present."""
    from sqlalchemy import text

    connection.execute(
        text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname = 'trg_canonical_hos_logs_no_mutation'
                ) THEN
                    CREATE TRIGGER trg_canonical_hos_logs_no_mutation
                    BEFORE UPDATE OR DELETE ON canonical_hos_logs
                    FOR EACH ROW EXECUTE FUNCTION dcw_block_canonical_mutation();
                END IF;
            END $$;
            """
        )
    )


async def close_db() -> None:
    """Dispose of the async engine connection pool."""
    await engine.dispose()
