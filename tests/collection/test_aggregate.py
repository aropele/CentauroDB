"""aggregate() — pre-1.0 reservation of the multi-row aggregation API.

The current single-row aggregates (max_field / min_field on
read_objects) don't scale to the natural follow-ups: ``count``,
``sum``, ``avg``, ``group_by``. Inflating read_objects with those
kwargs would either change its return type or force a soup of
overloads.

We reserve the shape now via ``Collection.aggregate(...)`` so
client code that wants count today writes the call in the form
that will keep working when the rest of the surface lands.
v1.0 supports only ``count=True``; anything else raises
NotImplementedError pointing at the roadmap.
"""

from __future__ import annotations

import pytest

from centaurodb import (
    CentauroModel,
    Collection,
    Engine,
)


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


class TestAggregateCount:
    def test_count_all(self, col):
        assert col.aggregate(Item, count=True) == 20

    def test_count_with_conditions(self, col):
        assert col.aggregate(Item.fields.bucket == "even", count=True) == 10

    def test_count_with_no_match(self, col):
        assert col.aggregate(Item.fields.bucket == "missing", count=True) == 0

    def test_count_matches_count_objects(self, col):
        # aggregate(count=True) and count_objects must agree —
        # one is the shorthand, the other the structured form.
        for cond in (Item, Item.fields.n > 10, Item.fields.bucket == "odd"):
            assert col.aggregate(cond, count=True) == col.count_objects(cond)


class TestAggregateNotYetImplemented:
    """sum / avg / group_by are reserved but not wired in v1.0.
    The error must point at the roadmap, not just say
    'NotImplementedError'."""

    def test_sum_raises_not_implemented(self, col):
        with pytest.raises(NotImplementedError, match="sum"):
            col.aggregate(Item, sum=Item.fields.n)

    def test_avg_raises_not_implemented(self, col):
        with pytest.raises(NotImplementedError, match="avg"):
            col.aggregate(Item, avg=Item.fields.n)

    def test_group_by_raises_not_implemented(self, col):
        with pytest.raises(NotImplementedError, match="group_by"):
            col.aggregate(Item, count=True, group_by=Item.fields.bucket)

    def test_error_mentions_roadmap(self, col):
        with pytest.raises(NotImplementedError, match="roadmap"):
            col.aggregate(Item, sum=Item.fields.n)


class TestAggregateValidation:
    def test_no_aggregate_specified_raises(self, col):
        with pytest.raises(ValueError, match="at least one"):
            col.aggregate(Item)

    def test_count_with_sum_combo_raises_not_implemented(self, col):
        # count + sum is a real future feature (combined SELECT)
        # but not v1.0 — must surface as NotImplementedError, not
        # a silent count.
        with pytest.raises(NotImplementedError):
            col.aggregate(Item, count=True, sum=Item.fields.n)
