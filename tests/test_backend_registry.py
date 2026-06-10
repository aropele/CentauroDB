"""Backend registry: third parties can plug in custom Engine subclasses.

The dispatch in ``Engine.__new__`` is currently hardcoded to two
prefixes (``postgresql://``, ``sqlite``-anything). Anyone who wants
to add DuckDB, MotherDuck, MySQL, an in-memory mock, or a network
backend must monkey-patch ``__new__`` or fork the library. The
registry makes ``register_backend`` the documented extension point.
"""

from __future__ import annotations

import pytest

from centaurodb import Engine
from centaurodb.engine import PostgresEngine, SQLiteEngine, register_backend


class _FakeEngine(Engine):
    """Minimal Engine stand-in used by the registry tests."""

    def __init__(self, url: str = "", **kwargs):
        self.url = url
        self.opened = True

    def close(self):
        self.opened = False


class TestBuiltinBackendsStillDispatch:
    def test_no_arg_is_sqlite(self):
        e = Engine()
        assert isinstance(e, SQLiteEngine)
        e.close()

    def test_sqlite_url_is_sqlite(self):
        e = Engine("sqlite://")
        assert isinstance(e, SQLiteEngine)
        e.close()

    def test_plain_path_is_sqlite(self, tmp_path):
        e = Engine(str(tmp_path / "x.db"))
        assert isinstance(e, SQLiteEngine)
        e.close()


class TestRegisterCustomBackend:
    def setup_method(self):
        # Snapshot any previous registration so tests don't bleed.
        self._prev = None

    def teardown_method(self):
        # Best-effort cleanup of the fake prefix between tests.
        from centaurodb.engine import _BACKEND_REGISTRY

        _BACKEND_REGISTRY.pop("fake", None)

    def test_register_routes_new_prefix(self):
        register_backend("fake", _FakeEngine)
        e = Engine("fake://anything")
        assert isinstance(e, _FakeEngine)
        assert e.url == "fake://anything"

    def test_register_overrides_previous_mapping(self):
        register_backend("fake", _FakeEngine)

        class OtherFake(_FakeEngine):
            pass

        register_backend("fake", OtherFake)
        e = Engine("fake://x")
        assert isinstance(e, OtherFake)

    def test_unregistered_prefix_falls_back_to_sqlite(self):
        # No registration for "unknown" -> still routed to SQLite
        # (existing behaviour). We avoid opening a connection here
        # because SQLite would then try to use the literal URL as a
        # file path; only the dispatch type matters.
        instance = Engine.__new__(Engine, "unknown://something")
        assert isinstance(instance, SQLiteEngine)

    def test_register_invalid_prefix_rejected(self):
        with pytest.raises(ValueError, match="prefix"):
            register_backend("", _FakeEngine)

    def test_register_non_engine_class_rejected(self):
        class NotAnEngine:
            pass

        with pytest.raises(TypeError, match="Engine"):
            register_backend("notengine", NotAnEngine)  # type: ignore[arg-type]


class TestPostgresRegistration:
    def test_postgres_prefix_still_dispatches(self):
        # We don't open a connection (no psycopg-driven setup) — just
        # confirm __new__ produces a PostgresEngine instance for the
        # registered prefix. ImportError surfaces from __init__, which
        # we suppress here for the type-check.
        from unittest.mock import patch

        with patch.object(PostgresEngine, "__init__", lambda self, *a, **k: None):
            e = Engine("postgresql://x")
            assert isinstance(e, PostgresEngine)

    def test_postgres_short_prefix_still_dispatches(self):
        from unittest.mock import patch

        with patch.object(PostgresEngine, "__init__", lambda self, *a, **k: None):
            e = Engine("postgres://x")
            assert isinstance(e, PostgresEngine)
