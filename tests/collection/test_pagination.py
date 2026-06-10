"""ATDD: pagination (limit/offset) and count_objects on Collection.

read_objects materialises the entire result set today; for any
non-trivial dataset that is a memory and latency hazard. Pagination
is the smallest unblocker: bounded reads + a count helper so
clients can drive their own pagers.
"""

from __future__ import annotations

import pytest
from centaurodb.collection import Collection
from centaurodb.engine import Engine
from centaurodb.model import CentauroModel


class Item(CentauroModel):
    __centauro_name__ = "Item"
    n: int = 0
    bucket: str = ""


@pytest.fixture
def col():
    with Engine(":memory:") as engine:
        c = Collection(engine, "items")
        for i in range(20):
            c.write_object(Item(n=i, bucket="even" if i % 2 == 0 else "odd"))
        yield c


class TestReadObjectsLimit:
    def test_limit_caps_result_size(self, col):
        rows = col.read_objects(Item, limit=5)
        assert len(rows) == 5

    def test_limit_with_conditions(self, col):
        rows = col.read_objects(Item.fields.bucket == "even", limit=3)
        assert len(rows) == 3
        assert all(r.bucket == "even" for r in rows)

    def test_limit_zero_returns_empty(self, col):
        assert col.read_objects(Item, limit=0) == []

    def test_negative_limit_raises(self, col):
        with pytest.raises(ValueError, match="limit"):
            col.read_objects(Item, limit=-1)


class TestReadObjectsOffset:
    def test_offset_skips_rows(self, col):
        page1 = col.read_objects(Item, limit=5, offset=0)
        page2 = col.read_objects(Item, limit=5, offset=5)
        assert len(page1) == 5 and len(page2) == 5
        ids1 = {o._row.id for o in page1}
        ids2 = {o._row.id for o in page2}
        assert ids1.isdisjoint(ids2)

    def test_offset_requires_limit(self, col):
        with pytest.raises(ValueError, match="offset"):
            col.read_objects(Item, offset=5)

    def test_negative_offset_raises(self, col):
        with pytest.raises(ValueError, match="offset"):
            col.read_objects(Item, limit=5, offset=-1)

    def test_pagination_is_deterministic_across_calls(self, col):
        a = col.read_objects(Item, limit=10, offset=0)
        b = col.read_objects(Item, limit=10, offset=0)
        assert [o._row.id for o in a] == [o._row.id for o in b]


class TestCountObjects:
    def test_count_all_of_model(self, col):
        assert col.count_objects(Item) == 20

    def test_count_with_conditions(self, col):
        assert col.count_objects(Item.fields.bucket == "even") == 10
        assert col.count_objects(Item.fields.n > 14) == 5

    def test_count_zero_when_no_match(self, col):
        assert col.count_objects(Item.fields.bucket == "missing") == 0
