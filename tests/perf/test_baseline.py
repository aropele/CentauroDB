"""Performance regression baselines.

These are not micro-benchmarks. The bounds are intentionally
generous (5x-10x what the operation costs on a modest machine)
so the suite doesn't flake on slow CI runners. The point is to
catch order-of-magnitude regressions — an O(N) path that drifts
to O(N^2), or an accidentally re-opened transaction inside a
hot loop. Tightening the numbers belongs in a dedicated
benchmarking project, not here.

Run only the perf bucket:
    uv run python -m pytest -m perf

Skip it on a slow runner:
    uv run python -m pytest -m "not perf"
"""

from __future__ import annotations

import time
from contextlib import contextmanager

import pytest

from centaurodb import (
    CentauroModel,
    Collection,
    Engine,
    renamed_from,
)


@contextmanager
def _timed(label: str, budget_s: float):
    t0 = time.perf_counter()
    yield
    elapsed = time.perf_counter() - t0
    assert elapsed < budget_s, (
        f"{label}: {elapsed:.3f}s exceeded {budget_s:.3f}s budget. "
        f"Possible order-of-magnitude regression."
    )


class Item(CentauroModel):
    __centauro_name__ = "Item"
    n: int = 0
    bucket: str = ""


@pytest.mark.perf
def test_thousand_writes_within_generous_budget(tmp_path):
    """1000 single writes through a file-backed engine. Catches
    accidental per-write overhead spikes (extra commits, schema
    re-introspection, etc.). Budget: 5s."""
    db = tmp_path / "perf.db"
    with Engine(str(db)) as e:
        col = Collection(e, "items")
        with _timed("1000 writes", budget_s=5.0):
            for i in range(1000):
                col.write_object(Item(n=i, bucket="even" if i % 2 == 0 else "odd"))


@pytest.mark.perf
def test_batch_write_is_faster_than_singles(tmp_path):
    """write_objects(N) must beat N x write_object() — that's
    its entire reason to exist. We don't pin a ratio (machine-
    dependent), only the direction."""
    db_a = tmp_path / "single.db"
    db_b = tmp_path / "batch.db"
    objs_a = [Item(n=i) for i in range(500)]
    objs_b = [Item(n=i) for i in range(500)]

    with Engine(str(db_a)) as e:
        col = Collection(e, "items")
        t0 = time.perf_counter()
        for o in objs_a:
            col.write_object(o)
        single_dt = time.perf_counter() - t0

    with Engine(str(db_b)) as e:
        col = Collection(e, "items")
        t0 = time.perf_counter()
        col.write_objects(objs_b)
        batch_dt = time.perf_counter() - t0

    assert batch_dt < single_dt, (
        f"Batch ({batch_dt:.3f}s) must be faster than singles "
        f"({single_dt:.3f}s) — write_objects appears to have lost "
        f"its single-transaction advantage."
    )


@pytest.mark.perf
def test_deep_pagination_does_not_explode(tmp_path):
    """Reading the last page of a 10k row table must remain
    bounded. Catches a regression where pagination accidentally
    materialises the full result set before slicing. Budget: 2s."""
    db = tmp_path / "page.db"
    with Engine(str(db)) as e:
        col = Collection(e, "items")
        col.write_objects([Item(n=i) for i in range(10_000)])

    with Engine(str(db)) as e:
        col = Collection(e, "items")
        with _timed("deep pagination read", budget_s=2.0):
            tail = col.read_objects(Item, limit=100, offset=9_900)
        assert len(tail) == 100


@pytest.mark.perf
def test_count_objects_does_not_materialize_rows(tmp_path):
    """count_objects() over 10k rows must be much faster than
    materialising them. Catches a regression where COUNT(*)
    accidentally goes through the hydration path. Budget: 0.5s."""
    db = tmp_path / "count.db"
    with Engine(str(db)) as e:
        Collection(e, "items").write_objects(
            [Item(n=i, bucket="even" if i % 2 == 0 else "odd") for i in range(10_000)]
        )

    with Engine(str(db)) as e:
        col = Collection(e, "items")
        with _timed("count_objects on 10k", budget_s=0.5):
            n = col.count_objects(Item.fields.bucket == "even")
        assert n == 5_000


@pytest.mark.perf
def test_schema_evolution_read_is_not_quadratic(tmp_path):
    """Read 5000 rows written under v1 through a v2 class with a
    renamed field. Catches a regression where alias resolution
    becomes per-row expensive. Budget: 5s."""
    db = tmp_path / "evo.db"

    class BookV1(CentauroModel):
        __centauro_name__ = "Book"
        title: str = ""
        pg: int = 0

    with Engine(str(db)) as e:
        col = Collection(e, "library")
        col.write_objects([BookV1(title=f"B{i}", pg=i) for i in range(5_000)])

    class BookV2(CentauroModel):
        __centauro_name__ = "Book"
        title: str = ""
        pages: int = renamed_from("pg", default=0)

    with Engine(str(db)) as e:
        col = Collection(e, "library")
        with _timed("read 5k v1 rows under v2 schema", budget_s=5.0):
            books = col.read_objects(BookV2)
        assert len(books) == 5_000
        assert books[0].pages == 0
        assert books[-1].pages == 4_999
