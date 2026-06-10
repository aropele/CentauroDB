import pytest

from centaurodb import (
    AsyncCollection,
    AsyncTimeSeriesCollection,
    Engine,
    CentauroModel,
    CentauroModelSeries,
    CentauroValues,
    ThreadSafetyError,
)


class Item(CentauroModel):
    __centauro_name__ = "Item"
    color: str = ""


class Metric(CentauroModelSeries):
    __centauro_name__ = "Metric"
    unit: str = ""


@pytest.fixture
def conn():
    with Engine(check_same_thread=False) as c:
        yield c


def test_async_collection_rejects_check_same_thread():
    with Engine() as conn:
        with pytest.raises(ThreadSafetyError):
            AsyncCollection(conn, "items")


def test_async_timeseries_rejects_check_same_thread():
    with Engine() as conn:
        with pytest.raises(ThreadSafetyError):
            AsyncTimeSeriesCollection(conn, "metrics")


@pytest.mark.asyncio
async def test_async_collection_write_read(conn):
    col = AsyncCollection(conn, "items")
    obj = Item(color="red")
    await col.write_object(obj)
    assert obj._persisted
    loaded = await col.read_object_by_id(obj._row.id, Item)
    assert loaded.color == "red"


@pytest.mark.asyncio
async def test_async_collection_update(conn):
    col = AsyncCollection(conn, "items")
    obj = Item(color="blue")
    await col.write_object(obj)
    obj.color = "green"
    await col.update_object(obj)
    loaded = await col.read_object_by_id(obj._row.id, Item)
    assert loaded.color == "green"


@pytest.mark.asyncio
async def test_async_collection_refresh(conn):
    col = AsyncCollection(conn, "items")
    obj = Item(color="blue")
    await col.write_object(obj)

    # Modify directly in DB
    conn.execute(
        "UPDATE items_objects SET meta = json(?) WHERE id = ?",
        (Item(color="yellow").model_dump_json(), obj._row.id),
    )
    conn.commit()

    await col.refresh(obj)
    assert obj.color == "yellow"


@pytest.mark.asyncio
async def test_async_collection_create_view_and_describe(conn):
    col = AsyncCollection(conn, "items")
    await col.write_object(Item(color="red"))
    await col.create_view("all", Item)
    views = await col.describe_views()
    assert "items_view_all" in views


@pytest.mark.asyncio
async def test_async_timeseries_write_read(conn):
    col = AsyncTimeSeriesCollection(conn, "metrics")
    obj = Metric(
        unit="kg",
        values=[
            CentauroValues(time="2024-01-01T00:00:00", value=1.0),
            CentauroValues(time="2024-01-02T00:00:00", value=2.0),
        ],
    )
    await col.write_object(obj)
    assert obj._persisted
    loaded = await col.read_object_by_id(obj._row.id, Metric)
    assert loaded.unit == "kg"
    assert len(loaded.values) == 2
    assert loaded.values[0].value == 1.0


@pytest.mark.asyncio
async def test_async_timeseries_read_no_hydrate(conn):
    col = AsyncTimeSeriesCollection(conn, "metrics")
    obj = Metric(
        unit="m",
        values=[
            CentauroValues(time="2024-01-01T00:00:00", value=5.0),
        ],
    )
    await col.write_object(obj)
    loaded = await col.read_object_by_id(obj._row.id, Metric, hydrate_values=False)
    assert loaded.unit == "m"
    assert loaded.values == []


@pytest.mark.asyncio
async def test_async_timeseries_update_append(conn):
    col = AsyncTimeSeriesCollection(conn, "metrics")
    obj = Metric(
        unit="m",
        values=[
            CentauroValues(time="2024-01-01T00:00:00", value=1.0),
        ],
    )
    await col.write_object(obj)

    obj.values = [
        CentauroValues(time="2024-01-01T00:00:00", value=999.0),  # duplicate, ignored
        CentauroValues(time="2024-01-02T00:00:00", value=2.0),  # new
    ]
    await col.update_object(obj, values_mode="append")

    loaded = await col.read_object_by_id(obj._row.id, Metric)
    assert len(loaded.values) == 2
    assert loaded.values[0].value == 1.0  # original kept
    assert loaded.values[1].value == 2.0


@pytest.mark.asyncio
async def test_async_timeseries_update_replace(conn):
    col = AsyncTimeSeriesCollection(conn, "metrics")
    obj = Metric(
        unit="m",
        values=[
            CentauroValues(time="2024-01-01T00:00:00", value=1.0),
        ],
    )
    await col.write_object(obj)

    obj.values = [
        CentauroValues(time="2024-01-02T00:00:00", value=9.0),
    ]
    await col.update_object(obj, values_mode="replace")

    loaded = await col.read_object_by_id(obj._row.id, Metric)
    assert len(loaded.values) == 1
    assert loaded.values[0].value == 9.0


@pytest.mark.asyncio
async def test_async_collection_read_latest_object(conn):
    col = AsyncCollection(conn, "items")
    await col.write_object(Item(color="red"))
    await col.write_object(Item(color="blue"))

    latest = await col.read_latest_object(Item)
    assert latest is not None
    assert latest.color == "blue"


@pytest.mark.asyncio
async def test_async_collection_read_latest_object_empty(conn):
    col = AsyncCollection(conn, "items")
    result = await col.read_latest_object(Item)
    assert result is None


@pytest.mark.asyncio
async def test_async_timeseries_read_latest_object(conn):
    col = AsyncTimeSeriesCollection(conn, "metrics")
    await col.write_object(
        Metric(
            unit="kg",
            values=[
                CentauroValues(time="2024-01-01T00:00:00", value=1.0),
            ],
        )
    )
    await col.write_object(
        Metric(
            unit="m",
            values=[
                CentauroValues(time="2024-01-02T00:00:00", value=2.0),
            ],
        )
    )

    latest = await col.read_latest_object(Metric)
    assert latest is not None
    assert latest.unit == "m"
    assert len(latest.values) == 1
    assert latest.values[0].value == 2.0


@pytest.mark.asyncio
async def test_async_timeseries_read_latest_object_no_hydrate(conn):
    col = AsyncTimeSeriesCollection(conn, "metrics")
    await col.write_object(
        Metric(
            unit="kg",
            values=[
                CentauroValues(time="2024-01-01T00:00:00", value=1.0),
            ],
        )
    )

    latest = await col.read_latest_object(Metric, hydrate_values=False)
    assert latest is not None
    assert latest.unit == "kg"
    assert latest.values == []


@pytest.mark.asyncio
async def test_async_collection_write_objects(conn):
    col = AsyncCollection(conn, "items")
    objs = [Item(color="red"), Item(color="blue"), Item(color="green")]
    await col.write_objects(objs)

    assert all(o._persisted for o in objs)
    assert objs[0].row.id == 1
    assert objs[2].row.id == 3

    # Verify all are readable
    for o in objs:
        loaded = await col.read_object_by_id(o.row.id, Item)
        assert loaded.color == o.color


@pytest.mark.asyncio
async def test_async_collection_write_objects_empty(conn):
    col = AsyncCollection(conn, "items")
    await col.write_objects([])
    # Should not raise


@pytest.mark.asyncio
async def test_async_collection_read_objects_model_class(conn):
    col = AsyncCollection(conn, "items")
    await col.write_objects([Item(color="red"), Item(color="blue")])

    results = await col.read_objects(Item)
    assert len(results) == 2
    assert all(isinstance(r, Item) for r in results)


@pytest.mark.asyncio
async def test_async_collection_read_objects_model_class_empty(conn):
    col = AsyncCollection(conn, "items")
    results = await col.read_objects(Item)
    assert results == []


@pytest.mark.asyncio
async def test_async_timeseries_write_objects(conn):
    col = AsyncTimeSeriesCollection(conn, "metrics")
    objs = [
        Metric(
            unit="kg",
            values=[CentauroValues(time="2024-01-01T00:00:00", value=1.0)],
        ),
        Metric(
            unit="m",
            values=[
                CentauroValues(time="2024-01-02T00:00:00", value=2.0),
                CentauroValues(time="2024-01-03T00:00:00", value=3.0),
            ],
        ),
    ]
    await col.write_objects(objs)

    assert all(o._persisted for o in objs)
    loaded = await col.read_object_by_id(objs[1].row.id, Metric)
    assert loaded.unit == "m"
    assert len(loaded.values) == 2


@pytest.mark.asyncio
async def test_async_timeseries_read_objects_model_class(conn):
    col = AsyncTimeSeriesCollection(conn, "metrics")
    await col.write_objects(
        [
            Metric(unit="kg"),
            Metric(unit="m"),
        ]
    )

    results = await col.read_objects(Metric)
    assert len(results) == 2
    assert all(isinstance(r, Metric) for r in results)


@pytest.mark.asyncio
async def test_async_timeseries_read_objects_hydrate_values_false(conn):
    col = AsyncTimeSeriesCollection(conn, "metrics")
    await col.write_objects(
        [
            Metric(
                unit="kg",
                values=[CentauroValues(time="2024-01-01T00:00:00", value=1.0)],
            ),
            Metric(
                unit="m",
                values=[CentauroValues(time="2024-01-02T00:00:00", value=2.0)],
            ),
        ]
    )

    results = await col.read_objects(Metric, hydrate_values=False)
    assert len(results) == 2
    for obj in results:
        assert obj.values == []
        assert obj._values_hydrated is False


@pytest.mark.asyncio
async def test_async_timeseries_read_objects_hydrate_values_true(conn):
    col = AsyncTimeSeriesCollection(conn, "metrics")
    await col.write_objects(
        [
            Metric(
                unit="kg",
                values=[CentauroValues(time="2024-01-01T00:00:00", value=1.0)],
            ),
            Metric(
                unit="m",
                values=[
                    CentauroValues(time="2024-01-02T00:00:00", value=2.0),
                    CentauroValues(time="2024-01-03T00:00:00", value=3.0),
                ],
            ),
        ]
    )

    results = await col.read_objects(Metric, hydrate_values=True)
    assert len(results) == 2
    assert len(results[0].values) == 1
    assert results[0].values[0].value == 1.0
    assert results[0]._values_hydrated is True
    assert len(results[1].values) == 2
    assert results[1].values[0].value == 2.0
    assert results[1].values[1].value == 3.0
    assert results[1]._values_hydrated is True
