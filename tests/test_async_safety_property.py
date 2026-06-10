"""Engine.async_safety property — extension point for async backends.

AsyncCollection currently checks ``conn._check_same_thread`` (a private
SQLiteEngine attribute). When a future native-async backend lands
(psycopg.AsyncConnection + pool), AsyncCollection has no clean way to
know whether the engine is safe for concurrent dispatch. The property
makes the contract explicit and lets backends opt in/out.

Contract values:

- ``"unsafe"``: the engine cannot be used from a background thread
  at all. AsyncCollection raises ThreadSafetyError.
- ``"serial"``: the engine is safe under to_thread but only for
  sequential calls (current SQLite + check_same_thread=False).
- ``"concurrent"``: the engine is safe for parallel calls (future
  pool-backed backends).
"""

from __future__ import annotations

import pytest

from centaurodb import (
    AsyncCollection,
    AsyncTimeSeriesCollection,
    Engine,
    ThreadSafetyError,
)


class TestAsyncSafetyProperty:
    def test_default_sqlite_is_unsafe(self):
        with Engine() as e:
            assert e.async_safety == "unsafe"

    def test_sqlite_with_check_same_thread_false_is_serial(self):
        with Engine(check_same_thread=False) as e:
            assert e.async_safety == "serial"

    def test_property_is_documented_literal(self):
        # The annotation should narrow to the three documented states
        # so type-checkers can reason about backend safety.
        with Engine() as e:
            assert e.async_safety in ("unsafe", "serial", "concurrent")


class TestAsyncCollectionUsesProperty:
    """AsyncCollection must reject 'unsafe' engines via the property,
    not by reaching into the private _check_same_thread attribute."""

    def test_async_collection_rejects_unsafe_engine(self):
        with Engine() as e:
            with pytest.raises(ThreadSafetyError):
                AsyncCollection(e, "items")

    def test_async_timeseries_rejects_unsafe_engine(self):
        with Engine() as e:
            with pytest.raises(ThreadSafetyError):
                AsyncTimeSeriesCollection(e, "metrics")

    def test_async_collection_accepts_serial_engine(self):
        with Engine(check_same_thread=False) as e:
            col = AsyncCollection(e, "items")
            assert col.name == "items"

    def test_concurrent_backend_passes_safety_gate(self):
        """A custom backend reporting async_safety='concurrent' must
        pass the gate — this is the forward-compat path for a future
        native-async PostgresEngine. We only assert the gate logic;
        the underlying Collection construction needs a real dialect
        (out of scope for this property contract)."""

        class _ConcurrentEngine(Engine):
            def __init__(self, url: str = "", **kwargs):
                pass

            @property
            def async_safety(self):
                return "concurrent"

            def close(self):
                pass

        e = _ConcurrentEngine()
        # The gate must NOT raise ThreadSafetyError. Anything else
        # downstream (missing dialect, missing tables) is fine — we
        # are pinning the property contract, not the full happy path.
        try:
            AsyncCollection(e, "items")
        except ThreadSafetyError:
            pytest.fail("ThreadSafetyError raised for a 'concurrent' backend")
        except Exception:
            pass  # Downstream construction may fail; not our concern
