"""In-memory alert lock simulation for historical backtests."""

from __future__ import annotations

from app.core.redis import alert_lock_key


class InMemoryAlertLock:
    """Mirrors ``suppressor.py`` lock semantics without Redis."""

    def __init__(self) -> None:
        self._locks: set[str] = set()

    def would_suppress(
        self,
        tenant_id: str,
        driver_id: str,
        shift_id: str,
        rule: str,
        stage: str,
    ) -> bool:
        key = alert_lock_key(tenant_id, driver_id, shift_id, rule, stage)
        return key in self._locks

    def try_acquire(
        self,
        tenant_id: str,
        driver_id: str,
        shift_id: str,
        rule: str,
        stage: str,
    ) -> bool:
        key = alert_lock_key(tenant_id, driver_id, shift_id, rule, stage)
        if key in self._locks:
            return False
        self._locks.add(key)
        return True

    def would_dispatch(
        self,
        tenant_id: str,
        driver_id: str,
        shift_id: str,
        rule: str,
        stage: str,
    ) -> bool:
        """Return True when this violation would trigger a dispatch (not suppressed)."""
        if self.would_suppress(tenant_id, driver_id, shift_id, rule, stage):
            return False
        return self.try_acquire(tenant_id, driver_id, shift_id, rule, stage)
