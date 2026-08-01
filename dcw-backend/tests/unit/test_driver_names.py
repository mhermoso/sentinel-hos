"""Unit tests for driver display-name resolution."""

from app.domains.dashboard import driver_names as dn


def test_resolve_prefers_db_name(monkeypatch) -> None:
    monkeypatch.setattr(dn, "_RUNTIME_NAMES", {"b6": "From Cache"})
    monkeypatch.setattr(dn, "load_driver_name_map", lambda: {"b6": "From Seed"})
    assert dn.resolve_driver_name("b6", "From DB") == "From DB"


def test_resolve_uses_runtime_cache_when_db_null(monkeypatch) -> None:
    monkeypatch.setattr(dn, "_RUNTIME_NAMES", {"b6": "Cached Driver"})
    monkeypatch.setattr(dn, "load_driver_name_map", lambda: {})
    assert dn.resolve_driver_name("b6", None) == "Cached Driver"


def test_resolve_falls_back_to_seed_map(monkeypatch) -> None:
    monkeypatch.setattr(dn, "_RUNTIME_NAMES", {})
    monkeypatch.setattr(dn, "load_driver_name_map", lambda: {"b6": "Seed Driver"})
    assert dn.resolve_driver_name("b6") == "Seed Driver"


def test_driver_names_key() -> None:
    assert dn.driver_names_key("b_b_bros_transport") == (
        "hash:driver_names:b_b_bros_transport"
    )


def test_build_geotab_driver_name_map() -> None:
    class _FakeApi:
        def get(self, _type: str):
            return [
                {"id": "b6", "firstName": "Ada", "lastName": "Lovelace"},
                {"id": "b7", "name": "Legacy Name"},
                {"firstName": "No", "lastName": "Id"},
            ]

    from app.domains.ingestion.geotab_users import build_geotab_driver_name_map

    assert build_geotab_driver_name_map(_FakeApi()) == {  # type: ignore[arg-type]
        "b6": "Ada Lovelace",
        "b7": "Legacy Name",
    }
