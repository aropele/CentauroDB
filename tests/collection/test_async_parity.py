"""Sync/async surface parity.

`AsyncCollection` and `AsyncTimeSeriesCollection` are hand-maintained
wrappers — every public method on the sync class needs a counterpart
on the async class, or async users silently fall behind. This test is
the introspection guard: drift now is a test failure, not a forgotten
method discovered six months later.

If a new sync-only method is intentional (e.g., one that can't be made
async), add it to ``_INTENTIONAL_SYNC_ONLY`` with a note.
"""

from __future__ import annotations

from centaurodb.async_collection import AsyncCollection, AsyncTimeSeriesCollection
from centaurodb.collection import Collection, TimeSeriesCollection

# Methods whose sync-only nature is a deliberate design choice. Empty
# today; populate with (name, reason) pairs if a real exception arises.
_INTENTIONAL_SYNC_ONLY: set[str] = set()


def _public_callables(cls: type) -> set[str]:
    return {
        name
        for name in dir(cls)
        if not name.startswith("_") and callable(getattr(cls, name))
    }


class TestSurfaceParity:
    def test_async_collection_mirrors_sync(self):
        sync = _public_callables(Collection)
        async_ = _public_callables(AsyncCollection)
        missing = sync - async_ - _INTENTIONAL_SYNC_ONLY
        assert not missing, (
            f"AsyncCollection is missing forwards for: {sorted(missing)}. "
            "Add the wrapper or document the omission in "
            "_INTENTIONAL_SYNC_ONLY."
        )

    def test_async_timeseries_mirrors_sync(self):
        sync = _public_callables(TimeSeriesCollection)
        async_ = _public_callables(AsyncTimeSeriesCollection)
        missing = sync - async_ - _INTENTIONAL_SYNC_ONLY
        assert not missing, (
            f"AsyncTimeSeriesCollection is missing forwards for: "
            f"{sorted(missing)}. Add the wrapper or document the "
            "omission in _INTENTIONAL_SYNC_ONLY."
        )
